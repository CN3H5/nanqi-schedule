"""window_layer.py —— 让桌面便签「沉到普通窗口下方」（移植自 src/window-layer.cjs + src/native/WindowLayer.cs）。

【原来的实现】
Electron 版靠一个单独的 C# 小工具 `WeeklightLayer.exe`：主进程把窗口句柄和自己的
PID 传给它，它先用 `IsWindow` 与 `GetWindowThreadProcessId` 确认「这个句柄确实是
调用者进程的窗口」，再调用 `SetWindowPos(hwnd, HWND_BOTTOM, ..., SWP_NOSIZE |
SWP_NOMOVE | SWP_NOACTIVATE)` 只改层叠顺序，不移动、不缩放、不激活。

【Python 版为什么不再需要那个 exe】
`ctypes` 可以直接调用同一个 Win32 API，于是省掉一个外部程序和一次进程创建。
但原来那两条**安全检查必须保留**：确认句柄有效、确认它属于当前进程，否则一旦
句柄被复用，就会去改别的程序窗口的层级。
"""

from __future__ import annotations

import ctypes
import os

__all__ = [
    "send_to_bottom",
    "set_always_on_top",
    "native_handle",
    "is_windows",
    "WM_NCHITTEST",
    "HTCLIENT",
    "HTCAPTION",
    "HTLEFT",
    "HTRIGHT",
    "HTTOP",
    "HTTOPLEFT",
    "HTTOPRIGHT",
    "HTBOTTOM",
    "HTBOTTOMLEFT",
    "HTBOTTOMRIGHT",
    "decode_hit_test_point",
    "screen_to_client",
    "client_size",
]

HWND_BOTTOM = 1
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
# 只改层叠顺序：不动位置、不动大小、不抢焦点。
_FLAGS = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE

# ---- 无边框窗口的命中测试（让便签能像普通窗口一样拖动与缩放） ----
# Windows 会在鼠标移动时发 WM_NCHITTEST 问「这个点在窗口的哪个部位」，
# 返回 HTCAPTION 就等于告诉系统「这里是标题栏」（于是原生拖动、贴边、Aero Snap 都生效），
# 返回 HTLEFT/HTBOTTOMRIGHT 等则得到原生的缩放（连鼠标指针形状都是系统画的）。
WM_NCHITTEST = 0x0084
HTCLIENT = 1
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17


def is_windows() -> bool:
    return os.name == "nt"


def native_handle(window) -> int:
    """取窗口的 Win32 句柄（对应 Electron 的 `getNativeWindowHandle`）。"""
    return int(window.winId())


def send_to_bottom(window, expected_pid: int | None = None) -> bool:
    """把窗口放到所有普通窗口下方；成功返回 True。

    不可见或已销毁的窗口直接跳过（与 JS 版一致），因为此时改层级没有意义，
    而且隐藏窗口在 Electron 里的句柄可能已经失效。
    """
    if not is_windows() or window is None:
        return False
    try:
        if not window.isVisible():
            return False
    except RuntimeError:
        return False

    user32 = ctypes.windll.user32
    try:
        handle = native_handle(window)
    except RuntimeError:
        return False
    if not handle:
        return False

    if not user32.IsWindow(handle):
        return False
    # 句柄可能已被系统复用给别的进程，必须再确认一次归属。
    owner = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
    if expected_pid is not None and int(owner.value) != int(expected_pid):
        return False

    return bool(user32.SetWindowPos(handle, HWND_BOTTOM, 0, 0, 0, 0, _FLAGS))


def set_always_on_top(window, enabled: bool, expected_pid: int | None = None) -> bool:
    """切换置顶状态（对应 Electron 的 `setAlwaysOnTop`）。

    这里用 `SetWindowPos` 而不是 Qt 的窗口标志：改 Qt 的 `WindowStaysOnTopHint`
    会让 Windows 重建窗口（窗口会闪一下、可能丢位置），而 Win32 调用是就地改层级。
    """
    if not is_windows() or window is None:
        return False
    user32 = ctypes.windll.user32
    try:
        handle = native_handle(window)
    except RuntimeError:
        return False
    if not handle or not user32.IsWindow(handle):
        return False
    owner = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
    if expected_pid is not None and int(owner.value) != int(expected_pid):
        return False
    target = HWND_TOPMOST if enabled else HWND_NOTOPMOST
    return bool(user32.SetWindowPos(handle, target, 0, 0, 0, 0, _FLAGS))


def decode_hit_test_point(message_pointer) -> tuple[int, int] | None:
    """从 WM_NCHITTEST 的消息里取出**屏幕坐标**。

    参数是 Qt `nativeEvent` 给的 MSG 指针。lParam 的低 16 位是 x、高 16 位是 y，
    并且都是**有符号**的 16 位（多显示器时可以出现负坐标）。
    """
    try:
        from ctypes import wintypes

        message = ctypes.cast(int(message_pointer), ctypes.POINTER(wintypes.MSG)).contents
    except Exception:  # noqa: BLE001 - 拿不到消息就当没有命中测试
        return None
    packed = int(message.lParam)
    x = ctypes.c_short(packed & 0xFFFF).value
    y = ctypes.c_short((packed >> 16) & 0xFFFF).value
    return x, y


def screen_to_client(handle: int, x: int, y: int) -> tuple[int, int]:
    """屏幕坐标 → 客户区坐标（物理像素）；Qt 的几何值是逻辑像素，所以必须让系统换算。"""
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    point = wintypes.POINT(int(x), int(y))
    user32.ScreenToClient(wintypes.HWND(handle), ctypes.byref(point))
    return int(point.x), int(point.y)


def client_size(handle: int) -> tuple[int, int]:
    """客户区尺寸（物理像素）。"""
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    user32.GetClientRect(wintypes.HWND(handle), ctypes.byref(rect))
    return int(rect.right - rect.left), int(rect.bottom - rect.top)
