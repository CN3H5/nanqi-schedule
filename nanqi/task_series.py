"""task_series.py —— 重复计划的分组、批量操作预览与执行（移植自 src/task-series.cjs）。

【这个文件解决什么问题】
每日 / 工作日 / 每周重复会生成一批独立事项，它们共享 `seriesId`，并按创建顺序
保存 `seriesIndex`。用户点「完成」或「删除」时常常需要「此项及后续」，所以在真正
改动数据之前必须先给出**预览**（实际会动到哪几项），并把预览结果哈希成一个
**确认令牌**交给前端；提交时再算一次，两边不一致就拒绝执行。

这样做的意义：确认期间如果别的窗口改了同一批事项（改期、改名、勾选），
批量操作会停下来要求重新确认，而不是照着过期的清单动手。

【令牌的哈希】
`sha256(JSON.stringify({id, action, source, tasks: 匹配项}))`，其中 JSON 用
`nanqi.jsjson` 按 ECMAScript 规则输出，因此 Python 版与 Electron 版算出的令牌
语义完全一致（同一次预览在同一份数据上得到同一个值）。
"""

from __future__ import annotations

import hashlib
import math
import re
import time

from .jsjson import dumps

__all__ = ["ACTIONS", "validate_series_tasks", "preview_task_action", "apply_task_action"]

ACTIONS = ("complete", "reopen", "delete")

# 旧版没有保存分组信息，只能靠「同一次创建 + 内容完全一致」来推测候选。
LEGACY_FIELDS = ("title", "time", "duration", "category", "reminder", "priority", "notes")


def _valid_id(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value))


def _has(value: dict, key: str) -> bool:
    """「字段存在」的判断，对应 JS 里的 `!== undefined`（null 也算存在）。"""
    return key in value


def validate_series_tasks(tasks: list[dict]) -> None:
    """校验重复分组元数据：要么两个字段都没有，要么成对且合法且组内序号不重复。"""
    seen: set[str] = set()
    for task in tasks:
        if not _has(task, "seriesId") and not _has(task, "seriesIndex"):
            continue
        series_id = task.get("seriesId")
        index = task.get("seriesIndex")
        if (
            not _valid_id(series_id)
            or not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or index > 9999
        ):
            raise ValueError("重复计划分组信息无效")
        key = f"{series_id}:{index}"
        if key in seen:
            raise ValueError("重复计划组中存在相同的事项序号")
        seen.add(key)


def _schedule(task: dict) -> str:
    return f"{task['date']}T{task['time']}"


def _hash(payload: dict) -> str:
    return hashlib.sha256(dumps(payload).encode("utf-8")).hexdigest()


def _display(task: dict) -> dict:
    return {
        "id": task["id"],
        "title": task["title"],
        "date": task["date"],
        "time": task["time"],
        "done": task["done"],
    }


def preview_task_action(tasks: list[dict], task_id, action: str) -> dict:
    """算出这次操作会影响到哪些事项，并给出确认令牌。

    范围来源有三种：
      * `series` —— 有分组信息，按不可变的 seriesIndex 取「本项及之后」；
      * `legacy` —— 没有分组，但创建时间与全部内容字段一致，按日期时间排序推测；
      * `single` —— 其余情况只影响本项。
    """
    if action not in ACTIONS:
        raise ValueError("计划操作无效")
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        raise ValueError("计划已被删除，请刷新后重试")

    source = "single"
    matches = [task]

    if _has(task, "seriesId"):
        source = "series"
        matches = sorted(
            (
                item
                for item in tasks
                if item.get("seriesId") == task.get("seriesId")
                and isinstance(item.get("seriesIndex"), int)
                and item["seriesIndex"] >= task.get("seriesIndex")
            ),
            key=lambda item: item["seriesIndex"],
        )
    else:
        created_at = task.get("createdAt")
        # `Number.isFinite(createdAt) && createdAt > 0` —— 0、负数、字符串、NaN、±Infinity
        # 都不算创建证据。必须显式判 isfinite：`inf > 0` 为真是 Python 与 JS 的差异点，
        # 漏掉它会让一个 Infinity 时间戳伪造出「旧版候选组」，把「此项及后续」的作用范围
        # 错误地扩大到内容相同的无关事项上（移植测试就是靠这一条抓出来的）。
        if (
            isinstance(created_at, (int, float))
            and not isinstance(created_at, bool)
            and math.isfinite(created_at)
            and created_at > 0
        ):
            candidates = [
                item
                for item in tasks
                if not _has(item, "seriesId")
                and not _has(item, "seriesIndex")
                and item.get("createdAt") == created_at
                and all(item.get(field) == task.get(field) for field in LEGACY_FIELDS)
            ]
            if len(candidates) > 1:
                source = "legacy"
                current = _schedule(task)
                matches = sorted(
                    (item for item in candidates if _schedule(item) >= current),
                    # 与 JS 一致：先比日期时间，再比 id（字段名与取值都限定为 ASCII）。
                    key=lambda item: (_schedule(item), item.get("id") or ""),
                )

    token = _hash({"id": task_id, "action": action, "source": source, "tasks": matches})
    single_token = _hash({"id": task_id, "action": action, "task": task})
    return {
        "task": _display(task),
        "source": source,
        "followingCount": len(matches),
        "items": [_display(item) for item in matches],
        "token": token,
        "singleToken": single_token,
    }


def apply_task_action(state: dict, request) -> dict:
    """执行已经确认过的操作；令牌不符或参数非法时抛错且**不改动任何数据**。"""
    if not isinstance(request, dict) or request.get("scope") not in ("single", "following"):
        raise ValueError("请选择有效的操作范围")

    preview = preview_task_action(state["tasks"], request.get("id"), request.get("action"))
    token = request.get("token")
    if request["scope"] == "following" and (not isinstance(token, str) or token != preview["token"]):
        raise ValueError("事项已发生变化，请重新确认操作范围")
    if (
        request["scope"] == "single"
        and request.get("action") == "delete"
        and (not isinstance(token, str) or token != preview["singleToken"])
    ):
        raise ValueError("事项已发生变化，请重新确认删除")

    if request["scope"] == "following":
        ids = {item["id"] for item in preview["items"]}
    else:
        ids = {request["id"]}

    if request.get("action") == "delete":
        state["tasks"] = [item for item in state["tasks"] if item.get("id") not in ids]
        state["history"] = [entry for entry in state["history"] if entry.get("taskId") not in ids]
    else:
        completed = request["action"] == "complete"
        for item in state["tasks"]:
            if item.get("id") in ids:
                item["done"] = completed
                item["snoozeUntil"] = None
                item["updatedAt"] = int(time.time() * 1000)
        if completed:
            for entry in state["history"]:
                if entry.get("taskId") in ids:
                    entry["dismissed"] = True
    return state
