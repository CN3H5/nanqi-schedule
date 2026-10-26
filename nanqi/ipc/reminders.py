r"""reminders.py —— 提醒相关的调用（稍后提醒 / 完成 / 忽略 / 测试提醒）。

【规则与上游一致】
  * `snooze`：从现在起 10 分钟后再响；
  * `done`：标记完成并清掉稍后提醒；
  * `dismiss`：只是忽略这次提醒，计划本身不动；
  * 三种动作都会把对应的历史提醒标记为已忽略，否则提醒面板会挂着点不开的条目。

`test_reminder` 的顺序是「先推进一句名句，再通知并弹窗」——与 JS 版一致，
这样点一次测试提醒就能看到下一句文案。
"""

from __future__ import annotations

import time

from .base import HandlerMixin
from .envelope import ok

__all__ = ["ReminderHandlers"]

SNOOZE_MS = 600000  # 10 分钟


class ReminderHandlers(HandlerMixin):
    """提醒动作。"""

    def _handle_reminder_action(self, task_id, action) -> dict:
        def mutate(next_state: dict) -> None:
            task = next((item for item in next_state["tasks"] if item.get("id") == task_id), None)
            if task is None:
                return
            if action == "snooze":
                task["snoozeUntil"] = int(time.time() * 1000) + SNOOZE_MS
            elif action == "done":
                task["done"] = True
                task["snoozeUntil"] = None
            elif action != "dismiss":
                raise ValueError("操作无效")
            for entry in next_state["history"]:
                if entry.get("taskId") == task_id:
                    entry["dismissed"] = True

        return ok(self.store.change(mutate))

    def _handle_test_reminder(self) -> dict:
        """发送测试提醒：先推进一句文案，再通知并弹出提醒窗口（与 JS 版顺序一致）。"""
        self.store.change(lambda next_state: self.store.next_reminder_message(next_state))
        self._host_call("test_reminder")
        return ok(True)

    def _handle_next_reminder_message(self) -> dict:
        return ok(
            self.store.change(lambda next_state: self.store.next_reminder_message(next_state))
        )
