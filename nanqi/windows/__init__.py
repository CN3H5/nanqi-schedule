"""窗口层：主日历窗口、桌面便签窗口、提醒窗口。

三个窗口都是「QWebEngineView 里跑原来的 HTML/CSS/JS」，因为原版就是 Electron
（Chromium 内核）实现的界面——保留前端文件不动，只把主进程换成 Python，
界面才能和原版逐像素一致。
"""

from __future__ import annotations

__all__ = ["WebPageView", "MainWindow", "WidgetWindow", "ReminderWindow"]


def __getattr__(name: str):
    # 延迟导入：避免 QtWebEngine 在只需要其中一个窗口时被提前初始化。
    if name == "WebPageView":
        from .webview import WebPageView

        return WebPageView
    if name == "MainWindow":
        from .main_window import MainWindow

        return MainWindow
    if name == "WidgetWindow":
        from .widget_window import WidgetWindow

        return WidgetWindow
    if name == "ReminderWindow":
        from .reminder_window import ReminderWindow

        return ReminderWindow
    raise AttributeError(name)
