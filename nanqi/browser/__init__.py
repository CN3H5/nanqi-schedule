"""内置浏览器（移植自 ustc-assistant 的 QtWebEngine 浏览器）。

原来的 `ui/browser_view.py` 提供：多标签页、地址栏与前进/后退/刷新/主页、书签主页。
这里整份搬过来，只做了两处必要适配：

  * 主题色不再依赖原来的 `ui.theme`，改用本项目的 `palette`（同一套 sage 绿色）；
  * 书签在科大常用站点之外补了几个通用站点。

`view.py` 里保留了原文件对「为什么用 QtWebEngine」「打包后为什么会白屏」的说明。
打包相关的修复（`QTWEBENGINEPROCESS_PATH` 等）由 `nanqi.webengine.py` 与
`packaging/rthook_webengine.py` 负责。
"""

from __future__ import annotations

from .palette import PRIMARY
from .view import (
    BOOKMARKS,
    BrowserHomePage,
    BrowserTab,
    BrowserView,
    BrowserWindow,
    is_webengine_available,
)

__all__ = [
    "BOOKMARKS",
    "BrowserHomePage",
    "BrowserTab",
    "BrowserView",
    "BrowserWindow",
    "is_webengine_available",
    "PRIMARY",
]
