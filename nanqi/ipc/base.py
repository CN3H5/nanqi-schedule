r"""base.py —— 各领域处理器共用的底座。

【分派方式】
`Bridge._dispatch` 用 `getattr(self, "_handle_" + name)` 找处理函数，所以只要把
「一组方法」写进一个 mixin、再让 `Bridge` 继承它，分发表就自动多出这些方法，
不需要额外的注册表。好处是：

  * 接口清单仍然是「一个文件里的一组函数」，和 preload.cjs 一一对得上；
  * 新加一个领域 = 新加一个 mixin 文件，不用改 `_dispatch`；
  * 每个 mixin 只依赖 `self.store` / `self.host` 这两个约定好的属性。

【约定】
  * 处理函数一律叫 `_handle_<调用名>`，调用名里的连字符换成下划线
    （前端 `saveTask` → `_handle_save_task`，`check-updates` → `_handle_check_updates`）；
  * 返回信封（用 `envelope.ok()`），失败直接抛异常，由 `invoke` 统一转成错误信封。
"""

from __future__ import annotations

from .envelope import ok

__all__ = ["HandlerMixin"]


class HandlerMixin:
    """处理器 mixin 的共用小工具（不定义 `__init__`，由 Bridge 统一初始化）。"""

    # 由 Bridge.__init__ 注入
    store: object
    host: object

    def _snapshot(self) -> dict:
        """当前状态的完整快照（界面唯一认可的数据形状）。

        每次都重新生成而不是缓存：快照里包含更新检查状态与开机启动状态，
        这两者会随时变化，缓存反而会给出过期信息。
        """
        return self.store.snapshot()  # type: ignore[attr-defined]

    def _host_call(self, method: str, *args) -> dict:
        """调用由窗口层实现的操作；缺失时给出明确提示而不是崩掉。"""
        function = getattr(self.host, method, None)
        if function is None:
            raise ValueError("此操作在当前环境不可用")
        return ok(function(*args))
