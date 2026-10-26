"""ui_smoke.py —— 端到端冒烟测试（真窗口 + 真 WebEngine，不是单元测试）。

它验证的是「Python 重编译版到底能不能用」这件事本身：

   1. 主窗口能加载 index.html，页面里的 `window.nanqi` 通过 QWebChannel 连通；
   2. 通过页面发起的 saveTask / 完成任务 / 删除 都能真正落盘并回推状态；
   3. 前端确实把计划渲染出来了（说明「状态广播」这条链路是通的）；
   4. 篡改确认令牌会被拒绝（批量操作的防误改机制在 Python 侧同样生效）；
   5. 桌面便签能打开，并把「标题栏可拖动区域」上报给 Python；
   6. 提醒窗口能弹出；
   7. 内置浏览器能创建窗口并真的加载页面；
   8. plans.json 的内容与 Electron 版格式一致（键顺序 + 两空格缩进 + 中文不转义）。

用法：
    python tests/ui_smoke.py
退出码 0 表示全部通过。

实现上有两个必须注意的点（都踩过）：
  * PySide6 的 `runJavaScript` 只在脚本返回**字符串/数字/布尔**时把值带回 Python，
    返回 JS 对象时回调收到空字符串；所以所有取值脚本都写成 `JSON.stringify(...)`。
  * 页面里的异步动作（Promise）要轮询等待，不能用一次 runJavaScript 就断言。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 【必须放在最前面】先把数据目录指到独立的临时目录，再去 import nanqi.webengine 并
# 调用 prepare_webengine()：QtWebEngine 的缓存目录是在 prepare 时确定的，顺序反了
# 就会和真实应用共用 %APPDATA%\nanqi_schedule\webengine —— 既污染用户数据，也可能因为
# 缓存被占用/损坏而让页面加载不出来（表现就是「前端通道探测失败」，且时好时坏）。
_TEMP_DATA_DIR = tempfile.mkdtemp(prefix="orange-schedule-smoke-")
os.environ["NANQI_DATA_DIR"] = _TEMP_DATA_DIR

# 必须在 QApplication 之前：修 QtWebEngine 环境。
from nanqi import webengine  # noqa: E402

webengine.prepare_webengine()

from PySide6 import QtWebEngineWidgets  # noqa: E402,F401  (必须在 QApplication 之前 import)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

POLL_INTERVAL_MS = 150
TOTAL_BUDGET_S = 150

RESULTS: list[tuple[str, bool, str]] = []
FAILED = False
_STATE: dict = {}


def record(label: str, ok: bool, extra: str = "") -> None:
    global FAILED
    RESULTS.append((label, ok, extra))
    if not ok:
        FAILED = True
    print(f"[{'OK' if ok else '!!'}] {label}" + (f" — {extra}" if extra else ""), flush=True)


def pending(value) -> bool:
    """页面还没算出结果（null / 空字符串）。"""
    return value is None or value == "null" or value == ""


def run_js(view, script: str) -> None:
    """执行一段脚本（用于触发动作，不关心返回值）。"""
    view.page().runJavaScript(script)


def await_js(view, expression: str, timeout_s: float, on_value, label: str) -> None:
    """轮询 `JSON.stringify(expression)`，直到拿到非空结果或超时。"""
    deadline = time.monotonic() + timeout_s

    def attempt() -> None:
        def handle(raw) -> None:
            value = None
            if not pending(raw):
                try:
                    value = json.loads(raw)
                except (TypeError, ValueError):
                    value = None
            if value is not None:
                on_value(value)
                return
            if time.monotonic() > deadline:
                fail_and_quit(label, f"等待超时，最后一次返回值={raw!r}")
                return
            QTimer.singleShot(POLL_INTERVAL_MS, attempt)

        view.page().runJavaScript("JSON.stringify(" + expression + ")", handle)

    attempt()


def main() -> int:
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    from nanqi.controller import AppController

    controller = AppController(app, start_hidden=False)
    view = controller.main_window.view
    data_file = Path(controller.store.data_file)
    today = time.strftime("%Y-%m-%d")

    def fail_and_quit(label: str, extra: str = "超时") -> None:
        record(label, False, extra)
        controller.request_quit()

    # ------------------------------------------------------------ 1. 页面与通道
    def step1() -> None:
        await_js(
            view,
            "{bridge: typeof window.nanqi, get: typeof (window.nanqi||{}).get}",
            40,
            finish1,
            "页面加载与 QWebChannel 连通",
        )

    def finish1(value: dict) -> None:
        record(
            "页面加载与 QWebChannel 连通",
            value.get("bridge") == "object" and value.get("get") == "function",
            json.dumps(value, ensure_ascii=False),
        )
        step2()

    # ------------------------------------------------------------ 2. get() 往返
    def step2() -> None:
        run_js(
            view,
            "(()=>{window.__r=null;window.nanqi.get().then(s=>{window.__r={version:s.version,"
            "tasks:s.tasks.length,dataDir:s.dataDir,packaged:s.packaged,"
            "reminderCount:s.reminderMessageCount,startupError:s.startupError};})"
            ".catch(e=>{window.__r={error:e.message};});})()",
        )
        await_js(view, "window.__r", 20, finish2, "nanqi.get() 往返")

    def finish2(value: dict) -> None:
        if "error" in value:
            fail_and_quit("nanqi.get() 往返", value["error"])
            return
        record(
            "nanqi.get() 往返",
            value.get("version") == 1 and value.get("tasks") == 0 and value.get("reminderCount") == 120,
            json.dumps(value, ensure_ascii=False)[:200],
        )
        step3()

    # ------------------------------------------------------------ 3. 保存计划
    def step3() -> None:
        run_js(
            view,
            "(()=>{window.__r=null;window.nanqi.saveTask({title:'冒烟测试：写完移植报告',date:'"
            + today
            + "',time:'09:30',duration:60,category:'study',reminder:-1,priority:'high',"
            "notes:'由 ui_smoke.py 创建'},1).then(s=>{window.__r={count:s.tasks.length,"
            "title:(s.tasks[0]||{}).title};}).catch(e=>{window.__r={error:e.message};});})()",
        )
        await_js(view, "window.__r", 20, finish3, "saveTask 落盘")

    def finish3(value: dict) -> None:
        if "error" in value:
            fail_and_quit("saveTask 落盘", value["error"])
            return
        record(
            "saveTask 落盘",
            value.get("count") == 1 and value.get("title") == "冒烟测试：写完移植报告",
            json.dumps(value, ensure_ascii=False),
        )
        step4()

    # ------------------------------------------------------------ 4. 每日重复展开
    def step4() -> None:
        run_js(
            view,
            "(()=>{window.__r=null;window.nanqi.saveTask({title:'冒烟测试：每天复盘',date:'"
            + today
            + "',time:'20:00',duration:30,category:'life',reminder:-1,priority:'normal',notes:''},"
            "{frequency:'daily',until:'"
            + _add_days(today, 2)
            + "'}).then(s=>{window.__r={count:s.tasks.length,"
            "series:s.tasks.filter(t=>t.seriesId).length,dates:s.tasks.map(t=>t.date).sort()};})"
            ".catch(e=>{window.__r={error:e.message};});})()",
        )
        await_js(view, "window.__r", 20, finish4, "每日重复生成 3 项")

    def finish4(value: dict) -> None:
        if "error" in value:
            fail_and_quit("每日重复生成 3 项", value["error"])
            return
        record(
            "每日重复生成 3 项（含同组 seriesId）",
            value.get("count") == 4 and value.get("series") == 3,
            json.dumps(value, ensure_ascii=False),
        )
        step5()

    # ------------------------------------------------------------ 5. plans.json 格式
    def step5() -> None:
        text = data_file.read_text(encoding="utf-8")
        parsed = json.loads(text)
        ok = (
            text.startswith('{\n  "version": 1,\n  "tasks": [\n    {\n      "id": "')
            and '"title": "冒烟测试：写完移植报告"' in text
            and parsed["settings"]["sound"] is True
            and parsed["settings"]["widget"]["theme"] == "sage"
            and len(parsed["tasks"]) == 4
            and "冒烟测试：写完移植报告".encode("utf-8").decode("utf-8") in text
        )
        record("plans.json 与 Electron 版格式一致（缩进/键顺序/中文）", ok, text.splitlines()[0])
        step6()

    # ------------------------------------------------------------ 6. 前端渲染
    def step6() -> None:
        await_js(
            view,
            "{rendered: document.querySelectorAll('.task-row,.event').length,"
            " hasTitle: document.body.innerText.indexOf('冒烟测试')>=0}",
            20,
            finish6,
            "前端渲染出计划",
        )

    def finish6(value: dict) -> None:
        record(
            "前端渲染出计划（状态广播链路）",
            value.get("rendered", 0) >= 2 and value.get("hasTitle") is True,
            json.dumps(value, ensure_ascii=False),
        )
        step7()

    # ------------------------------------------------------------ 7. 完成任务
    def step7() -> None:
        run_js(
            view,
            "(()=>{window.__r=null;window.nanqi.get().then(s=>{const t=s.tasks.find(x=>!x.seriesId);"
            "return window.nanqi.previewTaskAction(t.id,'complete').then(p=>"
            "window.nanqi.applyTaskAction({id:t.id,action:'complete',scope:'single',token:p.singleToken}));})"
            ".then(s=>{window.__r={done:s.tasks.filter(x=>x.done).length};})"
            ".catch(e=>{window.__r={error:e.message};});})()",
        )
        await_js(view, "window.__r", 20, finish7, "完成任务链路")

    def finish7(value: dict) -> None:
        if "error" in value:
            fail_and_quit("完成任务链路", value["error"])
            return
        record("完成任务链路（预览令牌 + 应用）", value.get("done") == 1, json.dumps(value))
        step8()

    # ------------------------------------------------------------ 8. 令牌防篡改
    def step8() -> None:
        run_js(
            view,
            "(()=>{window.__r=null;window.nanqi.get().then(s=>{const t=s.tasks.find(x=>!x.seriesId);"
            "return window.nanqi.applyTaskAction({id:t.id,action:'delete',scope:'single',token:'tampered'})"
            ".then(()=>({unexpected:'accepted'})).catch(e=>({rejected:e.message}));})"
            ".then(r=>{window.__r=r;}).catch(e=>{window.__r={error:e.message};});})()",
        )
        await_js(view, "window.__r", 20, finish8, "篡改令牌被拒绝")

    def finish8(value: dict) -> None:
        record(
            "篡改令牌被拒绝（数据未被改动）",
            value.get("unexpected") is None and "发生变化" in str(value.get("rejected", "")),
            json.dumps(value, ensure_ascii=False)[:160],
        )
        step9()

    # ------------------------------------------------------------ 9. 桌面便签
    def step9() -> None:
        controller.open_widget()
        QTimer.singleShot(3000, finish9)

    def finish9() -> None:
        window = controller.widget_window
        visible = window is not None and window.isVisible()
        regions = list(getattr(window, "_drag_regions", []) or []) if window is not None else []
        record("桌面便签打开", visible, f"visible={visible}")
        record(
            "便签上报「标题栏可拖动区域」",
            bool(regions),
            f"regions={[[round(v) for v in r] for r in regions]}",
        )
        step10()

    # ------------------------------------------------------------ 10. 提醒窗口
    def step10() -> None:
        controller.test_reminder()
        QTimer.singleShot(3000, finish10)

    def finish10() -> None:
        window = controller.reminder_window
        record("提醒窗口弹出", window is not None and window.isVisible())
        step11()

    # ------------------------------------------------------------ 11. 内置浏览器
    # 走**前端通道**打开（就是主界面右上角「浏览器」按钮的那条路径），
    # 而不是直接调 Python 方法——这样连 bridge.js 的接口一起验了。
    def step11() -> None:
        run_js(
            view,
            "(()=>{window.__r=null;window.nanqi.openBrowser('')"
            ".then(()=>{window.__r={opened:true};}).catch(e=>{window.__r={error:e.message};});})()",
        )
        await_js(view, "window.__r", 25, finish11, "内置浏览器（经前端通道打开）")

    def finish11(value: dict) -> None:
        if "error" in value:
            fail_and_quit("内置浏览器（经前端通道打开）", value["error"])
            return
        QTimer.singleShot(2500, finish11b)

    def finish11b() -> None:
        window = controller.browser_window
        tabs = len(getattr(window.browser, "_tabs", [])) if window is not None else 0
        bookmarks = 0
        try:
            from nanqi.browser import BOOKMARKS

            bookmarks = len(BOOKMARKS)
        except Exception:  # noqa: BLE001
            pass
        record(
            "内置浏览器经前端通道打开（多标签 + 16 条书签）",
            window is not None and window.isVisible() and tabs >= 1 and bookmarks == 16,
            f"tabs={tabs} bookmarks={bookmarks}",
        )
        step12()

    # ------------------------------------------------------------ 12. 浏览器真的加载页面
    def step12() -> None:
        window = controller.browser_window
        if window is None:
            record("内置浏览器加载网页", False, "没有窗口")
            step13()
            return
        window.open_url("about:blank")
        QTimer.singleShot(2500, finish12)

    def finish12() -> None:
        window = controller.browser_window
        tabs = list(getattr(window.browser, "_tabs", [])) if window is not None else []
        last = tabs[-1] if tabs else None
        url = last.web_view.url().toString() if (last and last.web_view) else ""
        record("内置浏览器加载网页", bool(url) and len(tabs) >= 2, f"tabs={len(tabs)} url={url}")
        step13()

    # ------------------------------------------------------------ 13. 设置与持久化
    def step13() -> None:
        run_js(
            view,
            "(()=>{window.__r=null;window.nanqi.settings({sound:false,popup:true})"
            ".then(s=>{window.__r={sound:s.settings.sound,popup:s.settings.popup,"
            "packaged:s.packaged,autostartError:s.autostartError};})"
            ".catch(e=>{window.__r={error:e.message};});})()",
        )
        await_js(view, "window.__r", 20, finish13, "设置写盘")

    def finish13(value: dict) -> None:
        record(
            "设置写盘（关闭提醒声音）",
            value.get("sound") is False and value.get("popup") is True,
            json.dumps(value, ensure_ascii=False)[:160],
        )
        on_disk = json.loads(data_file.read_text(encoding="utf-8"))
        record("设置已持久化到 plans.json", on_disk["settings"]["sound"] is False)
        step14()

    # ------------------------------------------------------------ 14. 便签位置保存
    def step14() -> None:
        window = controller.widget_window
        if window is None:
            record("便签位置写回设置", False, "没有便签窗口")
            finish()
            return
        window.move(window.x() + 40, window.y() + 30)
        window.resize(520, 700)
        QTimer.singleShot(1200, finish14)

    def finish14() -> None:
        controller.widget_window.save_bounds_now()
        widget_prefs = json.loads(data_file.read_text(encoding="utf-8"))["settings"]["widget"]
        bounds = widget_prefs.get("bounds") or {}
        record(
            "便签位置与尺寸写回设置",
            bounds.get("width") == 520 and bounds.get("height") == 700,
            json.dumps(bounds),
        )
        step15()

    def step15() -> None:
        absent = "update" not in controller.store.snapshot() and not hasattr(controller.bridge, "_handle_check_updates")
        record("旧更新通道已移除", absent, "不访问上游更新地址")
        finish()

    def finish() -> None:
        controller.request_quit()

    QTimer.singleShot(400, step1)
    QTimer.singleShot(TOTAL_BUDGET_S * 1000, lambda: (fail_and_quit("整体超时"), None))
    app.exec()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print()
    print(f"冒烟测试：{passed}/{len(RESULTS)} 项通过", flush=True)
    for label, ok, extra in RESULTS:
        if not ok:
            print(f"  未通过：{label} {extra}", flush=True)
    return 1 if FAILED else 0


def _add_days(date_text: str, days: int) -> str:
    from datetime import datetime, timedelta

    return (datetime.strptime(date_text, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


if __name__ == "__main__":
    # 数据目录已在模块顶部（import 阶段）就指向了 _TEMP_DATA_DIR：QtWebEngine 的缓存
    # 目录必须在 prepare_webengine() 之前定好。这里不再用 TemporaryDirectory——它的
    # 清理发生在 WebEngine 释放 leveldb 之前，会报「文件正被另一个程序使用」。
    code = main()
    shutil.rmtree(_TEMP_DATA_DIR, ignore_errors=True)
    raise SystemExit(code)
