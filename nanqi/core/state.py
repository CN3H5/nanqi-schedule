r"""state.py —— 整份数据的校验与初始状态（`validateState` / 初始 `state`）。

【校验哲学：宁可报错，也不「尽量修复」】
导入备份时，任何一处不合法都直接抛错，由调用方把原文件留一份恢复副本。
理由：悄悄丢掉几项计划、或者把非法字段替换成默认值，用户会在几周后才发现
「我的计划怎么少了」；而报错至少会让他知道文件有问题、原文件还在。

【上限】
计划总数 10000、历史（提醒事件）保留最近 300 条。这两个数字与上游一致，
界面上的「最多 10000 项」提示也来自这里。
"""

from __future__ import annotations

import re

from ..reminder_copy import normalize_rotation
from ..task_series import validate_series_tasks
from ..weather import normalize_settings as normalize_weather
from ..widget_model import normalize_prefs
from .categories import normalize_categories, MAX_CATEGORIES, CATEGORY_ID_RE
from .numbers import is_finite_number
from .tasks import clean_task

__all__ = ["MAX_TASKS", "MAX_HISTORY", "validate_state", "initial_state"]

MAX_TASKS = 10000
MAX_HISTORY = 300

_ID_RE = re.compile(r"[a-zA-Z0-9_-]{1,80}")
_INVALID_BACKUP = "不是有效的南七日程备份（最多 10000 项）"


def validate_state(raw) -> dict:
    """校验并规范化整份备份，等价于 `validateState(raw)`。"""
    if not isinstance(raw, dict):
        raise ValueError(_INVALID_BACKUP)
    version = raw.get("version")
    if isinstance(version, bool) or version != 1:
        raise ValueError(_INVALID_BACKUP)
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, list) or len(raw_tasks) > MAX_TASKS:
        raise ValueError(_INVALID_BACKUP)
    if isinstance(raw.get("categories"), list) and len(raw["categories"]) > MAX_CATEGORIES:
        raise ValueError("分类最多 20 个")

    seen: set[str] = set()
    tasks: list[dict] = []
    # 自定义分类先于任务校验：任务上的 category 必须指向一个存在的分类。
    # 老备份没有 `categories` 键，normalize_categories 会回退到默认三分类。
    categories = normalize_categories(raw.get("categories"))
    category_ids = [c["id"] for c in categories]
    for item in raw_tasks:
        if not isinstance(item, dict):
            raise ValueError("备份包含无效或重复的计划 ID")
        task_id = item.get("id")
        if not isinstance(task_id, str) or not _ID_RE.fullmatch(task_id) or task_id in seen:
            raise ValueError("备份包含无效或重复的计划 ID")
        seen.add(task_id)
        cleaned = clean_task(item, item, categories=category_ids)
        if "updatedAt" in item and is_finite_number(item["updatedAt"]) and item["updatedAt"] >= 0:
            cleaned["updatedAt"] = item["updatedAt"]
        for key in ("notifiedAt", "snoozeUntil"):
            value = item.get(key)
            if value is not None and (not is_finite_number(value) or value < 0):
                raise ValueError("备份中的提醒状态无效")
        tasks.append(cleaned)
    validate_series_tasks(tasks)

    settings = raw.get("settings") or {}
    if not isinstance(settings, dict):
        settings = {}
    history = raw.get("history")
    if not isinstance(history, list):
        history = []
    kept: list[dict] = []
    for entry in history:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and isinstance(entry.get("taskId"), str)
            and is_finite_number(entry.get("at"))
        ):
            kept.append(entry)

    return {
        "version": 1,
        "tasks": tasks,
        "categories": categories,
        "settings": {
            "sound": settings.get("sound") is not False,
            "popup": settings.get("popup") is not False,
            "widget": normalize_prefs(settings.get("widget"), categories=category_ids),
            "reminderRotation": normalize_rotation(settings.get("reminderRotation")),
            # 天气提醒：属于**联网**功能，因此同样按 opt-in 处理，
            # 老备份没有这个键时是关闭的。默认坐标合肥。
            "weather": normalize_weather(settings.get("weather")),
        },
        "history": kept[-MAX_HISTORY:],
    }


def initial_state() -> dict:
    """全新安装时的空状态，等价于 main.cjs 里的初始 `state`。"""
    return {
        "version": 1,
        "tasks": [],
        "categories": normalize_categories(None),
        "settings": {
            "sound": True,
            "popup": True,
            "widget": normalize_prefs(None),
            "reminderRotation": normalize_rotation(None),
            "weather": normalize_weather(None),
        },
        "history": [],
    }


def check_state_for_write(state: dict) -> None:
    """Validate a candidate without using the normalizer's rewritten timestamps."""
    categories = state.get("categories")
    if not isinstance(categories, list) or not 1 <= len(categories) <= MAX_CATEGORIES:
        raise ValueError("分类须为 1–20 个")
    ids = [c.get("id") for c in categories if isinstance(c, dict)]
    if len(ids) != len(categories) or any(not isinstance(i, str) or not CATEGORY_ID_RE.fullmatch(i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("分类 ID 无效或重复")
    validate_state(state)
