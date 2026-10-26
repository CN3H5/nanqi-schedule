r"""bridge.py —— 暴露给页面的那个 Python 对象（在 JS 里就是 `window.nanqi` 的后端）。

【结构】
真正的处理函数按领域拆在同目录的几个 mixin 里：

    tasks.py      计划增删改、批量操作
    reminders.py  稍后提醒 / 完成 / 忽略 / 测试提醒
    settings.py   应用设置、便签设置、备份导入导出
    system.py     窗口与进程级操作

本文件只做三件事：`QObject` 的构造、**一个入口** `invoke` 的分派、以及把错误推给界面。
所以它很短，而「接口清单」看各个 mixin 的文件头即可。

【为什么只有一个入口】
原版 preload 暴露 20 多个方法、主进程用 `ipcMain.handle` 逐个注册。这里改成
`invoke(name, argsJson) -> 信封`：

  * 接口清单集中在分派逻辑一处，便于和 preload.cjs 核对有没有漏移植；
  * 返回值与异常走同一条 JSON 路径，不会被 Qt 的类型转换吃掉；
  * 异常一定被捕获——**PySide6 的 Slot 抛异常时 JS 的回调永远不会被调用**
    （前端会静默卡在 await），所以这里的 try/except 是必需的，不是可选的。
"""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal, Slot

from .envelope import envelope, reject
from .categories import CategoryHandlers
from .reminders import ReminderHandlers
from .settings import SettingsHandlers
from .system import SystemHandlers
from .tasks import TaskHandlers

__all__ = ["Bridge"]


class Bridge(
    TaskHandlers,
    CategoryHandlers,
    ReminderHandlers,
    SettingsHandlers,
    SystemHandlers,
    QObject,
):
    """暴露给页面的对象（JS 里对应 `window.nanqi` 的后端）。"""

    # 广播：完整快照（JSON 字符串）
    stateChanged = Signal(str)
    # 广播：需要提示给用户的错误
    appError = Signal(str)

    def __init__(self, store, host, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.host = host

    # ------------------------------------------------------------------ 入口

    @Slot(str, str, result=str)
    def invoke(self, name: str, arguments_json: str) -> str:
        try:
            arguments = json.loads(arguments_json) if arguments_json else []
        except Exception as error:  # noqa: BLE001
            return envelope(reject(f"参数格式不正确：{error}"))
        if not isinstance(arguments, list):
            arguments = [arguments]
        try:
            return envelope(self._dispatch(name, arguments))
        except Exception as error:  # noqa: BLE001 - Slot 里抛异常会静默卡住前端
            return envelope(reject(str(error) or error.__class__.__name__))

    def _dispatch(self, name: str, args: list) -> dict:
        allowed = {
            "get", "save-task", "preview-task-action", "apply-task-action", "move-task", "edit-task",
            "pick-attachments", "open-attachment", "save-category", "delete-category",
            "reminder-action", "test-reminder", "next-reminder-message", "settings", "widget-settings",
            "export", "import", "open-data", "show-main", "open-widget", "close-widget",
            "open-browser", "import-from", "open-mail", "shift-courses", "set-drag-regions", "quit",
        }
        handler = getattr(self, f"_handle_{name.replace('-', '_')}", None) if name in allowed else None
        if handler is None:
            return reject("不支持的调用：" + name)
        return handler(*args)

    # ------------------------------------------------------------------ 供窗口层调用

    def emit_error(self, message: str) -> None:
        """把错误推给所有窗口（对应 main.cjs 里的 `app-error`）。"""
        self.appError.emit(str(message))

    def emit_state(self, payload: str) -> None:
        self.stateChanged.emit(payload)
