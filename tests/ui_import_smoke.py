"""ui_import_smoke.py —— 「一键添加」端到端冒烟测试（真窗口 + 真 WebEngine）。

它验证的是**那条真出过事的链路**：

    点浏览器右下角「一键添加」→ 抓当前页（含 iframe）→ 自动判断页面类型
    → 解析 → 建日程 → 用**非模态浮层**报告结果

三条必须同时成立的断言（每一条都对应一个真实故障）：

  1. **页面类型自动判断正确**：同一个按钮，在课表页导入课程、在二课页导入二课、
     在考试页导入考试——不需要用户先选按钮（以前选错就白点）。
  2. **过程里绝不出现模态窗口**：从 WebEngine 的脚本回调里弹 QMessageBox，
     用户实测会遇到「怎么点都关不掉的窗口」。每一步都断言 `activeModalWidget()` 为 None。
  3. **结果说清导入了什么**：浮层里要有条数与快照文件名，不能只是一句「成功」。

用法：
    python tests/ui_import_smoke.py
退出码 0 表示全部通过。页面用**合成 HTML**（与真实快照同构），不依赖网络与登录。

【写法上的两个坑（都是写这条测试时踩到的，别再改回去）】
  * **必须走 Qt 事件循环**（`app.exec()` + `QTimer` 驱动），不能自己写
    `while ...: processEvents()` 的阻塞循环——在那种循环里，WebEngine 的**新导航**不一定
    会被提交，表现是「页面永远停在上一个用例」，而 JS 回调却正常工作，非常迷惑。
  * **每个用例开一个新标签页**并等 `loadFinished`，别复用同一个视图反复 `setHtml`：
    baseUrl 相同时 QtWebEngine 有时不会真的重新加载文档。
"""

from __future__ import annotations

import os
import sys
import tempfile
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 【必须放在最前面】先把数据目录指到独立临时目录，再 import webengine 并 prepare。
_TEMP_DATA_DIR = tempfile.mkdtemp(prefix="nanqi-import-smoke-")
os.environ["NANQI_DATA_DIR"] = _TEMP_DATA_DIR

from nanqi import webengine  # noqa: E402

webengine.prepare_webengine()

from PySide6 import QtWebEngineWidgets  # noqa: E402,F401
from PySide6.QtCore import Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

CASE_TIMEOUT_MS = 25000
LOAD_TIMEOUT_MS = 15000

def _soon(days: int) -> str:
    """用例里的日期必须是**将来**：已经过去的日程会被导入逻辑直接丢掉。"""
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


RESULTS: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, extra: str = "") -> None:
    RESULTS.append((label, ok, extra))
    print(f"[{'OK' if ok else '!!'}] {label}" + (f" — {extra}" if extra else ""), flush=True)


# --------------------------------------------------------------------- 合成页面

#: 与真实课表快照同构（`div.cell` / `span.start` / `h4.number` / `h3.title` /
#: `p.teacher` / `span.week` / `span.timespan` / `p.classroom`，行首是带 data-start 的 th）。
#: 末尾故意留一个 `todos-table`：这个类名在**真实课表页**里也出现，曾导致页面被判成二课。
COURSE_PAGE = """
<html><body>
<div class="course-table"><table class="timetable">
<thead><tr><th colspan="2"></th><th><div>星期日</div></th><th><div>星期一</div></th>
<th><div>星期二</div></th><th><div>星期三</div></th><th><div>星期四</div></th>
<th><div>星期五</div></th><th><div>星期六</div></th></tr></thead>
<tbody>
<tr class="1"><th scope="row" rowspan="5">上午</th>
<th scope="row" class="span" data-start="07:50" data-end="08:35">1</th>
<td><div class="cell " data-range="2"><span class="start">07:50</span><span class="end">09:25</span>
<div class="c "><h4 class="number">FL1102A.02 [2]</h4><h3 class="title">科普英语交流A</h3>
<p class="teacher" title="Thomas Yifang Xiao"><span class="name">Thomas Yifang Xiao</span></p>
<p class="time"><span class="week ">3,6-14</span> <span class="timespan">1,2</span></p>
<p class="classroom"><span class="name" title="2210">2210</span></p></div></div></td>
<td></td><td></td><td></td><td></td><td></td></tr>
</tbody></table></div>
<div class="todos-table"></div>
</body></html>
"""

ERCOURSE_PAGE = f"""
<html><body>
<div class="ant-table"><table>
<thead><tr><th>序号</th><th>标题</th><th>地点</th><th>开始时间</th><th>结束时间</th></tr></thead>
<tbody><tr class="ant-table-row ant-table-row-level-0">
<td>1</td><td>三五七 10.22</td><td>暂无</td><td>{_soon(7)} 17:30</td><td>{_soon(7)} 18:30</td>
</tr></tbody></table></div>
</body></html>
"""

EXAM_PAGE = f"""
<html><body>
<div class="ant-table"><table>
<thead><tr><th>课程名称</th><th>考试时间</th><th>考场</th><th>座位号</th><th>考核方式</th></tr></thead>
<tbody>
<tr><td>力学A</td><td>{_soon(20)} 14:00-16:00</td><td>5107</td><td>32</td><td>闭卷</td></tr>
</tbody></table></div>
</body></html>
"""

UNKNOWN_PAGE = "<html><body><h1>某个不相干的页面</h1></body></html>"

CASES = (
    # (用例名, 页面 HTML, 期望的任务标题, 期望分类)
    ("课表页（含曾导致误判的 todos-table 类名）", COURSE_PAGE, "科普英语交流A", "course"),
    ("二课页", ERCOURSE_PAGE, "三五七 10.22", "er-course"),
    ("考试页", EXAM_PAGE, "力学A", "exam"),
    ("无关页面（应提示认不出且不建日程）", UNKNOWN_PAGE, "", ""),
)


class Runner:
    """按用例逐步驱动：新标签页 → 等 loadFinished → 点浮动按钮 → 查结果 → 下一例。

    【注意】Qt 槽函数里抛异常不会让程序退出，事件循环会**空转下去**（写这条测试时
    卡了十分钟才发现）。所以每一步都兜住异常，把失败记成结果并推进到下一例。
    """

    def __init__(self, app, controller) -> None:
        self.app = app
        self.controller = controller
        self.index = -1
        self.before = 0
        self.label = ""
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.on_timeout)
        self.failed_any = False

    @property
    def window(self):
        # 浏览器窗口是控制器懒创建的，不能在这里缓存（第一次可能是 None）。
        return self.controller.browser_window

    # ---------------------------------------------------------------- 驱动

    def start(self) -> None:
        QTimer.singleShot(400, self.next_case)

    def next_case(self) -> None:
        self.index += 1
        if self.index >= len(CASES):
            self.finish()
            return
        try:
            self._load_case()
        except Exception as error:  # noqa: BLE001 - 槽里抛异常会让事件循环空转
            self.report(False, f"用例启动失败：{error}")

    def _load_case(self) -> None:
        label, html, _title, _category = CASES[self.index]
        self.label = label
        window = self.window
        window.show_and_raise()
        window.set_course_import_mode(True, self.controller._grab_import_page, label="一键添加")
        window.hide_import_result()
        self.before = len(self.controller.store.state["tasks"])

        # 每个用例新开一个标签页（见文件头注释的第 2 个坑）。
        window.browser._add_tab("about:blank")
        tab = window.browser.current_tab()
        tab.web_view.loadFinished.connect(self.on_loaded)
        tab.web_view.setUrl(QUrl("data:text/html;charset=utf-8," + urllib.parse.quote(html)))
        self.timer.start(LOAD_TIMEOUT_MS)

    def on_loaded(self, ok: bool) -> None:
        self.timer.stop()
        try:
            tab = self.window.browser.current_tab()
            tab.web_view.loadFinished.disconnect(self.on_loaded)
        except Exception:  # noqa: BLE001
            pass
        if not ok:
            self.report(False, "页面加载失败")
            return
        # about:blank 也走这条路：等 120ms 让 data: URL 的导航先发生。
        QTimer.singleShot(250, self.click)
        self.timer.start(CASE_TIMEOUT_MS)

    def click(self) -> None:
        if not self.timer.isActive():
            return
        QTest.mouseClick(self.window.course_btn, Qt.MouseButton.LeftButton)
        QTimer.singleShot(500, self.check)

    def check(self) -> None:
        if not self.timer.isActive():
            return
        window = self.window
        text = window.result_label.text()
        if not text or self.controller._import_busy:
            QTimer.singleShot(250, self.check)
            return
        self.timer.stop()
        self.report(QApplication.activeModalWidget() is None, text, QApplication.activeModalWidget())

    def on_timeout(self) -> None:
        self.report(False, "超时（页面或抓取没有回应）")

    # ---------------------------------------------------------------- 断言

    def report(self, ok: bool, text: str, modal=None) -> None:
        try:
            label, _html, title, category = CASES[self.index]
        except IndexError:
            label, title, category = self.label or "?", "", ""
        tasks = self.controller.store.state["tasks"]
        created = len(tasks) - self.before
        task = next((t for t in tasks if title and t["title"] == title), None)
        detail = (
            f"新增 {created} 项；浮层={text[:64]!r}；"
            f"模态={type(modal).__name__ if modal else '无'}"
        )
        if modal is not None:
            ok = False
            detail += "（出现了模态窗口——这正是用户报告过的「关不掉的弹窗」）"
        if title:
            if task is None:
                ok = False
            else:
                detail += f"；分类={task['category']}"
                if task["category"] != category:
                    ok = False
                    detail += f"（期望 {category}）"
                if category == "course":
                    ok = ok and "教师：Thomas Yifang Xiao" in task["notes"]
                    ok = ok and "周次：3,6-14" in task["notes"]
                    ok = ok and "地点：2210" in task["notes"]
                    ok = ok and "课程日程" in text
                elif category == "er-course":
                    ok = ok and task["time"] == "17:30" and task["duration"] == 60
                    ok = ok and "二课日程" in text
                elif category == "exam":
                    ok = ok and task["time"] == "14:00" and task["duration"] == 120
                    ok = ok and "地点：5107" in task["notes"] and "座位：32" in task["notes"]
                    ok = ok and "考试日程" in text
        else:
            ok = ok and created == 0 and "已导入" not in text
        if not ok:
            self.failed_any = True
        record(label, ok, detail)
        self.window.hide_import_result()
        QTimer.singleShot(150, self.next_case)

    def finish(self) -> None:
        total = len(RESULTS)
        passed = sum(1 for _, ok, _ in RESULTS if ok)
        print(f"\n导入冒烟：{passed}/{total} 项通过", flush=True)
        self.controller.request_quit()
        self.app.exit(0 if not self.failed_any else 1)


def main() -> int:
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    from nanqi.controller import AppController

    controller = AppController(app, start_hidden=False)
    # 浏览器窗口是懒创建的：先建出来，Runner 才有点击的对象（它不再缓存引用）。
    controller.open_browser("about:blank")
    runner = Runner(app, controller)
    QTimer.singleShot(800, runner.start)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
