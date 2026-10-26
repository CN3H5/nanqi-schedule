"""ui_erc_api_smoke.py —— 二课「一键添加」全量抓取（接口 + 回落）的端到端测试。

【它解决什么问题】
团学平台的「我的日程」表格是**按所选日期过滤**的：抓一次页面只有那一天的数据
（用户实测：只点某天再点「一键添加」才有内容）。页面自己的接口一次也只给一天：

    GET /login/wisdom-group-learning-bg/personal/scCalendar/getPageUserList?now=YYYY-MM-DD

所以真正的实现是：在页面里按天并发调这个接口，把整个学期拉全。这条链路没法在
真实站点上自动测试（要登录），于是这里**起一个本地假接口**，用
`NANQI_ERC_API_HOSTS=127.0.0.1` 把它放进白名单，然后驱动真实的 `BrowserWindow`
点浮动按钮，验证：

  1. 跨多天的记录全部被拿到（而不是只有当天那一条）；
  2. 已经过去的记录被丢掉、不建任务；
  3. 接口整体失败时退回「抓当前页表格」，并明确告诉用户只覆盖当天；
  4. 全程不出现模态窗口。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TEMP_DATA_DIR = tempfile.mkdtemp(prefix="nanqi-erc-api-")
os.environ["NANQI_DATA_DIR"] = _TEMP_DATA_DIR
os.environ["NANQI_ERC_API_HOSTS"] = "127.0.0.1"
os.environ["NANQI_INSTANCE_NAME"] = "nanqi-erc-api-smoke"

from nanqi import webengine  # noqa: E402

webengine.prepare_webengine()

from PySide6 import QtWebEngineWidgets  # noqa: E402,F401
from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


def _day(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


#: 假接口的数据：三天有活动（含一条跨天的），另外一天是「已经过去」的，必须被丢掉。
EVENTS = {
    _day(7): [
        {
            "id": "a",
            "title": "三五七 10.22",
            "place": None,
            "startTime": f"{_day(7)} 17:30:00",
            "endTime": f"{_day(7)} 18:30:00",
        }
    ],
    _day(9): [
        {
            "id": "b",
            "title": "物理学院25级联合班会：本科生的学术科研",
            "place": "理化大楼西三报告厅",
            "startTime": f"{_day(9)} 19:00:00",
            "endTime": f"{_day(9)} 21:00:00",
        }
    ],
    _day(11): [
        {
            "id": "c",
            "title": "追“锋”计划",
            "place": "暂无",
            "startTime": f"{_day(11)} 20:00:00",
            "endTime": f"{_day(14)} 23:45:00",
        }
    ],
    _day(-3): [
        {
            "id": "past",
            "title": "已经过去的志愿活动",
            "place": "暂无",
            "startTime": f"{_day(-3)} 19:00:00",
            "endTime": f"{_day(-3)} 21:00:00",
        }
    ],
}

#: 「我的日程」页面：只有当天那一条（模拟按日期过滤的表格），外加一个假的日历。
ERC_PAGE = f"""
<html><body>
<div class="ant-table"><table>
<thead><tr><th>序号</th><th>标题</th><th>地点</th><th>开始时间</th><th>结束时间</th></tr></thead>
<tbody><tr class="ant-table-row"><td>1</td><td>只有当天这一条</td><td>暂无</td>
<td>{_day(7)} 17:30</td><td>{_day(7)} 18:30</td></tr></tbody></table></div>
</body></html>
"""

REQUEST_COUNT = {"n": 0}
MODE = {"fail": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - 静音访问日志
        pass

    def do_GET(self):  # noqa: N802 - http.server 的约定
        parsed = urlparse(self.path)
        if parsed.path.endswith("/personal/scCalendar/getPageUserList"):
            REQUEST_COUNT["n"] += 1
            if MODE["fail"]:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"boom")
                return
            day = (parse_qs(parsed.query).get("now") or [""])[0]
            body = json.dumps(
                {"code": 200, "success": True, "result": {"records": EVENTS.get(day, []), "total": 0}},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = ERC_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

app = QApplication([])
app.setQuitOnLastWindowClosed(False)

from nanqi.controller import AppController  # noqa: E402

controller = AppController(app, start_hidden=True)
controller.open_browser(f"http://127.0.0.1:{PORT}/")
window = controller.browser_window

RESULTS: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    RESULTS.append((label, ok, extra))
    print(f"[{'OK' if ok else '!!'}] {label}" + (f" — {extra}" if extra else ""), flush=True)


def modal_name() -> str:
    widget = QApplication.activeModalWidget()
    return type(widget).__name__ if widget else ""


def tasks_by_title() -> dict[str, dict]:
    return {task["title"]: task for task in controller.store.state["tasks"]}


class Runner:
    """用例 1：接口可用 → 拿到全部 3 条（不含过去那条）。"""

    def __init__(self) -> None:
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.step)
        self.deadline = 0
        self.wait_for = ""

    def begin(self) -> None:
        self.timer.start(1200)

    def step(self) -> None:
        try:
            self.advance()
        except Exception as error:  # noqa: BLE001 - 槽函数抛异常会让事件循环空转
            record("执行异常", False, repr(error))
            self.finish()

    def advance(self) -> None:
        if REQUEST_COUNT["n"] == 0:
            window.set_course_import_mode(True, controller._grab_import_page, label="一键添加")
            window.hide_import_result()
            controller._grab_import_page()
            self.wait_for = "crawl"
            self.timer.start(400)
            return

        busy = bool(getattr(controller, "_import_busy", False))
        text = window.result_label.text()
        if self.wait_for == "crawl":
            if busy or not text or "正在读取" in text:
                self.timer.start(400)
                return
            self.check_full_import(text)
            self.wait_for = "fallback"
            self.clicked = False
            self.timer.start(300)
            return

        if self.wait_for == "fallback":
            if not getattr(self, "clicked", False):
                # 清空任务、让假接口开始报错，再点一次按钮：这一轮应当走「回落」分支。
                MODE["fail"] = True

                def clear(next_state):
                    next_state["tasks"] = []

                controller.store.change(clear)
                window.set_course_import_mode(True, controller._grab_import_page, label="一键添加")
                controller._grab_import_page()
                self.clicked = True
                self.timer.start(500)
                return
            if busy or "正在读取" in text:
                self.timer.start(400)
                return
            self.check_fallback(text)
            self.finish()
            return

    def check_full_import(self, text: str) -> None:
        by_title = tasks_by_title()
        record("接口可用时拿到全部跨天记录", len(by_title) == 3, f"任务={sorted(by_title)}")
        record("不再只抓当天那一行", "只有当天这一条" not in by_title)
        record("已经过去的记录被丢弃", "已经过去的志愿活动" not in by_title)
        record("接口确实被按天调用", REQUEST_COUNT["n"] >= 100, f"请求 {REQUEST_COUNT['n']} 次")
        joined = " ".join(by_title)
        record("跨天活动进来了", "追“锋”计划" in joined)
        task = by_title.get("追“锋”计划")
        if task:
            record("跨天活动时长封顶 24 小时", task["duration"] == 1440, str(task["duration"]))
            record("跨天活动备注写了结束时刻", "结束：" in task["notes"])
        record("结果浮层文案正确", "已导入 3 项二课日程" in text, text[:60])
        record("全程没有模态窗口", modal_name() == "", modal_name())

    def check_fallback(self, text: str) -> None:
        record("接口失败时退回抓页面表格", "接口不可用" in text, text[:60])
        record("回落时只导入当天那一条", len(tasks_by_title()) == 1, str(sorted(tasks_by_title())))
        record("回落时没有模态窗口", modal_name() == "", modal_name())

    def finish(self) -> None:
        server.shutdown()
        ok = sum(1 for _label, flag, _extra in RESULTS if flag)
        total = len(RESULTS)
        print(f"\n导入冒烟：{ok}/{total} 项通过", flush=True)
        controller.request_quit()
        app.exit(0 if ok == total else 1)


runner = Runner()
QTimer.singleShot(1500, runner.begin)
code = app.exec()
sys.exit(code)
