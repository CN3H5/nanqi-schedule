"""widget_model.py —— 桌面便签的偏好设置与选任务规则（移植自 src/widget-model.cjs）。

【这个文件对应原来的哪一部分】
桌面便签（widget）在 Electron 版里有一份自己的「纯函数」模型：偏好怎么归一化、
当前看的是哪一天或哪一周、这一范围里有哪些待办与已办。主进程和便签页面都用它，
所以这里保持同样的边界——任何非法输入都退化成默认值，绝不抛错，也绝不改动入参。

范围规则（与 JS 一致）：
  * 「日」模式从所选日期当天 00:00 到次日 00:00（左闭右开）；
  * 「周」模式从所在周的周一 00:00 到下周一 00:00；
  * anchor 为空表示「跟随今天」，每次计算都用当前日期。
"""

from __future__ import annotations

import re
from datetime import date as _date
from datetime import datetime, timedelta

__all__ = ["date_key", "date_valid", "normalize_prefs", "get_range", "select_tasks"]

_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_CATEGORIES = ("work", "study", "life")
_THEMES = ("cream", "lavender")

DEFAULT_PREFS = {
    "enabled": False,
    "mode": "day",
    "anchor": None,
    "category": "all",
    "onTop": False,
    "theme": "sage",
    "showCompleted": True,
    "bounds": None,
}


def date_key(value) -> str:
    """`YYYY-MM-DD`（本地日历日）。"""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, _date):
        return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"
    raise TypeError("date_key 需要 date 或 datetime")


def date_valid(text) -> bool:
    """严格校验日期字符串：ASCII 数字、真实存在、规范写法（不接受 2026-9-28）。"""
    if not isinstance(text, str) or not _DATE_RE.match(text):
        return False
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d")


def _is_finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and value not in (
        float("inf"),
        float("-inf"),
    )


def _normalize_bounds(raw):
    """位置与尺寸边界：越界或不合法的记录一律丢弃，避免便签跑到屏幕外。"""
    if not isinstance(raw, dict):
        return None
    if not all(_is_finite(raw.get(key)) for key in ("x", "y", "width", "height")):
        return None
    if abs(raw["x"]) >= 50000 or abs(raw["y"]) >= 50000:
        return None
    if not 340 <= raw["width"] <= 680 or not 380 <= raw["height"] <= 1000:
        return None
    return {key: int(round(raw[key])) for key in ("x", "y", "width", "height")}


def normalize_prefs(source=None, categories: list | None = None) -> dict:
    """把任意输入归一化成一份可用的便签偏好（对非法值一律回退默认）。

    参数可以省略（对应 JS 的 `normalizePrefs()`）：老版本备份里可能根本没有
    `settings.widget`，调用方不该被迫先造一个空对象再传进来。

    `categories` 是可选的分类 id 列表：自定义分类功能之后，便签的分类筛选
    不再局限于 work/study/life；调用方把当前分类 id 列表传进来即可。
    不传时维持 v1.0 的行为（只认内置三分类），保证旧测试与旧调用不变。
    """
    prefs = source if isinstance(source, dict) else {}
    allowed_categories = set(_CATEGORIES) if categories is None else set(categories)
    return {
        "enabled": prefs.get("enabled") is True,
        "mode": "week" if prefs.get("mode") == "week" else "day",
        "anchor": prefs.get("anchor") if date_valid(prefs.get("anchor")) else None,
        "category": prefs.get("category") if prefs.get("category") in allowed_categories else "all",
        "onTop": prefs.get("onTop") is True,
        "theme": prefs.get("theme") if prefs.get("theme") in _THEMES else "sage",
        "showCompleted": prefs.get("showCompleted") is not False,
        "bounds": _normalize_bounds(prefs.get("bounds")),
    }


def get_range(source, now: datetime | None = None) -> dict:
    """返回当前查看范围的 `{'start': 起（含）, 'end': 止（不含）}`。"""
    prefs = normalize_prefs(source)
    moment = now or datetime.now()
    anchor_key = prefs["anchor"] or date_key(moment)
    current = _parse(anchor_key)
    if prefs["mode"] == "week":
        # 周一为一周的开始：Python 的 weekday() 已经是 Mon=0，正好对应 JS 的 (getDay()+6)%7。
        current -= timedelta(days=current.weekday())
    span = 7 if prefs["mode"] == "week" else 1
    return {"start": date_key(current), "end": date_key(current + timedelta(days=span))}


def select_tasks(tasks: list[dict], source=None, now: datetime | None = None, categories: list | None = None) -> dict:
    """挑出范围内的计划，并按日期时间排序后拆成待办与已办（`source` 可省略）。"""
    prefs = normalize_prefs(source, categories=categories)
    window = get_range(prefs, now)
    selected = [
        task
        for task in tasks
        if isinstance(task.get("date"), str)
        and window["start"] <= task["date"] < window["end"]
        and (prefs["category"] == "all" or task.get("category") == prefs["category"])
    ]
    # 与 JS 一致的主排序键是 `date+time`；标题只作为并列时的次序，且不改变主顺序。
    selected.sort(key=lambda task: (f"{task['date']}{task.get('time', '')}", task.get("title") or ""))
    return {
        "start": window["start"],
        "end": window["end"],
        "tasks": selected,
        "pending": [task for task in selected if not task.get("done")],
        "done": [task for task in selected if task.get("done")],
    }
