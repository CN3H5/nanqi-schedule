r"""reminder.py —— 到点提醒的判定（`dueTasks`）。

【为什么单独成模块】
判定规则里有三处「JS 真值语义」的坑，集中在一处才不容易改错：

  * `snoozeUntil` 用 `if (snooze)` 判断：`0` 在 JS 里是假值，等价于「没有稍后提醒」；
  * `notifiedAt` 同理：`0` 等同于「还没提醒过」；
  * `reminder >= 0` 才提醒，`-1` 表示这项计划不要提醒。

还有一个行为是**有意为之**：只要没人把 `notifiedAt` 写掉，重启应用或从休眠唤醒后
都会补发已经错过的提醒（用户宁可看到迟到提醒，也不要静默漏掉）。
"""

from __future__ import annotations

from .clock import at
from .numbers import is_finite_number, now_ms

__all__ = ["due_tasks"]


def due_tasks(tasks: list[dict], now: int | None = None) -> list[dict]:
    """到点需要提醒的计划，按发生时刻排序，等价于 `dueTasks(tasks, now)`。

    规则：未完成、开启了提醒（reminder >= 0），并且
      - 设过「稍后提醒」时，只看 snoozeUntil 是否已到；
      - 否则要求尚未提醒过（notifiedAt 为假值）且已进入提醒区间。
    """
    moment = now_ms() if now is None else now

    def is_due(task: dict) -> bool:
        if task.get("done") or not is_finite_number(task.get("reminder")):
            return False
        if int(task["reminder"]) < 0:
            return False
        snooze = task.get("snoozeUntil")
        if snooze:  # JS 真值判断：0 视为「没有稍后提醒」
            return snooze <= moment
        if task.get("notifiedAt"):  # 同上
            return False
        return at(task) - int(task["reminder"]) * 60000 <= moment

    return sorted((task for task in tasks if is_due(task)), key=at)
