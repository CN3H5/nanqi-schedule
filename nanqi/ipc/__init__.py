r"""ipc —— 前端与 Python 之间的调用层（对应 preload.cjs + ipcMain）。

【怎么用】
窗口层只需要：

    from .ipc import Bridge, MainSignals
    bridge = Bridge(store, host)

前端拿到的是 `window.nanqi`（由 `assets/web/bridge.js` 建好），所有调用都走
`bridge.invoke(name, argsJson)`。

【为什么拆成包】
原来一个 `bridge.py`（约 360 行）里塞了 30 个处理器。拆分后按「改动的理由」分组：
改计划逻辑只动 `tasks.py`，改设置只动 `settings.py`，加一个新领域就是加一个文件，
`_dispatch` 不用改。对外仍然只有 `Bridge` 一个对象、一个 `invoke` 入口。
"""

from __future__ import annotations

from .bridge import Bridge
from .envelope import envelope, ok, reject
from .signals import MainSignals

__all__ = ["Bridge", "MainSignals", "ok", "reject", "envelope"]
