r"""system.py —— 窗口与进程级操作（显示主窗口、便签、浏览器、拖动区域、退出）。

【为什么这些调用统一走 `_host_call`】
它们都由**窗口层**（`AppController`）实现，而不是数据层。处理器只负责转交，
这样「IPC 层不直接碰窗口」这条线就守住了：数据层能单独测试，窗口层只管窗口。

`_host_call` 在宿主没有该方法时会抛「此操作在当前环境不可用」，而不是崩掉——
例如在只跑了数据层的测试里调用 `open_browser`。
"""

from __future__ import annotations

from .base import HandlerMixin
from .envelope import ok

__all__ = ["SystemHandlers"]


class SystemHandlers(HandlerMixin):
    """窗口与生命周期。"""

    def _handle_show_main(self) -> dict:
        return self._host_call("show_main")

    def _handle_open_widget(self) -> dict:
        self._host_call("open_widget")
        return ok(self._snapshot())

    def _handle_close_widget(self) -> dict:
        self._host_call("close_widget")
        return ok(self._snapshot())

    def _handle_open_browser(self, url: str = "") -> dict:
        return self._host_call("open_browser", url)


    def _handle_import_from(self, kind="schedule") -> dict:
        """「一键添加」：kind = schedule / ercourse / exam。"""
        return self._host_call("start_import", kind)

    def _handle_open_mail(self) -> dict:
        """打开邮箱窗口（收件箱 / 写邮件 / 网页登录）。"""
        return self._host_call("open_mail")


    def _handle_shift_courses(self, days=0) -> dict:
        """把课程整体平移 N 天（对齐真实教学周）。"""
        return self._host_call("shift_courses", days)

    def _handle_set_drag_regions(self, regions) -> dict:
        return self._host_call("set_drag_regions", regions)

    def _handle_quit(self) -> dict:
        return self._host_call("request_quit")
