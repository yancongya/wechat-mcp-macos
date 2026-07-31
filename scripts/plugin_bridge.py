#!/usr/bin/env python3
"""Hana 插件与仓库查询核心之间的 JSON stdin/stdout 桥。"""

from __future__ import annotations

import base64
import json
import sys
from collections import defaultdict

from chat_query import query_messages, query_messages_page, resolve_chat
from date_range import resolve_date_range
from wechat_mcp_macos.config import load_config
from wechat_mcp_macos.db import WeChatDB
from wechat_mcp_macos.key_extractor import (
    get_cached_keys,
    get_wechat_pid,
    is_wechat_signed,
)


def date_range_from_input(data: dict):
    return resolve_date_range(
        date_value=data.get("date"),
        start_value=data.get("start"),
        end_value=data.get("end"),
        hours=data.get("hours"),
        today=bool(data.get("today")),
        yesterday=bool(data.get("yesterday")),
        last=data.get("last"),
        day_start=data.get("day_start") or "04:00",
    )


def format_read(data: dict, messages: list[dict], page: dict, date_range) -> str:
    sender_filter = (data.get("sender") or "").strip()
    if sender_filter:
        messages = [m for m in messages if m["sender"] == sender_filter]
    if not messages:
        return f"{date_range.label}内无消息"

    lines = [f"共 {len(messages)} 条消息 · {date_range.label}"]
    if page.get("has_more"):
        lines.append(f"还有下一页，next_cursor={page['next_cursor']}")

    if data.get("format") == "grouped":
        grouped = defaultdict(list)
        for message in messages:
            grouped[message["sender"]].append(message)
        for sender, items in sorted(grouped.items(), key=lambda item: -len(item[1])):
            lines.append(f"\n── {sender} ({len(items)} 条) ──")
            for item in items:
                lines.append(f"  [{item['time_str']}] {item['text'][:200]}")
    else:
        for item in messages:
            lines.append(f"[{item['time_str']}] {item['sender']}: {item['text'][:300]}")
    return "\n".join(lines)


def handle_read(data: dict, db: WeChatDB) -> dict:
    chat = (data.get("chat") or "").strip()
    if not chat:
        raise ValueError("缺少 chat")
    username, name, is_group = resolve_chat(db, chat)
    date_range = date_range_from_input(data)
    page = query_messages_page(
        db, username, name,
        int(date_range.start.timestamp()), int(date_range.end.timestamp()),
        page_size=data.get("page_size") or data.get("limit") or 100,
        cursor=data.get("cursor"),
    )
    return {
        "ok": True,
        "chat": name,
        "wxid": username,
        "is_group": is_group,
        "range": date_range.to_dict(),
        "messages": page["messages"],
        "has_more": page["has_more"],
        "next_cursor": page["next_cursor"],
        "text": format_read(data, page["messages"], page, date_range),
    }


def all_chat_targets(db: WeChatDB) -> list[tuple[str, str, bool]]:
    seen = set()
    targets = []
    for session in db.get_recent_sessions(limit=500):
        username = session.get("username")
        if not username or username in seen:
            continue
        seen.add(username)
        targets.append((username, session.get("name") or username, bool(session.get("is_group"))))
    return targets


def encode_search_cursor(item: dict) -> str:
    payload = json.dumps(
        {"ts": item["timestamp"], "local_id": item.get("local_id", 0), "wxid": item.get("wxid", "")},
        separators=(",", ":"), ensure_ascii=False,
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_search_cursor(cursor: str | None) -> tuple[int, int, str] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        return int(payload["ts"]), int(payload["local_id"]), str(payload["wxid"])
    except Exception as exc:
        raise ValueError("无效的搜索 cursor") from exc


def handle_search(data: dict, db: WeChatDB) -> dict:
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        raise ValueError("缺少 keyword")
    date_range = date_range_from_input(data)
    if data.get("chat"):
        targets = [resolve_chat(db, data["chat"])]
    else:
        targets = all_chat_targets(db)

    page_size = max(1, min(int(data.get("page_size") or data.get("limit") or 50), 500))
    scan_per_chat = max(page_size, min(int(data.get("max_scan_per_chat") or 5000), 20000))
    cursor_key = decode_search_cursor(data.get("cursor"))
    matches = []
    truncated_chats = []
    for username, name, is_group in targets:
        messages, truncated = query_messages(
            db, username, name,
            int(date_range.start.timestamp()), int(date_range.end.timestamp()),
            scan_per_chat,
        )
        if truncated:
            truncated_chats.append(name)
        for message in messages:
            if keyword.casefold() in (message.get("text") or "").casefold():
                matches.append({**message, "chat": name, "wxid": username, "is_group": is_group})

    matches.sort(key=lambda item: (item["timestamp"], item.get("local_id", 0), item.get("wxid", "")), reverse=True)
    total_found = len(matches)
    if cursor_key:
        matches = [
            item for item in matches
            if (item["timestamp"], item.get("local_id", 0), item.get("wxid", "")) < cursor_key
        ]
    has_more = len(matches) > page_size
    matches = matches[:page_size]
    next_cursor = encode_search_cursor(matches[-1]) if has_more and matches else None
    group_by = data.get("group_by") or "none"
    lines = [f"找到 {total_found} 条匹配消息 · {date_range.label}"]
    if has_more:
        lines.append(f"还有下一页，next_cursor={next_cursor}")
    if truncated_chats:
        lines.append(f"以下会话达到扫描上限：{', '.join(truncated_chats[:10])}")

    if group_by == "sender":
        grouped = defaultdict(list)
        for item in matches:
            grouped[item["sender"]].append(item)
        for sender, items in sorted(grouped.items(), key=lambda item: -len(item[1])):
            lines.append(f"\n── {sender} ({len(items)} 条) ──")
            for item in items:
                lines.append(f"  [{item['time_str']}] ({item['chat']}) {item['text'][:200]}")
    elif group_by == "time":
        grouped = defaultdict(list)
        for item in matches:
            grouped[item["time_str"][:13]].append(item)
        for hour in sorted(grouped):
            lines.append(f"\n── {hour}:00 ({len(grouped[hour])} 条) ──")
            for item in grouped[hour]:
                lines.append(f"  {item['sender']} ({item['chat']}): {item['text'][:200]}")
    else:
        for item in matches:
            lines.append(f"[{item['time_str']}] {item['chat']} · {item['sender']}: {item['text'][:300]}")

    return {
        "ok": True,
        "keyword": keyword,
        "range": date_range.to_dict(),
        "total_found": total_found,
        "returned": len(matches),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "truncated_chats": truncated_chats,
        "matches": matches,
        "text": "\n".join(lines),
    }


def handle_groups(db: WeChatDB) -> dict:
    groups = db.get_groups()
    lines = [f"共 {len(groups)} 个群聊:"]
    lines.extend(f"  {group['name']} ({group['username']})" for group in groups)
    return {"ok": True, "groups": groups, "text": "\n".join(lines)}


def handle_status() -> dict:
    cfg = load_config()
    keys = get_cached_keys()
    pid = get_wechat_pid()
    signed = is_wechat_signed()
    status = {
        "database": bool(cfg.get("db_dir")),
        "key_count": len(keys or {}),
        "wechat_pid": pid,
        "signed": signed,
    }
    lines = [
        f"数据库目录: {'✅' if status['database'] else '❌ 未配置'}",
        f"加密密钥: {'✅ ' + str(status['key_count']) + ' 个数据库' if status['key_count'] else '❌ 未提取'}",
        f"微信进程: {'✅ 运行中 (PID ' + str(pid) + ')' if pid else '❌ 未运行'}",
        f"微信签名: {'✅ ad-hoc' if signed else '❌ hardened runtime'}",
    ]
    return {"ok": True, "status": status, "text": "=== 微信 MCP 状态 ===\n" + "\n".join(lines)}


def main() -> None:
    operations = {"read", "search", "groups", "status"}
    if len(sys.argv) != 2 or sys.argv[1] not in operations:
        raise SystemExit("用法: plugin_bridge.py read|search|groups|status，参数通过 JSON stdin 传入")
    try:
        data = json.load(sys.stdin)
        operation = sys.argv[1]
        if operation == "status":
            result = handle_status()
        else:
            db = WeChatDB(load_config()["db_dir"], get_cached_keys())
            if operation == "read":
                result = handle_read(data, db)
            elif operation == "search":
                result = handle_search(data, db)
            else:
                result = handle_groups(db)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
