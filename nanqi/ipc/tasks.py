r"""tasks.py —— 计划的增删改与批量操作（对应 preload 的 saveTask / toggleTask 等）。

【为什么单独一组】
计划相关的调用最多、也最容易改错，而且它们的共同点是：都通过
`store.change(mutate)` 完成——在**深拷贝上改、落盘成功后才替换内存状态并广播**。
所以任何一项都不允许直接改 `self.store.state`，否则校验失败时内存已经被改脏了。

【两个容易踩的坑】
  * 保存旧编辑内容时，如果计划已被删除（另一个窗口删掉了），必须报错而不是「复活」它；
  * 计划被标记完成/删除/改期后，同一项的历史提醒要一并标记为已忽略，
    否则提醒面板会一直挂着一条点不开的记录。
"""

from __future__ import annotations

from ..core import clean_task, make_series
from ..task_series import apply_task_action, preview_task_action
from .base import HandlerMixin
from .envelope import ok

__all__ = ["TaskHandlers", "MAX_TASKS", "to_repeat"]

MAX_TASKS = 10000


def to_repeat(value):
    """把前端传来的 repeat 参数归一化成 `make_series` 认识的形式。

    对应 JS 的 `typeof repeat === 'object' ? repeat : Number(repeat)`：
    能转成数字就转，转不了就给一个「非法数字」，由 `make_series` 报「重复次数不正确」。
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, bool):
        return float("nan")
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return float("nan")
    return int(number) if float(number).is_integer() else number


class TaskHandlers(HandlerMixin):
    """计划读写。"""

    # ------------------------------------------------------------------ 读

    def _handle_get(self) -> dict:
        return ok(self._snapshot())

    def _category_ids(self) -> list[str]:
        """当前允许的分类 id（自定义分类功能之后，clean_task 需要这份列表）。"""
        return [c["id"] for c in self.store.state.get("categories", [])]

    # ------------------------------------------------------------------ 写

    def _handle_save_task(self, payload, repeat=1, exam_date=None) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("计划格式不正确")
        state = self.store.state
        existing = next(
            (task for task in state["tasks"] if task.get("id") == payload.get("id")), None
        )
        if payload.get("id") and existing is None:
            raise ValueError("计划已被删除，无法保存旧编辑内容")

        draft = {**payload, "done": existing.get("done") if existing else payload.get("done")}
        task = clean_task(draft, existing, categories=self._category_ids())
        created = [] if existing else make_series(task, to_repeat(repeat))
        if not existing and task.get("kind") == "review" and exam_date:
            from datetime import date, timedelta
            from ..core.clock import parse_calendar_date
            exam = parse_calendar_date(exam_date)
            if exam is None:
                raise ValueError("考试日期不正确")
            for offset in (7, 3, 1):
                day = (exam - timedelta(days=offset)).isoformat()
                if day >= date.today().isoformat() and day != task["date"]:
                    created.append(clean_task({**payload, "id": "", "date": day,
                                              "title": "复习：" + task["title"], "done": False},
                                             categories=self._category_ids()))
        if len(state["tasks"]) + len(created) > MAX_TASKS:
            raise ValueError("计划总数不能超过 10000 项，请缩短重复范围或先导出归档")

        def mutate(next_state: dict) -> None:
            if existing:
                next_state["tasks"] = [
                    task if item.get("id") == task["id"] else item for item in next_state["tasks"]
                ]
                if task.get("done"):
                    _dismiss_history(next_state, task["id"])
            else:
                next_state["tasks"].extend(created)

        return ok(self.store.change(mutate))

    def _handle_preview_task_action(self, task_id, action) -> dict:
        return ok(preview_task_action(self.store.state["tasks"], task_id, action))

    def _handle_apply_task_action(self, request) -> dict:
        return ok(self.store.change(lambda next_state: apply_task_action(next_state, request)))



    def _handle_move_task(self, task_id, date, time_text=None) -> dict:
        existing = next(
            (item for item in self.store.state["tasks"] if item.get("id") == task_id), None
        )
        if existing is None:
            raise ValueError("计划不存在")
        updated = clean_task(
            {**existing, "date": date, "time": time_text or existing["time"]},
            existing,
            categories=self._category_ids(),
        )

        def mutate(next_state: dict) -> None:
            next_state["tasks"] = [
                updated if item.get("id") == task_id else item for item in next_state["tasks"]
            ]
            _dismiss_history(next_state, task_id)

        return ok(self.store.change(mutate))

    def _handle_edit_task(self, task_id) -> dict:
        if not any(task.get("id") == task_id for task in self.store.state["tasks"]):
            raise ValueError("该计划已被删除")
        return self._host_call("edit_task", task_id)

    # ------------------------------------------------------------------ 附件

    def _handle_pick_attachments(self) -> dict:
        """弹出文件选择框，返回 [{name, path}]；取消时返回空列表。"""
        return self._host_call("pick_attachments")

    def _handle_open_attachment(self, path) -> dict:
        return self._host_call("open_attachment", path)


def _dismiss_history(state: dict, task_id) -> None:
    """把这项计划的历史提醒标记为已忽略（完成/删除/改期后不该再挂在提醒面板上）。"""
    for entry in state["history"]:
        if entry.get("taskId") == task_id:
            entry["dismissed"] = True
