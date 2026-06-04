#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT_DIR/backend/.venv/bin/python"
OUT_BASE="$ROOT_DIR/prompts/summaries"

if [[ $# -lt 1 ]]; then
  echo "用法:"
  echo "  $0 prepare <群名或wxid> [hours=0, 0表示今天自然日]"
  echo "  $0 render <summary.json路径> [output.png路径]"
  exit 1
fi

cmd="$1"
shift || true

slugify() {
  printf '%s' "$1" | tr ' /:@' '_' | tr -cd '[:alnum:]_-.一-龥'
}

case "$cmd" in
  prepare)
    if [[ $# -lt 1 ]]; then
      echo "缺少群名或 wxid"
      exit 1
    fi

    chat="$1"
    hours="${2:-0}"
    stamp="$(date +%F-%H%M%S)"
    slug="$(slugify "$chat")"
    outdir="$OUT_BASE/${slug}-${stamp}"
    mkdir -p "$outdir"

    echo "[1/4] 解析群信息..."
    "$PY" "$ROOT_DIR/prompts/render.py" "$chat" --hours "$hours" --json > "$outdir/context.json"
    wxid="$($PY - <<PY
import json
with open('$outdir/context.json', encoding='utf-8') as f:
    data=json.load(f)
print(data['wxid'])
PY
)"

    echo "[2/4] 生成规则摘要..."
    "$PY" "$ROOT_DIR/pipeline.py" --dry-run --hours "$hours" --chat "$wxid" --json > "$outdir/pipeline.json"

    echo "[3/4] 生成 LLM JSON prompt..."
    "$PY" "$ROOT_DIR/prompts/render.py" "$chat" --hours "$hours" > "$outdir/prompt.txt"

    echo "[4/4] 准备完成"
    cat <<EOF
输出目录：$outdir
- 上下文：$outdir/context.json
- 规则摘要：$outdir/pipeline.json
- LLM Prompt：$outdir/prompt.txt
- 下一步把 LLM 返回内容保存为：$outdir/summary.json
- 然后执行：
  $0 render "$outdir/summary.json"
EOF
    ;;

  render)
    if [[ $# -lt 1 ]]; then
      echo "缺少 summary.json 路径"
      exit 1
    fi

    input_json="$1"
    if [[ ! -f "$input_json" ]]; then
      echo "文件不存在：$input_json"
      exit 1
    fi

    output_png="${2:-${input_json%.json}.png}"
    enriched_json="${input_json%.json}.enriched.json"
    "$PY" "$ROOT_DIR/scripts/enrich_summary_json.py" "$input_json" > "$enriched_json"
    "$PY" "$ROOT_DIR/scripts/validate_summary_json.py" "$enriched_json"
    "$PY" "$ROOT_DIR/summary_img.py" --input "$enriched_json" --output "$output_png"
    echo "已生成：$output_png"
    echo "增强版 JSON：$enriched_json"
    ;;

  *)
    echo "未知命令：$cmd"
    exit 1
    ;;
esac
