"""notify.py —— 系统通知（移植自 src/main.cjs 里的 `notify()`）。

【原来的行为】
Electron 用 `new Notification({title, body, silent: !settings.sound, icon})`，
点击通知会打开主窗口，通知对象会保留引用直到关闭（否则 GC 掉就消失）。

【Python 版】
用 `QSystemTrayIcon.showMessage` 发系统通知：这是 Qt 在 Windows 上唯一的原生通知
入口，点击后由托盘图标的 `messageClicked` 信号通知主窗口。

关于「提醒声音」设置，需要说明一处**刻意的差异**：Qt 的 `showMessage` 不接受
「静音」参数（Windows 的 `NIIF_NOSOUND` 标志没有被 Qt 暴露），所以无法阻止气泡
自带系统提示音。这里的处理是：
  * 开启「提醒声音」时，额外主动播放一次系统提示音，保证一定听得见；
  * 关闭时，应用自己不再发出任何声音。
气泡自身是否发声仍由 Windows 的「通知」系统设置决定——这也是系统级开关，
用户可以在那里统一控制。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal

__all__ = ["Notifier", "supports_sound"]

# 通知显示时长（毫秒）；与 Electron 版 60 秒的引用保留时间无关，只是气泡停留时间。
MESSAGE_TIMEOUT_MS = 10000


def supports_sound() -> bool:
    """Windows 上才能主动播放提示音。"""
    return os.name == "nt"


class Notifier(QObject):
    """把通知统一收在这一处：托盘气泡 + 可选提示音。"""

    clicked = Signal()

    def __init__(self, tray=None, icon=None, parent=None) -> None:
        super().__init__(parent)
        self._tray = tray
        self._icon = icon
        self._last_title = ""
        self._last_body = ""
        if tray is not None:
            try:
                tray.messageClicked.connect(self._on_clicked)
            except Exception:  # noqa: BLE001
                pass

    def set_tray(self, tray) -> None:
        """托盘图标创建得比通知器晚时，用它补上连接。"""
        self._tray = tray
        try:
            tray.messageClicked.connect(self._on_clicked)
        except Exception:  # noqa: BLE001
            pass

    def notify(self, title: str, body: str, silent: bool = False) -> bool:
        """发一条系统通知；`silent=True` 表示应用不主动发声。"""
        self._last_title = title
        self._last_body = body
        if not silent:
            self._play_sound()
        if self._tray is None:
            return False
        try:
            from PySide6.QtWidgets import QSystemTrayIcon

            if not QSystemTrayIcon.supportsMessages():
                return False
            icon = self._icon if self._icon is not None else QSystemTrayIcon.MessageIcon.Information
            self._tray.showMessage(title, body, icon, MESSAGE_TIMEOUT_MS)
            return True
        except Exception:  # noqa: BLE001 - 通知失败不能影响提醒流程
            return False

    def _play_sound(self) -> None:
        if not supports_sound():
            return
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:  # noqa: BLE001
            pass

    def _on_clicked(self) -> None:
        self.clicked.emit()

    # ------------------------------------------------------------------ 诊断

    def icon_path(self) -> str:
        """托盘/通知使用的图标路径（由 paths 模块统一解析）。"""
        from .paths import icon_path

        return str(icon_path())
