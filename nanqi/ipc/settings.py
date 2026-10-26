r"""settings.py —— 应用设置、便签设置、备份导入导出与数据目录。

【开机自动启动要单独处理】
它不是存在 `plans.json` 里的设置，而是 Windows 注册表里的一项。所以这里必须
**先写注册表、失败就立刻抛错**：如果把错误吞掉继续保存其它设置，用户会看到
「设置已保存」但下次开机并不会自动启动——那比直接报错糟得多。

写完之后还要作废 `Store` 里的开机启动状态缓存，否则设置页可能仍显示旧值
（缓存是为了省掉每次广播都读注册表的开销，见 `store.autostart_status`）。

【便签设置的白名单】
只允许改 `_WIDGET_PATCH_FIELDS` 里的字段，其它一律忽略——前端不应该能越权改配置。
"""

from __future__ import annotations

from ..weather import normalize_settings as normalize_weather
from ..widget_model import normalize_prefs
from .base import HandlerMixin
from .envelope import ok

__all__ = ["SettingsHandlers", "WIDGET_PATCH_FIELDS"]

# 允许通过便签设置修改的字段（其它字段一律忽略，避免前端越权改配置）。
WIDGET_PATCH_FIELDS = ("mode", "anchor", "category", "onTop", "theme", "showCompleted")


class SettingsHandlers(HandlerMixin):
    """设置与备份。"""

    # ------------------------------------------------------------------ 应用设置

    def _handle_settings(self, settings) -> dict:
        if not isinstance(settings, dict):
            raise ValueError("设置格式无效")
        # 开机启动先写注册表：失败要直接把错误抛给设置页，而不是「静默保存其它项」。
        if isinstance(settings.get("autostart"), bool):
            self.store.login["set_open_at_login"](settings["autostart"])
            self.store.invalidate_autostart()

        def mutate(next_state: dict) -> None:
            for key in ("sound", "popup"):
                if isinstance(settings.get(key), bool):
                    next_state["settings"][key] = settings[key]
            # 天气是嵌套对象：只走白名单与边界校验，前端不能塞进任意键。
            if isinstance(settings.get("weather"), dict):
                next_state["settings"]["weather"] = normalize_weather(
                    {**next_state["settings"].get("weather", {}), **settings["weather"]}
                )

        return ok(self.store.change(mutate))

    # ------------------------------------------------------------------ 便签设置

    def _handle_widget_settings(self, patch) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("便签设置格式无效")
        allowed = {key: patch[key] for key in WIDGET_PATCH_FIELDS if key in patch}
        category_ids = [c["id"] for c in self.store.state.get("categories", [])]

        def mutate(next_state: dict) -> None:
            next_state["settings"]["widget"] = normalize_prefs(
                {**next_state["settings"]["widget"], **allowed}, categories=category_ids
            )

        result = self.store.change(mutate)
        # 「置顶」是窗口层级属性，改完要立刻应用到真实窗口上。
        if "onTop" in allowed and getattr(self.host, "update_widget_layer", None):
            self.host.update_widget_layer()
        return ok(result)

    # ------------------------------------------------------------------ 备份与数据目录

    def _handle_export(self) -> dict:
        return self._host_call("export_data")

    def _handle_import(self) -> dict:
        return self._host_call("import_data")

    def _handle_open_data(self) -> dict:
        return self._host_call("open_data_dir")
