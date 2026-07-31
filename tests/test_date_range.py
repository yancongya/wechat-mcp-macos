#!/usr/bin/env python3
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from date_range import resolve_date_range


class DateRangeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 31, 18, 0)

    def test_single_date_uses_four_am_boundary(self):
        result = resolve_date_range(date_value="2026-07-30", now=self.now)
        self.assertEqual(result.start, datetime(2026, 7, 30, 4, 0))
        self.assertEqual(result.end, datetime(2026, 7, 31, 4, 0))

    def test_date_range_includes_end_date(self):
        result = resolve_date_range(
            start_value="2026-07-25",
            end_value="2026-07-31",
            now=self.now,
        )
        self.assertEqual(result.start, datetime(2026, 7, 25, 4, 0))
        self.assertEqual(result.end, datetime(2026, 8, 1, 4, 0))

    def test_precise_end_is_exclusive(self):
        result = resolve_date_range(
            start_value="2026-07-30 18:00",
            end_value="2026-07-31 04:00",
            now=self.now,
        )
        self.assertEqual(result.start, datetime(2026, 7, 30, 18, 0))
        self.assertEqual(result.end, datetime(2026, 7, 31, 4, 0))

    def test_before_four_am_belongs_to_previous_logical_day(self):
        result = resolve_date_range(today=True, now=datetime(2026, 7, 31, 3, 0))
        self.assertEqual(result.start, datetime(2026, 7, 30, 4, 0))
        self.assertEqual(result.end, datetime(2026, 7, 31, 3, 0))

    def test_last_seven_days_starts_six_logical_days_ago(self):
        result = resolve_date_range(last="7d", now=self.now)
        self.assertEqual(result.start, datetime(2026, 7, 25, 4, 0))
        self.assertEqual(result.end, self.now)

    def test_conflicting_arguments_fail(self):
        with self.assertRaises(ValueError):
            resolve_date_range(date_value="2026-07-30", today=True, now=self.now)


if __name__ == "__main__":
    unittest.main()
