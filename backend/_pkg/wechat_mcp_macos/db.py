"""
WeChat database query layer — contacts, groups, messages, and search.

Adapted from the WeChatDB class in the wechat-summary-share project.

Credits:
  - Zhan (盏): WeChatDB query interface, message parsing, FTS search,
    zstd decompression, sliding-window keyword matching
  - ylytdeng/wechat-decrypt: Database structure knowledge
"""

import hashlib
import logging
import os
import re
import sqlite3
import tempfile
from datetime import datetime

import zstandard as zstd

from .decryptor import decrypt_database, decrypt_wal

logger = logging.getLogger(__name__)

_zstd_dctx = zstd.ZstdDecompressor()

# Extract <title> from XML messages
_RE_TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


def _clean_msg_text(text: str) -> str | None:
    """Clean WeChat message text, converting XML messages to readable text.

    Returns None for messages that should be skipped (system, pat-pat, etc.).
    """
    if not text:
        return None

    stripped = text.strip()

    # Plain text: doesn't start with <
    if not stripped.startswith("<"):
        return text

    # System messages → skip
    if "<sysmsg" in stripped:
        return None

    # appmsg: quoted replies, links, files, pat-pat
    if "<appmsg" in stripped:
        m = _RE_TITLE.search(stripped)
        if m:
            title = m.group(1).strip()
            if not title:
                return None
            if "拍了拍" in title:
                return None
            if "<type>57</type>" in stripped:
                return f"[回复] {title}"
            if "<type>6</type>" in stripped:
                return f"[文件] {title}"
            return f"[链接] {title}"
        return None

    if "<img " in stripped or "<img>" in stripped:
        return "[图片]"
    if "<emoji " in stripped or "<emoji>" in stripped:
        return "[表情]"
    if "<voicemsg " in stripped:
        return "[语音]"
    if "<videomsg " in stripped:
        return "[视频]"

    # Other unrecognised XML → skip
    if stripped.startswith("<?xml") or stripped.startswith("<msg"):
        return None

    return text


class WeChatDB:
    """Query interface for the decrypted WeChat SQLite databases."""

    CACHE_DIR = os.path.join(tempfile.gettempdir(), "wechat_mcp_cache")

    def __init__(self, db_dir: str, keys: dict):
        """
        Args:
            db_dir: Path to the WeChat db_storage directory.
            keys: {rel_path: {"enc_key": hex}, ...} key dictionary.
        """
        self.db_dir = db_dir
        self.keys = keys
        self._db_cache: dict[str, tuple[float, float, str]] = {}
        self._contacts: dict[str, str] | None = None
        self._contacts_full: list[dict] | None = None
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    def invalidate_cache(self):
        """Clear all decryption caches, forcing re-decrypt on next query."""
        self._db_cache.clear()
        self._contacts = None
        self._contacts_full = None

    def _get_key(self, rel_path: str) -> str | None:
        normalized = rel_path.replace("\\", "/")
        for candidate in (rel_path, normalized, normalized.replace("/", "\\")):
            if candidate in self.keys:
                return self.keys[candidate]["enc_key"]
        return None

    @staticmethod
    def _is_plain_sqlite(path: str) -> bool:
        try:
            with open(path, "rb") as f:
                return f.read(15) == b"SQLite format 3"
        except OSError:
            return False

    def _get_decrypted_db(self, rel_path: str) -> str | None:
        """Get decrypted database path (with mtime-based caching)."""
        db_path = os.path.join(self.db_dir, rel_path)
        if not os.path.exists(db_path):
            return None

        if self._is_plain_sqlite(db_path):
            return db_path

        enc_key = self._get_key(rel_path)
        if not enc_key:
            return None

        wal_path = db_path + "-wal"
        try:
            db_mtime = os.path.getmtime(db_path)
            wal_mtime = os.path.getmtime(wal_path) if os.path.exists(wal_path) else 0
        except OSError:
            return None

        if rel_path in self._db_cache:
            c_db_mt, c_wal_mt, c_path = self._db_cache[rel_path]
            if c_db_mt == db_mtime and c_wal_mt == wal_mtime and os.path.exists(c_path):
                return c_path

        h = hashlib.md5(rel_path.encode()).hexdigest()[:12]
        cache_path = os.path.join(self.CACHE_DIR, f"{h}.db")

        pages = decrypt_database(db_path, cache_path, enc_key)
        if pages == 0:
            return None

        if os.path.exists(wal_path):
            decrypt_wal(wal_path, cache_path, enc_key)

        self._db_cache[rel_path] = (db_mtime, wal_mtime, cache_path)
        return cache_path

    # ── Contacts ──

    def _load_contacts(self):
        if self._contacts is not None:
            return

        path = self._get_decrypted_db(os.path.join("contact", "contact.db"))
        if not path:
            self._contacts = {}
            self._contacts_full = []
            return

        names: dict[str, str] = {}
        full: list[dict] = []
        conn = sqlite3.connect(path)
        try:
            for uname, nick, remark in conn.execute(
                "SELECT username, nick_name, remark FROM contact"
            ):
                display = remark if remark else nick if nick else uname
                names[uname] = display
                full.append({"username": uname, "nick_name": nick or "", "remark": remark or ""})
        except Exception:
            pass
        finally:
            conn.close()

        self._contacts = names
        self._contacts_full = full

    def resolve_username(self, name: str) -> str | None:
        """Resolve a group name / remark / nickname to a username."""
        self._load_contacts()
        if name in self._contacts or "@chatroom" in name:
            return name
        name_lower = name.lower()
        # Exact match
        for uname, display in self._contacts.items():
            if name_lower == display.lower():
                return uname
        # Fuzzy match
        for uname, display in self._contacts.items():
            if name_lower in display.lower():
                return uname
        return None

    # ── Groups ──

    def get_groups(self) -> list[dict]:
        """Get all group chats.

        Returns:
            [{"username": "xxx@chatroom", "name": "Group Name"}, ...]
        """
        self._load_contacts()
        groups = []
        for c in self._contacts_full:
            if "@chatroom" in c["username"]:
                name = c["remark"] or c["nick_name"] or c["username"]
                groups.append({"username": c["username"], "name": name})
        return groups

    # ── Sessions ──

    def get_recent_sessions(self, limit: int = 30) -> list[dict]:
        """Get recent chat sessions (groups and DMs)."""
        path = self._get_decrypted_db(os.path.join("session", "session.db"))
        if not path:
            return []

        self._load_contacts()
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(
                """
                SELECT username, unread_count, summary, last_timestamp
                FROM SessionTable
                WHERE last_timestamp > 0
                ORDER BY last_timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except Exception:
            return []
        finally:
            conn.close()

        sessions = []
        for username, unread, summary, ts in rows:
            display = self._contacts.get(username, username)
            is_group = "@chatroom" in username

            if isinstance(summary, bytes):
                try:
                    summary = _zstd_dctx.decompress(summary).decode("utf-8", errors="replace")
                except Exception:
                    summary = ""
            if isinstance(summary, str) and ":\n" in summary:
                summary = summary.split(":\n", 1)[1]

            sessions.append(
                {
                    "username": username,
                    "name": display,
                    "is_group": is_group,
                    "unread": unread or 0,
                    "summary": summary or "",
                    "timestamp": ts,
                    "time_str": datetime.fromtimestamp(ts).strftime("%m-%d %H:%M"),
                }
            )

        return sessions

    # ── Messages ──

    def _find_msg_tables(self, username: str) -> list[tuple[str, str]]:
        """Find every shard that holds this chat's message table.

        WeChat splits a chat's history across message_N.db shards (older shard =
        bulk history, newer shard = recent messages). The shard must be ranked by
        its latest create_time, not by row count: picking the largest shard
        returns the stale history shard and hides everything since the split.

        Returns [(db_path, table_name), ...] newest shard first.
        """
        table_hash = hashlib.md5(username.encode()).hexdigest()
        table_name = f"Msg_{table_hash}"

        msg_keys = sorted(
            k
            for k in self.keys
            if re.search(
                r"message[/\\](?:biz_)?message_\d+\.db$", k.replace("\\", "/")
            )
        )

        found: list[tuple[float, str]] = []

        def _probe(path: str) -> None:
            conn = sqlite3.connect(path)
            try:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                if not exists:
                    return
                latest = conn.execute(
                    f"SELECT MAX(create_time) FROM [{table_name}]"
                ).fetchone()[0]
                found.append((float(latest or 0), path))
            except Exception:
                pass
            finally:
                conn.close()

        for rel_key in msg_keys:
            path = self._get_decrypted_db(rel_key)
            if path:
                _probe(path)

        # Also search cached decrypted files that might lack current keys
        for prefix in ("message", "biz_message"):
            for i in range(10):
                rel = f"message/{prefix}_{i}.db"
                if rel in self.keys:
                    continue
                h = hashlib.md5(rel.encode()).hexdigest()[:12]
                cache_path = os.path.join(self.CACHE_DIR, f"{h}.db")
                if os.path.exists(cache_path):
                    _probe(cache_path)

        found.sort(key=lambda item: item[0], reverse=True)
        return [(path, table_name) for _, path in found]

    def _find_msg_table(self, username: str) -> tuple[str | None, str | None]:
        """Return the newest shard holding this chat's message table."""
        shards = self._find_msg_tables(username)
        if shards:
            return shards[0]
        return None, None

    def count_messages_since(self, username: str, since_ts: int) -> int:
        """Count messages after a given timestamp. Returns -1 on failure."""
        shards = self._find_msg_tables(username)
        if not shards:
            return -1
        total = 0
        counted = False
        for db_path, table_name in shards:
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM [{table_name}] WHERE create_time > ?",
                    (since_ts,),
                ).fetchone()
                total += row[0] if row else 0
                counted = True
            except Exception:
                pass
            finally:
                conn.close()
        return total if counted else -1

    def get_messages(
        self, username: str, since_ts: int = 0, limit: int = 500
    ) -> list[dict]:
        """Get messages from a chat.

        Args:
            username: User or group ID (xxx@chatroom for groups).
            since_ts: Only get messages after this timestamp.
            limit: Maximum number of messages.

        Returns:
            [{"sender": str, "text": str, "timestamp": int,
              "time_str": str, "type": int}, ...]
            sender is empty string for messages sent by the account owner.
        """
        self._load_contacts()
        is_group = "@chatroom" in username

        shards = self._find_msg_tables(username)
        if not shards:
            return []

        rows = []
        for db_path, table_name in shards:
            conn = sqlite3.connect(db_path)
            try:
                if since_ts > 0:
                    part = conn.execute(
                        f"""
                        SELECT local_type, create_time, message_content,
                               WCDB_CT_message_content
                        FROM [{table_name}]
                        WHERE create_time > ?
                        ORDER BY create_time DESC
                        LIMIT ?
                        """,
                        (since_ts, limit),
                    ).fetchall()
                else:
                    part = conn.execute(
                        f"""
                        SELECT local_type, create_time, message_content,
                               WCDB_CT_message_content
                        FROM [{table_name}]
                        ORDER BY create_time DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
            except Exception:
                part = []
            finally:
                conn.close()
            rows.extend(part)

        # Newest `limit` messages across every shard, back in chronological order
        rows.sort(key=lambda row: row[1], reverse=True)
        rows = list(reversed(rows[:limit]))

        messages = []
        for local_type, create_time, content, ct in rows:
            if ct and ct == 4 and isinstance(content, bytes):
                try:
                    content = _zstd_dctx.decompress(content).decode(
                        "utf-8", errors="replace"
                    )
                except Exception:
                    content = None
            elif isinstance(content, bytes):
                try:
                    content = content.decode("utf-8", errors="replace")
                except Exception:
                    content = None

            if content is None:
                continue

            sender = ""
            text = content
            if is_group and ":\n" in content:
                sender, text = content.split(":\n", 1)
                sender = self._contacts.get(sender, sender)

            cleaned = _clean_msg_text(text)
            if cleaned is None:
                continue

            messages.append(
                {
                    "sender": sender,
                    "text": cleaned,
                    "timestamp": create_time,
                    "time_str": datetime.fromtimestamp(create_time).strftime(
                        "%m-%d %H:%M"
                    ),
                    "type": local_type,
                }
            )

        return messages

    # ── Search ──

    def search_messages(
        self,
        keywords: list[str],
        usernames: list[str],
        start_ts: float,
        end_ts: float,
        limit_per_group: int = 2000,
    ) -> dict[str, list[dict]]:
        """Cross-chat keyword search with sliding-window matching.

        Uses FTS index when available, falls back to direct DB scan.

        Args:
            keywords: Search terms (AND logic).
            usernames: Chat usernames to search in.
            start_ts: Start timestamp.
            end_ts: End timestamp.
            limit_per_group: Max messages to scan per chat.

        Returns:
            {username: [{"sender", "text", "timestamp", "time_str", "group_name"}]}
        """
        fts_path = self._get_fts_db()
        if fts_path:
            logger.info("Using FTS index for search")
            return self._search_via_fts(
                fts_path, keywords, usernames, start_ts, end_ts, limit_per_group
            )
        logger.info("FTS not available, falling back to direct DB search")
        return self._search_via_db(
            keywords, usernames, start_ts, end_ts, limit_per_group
        )

    def _get_fts_db(self) -> str | None:
        return self._get_decrypted_db("message/message_fts.db")

    def _build_fts_lookup(
        self, fts_conn: sqlite3.Connection
    ) -> tuple[dict, dict, dict]:
        self._load_contacts()
        name2id: dict[str, int] = {}
        id2name: dict[int, str] = {}
        id2sender_name: dict[int, str] = {}

        for rid, uname in fts_conn.execute("SELECT rowid, username FROM name2id"):
            name2id[uname] = rid
            id2name[rid] = uname
            id2sender_name[rid] = self._contacts.get(uname, uname)

        return name2id, id2name, id2sender_name

    def _search_via_fts(
        self, fts_path, keywords, usernames, start_ts, end_ts, limit_per_group
    ):
        self._load_contacts()
        results: dict[str, list[dict]] = {}

        fts_conn = sqlite3.connect(fts_path)
        try:
            name2id, id2name, id2sender_name = self._build_fts_lookup(fts_conn)
        except Exception as e:
            logger.error("FTS lookup build failed: %s", e)
            fts_conn.close()
            return results

        keywords_lower = [kw.lower() for kw in keywords]
        target_sessions: dict[int, str] = {}
        for uname in usernames:
            sid = name2id.get(uname)
            if sid is not None:
                target_sessions[sid] = uname

        FTS_TABLES = [f"message_fts_v4_{i}_content" for i in range(4)]

        for sid, uname in target_sessions.items():
            group_name = self._contacts.get(uname, uname)
            all_msgs = []

            for tbl in FTS_TABLES:
                try:
                    rows = fts_conn.execute(
                        f"""
                        SELECT c0, c5, c6 FROM [{tbl}]
                        WHERE c4 = ? AND c6 >= ? AND c6 <= ?
                        ORDER BY c6 ASC LIMIT ?
                        """,
                        (sid, int(start_ts), int(end_ts), limit_per_group),
                    ).fetchall()
                except Exception:
                    continue

                for text, sender_id, create_time in rows:
                    if not text or not create_time:
                        continue
                    sender = id2sender_name.get(sender_id, str(sender_id))
                    all_msgs.append(
                        {
                            "sender": sender,
                            "text": text,
                            "text_lower": text.lower(),
                            "timestamp": create_time,
                            "time_str": datetime.fromtimestamp(create_time).strftime(
                                "%m-%d %H:%M"
                            ),
                            "group_name": group_name,
                        }
                    )

            all_msgs.sort(key=lambda m: m["timestamp"])
            if len(all_msgs) > limit_per_group:
                all_msgs = all_msgs[:limit_per_group]

            if not all_msgs:
                continue

            # Sliding window search (context = 1 message each side)
            matched_indices: set[int] = set()
            for i in range(len(all_msgs)):
                win_start = max(0, i - 1)
                win_end = min(len(all_msgs), i + 2)
                combined = " ".join(
                    all_msgs[j]["text_lower"] for j in range(win_start, win_end)
                )
                if all(kw in combined for kw in keywords_lower):
                    for j in range(win_start, win_end):
                        matched_indices.add(j)

            matched = [
                {
                    "sender": all_msgs[i]["sender"],
                    "text": all_msgs[i]["text"],
                    "timestamp": all_msgs[i]["timestamp"],
                    "time_str": all_msgs[i]["time_str"],
                    "group_name": all_msgs[i]["group_name"],
                }
                for i in sorted(matched_indices)
            ]

            if matched:
                results[uname] = matched

        fts_conn.close()
        return results

    def _search_via_db(self, keywords, usernames, start_ts, end_ts, limit_per_group):
        self._load_contacts()
        results: dict[str, list[dict]] = {}

        table_cache: dict[str, list[tuple[str, str]]] = {}
        for username in usernames:
            shards = self._find_msg_tables(username)
            if shards:
                table_cache[username] = shards

        keywords_lower = [kw.lower() for kw in keywords]

        for username, shards in table_cache.items():
            is_group = "@chatroom" in username
            group_name = self._contacts.get(username, username)

            rows = []
            for db_path, table_name in shards:
                conn = sqlite3.connect(db_path)
                try:
                    part = conn.execute(
                        f"""
                        SELECT local_type, create_time, message_content,
                               WCDB_CT_message_content
                        FROM [{table_name}]
                        WHERE create_time >= ? AND create_time <= ?
                        ORDER BY create_time DESC LIMIT ?
                        """,
                        (int(start_ts), int(end_ts), limit_per_group),
                    ).fetchall()
                except Exception:
                    part = []
                finally:
                    conn.close()
                rows.extend(part)

            rows.sort(key=lambda row: row[1], reverse=True)
            rows = list(reversed(rows[:limit_per_group]))

            if not rows:
                continue

            all_msgs = []
            for local_type, create_time, content, ct in rows:
                if ct and ct == 4 and isinstance(content, bytes):
                    try:
                        content = _zstd_dctx.decompress(content).decode(
                            "utf-8", errors="replace"
                        )
                    except Exception:
                        continue
                elif isinstance(content, bytes):
                    try:
                        content = content.decode("utf-8", errors="replace")
                    except Exception:
                        continue
                if content is None:
                    continue

                sender = ""
                text = content
                if is_group and ":\n" in content:
                    sender, text = content.split(":\n", 1)
                    sender = self._contacts.get(sender, sender)

                text = _clean_msg_text(text)
                if text is None:
                    continue

                all_msgs.append(
                    {
                        "sender": sender,
                        "text": text,
                        "text_lower": text.lower(),
                        "timestamp": create_time,
                        "time_str": datetime.fromtimestamp(create_time).strftime(
                            "%m-%d %H:%M"
                        ),
                        "group_name": group_name,
                    }
                )

            if not all_msgs:
                continue

            matched_indices: set[int] = set()
            for i in range(len(all_msgs)):
                win_start = max(0, i - 1)
                win_end = min(len(all_msgs), i + 2)
                combined = " ".join(
                    all_msgs[j]["text_lower"] for j in range(win_start, win_end)
                )
                if all(kw in combined for kw in keywords_lower):
                    for j in range(win_start, win_end):
                        matched_indices.add(j)

            matched = [
                {
                    "sender": all_msgs[i]["sender"],
                    "text": all_msgs[i]["text"],
                    "timestamp": all_msgs[i]["timestamp"],
                    "time_str": all_msgs[i]["time_str"],
                    "group_name": all_msgs[i]["group_name"],
                }
                for i in sorted(matched_indices)
            ]

            if matched:
                results[username] = matched

        return results
