r"""clock.py —— 日期与时间的解析，以及「本地时区毫秒」的换算。

【为什么集中在这里】
上游 `core.cjs` 里的时间逻辑只有一行行 `new Date(...)`，但它踩到的坑不少，
而且都只在跨月/跨年/跨闰日/跨时区时才暴露：

  * `new Date('2026-09-28T10:00:00')`（**无时区后缀**）按**本地时区**解析；
  * `Date.parse('2026-09-28T00:00:00Z')`（**带 Z**）按 UTC 解析——
    重复展开用这一种，因为「相隔几天」不能被打时区偏移影响；
  * 两者混用会让「每日重复」在夏令时切换日少一天/多一天。

所以这里明确分成两组：
  * `local_ms()` / `at()`   —— 给人看的时刻（提醒到点判定用）；
  * `utc_ms()` / `day_key()` —— 纯粹的「第几天」运算（重复展开用）。
"""

from __future__ import annotations

import re
from datetime import date as _date
from datetime import datetime, timezone

__all__ = [
    "DATE_RE",
    "TIME_RE",
    "local_ms",
    "at",
    "utc_ms",
    "date_key",
    "parse_calendar_date",
]

# `\d` 在 Python 的 re 里默认匹配 Unicode 数字（例如全角"２０２６"），
# 而 JS 的 `\d` 只匹配 ASCII 数字，所以这里必须显式写成 [0-9]。
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def local_ms(date_text: str, time_text: str = "00:00:00") -> int:
    """把 `'YYYY-MM-DD'+'T'+'HH:MM:SS'` 按**本地时区**解析成毫秒，等价于 `new Date(...)`。"""
    parsed = datetime.fromisoformat(f"{date_text}T{time_text}")
    return int(parsed.timestamp() * 1000)


def at(task: dict) -> int:
    """计划的发生时刻（本地毫秒），等价于 `new Date(task.date+'T'+task.time+':00')`。"""
    return local_ms(task["date"], f"{task['time']}:00")


def utc_ms(date_text: str) -> int:
    """`Date.parse(date+'T00:00:00Z')`：按 UTC 解析，跨月跨年跨闰日都不受时区影响。"""
    parsed = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def date_key(value: _date | datetime) -> str:
    """本地日历日的 `YYYY-MM-DD`。"""
    return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"


def parse_calendar_date(text) -> _date | None:
    """严格解析 `YYYY-MM-DD`：必须是 ASCII 数字、真实存在、且是规范写法。"""
    if not isinstance(text, str) or not DATE_RE.match(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
