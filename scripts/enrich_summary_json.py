#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path


def parse_text_count(summary_text: str) -> int:
    m = re.search(r"共\s*\d+\s*条消息,\s*(\d+)\s*条文本", summary_text or "")
    return int(m.group(1)) if m else 0


def main():
    if len(sys.argv) < 2:
        print("用法: enrich_summary_json.py <summary.json> [context.json] [pipeline.json]", file=sys.stderr)
        sys.exit(1)

    summary_path = Path(sys.argv[1]).resolve()
    base_dir = summary_path.parent
    context_path = Path(sys.argv[2]).resolve() if len(sys.argv) >= 3 else base_dir / "context.json"
    pipeline_path = Path(sys.argv[3]).resolve() if len(sys.argv) >= 4 else base_dir / "pipeline.json"

    data = json.loads(summary_path.read_text(encoding="utf-8"))
    context = json.loads(context_path.read_text(encoding="utf-8")) if context_path.exists() else {}

    pipeline_text = pipeline_path.read_text(encoding="utf-8") if pipeline_path.exists() else ""
    start = pipeline_text.find("{")
    end = pipeline_text.rfind("}")
    pipeline = json.loads(pipeline_text[start:end + 1]) if start != -1 and end != -1 and end > start else {}

    compressed = context.get("compressed_context", {})
    metrics = dict(compressed.get("metrics", {}))
    if pipeline:
        metrics.setdefault("message_count", pipeline.get("message_count", 0))
        metrics.setdefault("text_count", parse_text_count(pipeline.get("summary", "")))
    metrics.setdefault("sender_count", context.get("stats", {}).get("sender_count", 0))

    if metrics:
        msg_count = metrics.get("message_count", 0)
        sender_count = metrics.get("sender_count", 0)
        text_count = metrics.get("text_count", 0)
        data.setdefault("header", {})["stats"] = f"约{msg_count}条消息 · {sender_count}人参与 · 文本{text_count}条"

    data["report_meta"] = metrics
    data["activity"] = compressed.get("activity_by_hour", [])
    data["top_speakers"] = compressed.get("top_speakers", [])[:6]
    data["keyword_tags"] = compressed.get("hot_words", [])[:8]

    summary_items = data.get("summary", [])
    prefix = []
    raw_chars = metrics.get("raw_chars", 0)
    compressed_chars = metrics.get("compressed_chars", 0)
    tokens_saved = metrics.get("estimated_tokens_saved", 0)
    chars_saved = metrics.get("chars_saved", 0)
    if raw_chars and compressed_chars:
        prefix.append(f"原始文本约 {raw_chars} 字，压缩提取为 {compressed_chars} 字，减少约 {chars_saved} 字。")
    if tokens_saved:
        prefix.append(f"按当前中文场景粗估，约减少 {tokens_saved} 个输入 tokens 消耗。")
    if prefix:
        data["summary"] = prefix + summary_items

    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
