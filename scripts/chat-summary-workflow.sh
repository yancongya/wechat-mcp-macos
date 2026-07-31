#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT_DIR/backend/.venv/bin/python"
OUT_BASE="$ROOT_DIR/prompts/summaries"

usage() {
  cat <<'EOF'
用法：
  chat-summary-workflow.sh prepare --chat <群名/联系人/wxid> [日期参数]
  chat-summary-workflow.sh render <summary.json> [output.png]

日期参数（选一组）：
  --today | --yesterday | --date YYYY-MM-DD
  --start YYYY-MM-DD[ HH:MM] --end YYYY-MM-DD[ HH:MM]
  --last 7d | --hours 24
  --day-start HH:MM（默认 04:00）
  --max-messages N（默认 10000）
  --skip-cleanup（跳过本次运行前清理）

兼容旧调用：
  chat-summary-workflow.sh prepare <群名/wxid> [hours]
EOF
}

slugify() {
  printf '%s' "$1" | tr ' /:@' '_' | tr -cd '[:alnum:]_-.一-龥'
}

[[ $# -ge 1 ]] || { usage; exit 1; }
cmd="$1"
shift

case "$cmd" in
  prepare)
    chat=""
    skip_cleanup=0
    render_args=()

    if [[ $# -gt 0 && "$1" != --* ]]; then
      chat="$1"
      shift
      if [[ $# -gt 0 && "$1" =~ ^[0-9]+$ ]]; then
        render_args+=(--hours "$1")
        shift
      fi
    fi

    while [[ $# -gt 0 ]]; do
      case "$1" in
        --chat) chat="${2:-}"; shift 2 ;;
        --today|--yesterday) render_args+=("$1"); shift ;;
        --skip-cleanup) skip_cleanup=1; shift ;;
        --date|--start|--end|--last|--hours|--day-start|--max-messages)
          [[ $# -ge 2 ]] || { echo "参数 $1 缺少值" >&2; exit 1; }
          render_args+=("$1" "$2"); shift 2 ;;
        *) echo "未知参数：$1" >&2; usage; exit 1 ;;
      esac
    done

    [[ -n "$chat" ]] || { echo "缺少 --chat" >&2; exit 1; }

    if [[ "$skip_cleanup" -eq 0 ]] && "$PY" - "$ROOT_DIR/cleanup-policy.json" <<'PY'
import json, sys
policy = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if policy.get("run_before_prepare", True) else 1)
PY
    then
      echo "[清理] 按 cleanup-policy.json 执行运行前清理..."
      "$PY" "$ROOT_DIR/cleanup.py"
    fi

    stamp="$(date +%F-%H%M%S)"
    slug="$(slugify "$chat")"
    outdir="$OUT_BASE/${slug}-${stamp}"
    mkdir -p "$outdir"

    echo "[1/4] 解析日期、聊天与消息..."
    "$PY" "$ROOT_DIR/prompts/render.py" "$chat" "${render_args[@]}" --json > "$outdir/context.json"

    echo "[2/4] 导出统计摘要..."
    "$PY" - "$outdir/context.json" "$outdir/pipeline.json" <<'PY'
import json, sys
src, dest = sys.argv[1:3]
data = json.load(open(src, encoding="utf-8"))
out = {
    "group": data["wxid"],
    "message_count": data["stats"]["total"],
    "summary": (
        f"共 {data['stats']['total']} 条消息, "
        f"{data['compressed_context']['metrics'].get('text_count', 0)} 条文本, "
        f"{data['stats']['sender_count']} 人参与"
    ),
    "range": data["range"],
    "truncated": data.get("truncated", False),
}
json.dump(out, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY

    echo "[3/4] 导出 LLM JSON Prompt..."
    "$PY" - "$outdir/context.json" "$outdir/prompt.txt" <<'PY'
import json, sys
src, dest = sys.argv[1:3]
data = json.load(open(src, encoding="utf-8"))
open(dest, "w", encoding="utf-8").write(data.get("prompt") or "")
PY

    echo "[4/4] 准备完成"
    cat <<EOF
输出目录：$outdir
- 上下文：$outdir/context.json
- 统计：$outdir/pipeline.json
- LLM Prompt：$outdir/prompt.txt
- 将 LLM 返回的 JSON 保存为：$outdir/summary.json
- 然后执行：
  $0 render "$outdir/summary.json"
EOF
    ;;

  render)
    [[ $# -ge 1 ]] || { echo "缺少 summary.json 路径" >&2; exit 1; }
    input_json="$1"
    [[ -f "$input_json" ]] || { echo "文件不存在：$input_json" >&2; exit 1; }
    output_png="${2:-${input_json%.json}.png}"
    enriched_json="${input_json%.json}.enriched.json"
    "$PY" "$ROOT_DIR/scripts/enrich_summary_json.py" "$input_json" > "$enriched_json"
    "$PY" "$ROOT_DIR/scripts/validate_summary_json.py" "$enriched_json"
    "$PY" "$ROOT_DIR/summary_img.py" --input "$enriched_json" --output "$output_png"
    echo "已生成：$output_png"
    echo "增强版 JSON：$enriched_json"
    ;;

  *) usage; exit 1 ;;
esac
