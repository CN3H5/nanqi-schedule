r"""webview.py —— 让页面能调用 Python 的公共底座（替代 Electron 的 preload + ipcRenderer）。

【原版是怎么通的】
Electron 的 preload 脚本用 `contextBridge.exposeInMainWorld('weeklight', {...})`
把一组 `ipcRenderer.invoke` 包装注入页面，主进程用 `ipcMain.handle` 应答。
换成 QtWebEngine 后，等价的官方通道是 **QWebChannel**：

    Python 侧：QWebChannel(page) + registerObject('bridge', Bridge())
    页面侧：  读 Qt 注入的 `qt.webChannelTransport`，用 qwebchannel.js 建连接

（本版把注入到页面的全局对象命名为 `window.nanqi`，与项目英文名一致。）

【三个必须注意的点】

1. **qwebchannel.js 从 Qt 自己的资源里取**（`:/qtwebchannel/qwebchannel.js`）。
   它在 PySide6 里不是磁盘文件，而是编进 QtWebChannel 模块的资源；从资源读出来
   注入，能保证 JS 客户端与当前 Qt 版本完全匹配（自己复制一份早晚会不同步）。
   注入点选 `DocumentCreation`，让它在页面脚本之前就绪。

2. **注入脚本不受页面 CSP 限制**，但页面自己的内联脚本受限制——原来三个 HTML
   都带 `script-src 'self'`，所以连接逻辑必须放在**外部文件** `bridge.js` 里
   （见 assets/web/bridge.js），不能写成内联 <script>。

3. **链接策略按 Qt 6 的 API 实现**。原版用 Electron 的 `will-navigate`
   （阻止页面导航）与 `setWindowOpenHandler`（拒绝弹窗）。Qt 里对应的位置是
   `QWebEnginePage.acceptNavigationRequest()` 与 `newWindowRequested` 信号。
   这里保留「应用页面本身绝不跳走」，并把外链交给内置浏览器打开（原版是直接
   拦掉什么都不做，交给内置浏览器更符合「应用里有浏览器」的预期）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject, QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView

__all__ = ["AppWebEnginePage", "WebPageView", "read_qt_text_resource", "install_channel"]

_CHANNEL_RESOURCE = ":/qtwebchannel/qwebchannel.js"
_SCRIPT_NAME = "nanqi-schedule-qwebchannel"


def read_qt_text_resource(path: str) -> str:
    """读取 Qt 资源里的文本文件（qwebchannel.js 就放在那里）。"""
    handle = QFile(path)
    if not handle.exists() or not handle.open(QIODevice.ReadOnly):
        raise RuntimeError(f"缺少 Qt 资源：{path}")
    try:
        return bytes(handle.readAll()).decode("utf-8")
    finally:
        handle.close()


def install_channel(page, objects: dict, parent=None) -> QWebChannel:
    """给一个页面装上 QWebChannel，并注入 qwebchannel.js。

    `objects` 是「JS 里的对象名 → QObject」；`bridge` 所有页面共用，
    另外主窗口会额外注册 `main`（只用于接收新建/编辑跳转，不广播到其它窗口）。
    """
    channel = QWebChannel(page)
    for name, target in objects.items():
        channel.registerObject(name, target)
    page.setWebChannel(channel)

    script = QWebEngineScript()
    script.setName(_SCRIPT_NAME)
    script.setSourceCode(read_qt_text_resource(_CHANNEL_RESOURCE))
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    page.scripts().insert(script)
    return channel


class AppWebEnginePage(QWebEnginePage):
    """应用内页面使用的 QWebEnginePage：拦下所有「离开本地页面」的导航。

    只允许 `file:` 的本地页面在自身内部跳转；其余地址一律拒绝，并（如果配好了）
    转交内置浏览器。这样日历、便签、提醒三个窗口永远是应用自己的界面。
    """

    def __init__(self, profile=None, parent=None, *, trusted_file=None) -> None:
        if profile is None:
            super().__init__(parent)
        else:
            super().__init__(profile, parent)
        self._external_opener = None
        self._trusted_file = Path(trusted_file).resolve() if trusted_file else None
        try:
            self.newWindowRequested.connect(self._on_new_window_requested)
        except AttributeError:  # 极老版本没有这个信号
            pass

    def set_external_opener(self, opener) -> None:
        self._external_opener = opener

    def _hand_off(self, url: str) -> None:
        if self._external_opener is None or QUrl(url).scheme().lower() not in ("http", "https", "mailto"):
            return
        try:
            self._external_opener(url)
        except Exception:  # noqa: BLE001 - 打不开外链不能影响应用页面
            pass

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:  # noqa: N802
        if is_main_frame and url.scheme() == "file" and self._trusted_file:
            if Path(url.toLocalFile()).resolve() == self._trusted_file:
                return True
        if is_main_frame:
            self._hand_off(url.toString())
        return False

    def javaScriptConsoleMessage(self, level, message: str, line: int, source: str) -> None:  # noqa: N802
        """把页面里的 console 输出转到 stderr。

        应用平时没有开发者工具，页面一旦报错就只能看到「白屏」或者「界面没反应」。
        打包成 exe 后没有控制台时这些内容会被忽略，但用 `--doctor`/命令行启动时
        它是排查前端问题最直接的线索。
        """
        try:
            print(f"[页面] {source}:{line} {message}", file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001
            pass
        super().javaScriptConsoleMessage(level, message, line, source)

    def _on_new_window_requested(self, request) -> None:
        """对应 Electron 的 `setWindowOpenHandler(() => ({action:'deny'}))`。

        不调用 `request.openIn(...)` 就等于拒绝；地址转交内置浏览器。
        """
        try:
            self._hand_off(request.requestedUrl().toString())
        except Exception:  # noqa: BLE001
            pass


class WebPageView(QWebEngineView):
    """带 Python 通道的 QWebEngineView（页面 + 通道 + 链接策略的三件套装配）。"""

    def __init__(
        self,
        url: str,
        objects: dict,
        profile=None,
        parent=None,
        transparent: bool = False,
    ) -> None:
        super().__init__(parent)
        self.page_impl = AppWebEnginePage(profile, self, trusted_file=url)
        self.setPage(self.page_impl)
        if transparent:
            # 桌面便签是圆角卡片 + CSS 阴影：页面背景必须真的透明，
            # 否则 QtWebEngine 会画一层不透明底色，圆角外面就变成白框。
            from PySide6.QtCore import Qt as _Qt
            from PySide6.QtGui import QColor

            self.page_impl.setBackgroundColor(QColor(_Qt.GlobalColor.transparent))
        # channel 必须留引用：被 GC 掉页面就再也调不到 Python 了。
        self.channel = install_channel(self.page_impl, objects, parent=self)
        self.load(QUrl.fromLocalFile(url))

    def set_external_opener(self, opener) -> None:
        self.page_impl.set_external_opener(opener)

    @property
    def app_page(self) -> AppWebEnginePage:
        return self.page_impl
