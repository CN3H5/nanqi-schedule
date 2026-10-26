"""main_window.py —— 主日历窗口。

对应 Electron 版 `main.cjs` 里的 `createWindow()`：

    width 1450 / height 950 / min 1050x720，标题「南七日程 · 计划你的每一周」,
    关掉菜单栏，关闭按钮**不退出程序**（收进托盘继续提醒）。

`minWidth/minHeight` 在 Qt 里用 `setMinimumSize`，`autoHideMenuBar` 对应
`setMenuBar(None)`（本来就没建菜单栏，所以只需不建即可）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from ..window_branding import apply_branding
from .webview import WebPageView

__all__ = ["MainWindow"]

TITLE = "南七日程 · 计划你的每一周"
DEFAULT_SIZE = (1450, 950)
MINIMUM_SIZE = (1050, 720)


class MainWindow(QMainWindow):
    def __init__(self, bridge, main_signals, web_dir: Path, packaged: bool | None = None, parent=None) -> None:
        super().__init__(parent)
        self._quitting = False
        self.setWindowTitle(TITLE)
        self.resize(*DEFAULT_SIZE)
        self.setMinimumSize(*MINIMUM_SIZE)

        self.view = WebPageView(
            str(web_dir / "index.html"),
            {"bridge": bridge, "main": main_signals},
            parent=self,
        )
        self.setCentralWidget(self.view)
        apply_branding(self, packaged)

    # ------------------------------------------------------------------ 行为

    def show_main(self) -> None:
        """把主窗口带到前台（对应 `showMain()`）。"""
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def allow_close(self) -> None:
        """真正要退出时调用，让 `close()` 生效。"""
        self._quitting = True

    def closeEvent(self, event) -> None:  # noqa: N802
        # 与 Electron 版一致：关闭主窗口只是隐藏，托盘与提醒继续工作。
        if self._quitting:
            event.accept()
            return
        event.ignore()
        self.hide()
