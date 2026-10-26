"""reminder_window.py —— 独立提醒弹窗。

对应 `main.cjs` 里的 `showReminder()`：460x550、可缩放、**始终置顶**，
窗口里是名句 + 待处理提醒列表（完成 / 10 分钟后再提醒 / 知道了）。
原版每次弹窗关闭后销毁窗口对象，下次需要时重新创建；这里保持一致，
所以关闭事件直接接受（不拦截）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..window_branding import apply_branding
from .webview import WebPageView

__all__ = ["ReminderWindow"]

TITLE = "南七日程 · 提醒"
SIZE = (460, 550)
MINIMUM_SIZE = (400, 440)


class ReminderWindow(QWidget):
    def __init__(self, bridge, web_dir: Path, packaged: bool | None = None, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(TITLE)
        self.resize(*SIZE)
        self.setMinimumSize(*MINIMUM_SIZE)
        # 提醒必须盖在其它窗口上面（对应 `alwaysOnTop: true`）。
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.view = WebPageView(
            str(web_dir / "reminder.html"),
            {"bridge": bridge},
            parent=self,
        )
        layout.addWidget(self.view)
        apply_branding(self, packaged)

    def show_inactive(self) -> None:
        """不夺焦点地显示（对应 `showInactive()`），避免打断用户正在输入的内容。"""
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.show()
        self.raise_()
