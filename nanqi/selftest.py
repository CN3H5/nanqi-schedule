"""selftest.py —— 让打包后的 exe 自己验证界面（`--selftest-ui`）。

【为什么需要它】
`--selfcheck` 只能证明「文件和模块都在」，`--doctor` 只能证明「QtWebEngine 找得到」。
但用户真正会遇到的问题是**界面白屏 / 按钮点了没反应**——那属于「页面加载了没、
前端与 Python 的通道通了没」这一类只能在真窗口里才能验证的事。

而这个验证必须在**打包后的 exe 里**做才有意义：白屏的三大坑（前端资源没进包、
QtWebEngine 运行期变量没设对、依赖模块被排除）都只在打包后才出现。

【它做什么】
建好三个窗口（都保持隐藏），等页面加载完成后探测：

  1. 主日历页：`window.nanqi` 是否连通、`#calendar` 是否真的渲染出了内容、
     `nanqi.get()` 能否往返；
  2. 设置页的新控件（天气开关/坐标、课程整体平移）是否真的在 DOM 里、通道是否暴露了
     `nanqi.shiftCourses`——纯 DOM 与接口探测，不碰保存逻辑；
  3. 「一键添加」入口：按钮、类型选择弹窗（课表/二课/考试）、`nanqi.importFrom`；
  4. 桌面便签页：同一套通道是否连通、便签控件是否都在、`widget.js` 是否渲染过；
  5. 提醒页：通道是否连通、待处理列表是否渲染出来；
  6. 内置浏览器：QtWebEngine 组件是否可用。

全部**只读**：只调用 `get()`，不改动用户数据，也不显示任何窗口；探测过程中被
临时打开的便签窗口会先把偏好留底、结束时原样还原，并把它的落盘回调换成空实现
（否则退出流程会再写一次，把还原覆盖掉）。
"""

from __future__ import annotations

import copy
import json
import re
import sys
import time

from PySide6.QtCore import QTimer

__all__ = ["run_ui_selftest"]

POLL_INTERVAL_MS = 150


def _trace(message: str) -> None:
    """进度打到 stderr：自检卡住时，这一段是唯一能说明「卡在哪一步」的线索。"""
    stream = getattr(sys, "stderr", None)
    if stream is None:
        return
    try:
        print(f"[界面自检] {message}", file=stream, flush=True)
    except Exception:  # noqa: BLE001
        pass


def run_ui_selftest(app, controller, timeout_s: float = 90.0) -> list[tuple[str, bool, str]]:
    """跑完整套界面自检；结果通过 `controller._selftest_results` 交给调用方。"""
    results: list[tuple[str, bool, str]] = []
    deadline = time.monotonic() + timeout_s
    finished = {"done": False}
    # 自检必须**只读**：探测便签页会顺带打开便签窗口（从而把 enabled 写成 true），
    # 窗口定位还可能把位置写回设置。先把便签偏好整份留底，结束时原样还原。
    original_widget = copy.deepcopy(controller.store.state["settings"].get("widget"))

    def record(label: str, ok: bool, extra: str = "") -> None:
        results.append((label, ok, extra))
        _trace(f"{'OK' if ok else '!!'} {label}" + (f" — {extra}" if extra else ""))

    def restore_preferences() -> None:
        if controller.store.state["settings"].get("widget") == original_widget:
            return
        try:
            controller.store.change(
                lambda state: state["settings"].__setitem__(
                    "widget", copy.deepcopy(original_widget)
                )
            )
            _trace("已还原便签偏好（自检不修改用户设置）")
        except Exception as error:  # noqa: BLE001
            _trace(f"还原便签偏好失败：{error}")

    def finish() -> None:
        if finished["done"]:
            return
        finished["done"] = True
        restore_preferences()
        controller._selftest_results = results
        code = 0 if all(ok for _, ok, _ in results) else 1
        _trace(f"结束，共 {len(results)} 项，退出码 {code}")
        app.exit(code)

    def guard() -> bool:
        """整体超时保护：任何一步卡住都不让 exe 永远挂着。"""
        if time.monotonic() > deadline:
            record("整体超时", False, f"未在 {timeout_s:.0f} 秒内完成")
            finish()
            return False
        return True

    def run_js(view, script: str) -> None:
        """执行一段「只触发动作」的脚本，不等返回值。

        注意不能把这种带分号的语句塞进 `JSON.stringify(...)`——那是语法错误。
        """
        view.page().runJavaScript(script)

    def await_js(view, expression: str, seconds: float, on_value, label: str) -> None:
        """轮询 `JSON.stringify(表达式)`，直到拿到非空结果或超时。

        表达式在「还没就绪」时应返回 null，`await_js` 会继续等——直接用 typeof
        探测会在页面还是 about:blank 时立刻得到 undefined 并误判失败。

        PySide6 的 `runJavaScript` 只在脚本返回字符串/数字/布尔时把值带回 Python，
        返回 JS 对象时回调收到空字符串，所以脚本一律用 JSON.stringify 包一层。
        """
        limit = time.monotonic() + seconds

        def attempt() -> None:
            if not guard():
                return
            if time.monotonic() > limit:
                record(label, False, "等待页面响应超时")
                on_value(None)
                return

            def handle(raw) -> None:
                value = None
                if raw not in (None, "", "null"):
                    try:
                        value = json.loads(raw)
                    except (TypeError, ValueError):
                        value = None
                if value is None:
                    QTimer.singleShot(POLL_INTERVAL_MS, attempt)
                    return
                on_value(value)

            view.page().runJavaScript("JSON.stringify(" + expression + ")", handle)

        attempt()

    # ------------------------------------------------------------------ 主日历

    def probe_main() -> None:
        view = controller.main_window.view
        if controller.main_window.isVisible():
            controller.main_window.hide()

        def on_ready(value) -> None:
            if value is None:
                finish()
                return
            record(
                "主窗口页面加载 + 前端通道连通",
                value.get("bridge") == "object" and value.get("get") == "function",
                json.dumps(value, ensure_ascii=False),
            )
            await_js(
                view,
                "(function(){"
                " var cal = document.querySelector('#calendar');"
                " if (!cal || !cal.innerHTML) return null;"
                " return {calendar: cal.innerHTML.length,"
                " categories: (document.querySelector('#categories')||{children:{length:0}}).children.length,"
                " title: (document.querySelector('#page-title')||{}).textContent || ''};"
                "})()",
                20,
                on_dom,
                "主窗口界面已渲染",
            )

        def on_dom(value) -> None:
            if value is None:
                finish()
                return
            record(
                "主窗口界面已渲染",
                value.get("calendar", 0) > 100 and value.get("categories", 0) >= 3,
                json.dumps(value, ensure_ascii=False),
            )
            run_js(
                view,
                "window.__selftest=null;window.nanqi.get()"
                ".then(s=>{window.__selftest={version:s.version,tasks:s.tasks.length,"
                "dataDir:s.dataDir,reminder:s.reminderMessageCount};})"
                ".catch(e=>{window.__selftest={error:e.message};});",
            )
            await_js(view, "window.__selftest", 20, on_get, "nanqi.get() 往返")

        def on_get(value) -> None:
            if value is None:
                return
            if "error" in value:
                record("nanqi.get() 往返", False, value["error"])
            else:
                record(
                    "nanqi.get() 往返",
                    value.get("version") == 1 and value.get("reminder", 0) >= 100,
                    json.dumps(value, ensure_ascii=False),
                )
            probe_settings(view)

        await_js(
            view,
            "(function(){"
            " if (typeof window.nanqi !== 'object') return null;"
            " if (!(document.querySelector('#calendar')||{}).innerHTML) return null;"
            " return {bridge: typeof window.nanqi, get: typeof window.nanqi.get};"
            "})()",
            40,
            on_ready,
            "主窗口页面加载 + 前端通道连通",
        )

    # -------------------------------------------------------------- 设置页新控件

    def probe_settings(view) -> None:
        """设置页里新增的控件是否真的存在（天气 / 课程整体平移）。

        这是**纯 DOM + 接口探测**，只读 `get()`，不点开关、不写设置：
        它要挡住的是「后端加了设置项、前端标记忘了改」这类只在界面上才看得见的断链。
        """
        run_js(
            view,
            "window.__selftestSettings=null;window.nanqi.get().then(function(s){"
            "var w=(s.settings&&s.settings.weather)||{};"
            "var dom=document.querySelector('#setting-weather')"
            "&&document.querySelector('#weather-lat')"
            "&&document.querySelector('#weather-lon')"
            "&&document.querySelector('#shift-days')"
            "&&document.querySelector('#shift-courses');"
            "window.__selftestSettings={weather:typeof w.latitude==='number',"
            "coordinates:w.latitude+'N, '+w.longitude+'E',"
            "shift:typeof window.nanqi.shiftCourses==='function',"
            "dom:!!dom};"
            "}).catch(function(e){window.__selftestSettings={error:e.message};});",
        )

        def on_value(value) -> None:
            if value is None:
                return
            if "error" in value:
                record("设置页控件（天气 / 课程平移）就绪", False, value["error"])
            else:
                record(
                    "设置页控件（天气 / 课程平移）就绪",
                    value.get("weather") is True and value.get("shift") is True and value.get("dom") is True,
                    json.dumps(value, ensure_ascii=False),
                )
            probe_import_entry(view)

        await_js(view, "window.__selftestSettings", 20, on_value, "设置页控件（天气 / 课程平移）就绪")

    # ------------------------------------------------------------ 一键添加入口

    def probe_import_entry(view) -> None:
        """「一键添加」入口是否就绪：一个按钮 + 一个类型选择弹窗 + 通道方法。

        这三样缺一样，用户看到的就会是「点了没反应」——那是最难自查的一类故障。
        """
        run_js(
            view,
            "window.__selftestImport=(function(){"
            "var button=document.querySelector('#import-anything');"
            "var dialog=document.querySelector('#import-dialog');"
            "var choices=dialog?dialog.querySelectorAll('[data-import]'):[];"
            "return {button:!!button,dialog:!!dialog,choices:choices.length,"
            "kinds:Array.prototype.map.call(choices,function(b){return b.dataset.import;}).join(','),"
            "api:typeof window.nanqi.importFrom};"
            "})();",
        )

        def on_value(value) -> None:
            if value is None:
                return
            kinds = str(value.get("kinds") or "")
            record(
                "一键添加入口就绪（按钮 + 类型选择 + 通道）",
                value.get("button") is True
                and value.get("dialog") is True
                and value.get("choices") == 3
                and kinds == "schedule,ercourse,exam"
                and value.get("api") == "function",
                json.dumps(value, ensure_ascii=False),
            )
            probe_layout(view)

        await_js(view, "window.__selftestImport", 20, on_value, "一键添加入口就绪（按钮 + 类型选择 + 通道）")

    # -------------------------------------------------------------- 布局与统计口径

    def probe_layout(view) -> None:
        """只有真窗口才看得出的一类回归：日面板日期被挤成竖排（用户截图里的「错位」）。

        以前 `.panel-title` 作为 flex item 的 min-width 是 auto，中文可以在任意字符间
        换行，于是被压到 1 个字宽（实测 26×143），日期竖着排、和「‹ 今天 ›」叠在一起。
        这里直接量它的宽高：竖排时高度会明显大于宽度。
        """
        run_js(
            view,
            "window.__selftestLayout=(function(){"
            "var t=document.querySelector('.panel-title');"
            "var panel=document.querySelector('#day-panel');"
            "if(!t||!panel)return {missing:true};"
            "var r=t.getBoundingClientRect();"
            "var doc=document.documentElement;"
            "return {titleW:Math.round(r.width),titleH:Math.round(r.height),"
            "nowrap:getComputedStyle(t).whiteSpace,"
            "panelOverflow:panel.scrollWidth-panel.clientWidth,"
            "docOverflow:doc.scrollWidth-window.innerWidth};"
            "})();",
        )

        def on_value(value) -> None:
            if value is None:
                return
            width = value.get("titleW") or 0
            height = value.get("titleH") or 0
            # 正常的「10月23日」是横向一行：足够宽、高度不超过宽度、且不许换行。
            ok = width >= 60 and height <= max(width, 40) and value.get("nowrap") == "nowrap"
            record(
                "日面板日期横向一行（不再竖排错位）",
                bool(ok) and value.get("docOverflow") == 0,
                json.dumps(value, ensure_ascii=False),
            )
            probe_stats(view)

        await_js(view, "window.__selftestLayout", 20, on_value, "日面板日期横向一行（不再竖排错位）")

    def probe_stats(view) -> None:
        """「待办预计用时」必须按「不含课表」的口径算——拿前端渲染的数字对账。"""
        run_js(
            view,
            "window.__selftestStats=(function(){"
            "var cards=Array.prototype.map.call("
            "document.querySelectorAll('#summary .stat-card'),function(c){return c.textContent;});"
            "return {cards:cards,hasHours:cards.some(function(t){return t.indexOf('待办预计用时')>=0;})};"
            "})();",
        )

        def on_value(value) -> None:
            if value is None:
                return
            hour_card = ""
            for text in value.get("cards") or []:
                if "待办预计用时" in text:
                    hour_card = text
            expected = 0.0
            for task in controller.store.state["tasks"]:
                if task.get("category") == "course" or task.get("done"):
                    continue
                expected += float(task.get("duration") or 0) / 60.0
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*小时", hour_card)
            actual = float(match.group(1)) if match else -1.0
            record(
                "待办预计用时不含课表",
                value.get("hasHours") is True and abs(actual - round(expected, 1)) < 0.15,
                f"界面 {actual} / 期望 {round(expected, 1)}",
            )
            probe_widget()

        await_js(view, "window.__selftestStats", 20, on_value, "待办预计用时不含课表")

    # ------------------------------------------------------------------ 桌面便签

    def probe_widget() -> None:
        try:
            controller.open_widget()
            controller.widget_window.hide()
        except Exception as error:  # noqa: BLE001
            record("便签窗口可创建", False, str(error))
            probe_reminder()
            return
        window = controller.widget_window
        # 自检绝不写用户的便签位置：窗口一显示，Qt 就会因为 setGeometry 触发
        # move/resize，进而防抖落盘；退出流程里还会再存一次。把落盘回调换成空实现，
        # 比「事后还原」可靠得多（还原会被退出流程里那一次覆盖掉）。
        try:
            window._save_bounds = lambda bounds: None
            window._save_timer.stop()
        except Exception:  # noqa: BLE001
            pass

        def on_value(value) -> None:
            if value is None:
                finish()
                return
            record(
                "便签页面加载 + 通道 + 控件齐备",
                value.get("bridge") == "object"
                and value.get("chrome") is True
                and value.get("model") == "object"
                and value.get("rendered") is True,
                json.dumps(value, ensure_ascii=False),
            )
            probe_reminder()

        await_js(
            window.view,
            "(function(){"
            " if (typeof window.nanqi !== 'object') return null;"
            " var list = document.querySelector('#pending-list');"
            " if (!list || !list.innerHTML) return null;"
            " return {bridge: typeof window.nanqi,"
            " model: typeof window.NanqiWidget,"
            " chrome: !!document.querySelector('.widget-chrome'),"
            " rendered: !!((document.querySelector('#widget-title')||{}).textContent),"
            " title: (document.querySelector('#widget-title')||{}).textContent};"
            "})()",
            30,
            on_value,
            "便签页面加载 + 通道 + 控件齐备",
        )

    # ------------------------------------------------------------------ 提醒窗口

    def probe_reminder() -> None:
        try:
            controller.show_reminder()
        except Exception as error:  # noqa: BLE001
            record("提醒窗口可创建", False, str(error))
            probe_browser()
            return
        window = controller.reminder_window
        if window is None:
            record("提醒窗口可创建", False, "窗口未创建（请检查「独立提醒窗口」设置）")
            probe_browser()
            return
        window.hide()

        def on_value(value) -> None:
            if value is None:
                finish()
                return
            record(
                "提醒页面加载 + 通道 + 名句显示",
                value.get("bridge") == "object" and value.get("rendered") is True,
                json.dumps(value, ensure_ascii=False),
            )
            probe_browser()

        # 要等 reminder.js 把待处理列表渲染出来，才能证明「通道 + get() 往返」真的通了。
        await_js(
            window.view,
            "(function(){"
            " if (typeof window.nanqi !== 'object') return null;"
            " var box = document.querySelector('#reminders');"
            " if (!box || !box.innerHTML) return null;"
            " return {bridge: typeof window.nanqi, rendered: true,"
            " quote: ((document.querySelector('#quote-text')||{}).textContent||'').length > 0};"
            "})()",
            30,
            on_value,
            "提醒页面加载 + 通道 + 名句显示",
        )

    # ------------------------------------------------------------------ 内置浏览器

    def probe_browser() -> None:
        try:
            from .browser import BOOKMARKS, is_webengine_available

            available = is_webengine_available()
            record(
                "内置浏览器组件可用",
                available,
                f"书签 {len(BOOKMARKS)} 条" if available else "QtWebEngine 不可用",
            )
        except Exception as error:  # noqa: BLE001
            record("内置浏览器组件可用", False, str(error))
        finish()

    QTimer.singleShot(300, probe_main)
    # 绝对看门狗：不管上面哪一步因为「runJavaScript 回调没回来」而停住，
    # 到点都保证自检会结束并给出报告——诊断工具自己卡死是最糟的结果。
    QTimer.singleShot(
        int(timeout_s * 1000),
        lambda: (
            record("自检超时", False, f"未在 {timeout_s:.0f} 秒内完成所有探测")
            if not finished["done"]
            else None,
            finish(),
        ),
    )
    return results
