"""test_course_exam.py —— 考试安排解析、考试日程生成、以及「一键添加」的页面自动识别。

考试页的 HTML 结构没有拿到真实快照，所以这里用**与教务系统表格同构**的合成页面来测：
表头驱动的列定位、四种时间写法、去重，以及「这是不是考试页」的结构判定。
"""

from __future__ import annotations

import pytest

from nanqi.controller import AppController
from nanqi.import_service import ImportService
from nanqi.core import clean_task
from nanqi.course_exam import looks_like_exam, parse_exam_html
from nanqi.course_import import (
    PAGE_EMPTY,
    PAGE_ERCOURSE,
    PAGE_EXAM,
    PAGE_SCHEDULE,
    describe_page,
)
from nanqi.store import Store


TABLE = """
<html><body><div class="ant-table">
<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>
</div></body></html>
"""

HEAD = "<th>课程名称</th><th>考试时间</th><th>考场</th><th>座位号</th><th>考核方式</th>"


def row(*cells):
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def page(head=HEAD, *rows):
    return TABLE.format(head=head, rows="".join(rows))


# ------------------------------------------------------------------ 解析：标准表头


def test_parse_exam_table_with_header_columns():
    html = page(
        HEAD,
        row("力学A", "2026-11-15 14:00-16:00", "5107", "32", "闭卷"),
        row("线性代数(B1)", "2026-11-18 09:00-11:00", "5204", "7", "闭卷"),
    )
    items = parse_exam_html(html)
    assert len(items) == 2
    first = items[0]
    assert first["name"] == "力学A"
    assert first["date"] == "2026-11-15"
    assert first["time"] == "14:00" and first["end_time"] == "16:00"
    assert first["location"] == "5107"
    assert first["seat"] == "32"
    assert first["form"] == "闭卷"
    assert first["source"] == "考试"


def test_parse_columns_follow_header_order_not_position():
    """列顺序变了也得认（表头驱动，而不是「第 3 列是考场」）。"""
    html = page(
        "<th>考试时间</th><th>考场</th><th>课程名称</th>",
        row("2026-11-15 14:00", "5107", "力学A"),
    )
    item = parse_exam_html(html)[0]
    assert item["name"] == "力学A"
    assert item["location"] == "5107"
    assert item["date"] == "2026-11-15"


# ------------------------------------------------------------------ 解析：时间写法


@pytest.mark.parametrize(
    "when,date,start,end",
    [
        ("2026-11-15 14:00-16:00", "2026-11-15", "14:00", "16:00"),
        ("2026/11/15 14:00~16:00", "2026-11-15", "14:00", "16:00"),
        ("2026年11月15日 下午2:00-4:00", "2026-11-15", "14:00", "16:00"),
        ("2026.11.15 09:00", "2026-11-15", "09:00", ""),
        ("2026-11-15", "2026-11-15", "", ""),
    ],
)
def test_split_when_variants(when, date, start, end):
    html = page(HEAD, row("力学A", when, "5107", "", ""))
    item = parse_exam_html(html)[0]
    assert (item["date"], item["time"], item["end_time"]) == (date, start, end)


def test_evening_exam_keeps_pm_hour():
    html = page(HEAD, row("思政", "2026-12-01 晚上7:00-9:00", "5204", "", ""))
    item = parse_exam_html(html)[0]
    assert item["time"] == "19:00" and item["end_time"] == "21:00"


def test_rows_without_name_or_date_are_skipped():
    html = page(HEAD, row("", "2026-11-15 14:00", "5107", "", ""), row("力学A", "待定", "5107", "", ""))
    assert parse_exam_html(html) == []


def test_duplicate_exam_rows_collapse():
    html = page(
        HEAD,
        row("力学A", "2026-11-15 14:00-16:00", "5107", "", ""),
        row("力学A", "2026-11-15 14:00-16:00", "5107", "", ""),
    )
    assert len(parse_exam_html(html)) == 1


def test_no_table_returns_empty():
    assert parse_exam_html("<html><body>没有表格</body></html>") == []
    assert parse_exam_html("") == []
    assert parse_exam_html(None) == []


def test_fallback_without_header_guesses_by_content():
    html = page(
        "<th>一</th><th>二</th><th>三</th>",
        row("力学A", "2026-11-15 14:00-16:00", "5107"),
    )
    items = parse_exam_html(html)
    assert items and items[0]["name"] == "力学A"
    assert items[0]["location"] == "5107"


# ------------------------------------------------------------------ 「是不是考试页」


def test_looks_like_exam_needs_structural_evidence():
    assert looks_like_exam(page(HEAD, row("力学A", "2026-11-15 14:00", "5107", "", ""))) is True
    # 只有导航里的「考试」两个字不算证据
    assert looks_like_exam("<html><nav>我的考试</nav></html>") is False
    # 课表页面不能被当成考试页
    schedule = (
        "<html><table><thead><tr><th>节次</th><th>星期一</th></tr></thead>"
        "<tr><td>1</td><td>力学A</td></tr></table></html>"
    )
    assert looks_like_exam(schedule) is False


def test_describe_page_reports_import_kind_for_each_page_type():
    exam_html = page(HEAD, row("力学A", "2026-11-15 14:00", "5107", "", ""))
    assert describe_page(exam_html, url="https://jw.ustc.edu.cn/exam")["import"] == PAGE_EXAM

    schedule_html = (
        "<html><table><thead><tr><th>节次</th><th>星期一</th></tr></thead>"
        "<tr><td>1</td><td>力学A</td></tr></table></html>"
    )
    report = describe_page(schedule_html, url="https://jw.ustc.edu.cn/home")
    assert report["kind"] == PAGE_SCHEDULE and report["import"] == PAGE_SCHEDULE

    er_html = (
        '<html><body><div class="ant-table">'
        '<table><thead><tr><th>序号</th><th>标题</th><th>地点</th><th>开始时间</th><th>结束时间</th></tr></thead>'
        '<tbody><tr class="ant-table-row"><td>1</td><td>三五七 10.22</td><td>暂无</td>'
        "<td>2026-10-22 17:30</td><td>2026-10-22 18:30</td></tr></tbody></table></div></body></html>"
    )
    report = describe_page(er_html, url="https://young.ustc.edu.cn/mySchedule")
    assert report["kind"] == PAGE_ERCOURSE and report["import"] == PAGE_ERCOURSE

    assert describe_page("")["kind"] == PAGE_EMPTY
    assert describe_page("")["import"] == ""


# ------------------------------------------------------------------ 考试页里挂着课表框架

#: 真实情况（用户截图那一页）：教务系统的「考试信息」页面里，`我的课表` 的框架还挂在
#: DOM 上，所以整页抓下来**同时**能解析出上课时段和考试行。
MIXED_PAGE = (
    """<html><body>
    <table class="timetable"><thead><tr><th></th><th>星期日</th><th>星期一</th></tr></thead>
    <tbody><tr class="1"><th scope="row" class="span" data-start="08:00" data-end="08:45">1</th>
    <td></td><td><div class="cell " data-range="2"><span class="start">08:00</span>
    <span class="end">09:35</span><div class="c"><h4 class="number">MATH1009.11 [4]</h4>
    <h3 class="title">数学分析(B1)</h3><p class="teacher"><span class="name">张老师</span></p>
    <p class="time"><span class="week">1-16</span> <span class="timespan">1,2</span></p>
    <p class="classroom"><span class="name">5107</span></p></div></div></td></tr></tbody></table>
    """
    + page(HEAD, row("数学分析(B1)", "2026-11-15 08:30~10:30", "5401", "", ""))
    + "</body></html>"
)


def test_exam_table_wins_over_the_embedded_timetable():
    """这就是用户遇到的 bug：考试页被当成课表导入，结果「已导入 0 项课程日程」。"""
    report = describe_page(MIXED_PAGE, url="https://jw.ustc.edu.cn/home")
    assert report["kind"] == PAGE_EXAM
    assert report["import"] == PAGE_EXAM


def test_user_intent_can_override_detection():
    """用户明确点的是「课表」，就按课表导入（哪怕页面里也有考试表）。"""
    report = describe_page(MIXED_PAGE, url="https://jw.ustc.edu.cn/home", prefer="schedule")
    assert report["kind"] == PAGE_SCHEDULE and report["import"] == PAGE_SCHEDULE
    report = describe_page(MIXED_PAGE, url="https://jw.ustc.edu.cn/home", prefer="exam")
    assert report["kind"] == PAGE_EXAM and report["import"] == PAGE_EXAM


def test_parse_exam_real_rows_from_the_real_page():
    """真实考试页（`<table id="exams">`，8 列，时间写成 `2026-11-15 08:30~10:30`）。"""
    html = """
    <html><body><table id="exams" class="table table-bordered">
    <thead><tr><th width="10%">课堂号</th><th width="15%">考试类型</th><th width="15%">课程名称</th>
    <th width="15%">日期时间</th><th width="15%">考场</th><th width="10%">教学楼</th>
    <th width="10%">考场所在校区</th><th width="10%">考试说明</th></tr></thead>
    <tbody><tr><td>MATH1006.07</td><td>期中考试</td><td>数学分析(B1)</td>
    <td class="time">2026-11-15 08:30~10:30</td><td>5401</td><td>第五教学楼</td><td>东区</td>
    <td></td></tr></tbody></table></body></html>
    """
    items = parse_exam_html(html)
    assert len(items) == 1
    item = items[0]
    assert item["name"] == "数学分析(B1)"
    assert (item["date"], item["time"], item["end_time"]) == ("2026-11-15", "08:30", "10:30")
    assert item["location"] == "5401"


def test_past_exams_are_thrown_away(host):
    """过去的考试直接丢掉（不建任务、不标已完成）。"""
    past = dict(EXAMS[0], date="2020-01-01", time="09:00")
    future = dict(EXAMS[0], date="2099-01-01", time="09:00")
    created = ImportService._create_exam_tasks(host, [past, future])
    assert created == 1
    assert [t["date"] for t in host.store.state["tasks"]] == ["2099-01-01"]
    assert host._last_import_skipped == 1


# ------------------------------------------------------------------ 建日程


class _Host:
    def __init__(self, store):
        self.store = store

    def _ensure_category(self, next_state, category_id, name):
        return ImportService._ensure_category(self, next_state, category_id, name)


@pytest.fixture()
def host(tmp_path):
    return _Host(Store(data_dir=str(tmp_path / "data"), packaged=False))


EXAMS = [
    {
        "name": "力学A",
        "date": "2026-11-15",
        "time": "14:00",
        "end_time": "16:00",
        "location": "5107",
        "seat": "32",
        "form": "闭卷",
    },
    {
        "name": "线性代数(B1)",
        "date": "2026-11-18",
        "time": "09:00",
        "end_time": "",
        "location": "5204",
        "seat": "",
        "form": "",
    },
]


def test_create_exam_tasks_builds_exam_category_and_schedule(host):
    created = ImportService._create_exam_tasks(host, EXAMS)
    assert created == 2
    categories = {c["id"]: c for c in host.store.state["categories"]}
    assert categories["exam"]["name"] == "考试"
    exam = next(t for t in host.store.state["tasks"] if t["title"] == "力学A")
    assert exam["category"] == "exam"
    assert exam["date"] == "2026-11-15" and exam["time"] == "14:00"
    assert exam["duration"] == 120
    assert "地点：5107" in exam["notes"]
    assert "座位：32" in exam["notes"] and "形式：闭卷" in exam["notes"]
    # 没结束时刻时按两小时排
    second = next(t for t in host.store.state["tasks"] if t["title"].startswith("线性代数"))
    assert second["duration"] == 120


def test_create_exam_tasks_is_idempotent(host):
    assert ImportService._create_exam_tasks(host, EXAMS) == 2
    assert ImportService._create_exam_tasks(host, EXAMS) == 0
    assert len(host.store.state["tasks"]) == 2
    assert [c["id"] for c in host.store.state["categories"]].count("exam") == 1


def test_exam_tasks_are_course_like_for_reminders(host):
    """考试与课表同属第一课堂：都用课程式提醒。"""
    from nanqi.course_reminder import course_label, is_course

    ImportService._create_exam_tasks(host, EXAMS)
    exam = host.store.state["tasks"][0]
    assert is_course(exam) is True
    assert course_label(exam) == "考试提醒"


def test_exam_task_needs_existing_category_for_clean_task():
    """记录一条背景：`clean_task` 会校验分类是否在允许列表里。"""
    draft = {
        "title": "力学A",
        "date": "2026-11-15",
        "time": "14:00",
        "duration": 120,
        "category": "exam",
        "reminder": 30,
        "notes": "",
        "kind": "schedule",
        "priority": "high",
        "tags": [],
        "attachments": [],
        "done": False,
    }
    with pytest.raises(ValueError, match="分类"):
        clean_task(draft)
    assert clean_task(draft, categories=["exam"])["category"] == "exam"
