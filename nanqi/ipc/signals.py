r"""signals.py —— 只发给主窗口的信号（托盘菜单与「新建/编辑」跳转）。

【为什么要单独一个对象】
`new-task` / `edit-task` 必须**只送进主窗口**，不能广播到桌面便签或提醒窗口。
原版用 `win.webContents.send` 定向发送；Qt 这边让每个窗口各自连通道，只有主窗口
注册这个对象，于是定向发送就变成了「谁注册谁收到」。

放在独立模块里，是因为「窗口层只关心信号」而「IPC 层只关心调用分发」，
两者混在一起每次都会把文件越写越大。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

__all__ = ["MainSignals"]


class MainSignals(QObject):
    """主窗口专用的信号。"""

    newTask = Signal(str)
    editTask = Signal(str)
