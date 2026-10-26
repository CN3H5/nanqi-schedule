r"""tasks.py —— 单项计划的校验与规范化（`cleanTask`）。

【它守的是什么】
计划数据会从三个地方进来：界面表单、导入的备份文件、以及老版本留下的 `plans.json`。
三者都必须经过同一个校验，否则会出现「界面存不进去、但导入却能塞进来」的漏洞。
上游把这条规则叫做 `cleanTask`，这里逐字保留它的判断顺序与中文提示——
**提示语的顺序就是判断顺序**，用户看到的第一个错误必须是上游会给出的那一个。

【编辑语义（最容易搞错的部分）】
`existing` 不为空表示「编辑已有计划」：
  * id、创建时间、重复分组信息、提醒状态都从旧记录继承；
  * 只有「日期、时间、提醒」三者都没变时才保留 `notifiedAt` / `snoozeUntil`，
    这样改期后会自动重新提醒，而只改备注不会重复提醒。
"""

from __future__ import annotations

import uuid

from ..task_series import validate_series_tasks
from .categories import DEFAULT_CATEGORIES
from .clock import DATE_RE, TIME_RE, parse_calendar_date
from .numbers import is_finite_number, is_integer_number, js_len, now_ms, truncate_js

__all__ = ["CATEGORIES", "ALLOWED_REMINDERS", "KINDS", "MAX_TAGS", "MAX_ATTACHMENTS", "clean_task"]

CATEGORIES = [c["id"] for c in DEFAULT_CATEGORIES]

# `[...].includes(reminder)` 的那个列表：-1 表示不提醒，0 表示准点提醒。
ALLOWED_REMINDERS = [-1, 0, 5, 10, 15, 30, 60, 1440]

# 计划类型：日程（单个时间点的事）/ 计划（待办事项）/ 复习（针对考试）。
# 老数据没有这个字段，读取时按「计划」处理（见 KINDS 的默认值逻辑）。
KINDS = ("schedule", "plan", "review")

MAX_TAGS = 10
MAX_ATTACHMENTS = 20


def _category_ids_of(categories: list) -> list[str]:
    """接受 id 字符串列表或分类字典列表，统一取出 id。"""
    ids: list[str] = []
    for item in categories:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.append(item["id"])
    return ids


def _clean_tags(raw) -> list[str]:
    """标签列表：字符串、去空白、去重、每条最多 20 字（长度超限整体报错，与标题一致）。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("标签格式不正确")
    tags: list[str] = []
    for item in raw:
        tag = str(item).strip()
        if not tag:
            continue
        if js_len(tag) > 20:
            raise ValueError("单个标签最多 20 字")
        if tag not in tags:
            tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise ValueError(f"标签最多 {MAX_TAGS} 个")
    return tags


def _clean_attachments(raw) -> list[dict]:
    """附件列表：[{name, path}]，只存路径引用，不复制文件本身。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("附件格式不正确")
    result: list[dict] = []
    for item in raw[:MAX_ATTACHMENTS]:
        if not isinstance(item, dict):
            raise ValueError("附件格式不正确")
        path = str(item.get("path") or "").strip()
        if not path or js_len(path) > 500:
            raise ValueError("附件路径不正确")
        name = str(item.get("name") or "").strip() or path.replace("\\", "/").rsplit("/", 1)[-1]
        result.append({"name": truncate_js(name, 120), "path": path})
    return result


def clean_task(source, existing: dict | None = None, categories: list | None = None) -> dict:
    """校验并规范化一项计划，等价于 `cleanTask(input, existing)`。

    `categories` 是允许的分类 id 列表（自定义分类功能）；省略时用默认三分类，
    保持与 v1.0 一致的行为。
    """
    if not isinstance(source, dict):
        raise ValueError("计划格式不正确")

    raw_title = source.get("title") or ""
    title = str(raw_title).strip()
    if not title or js_len(title) > 160:
        raise ValueError("请填写 1–160 字的计划名称")

    raw_date = source.get("date") or ""
    if not isinstance(raw_date, str) or not DATE_RE.match(raw_date):
        raise ValueError("请选择有效日期")
    if parse_calendar_date(raw_date) is None:
        raise ValueError("请选择有效日期")

    raw_time = source.get("time") or ""
    if not isinstance(raw_time, str) or not TIME_RE.match(raw_time):
        raise ValueError("请选择有效时间")

    # `Number.isInteger` 的等价判断：必须是数字、且没有小数部分（'60' 这类字符串要拒绝）。
    duration = source.get("duration")
    if not is_integer_number(duration) or not 15 <= int(duration) <= 1440:
        raise ValueError("时长须为 15–1440 分钟")
    duration = int(duration)

    # `[...].includes(reminder)`：只接受列出的这几个数值。
    reminder = source.get("reminder")
    if not is_integer_number(reminder) or int(reminder) not in ALLOWED_REMINDERS:
        raise ValueError("提醒时间不正确")
    reminder = int(reminder)

    allowed_categories = (
        CATEGORIES if categories is None else _category_ids_of(categories)
    )
    if source.get("category") not in allowed_categories:
        raise ValueError("请选择有效分类")

    raw_notes = source.get("notes") or ""
    notes = truncate_js(str(raw_notes), 5000)

    # 新增字段（类型/标签/附件）只在**被显式给出**时才校验并写入：
    # 老备份与老版本界面传来的计划没有这些键，清洗结果必须与 v1.0 逐键一致，
    # 否则一次普通的「打开再保存」就会改写整份 plans.json。
    kind = source.get("kind")
    if kind is not None and kind not in KINDS:
        raise ValueError("计划类型不正确")

    if existing:
        created_at = existing.get("createdAt")
        created_at = int(created_at) if is_finite_number(created_at) and created_at > 0 else 0
        task_id = existing.get("id") or str(uuid.uuid4())
    else:
        created_at = now_ms()
        task_id = str(uuid.uuid4())

    task = {
        "id": task_id,
        "title": title,
        "date": raw_date,
        "time": raw_time,
        "duration": duration,
        "reminder": reminder,
        "category": source.get("category"),
        "priority": "high" if source.get("priority") == "high" else "normal",
        "notes": notes,
        "done": bool(source.get("done")),
        "createdAt": created_at,
        "updatedAt": now_ms(),
    }

    if kind is not None:
        task["kind"] = kind
    if "tags" in source:
        task["tags"] = _clean_tags(source.get("tags"))
    if "attachments" in source:
        task["attachments"] = _clean_attachments(source.get("attachments"))

    if existing and (
        existing.get("date") == task["date"]
        and existing.get("time") == task["time"]
        and existing.get("reminder") == task["reminder"]
    ):
        # `existing.notifiedAt || null`：0/false/'' 都退化成 null。
        task["notifiedAt"] = existing.get("notifiedAt") or None
        task["snoozeUntil"] = existing.get("snoozeUntil") or None
        saved = existing.get("deliveryState")
        if isinstance(saved, dict) and existing.get("duration") == task["duration"]:
            signature = "|".join(str(task.get(k, "")) for k in ("date", "time", "reminder", "duration"))
            stages = saved.get("stages")
            if saved.get("schedule") == signature and isinstance(stages, list) and all(s in ("early", "soon", "nap", "change") for s in stages):
                task["deliveryState"] = {"schedule": signature, "stages": list(dict.fromkeys(stages))}

    if existing and ("seriesId" in existing or "seriesIndex" in existing):
        task["seriesId"] = existing.get("seriesId")
        task["seriesIndex"] = existing.get("seriesIndex")
        validate_series_tasks([task])

    return task
