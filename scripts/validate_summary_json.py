#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

REQUIRED_ROOT = ["header", "summary", "topics"]
REQUIRED_HEADER = ["title", "date", "stats", "hot_word"]
REQUIRED_TOPIC = ["title", "time", "summary", "detail", "quotes"]
REQUIRED_QUOTE = ["name", "content"]


def fail(msg: str):
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str):
    print(f"[WARN] {msg}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        fail("用法: validate_summary_json.py <summary.enriched.json>")

    path = Path(sys.argv[1]).resolve()
    if not path.exists():
        fail(f"文件不存在: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"JSON 解析失败: {e}")

    for key in REQUIRED_ROOT:
        if key not in data:
            fail(f"缺少根字段: {key}")

    if not isinstance(data["summary"], list):
        fail("summary 必须是数组")
    if not isinstance(data["topics"], list) or not data["topics"]:
        fail("topics 必须是非空数组")

    header = data["header"]
    for key in REQUIRED_HEADER:
        if key not in header or not str(header[key]).strip():
            fail(f"header 缺少字段或为空: {key}")

    for i, topic in enumerate(data["topics"], 1):
        for key in REQUIRED_TOPIC:
            if key not in topic:
                fail(f"topics[{i}] 缺少字段: {key}")
        if not isinstance(topic["quotes"], list):
            fail(f"topics[{i}].quotes 必须是数组")
        for j, quote in enumerate(topic["quotes"], 1):
            if isinstance(quote, dict):
                for key in REQUIRED_QUOTE:
                    if key not in quote or not str(quote[key]).strip():
                        fail(f"topics[{i}].quotes[{j}] 缺少字段或为空: {key}")
                if "avatar_name" not in quote and "avatar_username" not in quote:
                    warn(f"topics[{i}].quotes[{j}] 未提供 avatar_name/avatar_username，可能退回占位头像")
            elif isinstance(quote, list):
                if len(quote) < 2:
                    fail(f"topics[{i}].quotes[{j}] 旧格式长度不足")
                warn(f"topics[{i}].quotes[{j}] 仍是旧数组格式，建议改成对象格式")
            else:
                fail(f"topics[{i}].quotes[{j}] 格式非法")

    report_meta = data.get("report_meta")
    if not report_meta:
        fail("缺少 report_meta，说明未经过 enrich 流程")

    activity = data.get("activity")
    if not isinstance(activity, list) or not activity:
        fail("activity 必须是非空数组，说明热度数据异常")
    granularity = report_meta.get("activity_granularity", "hour")
    if granularity not in ("hour", "day"):
        fail("report_meta.activity_granularity 必须是 hour 或 day")
    if granularity == "hour" and len(activity) != 24:
        fail("小时热度 activity 必须包含 24 个点")
    if granularity == "day" and not all(item.get("date") for item in activity):
        fail("每日热度 activity 必须为每个点提供 date")
    if granularity == "day" and len(activity) < 2:
        warn("每日热度不足 2 天，建议改用小时热度")

    non_zero = [x for x in activity if x.get("count", 0) > 0]
    if not non_zero:
        warn("activity 全为 0，可能当天确实无消息，也可能统计异常")

    top_speakers = data.get("top_speakers")
    if top_speakers is None:
        fail("缺少 top_speakers，说明 enrich 数据不完整")

    keyword_tags = data.get("keyword_tags")
    if keyword_tags is None:
        fail("缺少 keyword_tags，说明 enrich 数据不完整")

    stats_line = header.get("stats", "")
    if "文本" not in stats_line or "人参与" not in stats_line:
        warn("header.stats 口径看起来不像标准统计行")

    summary_text = " ".join(str(x) for x in data.get("summary", []))
    if "减少" not in summary_text or "tokens" not in summary_text:
        warn("省流版里未检测到 token 节省说明")

    print(f"[OK] 校验通过: {path}")


if __name__ == "__main__":
    main()
