#!/usr/bin/env python3
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from chat_query import (
    build_stats,
    decode_cursor,
    encode_cursor,
    query_messages_page,
    resolve_self_username,
)


class FakeDB:
    def __init__(self, db_path):
        self.db_path = db_path
        self._contacts = {
            "wxid_friend": "朋友",
            "wxid_owner": "烟囱鸭",
        }

    def _find_msg_table(self, _username):
        return self.db_path, "Msg_test"

    def _load_contacts(self):
        return None


class ChatQueryTests(unittest.TestCase):
    def test_self_username_inferred_from_account_directory(self):
        cfg = {
            "db_dir": "/tmp/wxid_owner123_abcd/db_storage",
            "self_name": "",
        }
        mapping = {"wxid_owner123": 2, "wxid_contact": 1}
        self.assertEqual(resolve_self_username(cfg, mapping, "wxid_contact"), "wxid_owner123")

    def test_cursor_round_trip(self):
        cursor = encode_cursor(123456, 789)
        self.assertEqual(decode_cursor(cursor), (123456, 789))
        with self.assertRaises(ValueError):
            decode_cursor("invalid-cursor")

    def test_keyset_pagination_keeps_same_second_messages(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as temp:
            conn = sqlite3.connect(temp.name)
            conn.execute("CREATE TABLE Name2Id (user_name TEXT)")
            conn.execute("INSERT INTO Name2Id(rowid, user_name) VALUES (1, 'wxid_friend')")
            conn.execute("INSERT INTO Name2Id(rowid, user_name) VALUES (2, 'wxid_owner')")
            conn.execute('''CREATE TABLE Msg_test (
                local_id INTEGER PRIMARY KEY,
                local_type INTEGER,
                create_time INTEGER,
                real_sender_id INTEGER,
                message_content TEXT,
                WCDB_CT_message_content INTEGER
            )''')
            conn.executemany(
                "INSERT INTO Msg_test VALUES (?, 1, ?, ?, ?, 0)",
                [
                    (1, 100, 2, "第一条"),
                    (2, 100, 1, "第二条"),
                    (3, 100, 2, "第三条"),
                    (4, 101, 1, "第四条"),
                ],
            )
            conn.commit()
            conn.close()

            cfg = {"db_dir": "/tmp/wxid_owner_suffix/db_storage", "self_name": "wxid_owner"}
            with patch("chat_query.load_config", return_value=cfg):
                first = query_messages_page(FakeDB(temp.name), "wxid_friend", "朋友", 100, 102, page_size=2)
                second = query_messages_page(
                    FakeDB(temp.name), "wxid_friend", "朋友", 100, 102,
                    page_size=2, cursor=first["next_cursor"],
                )

            self.assertTrue(first["has_more"])
            self.assertEqual([m["local_id"] for m in first["messages"]], [1, 2])
            self.assertEqual([m["local_id"] for m in second["messages"]], [3, 4])
            self.assertEqual(first["messages"][0]["sender"], "我")
            self.assertEqual(first["messages"][1]["sender"], "朋友")
            self.assertFalse(second["has_more"])

    def test_stats_keep_self_avatar_username(self):
        messages = [
            {
                "sender": "我",
                "sender_type": "self",
                "sender_username": "wxid_owner",
                "timestamp": 1785481583,
                "type": 1,
            },
            {
                "sender": "朋友",
                "sender_type": "contact",
                "sender_username": "wxid_friend",
                "timestamp": 1785481600,
                "type": 1,
            },
        ]
        stats = build_stats(messages)
        self_entry = next(item for item in stats["top_speakers"] if item["name"] == "我")
        self.assertEqual(self_entry["avatar_username"], "wxid_owner")
        self.assertEqual(self_entry["sender_type"], "self")
        self.assertEqual(stats["sender_count"], 2)


if __name__ == "__main__":
    unittest.main()
