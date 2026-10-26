r"""recurrence.py —— 重复计划展开（`makeSeries`）。

【为什么它自己一个模块】
这是全项目最容易写错的算法：要同时处理「每周重复 N 次」的历史格式、
「每天/每个工作日重复到某天」的新格式，还要在**分配 ID 之前**就算出总数量
（10000 项上限），否则失败时会白造一堆 UUID。它和「校验单项」是两件事，
混在一个文件里最容易在下一次改动时互相牵连。

两个关键点：
  * 日期推进用 **UTC**（`utc_ms`），这样「相隔几天」不会被夏令时偏移影响；
  * 工作日重复要跳过周六周日（JS 的 `getDay()`：0=周日，6=周六）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from .clock import DATE_RE, parse_calendar_date, utc_ms

__all__ = ["MAX_SERIES", "make_series"]

MAX_SERIES = 10000


def _weekday_count(first_day: int, days: int) -> int:
    """`days` 天里有多少个工作日（`first_day` 是首日的 JS getDay：0=周日）。"""
    count = days // 7 * 5
    for index in range(days % 7):
        if (first_day + index) % 7 not in (0, 6):
            count += 1
    return count


def make_series(task: dict, repeat=1) -> list[dict]:
    """按重复设置展开成独立事项，等价于 `makeSeries(task, repeat)`。

    `repeat` 有两种形态：
      * 数字 1/4/8/12 —— 每周重复，连续 N 周（历史格式，必须精确匹配）；
      * `{'frequency': 'daily'|'weekdays', 'until': 'YYYY-MM-DD'}` —— 每天或
        每个工作日重复到指定结束日期（含当天）；
      * `{'frequency': 'weekly', 'until': 'YYYY-MM-DD'}` —— 每周重复到结束日期
        （课表导入等需要「每周相同星期几、任意周数」的场景；历史数字格式只支持
        1/4/8/12 周，不足以表达「第 1–16 周」）。
    """
    count = repeat
    step = 7
    weekdays = False
    start_ms = utc_ms(task["date"])

    if isinstance(repeat, dict):
        frequency = repeat.get("frequency")
        if frequency not in ("daily", "weekdays", "weekly"):
            raise ValueError("重复设置不正确")
        until = repeat.get("until")
        if not isinstance(until, str) or not DATE_RE.match(until) or parse_calendar_date(until) is None:
            raise ValueError("请选择有效的重复结束日期")
        if until < task["date"]:
            raise ValueError("重复结束日期不能早于开始日期")
        days = (utc_ms(until) - start_ms) // 86400000 + 1
        if frequency == "weekly":
            # 每周重复到结束日期：用于「本学期每周三的一节课」这类日程。
            count = (days - 1) // 7 + 1
            if count < 1 or count > MAX_SERIES:
                raise ValueError(f"每周重复最多生成 {MAX_SERIES} 项，请缩短日期范围")
            step = 7
        else:
            weekdays = frequency == "weekdays"
            count = days
            if weekdays:
                first_day = (datetime.strptime(task["date"], "%Y-%m-%d").weekday() + 1) % 7
                count = _weekday_count(first_day, days)
                if count == 0:
                    raise ValueError("所选日期范围内没有工作日，请调整日期")
            if not isinstance(count, int) or count < 1 or count > MAX_SERIES:
                label = "工作日" if weekdays else "每日"
                raise ValueError(f"{label}重复最多生成 10000 项，请缩短日期范围")
            step = 1
    elif not (
        isinstance(repeat, int)
        and not isinstance(repeat, bool)
        and repeat in (1, 4, 8, 12)
    ):
        raise ValueError("重复次数不正确")

    series_id = None if repeat == 1 else str(uuid.uuid4())
    result: list[dict] = []
    offset = 0
    while len(result) < count:
        moment = datetime.strptime(task["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=offset)
        offset += step
        if weekdays and moment.weekday() >= 5:  # 5=周六 6=周日
            continue
        date_text = moment.strftime("%Y-%m-%d")
        if not DATE_RE.match(date_text):
            raise ValueError("重复日期超出支持范围")
        item = dict(task)
        item["id"] = task["id"] if not result else str(uuid.uuid4())
        item["date"] = date_text
        if series_id:
            item["seriesId"] = series_id
            item["seriesIndex"] = len(result)
        result.append(item)
    return result
