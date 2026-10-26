"""window_branding.py —— 任务栏身份与窗口图标（移植自 src/window-branding.cjs）。

【原版在解决什么问题】
Windows Shell 按「AppUserModelID」把窗口归组、决定任务栏图标与固定项。
Electron 版做了两件容易踩坑的事，这里都保留其意图：

  1. 开发版与安装版使用**不同**的 AppUserModelID，否则从源码启动会顶掉正式版
     在任务栏里的身份和图标；
  2. 图标用物理 .ico 文件（打包后放在资源目录里），而不是从归档内部读。

【Python 版的做法】
用 `SetCurrentProcessExplicitAppUserModelID` 设置进程级 AppUserModelID（这是
决定任务栏归组与图标归属的那一项），并把窗口图标设为 .ico。

【已知差异】
Electron 还能通过 `setAppDetails` 给每个窗口写「重新启动命令」与「图标索引」。
Qt 没有等价的公开接口，所以这两项由打包时的 `--icon`（exe 内嵌图标）承担，
效果上任务栏与开始菜单显示的是同一个图标。
"""

from __future__ import annotations

import ctypes
import os
import sys

from .paths import icon_path

__all__ = ["APP_ID", "APP_NAME", "resolve_app_id", "apply_branding", "apply_app_user_model_id"]

APP_ID = "local.nanqi.schedule"
APP_NAME = "南七日程"


def resolve_app_id(packaged: bool) -> str:
    """安装版用正式 ID；开发版加 `.development` 后缀，避免抢占正式版的身份。"""
    return APP_ID if packaged else APP_ID + ".development"


def _is_windows() -> bool:
    return os.name == "nt"


def apply_app_user_model_id(app_id: str) -> bool:
    """设置当前进程的 AppUserModelID；非 Windows 或调用失败时返回 False。"""
    if not _is_windows():
        return False
    try:
        shell32 = ctypes.windll.shell32
        result = shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
        return int(result) == 0
    except Exception:  # noqa: BLE001 - 品牌信息设置失败不应影响启动
        return False


def apply_branding(window, packaged: bool | None = None) -> str:
    """给窗口套上应用身份与图标，返回实际使用的 AppUserModelID。

    必须在窗口**第一次显示之前**调用：Windows 是在窗口创建时记录任务栏归属的。
    """
    if packaged is None:
        packaged = bool(getattr(sys, "frozen", False))
    app_id = resolve_app_id(packaged)
    apply_app_user_model_id(app_id)

    icon = icon_path()
    if icon.is_file():
        try:
            from PySide6.QtGui import QIcon

            window.setWindowIcon(QIcon(str(icon)))
        except Exception:  # noqa: BLE001
            pass
    return app_id
