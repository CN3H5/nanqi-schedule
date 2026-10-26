r"""widget_window.py —— 桌面便签窗口（移植自 src/widget-controller.cjs + src/window-layer.cjs）。

【它在模仿什么】
Electron 版的便签是一个 `frame:false, transparent:true, skipTaskbar:true` 的
BrowserWindow：圆角半透明卡片、可拖动、可缩放、默认沉在普通窗口下方、可以置顶。

【Qt 里怎么做到同样的事】

1. **无边框 + 透明**：`Qt.FramelessWindowHint | Qt.Tool`（Tool 同时等于
   `skipTaskbar`），加 `WA_TranslucentBackground`；页面背景色必须显式设为透明，
   否则 QtWebEngine 会画一层不透明底色。

2. **拖动与缩放**：不靠 `-webkit-app-region`（那是 Electron 专有的），而是接管
   `WM_NCHITTEST`——系统问「鼠标现在在窗口的哪个部位」时，我们回答
   「顶部标题区 = HTCAPTION（标题栏）」「边缘 = HTLEFT/HTBOTTOMRIGHT…（缩放边）」，
   剩下的回 HTCLIENT 交给网页。好处是拖动、双击最大化行为、贴边、鼠标指针形状
   全部由 Windows 原生完成，和普通窗口手感一致。
   标题区里那几个按钮（图钉 / ⋯ / ×）需要能点击，所以网页会把
   「不可拖动区域」的矩形上报给 Python（见 assets/web/bridge.js 的 `set-drag-regions`）。

3. **沉底与置顶**：用 `SetWindowPos` 改层级，而不是 Qt 的窗口标志——后者会让
   Windows 重建窗口，便签会闪一下而且可能丢位置。原生调用按顺序串行执行，
   避免「慢一步的沉底」把刚设置的置顶覆盖掉（原版专门为此写了串行队列）。

4. **位置与尺寸**：移动/缩放结束后防抖 400 ms 写回 `plans.json`，下次启动恢复；
   恢复前先按「屏幕工作区」夹紧，避免显示器拔掉后便签跑到看不见的地方。
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QRect, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .. import window_layer as layer
from ..window_branding import apply_branding
from .webview import WebPageView

__all__ = ["WidgetWindow"]

TITLE = "南七日程 · 桌面便签"
MIN_SIZE = (340, 380)
MAX_SIZE = (680, 1000)
DEFAULT_SIZE = (410, 650)
DEFAULT_OFFSET = (438, 44)  # 距主屏右上角：右边留 28 px，上边留 44 px
SAVE_DELAY_MS = 400
RESIZE_BORDER = 8  # 缩放边的宽度（逻辑像素）


class WidgetWindow(QWidget):
    """桌面便签窗口。"""

    def __init__(
        self,
        bridge,
        web_dir: Path,
        preferences,
        save_bounds,
        packaged: bool | None = None,
        report_error=None,
        parent=None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self._preferences = preferences
        self._save_bounds = save_bounds
        self._report_error = report_error or (lambda message: None)
        self._quitting = False
        self._bounds_restored = False
        # 网页上报的「可拖动 / 不可拖动」矩形（逻辑像素）。
        self._drag_regions: list[tuple[float, float, float, float]] = []
        self._no_drag_regions: list[tuple[float, float, float, float]] = []

        self.setWindowTitle(TITLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(*MIN_SIZE)
        self.setMaximumSize(*MAX_SIZE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.view = WebPageView(
            str(web_dir / "widget.html"),
            {"bridge": bridge},
            parent=self,
            transparent=True,
        )
        layout.addWidget(self.view)
        apply_branding(self, packaged)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save_bounds_now)

    # ------------------------------------------------------------------ 位置与尺寸

    def restore_bounds(self) -> None:
        """按保存的偏好（并夹紧到屏幕工作区）恢复位置与尺寸（只在首次显示时做一次）。"""
        if self._bounds_restored:
            return
        self._bounds_restored = True
        saved = self._preferences().get("bounds")
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            return
        area = primary.availableGeometry()
        if isinstance(saved, dict):
            rect = QRect(
                int(saved["x"]), int(saved["y"]), int(saved["width"]), int(saved["height"])
            )
        else:
            width, height = DEFAULT_SIZE
            offset_x, offset_y = DEFAULT_OFFSET
            rect = QRect(area.x() + area.width() - offset_x, area.y() + offset_y, width, height)

        target = self._screen_for(rect, area)
        width = max(MIN_SIZE[0], min(rect.width(), target.width()))
        height = max(MIN_SIZE[1], min(rect.height(), target.height()))
        x = min(max(rect.x(), target.x()), target.x() + target.width() - width)
        y = min(max(rect.y(), target.y()), target.y() + target.height() - height)
        self.setGeometry(x, y, width, height)

    @staticmethod
    def _screen_for(rect: QRect, fallback: QRect) -> QRect:
        """挑一个「和这个矩形明显相交」的显示器工作区；找不到就用主屏。"""
        for screen in QGuiApplication.screens():
            area = screen.availableGeometry()
            if (
                rect.x() + rect.width() > area.x() + 40
                and rect.x() < area.x() + area.width() - 40
                and rect.y() + rect.height() > area.y() + 40
                and rect.y() < area.y() + area.height() - 40
            ):
                return area
        return fallback

    def save_bounds_now(self) -> None:
        """把当前几何写回设置（只在真的变化时才写盘）。"""
        self._save_timer.stop()
        geometry = self.geometry()
        current = {
            "x": geometry.x(),
            "y": geometry.y(),
            "width": geometry.width(),
            "height": geometry.height(),
        }
        if self._preferences().get("bounds") == current:
            return
        try:
            self._save_bounds(current)
        except Exception as error:  # noqa: BLE001
            self._report_error("便签位置未保存：" + str(error))

    def _debounce_save(self) -> None:
        self._save_timer.start()

    # ------------------------------------------------------------------ 层级

    def apply_layer(self) -> None:
        """按偏好设置置顶 / 沉底（对应 `updateLayer()`，顺序与原版一致）。"""
        if not self.isVisible():
            return
        on_top = self._preferences().get("onTop") is True
        pid = _current_pid()
        if not layer.set_always_on_top(self, on_top, pid):
            # 非 Windows 或调用失败时退回 Qt 的窗口标志（会重建窗口，但至少语义正确）。
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
            self.show()
            return
        if not on_top:
            layer.send_to_bottom(self, pid)
            # 沉底之后系统可能把它排到最底，再确认一次状态与偏好一致。
            layer.set_always_on_top(self, False, pid)
        else:
            layer.set_always_on_top(self, True, pid)

    def settle_layer(self) -> None:
        """延后到当前事件处理结束再改层级（窗口刚显示时句柄才有效）。"""
        QTimer.singleShot(0, self.apply_layer)

    # ------------------------------------------------------------------ 拖放区域上报

    def set_drag_regions(self, payload) -> None:
        """接收网页上报的矩形（逻辑像素）：可拖动区域与不可拖动区域。"""
        if isinstance(payload, dict):
            drag = payload.get("drag") or []
            no_drag = payload.get("noDrag") or []
        elif isinstance(payload, list):
            drag, no_drag = payload, []
        else:
            return
        self._drag_regions = _clean_rects(drag)
        self._no_drag_regions = _clean_rects(no_drag)

    # ------------------------------------------------------------------ 命中测试

    def nativeEvent(self, event_type, message):  # noqa: N802
        """接管 WM_NCHITTEST：把「顶部标题区」变成原生标题栏，「边缘」变成原生缩放边。"""
        if layer.is_windows() and bytes(event_type) in (
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        ):
            try:
                packed = int(message)
                msg = _peek_message(packed)
                if msg is not None and msg.message == layer.WM_NCHITTEST:
                    code = self._hit_test(packed)
                    if code is not None:
                        return True, code
            except Exception:  # noqa: BLE001 - 命中测试失败就退回默认行为
                pass
        return super().nativeEvent(event_type, message)

    def _hit_test(self, message_pointer) -> int | None:
        if not self._drag_regions:
            return None
        point = layer.decode_hit_test_point(message_pointer)
        if point is None:
            return None
        handle = int(self.winId())
        x, y = layer.screen_to_client(handle, point[0], point[1])
        width, height = layer.client_size(handle)
        ratio = float(self.devicePixelRatioF()) or 1.0

        # 不可拖动区域（标题栏里的按钮）优先：必须回到网页处理点击。
        for rect in self._no_drag_regions:
            if _inside(rect, x, y, ratio):
                return None

        edge = _edge_code(x, y, width, height, int(RESIZE_BORDER * ratio))
        if edge is not None:
            return edge

        for rect in self._drag_regions:
            if _inside(rect, x, y, ratio):
                return layer.HTCAPTION
        return None

    # ------------------------------------------------------------------ 生命周期

    def show_widget(self) -> None:
        """显示便签（不抢焦点，与 `showInactive()` 一致）。"""
        self.restore_bounds()
        self.show()
        self.settle_layer()

    def hide_widget(self) -> None:
        self.save_bounds_now()
        self.hide()

    def allow_close(self) -> None:
        self._quitting = True

    def closeEvent(self, event) -> None:  # noqa: N802
        # 与 Electron 版一致：关闭便签只是隐藏（由控制器同时更新 enabled 偏好）。
        if self._quitting:
            event.accept()
            return
        event.ignore()
        self.hide()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._debounce_save()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._debounce_save()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        # 失去激活且没有置顶时重新沉底，保证便签不会挡住正在用的窗口。
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            if self._preferences().get("onTop") is not True:
                self.settle_layer()


def _peek_message(pointer: int):
    """把 Qt `nativeEvent` 给的 MSG 指针转成可读的 MSG 结构。"""
    from ctypes import wintypes

    return ctypes.cast(int(pointer), ctypes.POINTER(wintypes.MSG)).contents


def _clean_rects(values) -> list[tuple[float, float, float, float]]:
    rects: list[tuple[float, float, float, float]] = []
    for item in values if isinstance(values, list) else []:
        if isinstance(item, (list, tuple)) and len(item) == 4:
            try:
                rects.append(tuple(float(value) for value in item))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        elif isinstance(item, dict):
            try:
                rects.append(
                    (
                        float(item.get("x", 0)),
                        float(item.get("y", 0)),
                        float(item.get("width", 0)),
                        float(item.get("height", 0)),
                    )
                )
            except (TypeError, ValueError):
                continue
    return rects


def _inside(rect: tuple[float, float, float, float], x: int, y: int, ratio: float) -> bool:
    left, top, width, height = rect
    rx, ry = left * ratio, top * ratio
    return rx <= x <= rx + width * ratio and ry <= y <= ry + height * ratio


def _edge_code(x: int, y: int, width: int, height: int, border: int) -> int | None:
    """根据到四边的距离决定是缩放边还是普通客户区。"""
    if border <= 0:
        return None
    left = x <= border
    right = x >= width - border
    top = y <= border
    bottom = y >= height - border
    if top and left:
        return layer.HTTOPLEFT
    if top and right:
        return layer.HTTOPRIGHT
    if bottom and left:
        return layer.HTBOTTOMLEFT
    if bottom and right:
        return layer.HTBOTTOMRIGHT
    if left:
        return layer.HTLEFT
    if right:
        return layer.HTRIGHT
    if top:
        return layer.HTTOP
    if bottom:
        return layer.HTBOTTOM
    return None


def _current_pid() -> int:
    return os.getpid()
