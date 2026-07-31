#!/usr/bin/env python3
"""项目层微信消息查询适配器。

绕开第三方包对私聊 sender 置空的限制，并原生支持 [start, end) 区间。
只读取已解密缓存数据库，不修改微信原始数据。
"""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import zstandard

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from date_range import resolve_date_range  # noqa: E402
from wechat_mcp_macos.config import load_config  # noqa: E402
from wechat_mcp_macos.db import WeChatDB, _clean_msg_text  # noqa: E402
from wechat_mcp_macos.key_extractor import get_cached_keys  # noqa: E402


def resolve_chat(db: WeChatDB, query: str) -> tuple[str, str, bool]:
    groups = db.get_groups()
    for group in groups:
        if query in (group.get("name"), group.get("username")):
            return group["username"], group.get("name") or query, True

    for session in db.get_recent_sessions(limit=500):
        if query in (session.get("name"), session.get("username")):
            return session["username"], session.get("name") or query, bool(session.get("is_group"))

    username = db.resolve_username(query)
    if not username:
        raise ValueError(f"未找到匹配的聊天：{query}")
    return username, query, "@chatroom" in username


def load_name2id(conn: sqlite3.Connection) -> tuple[dict[int, str], dict[str, int]]:
    rows = conn.execute("SELECT rowid, user_name FROM Name2Id").fetchall()
    id_to_name = {int(rowid): username for rowid, username in rows if username}
    return id_to_name, {username: rowid for rowid, username in id_to_name.items()}


def resolve_self_username(cfg: dict, name_to_id: dict[str, int], contact_username: str) -> str:
    """Resolve the account owner's wxid without relying on optional config."""
    configured = (cfg.get("self_name") or "").strip()
    if configured in name_to_id:
        return configured

    account_dir = Path(cfg.get("db_dir", "")).resolve().parent.name
    inferred = account_dir.rsplit("_", 1)[0] if account_dir.startswith("wxid_") else ""
    if inferred in name_to_id:
        return inferred

    # In a private table the remaining stable wxid is the owner. This fallback
    # is intentionally limited to avoid guessing among group members.
    candidates = [name for name in name_to_id if name.startswith("wxid_") and name != contact_username]
    return candidates[0] if len(candidates) == 1 else ""


def decode_content(content, compression_type: int, decompressor) -> str | None:
    if content is None:
        return None
    if isinstance(content, bytes):
        try:
            if compression_type == 4:
                return decompressor.decompress(content).decode("utf-8", errors="replace")
            return content.decode("utf-8", errors="replace")
        except Exception:
            return None
    return str(content)


def display_name(db: WeChatDB, username: str, fallback: str = "") -> str:
    db._load_contacts()
    return db._contacts.get(username, fallback or username)


def encode_cursor(create_time: int, local_id: int) -> str:
    payload = json.dumps({"ts": int(create_time), "local_id": int(local_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[int, int]:
    if not cursor:
        return 0, 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        return int(payload["ts"]), int(payload["local_id"])
    except Exception as exc:
        raise ValueError("无效的分页 cursor") from exc


def query_messages_page(
    db: WeChatDB,
    username: str,
    chat_name: str,
    start_ts: int,
    end_ts: int,
    page_size: int = 100,
    cursor: str | None = None,
) -> dict:
    """Read one stable page ordered by (create_time, local_id)."""
    page_size = max(1, min(int(page_size), 500))
    cursor_ts, cursor_local_id = decode_cursor(cursor)
    db_path, table_name = db._find_msg_table(username)
    if not db_path:
        return {"messages": [], "has_more": False, "next_cursor": None}

    conn = sqlite3.connect(db_path)
    try:
        id_to_name, name_to_id = load_name2id(conn)
        cfg = load_config()
        self_username = resolve_self_username(cfg, name_to_id, username)
        self_id = name_to_id.get(self_username)
        contact_id = name_to_id.get(username)
        rows = conn.execute(
            f'''SELECT local_id, local_type, create_time, real_sender_id,
                       message_content, WCDB_CT_message_content
                FROM [{table_name}]
                WHERE create_time >= ? AND create_time < ?
                  AND (create_time > ? OR (create_time = ? AND local_id > ?))
                ORDER BY create_time ASC, local_id ASC
                LIMIT ?''',
            (start_ts, end_ts, cursor_ts, cursor_ts, cursor_local_id, page_size + 1),
        ).fetchall()
    finally:
        conn.close()

    has_more = len(rows) > page_size
    rows = rows[:page_size]
    decompressor = zstandard.ZstdDecompressor()
    is_group = "@chatroom" in username
    messages = []

    for local_id, local_type, create_time, sender_id, content, compression_type in rows:
        text = decode_content(content, compression_type, decompressor)
        if text is None:
            continue

        sender_username = id_to_name.get(int(sender_id or 0), "")
        is_me = bool(self_id and sender_id == self_id)

        if ":\n" in text:
            possible_sender, body = text.split(":\n", 1)
            if possible_sender.strip():
                sender_username = possible_sender.strip()
                text = body

        real_type = int(local_type or 0) & 0xFFFFFFFF
        cleaned = _clean_msg_text(text)
        if cleaned is None:
            continue

        if real_type == 3:
            cleaned = "[图片]"
        elif real_type == 34:
            cleaned = "[语音]"
        elif real_type == 43:
            cleaned = "[视频]"
        elif real_type == 47:
            cleaned = "[表情]"
        elif real_type == 50:
            cleaned = "[通话]"
        elif real_type in (49, 244813135921):
            cleaned = cleaned if cleaned.startswith("[") else f"[链接/文件] {cleaned[:300]}"
        elif real_type in (10000, 10002):
            cleaned = f"[系统] {cleaned[:100]}"

        if is_me:
            sender = "我"
            sender_type = "self"
            sender_username = self_username
        elif is_group:
            sender = display_name(db, sender_username, sender_username or str(sender_id))
            sender_type = "member"
        else:
            sender = chat_name
            sender_type = "contact"
            if contact_id and sender_id not in (contact_id, self_id):
                sender = display_name(db, sender_username, chat_name)

        messages.append({
            "local_id": int(local_id),
            "sender": sender,
            "sender_type": sender_type,
            "sender_username": sender_username,
            "text": cleaned,
            "timestamp": int(create_time),
            "time_str": datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M"),
            "type": real_type,
        })

    next_cursor = encode_cursor(rows[-1][2], rows[-1][0]) if has_more and rows else None
    return {"messages": messages, "has_more": has_more, "next_cursor": next_cursor}


def query_messages(
    db: WeChatDB,
    username: str,
    chat_name: str,
    start_ts: int,
    end_ts: int,
    max_messages: int,
) -> tuple[list[dict], bool]:
    """Collect pages up to max_messages without losing same-second rows."""
    max_messages = max(1, int(max_messages))
    messages = []
    cursor = None
    has_more = False
    while len(messages) < max_messages:
        page = query_messages_page(
            db, username, chat_name, start_ts, end_ts,
            page_size=min(500, max_messages - len(messages)), cursor=cursor,
        )
        messages.extend(page["messages"])
        has_more = page["has_more"]
        cursor = page["next_cursor"]
        if not has_more or not cursor:
            break
    return messages, has_more


def build_stats(messages: list[dict]) -> dict:
    hourly = Counter(datetime.fromtimestamp(m["timestamp"]).strftime("%H") for m in messages)
    speakers = Counter(m["sender"] for m in messages if m.get("sender"))
    speaker_meta = {}
    for message in messages:
        name = message.get("sender")
        if name:
            speaker_meta.setdefault(name, {
                "avatar_username": message.get("sender_username") or "",
                "sender_type": message.get("sender_type") or "",
            })
    type_counts = Counter(str(m["type"]) for m in messages)
    peak = hourly.most_common(1)[0][0] if hourly else None
    activity = [{"hour": f"{hour:02d}", "count": hourly.get(f"{hour:02d}", 0)} for hour in range(24)]
    ranked_speakers = speakers.most_common()
    visible_speakers = ranked_speakers[:8]
    self_entry = next((item for item in ranked_speakers if speaker_meta[item[0]]["sender_type"] == "self"), None)
    if self_entry and self_entry not in visible_speakers:
        visible_speakers = visible_speakers[:7] + [self_entry]
    return {
        "message_count": len(messages),
        "sender_count": len(speakers),
        "text_count": type_counts.get("1", 0),
        "peak_hour": f"{peak}:00 - {int(peak) + 1:02d}:00" if peak else "暂无数据",
        "type_counts": dict(type_counts),
        "activity_by_hour": activity,
        "top_speakers": [
            {
                "name": name,
                "avatar_name": name,
                "avatar_username": speaker_meta[name]["avatar_username"],
                "sender_type": speaker_meta[name]["sender_type"],
                "count": count,
            }
            for name, count in visible_speakers
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="按日期区间读取微信聊天记录")
    parser.add_argument("chat", help="群名、联系人或 wxid")
    parser.add_argument("--date", dest="date_value")
    parser.add_argument("--start", dest="start_value")
    parser.add_argument("--end", dest="end_value")
    parser.add_argument("--hours", type=int)
    parser.add_argument("--today", action="store_true")
    parser.add_argument("--yesterday", action="store_true")
    parser.add_argument("--last")
    parser.add_argument("--day-start", default="04:00")
    parser.add_argument("--max-messages", type=int, default=10000)
    parser.add_argument("--page-size", type=int, help="分页模式：每页 1-500 条")
    parser.add_argument("--cursor", help="分页模式：上一页返回的 cursor")
    args = parser.parse_args()

    try:
        date_range = resolve_date_range(
            date_value=args.date_value,
            start_value=args.start_value,
            end_value=args.end_value,
            hours=args.hours,
            today=args.today,
            yesterday=args.yesterday,
            last=args.last,
            day_start=args.day_start,
        )
        db = WeChatDB(load_config()["db_dir"], get_cached_keys())
        username, name, is_group = resolve_chat(db, args.chat)
        if args.page_size:
            page = query_messages_page(
                db, username, name,
                int(date_range.start.timestamp()), int(date_range.end.timestamp()),
                page_size=args.page_size, cursor=args.cursor,
            )
            messages = page["messages"]
            truncated = page["has_more"]
        else:
            messages, truncated = query_messages(
                db, username, name,
                int(date_range.start.timestamp()), int(date_range.end.timestamp()),
                max(1, args.max_messages),
            )
            page = {"has_more": truncated, "next_cursor": None}
    except ValueError as exc:
        parser.error(str(exc))

    output = {
        "chat": name,
        "wxid": username,
        "is_group": is_group,
        "range": date_range.to_dict(),
        "truncated": truncated,
        "has_more": page["has_more"],
        "next_cursor": page["next_cursor"],
        "page_size": args.page_size,
        "max_messages": args.max_messages,
        "stats": build_stats(messages),
        "messages": messages,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
