"""bridge.py —— 前端与 Python 之间的唯一通道（对外门面）。

真正的实现在 `nanqi/ipc/` 包里，按领域拆成了几个处理器 mixin：

    ipc/tasks.py      计划增删改与批量操作
    ipc/reminders.py  提醒动作
    ipc/settings.py   应用/便签设置、备份、数据目录
    ipc/system.py     窗口与进程级操作
    ipc/bridge.py     Bridge 对象本身（QObject + 唯一的 invoke 入口）
    ipc/signals.py    MainSignals（只发给主窗口）

这里保留一层薄门面，是为了让 `from .bridge import Bridge, MainSignals` 这类既有
写法不用改——包内怎么拆是实现细节，窗口层与测试不需要跟着改。
"""

from __future__ import annotations

from .ipc import Bridge, MainSignals, envelope, ok, reject

__all__ = ["Bridge", "MainSignals", "ok", "reject", "envelope"]
