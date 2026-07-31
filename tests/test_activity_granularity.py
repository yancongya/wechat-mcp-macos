#!/usr/bin/env python3
import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from date_range import DateRange

spec = importlib.util.spec_from_file_location("prompt_render", ROOT / "prompts" / "render.py")
prompt_render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prompt_render)
build_compressed_context = prompt_render.build_compressed_context


class ActivityGranularityTests(unittest.TestCase):
    def make_message(self, dt):
        return {
            "timestamp": int(dt.timestamp()),
            "time_str": dt.strftime("%Y-%m-%d %H:%M"),
            "type": 1,
            "sender": "测试用户",
            "sender_username": "wxid_test",
            "sender_type": "member",
            "text": "Blender 几何节点测试消息",
        }

    def test_week_range_uses_logical_day_activity(self):
        date_range = DateRange(
            datetime(2026, 7, 25, 4, 0),
            datetime(2026, 8, 1, 1, 0),
            "最近 7 天",
            "last",
            "04:00",
        )
        messages = [
            self.make_message(datetime(2026, 7, 25, 5, 0)),
            self.make_message(datetime(2026, 7, 26, 3, 0)),
            self.make_message(datetime(2026, 7, 26, 5, 0)),
        ]
        stats = {"total": 3, "peak_hour": "05:00 - 06:00", "sender_count": 1}
        result = build_compressed_context(messages, stats, date_range=date_range)
        self.assertEqual(result["activity_granularity"], "day")
        self.assertEqual(len(result["activity"]), 7)
        self.assertEqual(result["activity"][0]["date"], "2026-07-25")
        self.assertEqual(result["activity"][0]["count"], 2)
        self.assertEqual(result["activity"][1]["count"], 1)

    def test_single_day_uses_hour_activity(self):
        date_range = DateRange(
            datetime(2026, 7, 31, 4, 0),
            datetime(2026, 8, 1, 1, 0),
            "今天",
            "today",
            "04:00",
        )
        messages = [self.make_message(datetime(2026, 7, 31, 9, 0))]
        stats = {"total": 1, "peak_hour": "09:00 - 10:00", "sender_count": 1}
        result = build_compressed_context(messages, stats, date_range=date_range)
        self.assertEqual(result["activity_granularity"], "hour")
        self.assertEqual(len(result["activity"]), 24)
        self.assertEqual(result["activity"][9]["count"], 1)


if __name__ == "__main__":
    unittest.main()
