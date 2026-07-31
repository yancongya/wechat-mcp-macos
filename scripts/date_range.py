#!/usr/bin/env python3
"""统一解析微信总结的日期/时间区间。

日期参数按用户日界线解释，默认 04:00；结束日期包含整天。
精确到时间的 --end 保持左闭右开语义。
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class DateRange:
    start: datetime
    end: datetime
    label: str
    mode: str
    day_start: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["start"] = self.start.strftime("%Y-%m-%d %H:%M:%S")
        data["end"] = self.end.strftime("%Y-%m-%d %H:%M:%S")
        data["start_ts"] = int(self.start.timestamp())
        data["end_ts"] = int(self.end.timestamp())
        return data


def parse_day_start(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("--day-start 必须是 HH:MM，例如 04:00") from exc


def parse_value(value: str) -> tuple[datetime | date, bool]:
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt), True
        except ValueError:
            pass
    try:
        return datetime.strptime(value, "%Y-%m-%d").date(), False
    except ValueError as exc:
        raise ValueError(f"无法解析日期时间：{value}") from exc


def at_day_start(day: date, boundary: time) -> datetime:
    return datetime.combine(day, boundary)


def current_logical_day(now: datetime, boundary: time) -> date:
    return (now - timedelta(
        hours=boundary.hour,
        minutes=boundary.minute,
        seconds=boundary.second,
    )).date()


def resolve_date_range(
    *,
    date_value: str | None = None,
    start_value: str | None = None,
    end_value: str | None = None,
    hours: int | None = None,
    today: bool = False,
    yesterday: bool = False,
    last: str | None = None,
    day_start: str = "04:00",
    now: datetime | None = None,
) -> DateRange:
    now = now or datetime.now()
    boundary = parse_day_start(day_start)
    logical_today = current_logical_day(now, boundary)

    selected = sum(bool(x) for x in (date_value, start_value or end_value, today, yesterday, last, hours is not None))
    if selected > 1:
        raise ValueError("日期参数冲突：请只使用 --date、--start/--end、--today、--yesterday、--last 或 --hours 中的一种")

    if date_value:
        day, precise = parse_value(date_value)
        if precise:
            raise ValueError("--date 只接受 YYYY-MM-DD；精确时间请使用 --start/--end")
        start = at_day_start(day, boundary)
        end = start + timedelta(days=1)
        return DateRange(start, end, day.isoformat(), "date", day_start)

    if start_value or end_value:
        if not start_value or not end_value:
            raise ValueError("--start 和 --end 必须同时提供")
        start_raw, start_precise = parse_value(start_value)
        end_raw, end_precise = parse_value(end_value)
        start = start_raw if start_precise else at_day_start(start_raw, boundary)
        end = end_raw if end_precise else at_day_start(end_raw + timedelta(days=1), boundary)
        if end <= start:
            raise ValueError("结束时间必须晚于开始时间")
        label_end = end_raw.strftime("%Y-%m-%d %H:%M") if end_precise else end_raw.isoformat()
        label_start = start_raw.strftime("%Y-%m-%d %H:%M") if start_precise else start_raw.isoformat()
        return DateRange(start, end, f"{label_start} 至 {label_end}", "range", day_start)

    if yesterday:
        day = logical_today - timedelta(days=1)
        start = at_day_start(day, boundary)
        return DateRange(start, start + timedelta(days=1), "昨天", "yesterday", day_start)

    if last:
        match = re.fullmatch(r"(\d+)([dh])", last.strip().lower())
        if not match:
            raise ValueError("--last 格式应为 7d 或 12h")
        amount, unit = int(match.group(1)), match.group(2)
        if amount <= 0:
            raise ValueError("--last 必须大于 0")
        if unit == "h":
            start = now - timedelta(hours=amount)
            return DateRange(start, now, f"最近 {amount} 小时", "last", day_start)
        start_day = logical_today - timedelta(days=amount - 1)
        return DateRange(at_day_start(start_day, boundary), now, f"最近 {amount} 天", "last", day_start)

    if hours is not None and hours > 0:
        start = now - timedelta(hours=hours)
        return DateRange(start, now, f"最近 {hours} 小时", "hours", day_start)

    start = at_day_start(logical_today, boundary)
    return DateRange(start, now, "今天", "today", day_start)


def main() -> None:
    parser = argparse.ArgumentParser(description="解析微信总结日期区间")
    parser.add_argument("--date", dest="date_value")
    parser.add_argument("--start", dest="start_value")
    parser.add_argument("--end", dest="end_value")
    parser.add_argument("--hours", type=int)
    parser.add_argument("--today", action="store_true")
    parser.add_argument("--yesterday", action="store_true")
    parser.add_argument("--last")
    parser.add_argument("--day-start", default="04:00")
    args = parser.parse_args()
    try:
        result = resolve_date_range(**vars(args))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
