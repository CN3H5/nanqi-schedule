"""test_course_erc.py —— 二课（团学活动）解析与导入、课程整体平移、天气设置。

真实页面是一张 Ant Design 表格（列 `序号|标题|地点|开始时间|结束时间`），
所以这里用「和真实快照同构」的 HTML 片段来测，不需要联网、不需要浏览器。

同时覆盖两处控制器逻辑（用未绑定方法 + 假宿主对象，避免起真窗口）：
  * `_create_ercourse_tasks`：建分类、去重、时长与备注；
  * `shift_courses`：只平移课程、不动二课与普通计划。
"""

from __future__ import annotations

import pytest

from nanqi.controller import AppController
from nanqi.import_service import ImportService
from nanqi.course_erc import parse_erc_api_records, parse_ercourse
from nanqi.core import clean_task
from nanqi.ipc.bridge import Bridge
from nanqi.store import Store


# ------------------------------------------------------------------ 解析

TABLE_TEMPLATE = """
<div class="ant-table ant-table-small"><div class="ant-table-container"><table>
<thead class="ant-table-thead"><tr><th>序号</th><th>标题</th><th>地点</th><th>开始时间</th><th>结束时间</th></tr></thead>
<tbody class="ant-table-tbody">{rows}</tbody></table></div></div>
"""


def row(index, title, place, start, end, extra=""):
    return (
        f'<tr class="ant-table-row ant-table-row-level-0" data-row-key="{index}">'
        f"<td>{index}</td><td><a>{title}</a></td><td>{place}</td>"
        f"<td>{start}</td><td>{end}</td></tr>{extra}"
    )


def test_parse_ant_table_reads_five_fields():
    html = TABLE_TEMPLATE.format(
        rows=row(1, "第二次训练", "暂无", "2026-10-21 19:30", "2026-10-21 20:30")
    )
    items = parse_ercourse(html)
    assert items == [
        {
            "title": "第二次训练",
            "date": "2026-10-21",
            "time": "19:30",
            "end_time": "20:30",
            "end_date": "",  # 同一天的活动不带结束日期
            "location": "暂无",
            "source": "二课",
        }
    ]


def test_cross_day_event_keeps_end_date():
    """真实数据里有跨天的活动（10-10 20:00 → 10-13 23:45），要能看出它是跨天的。"""
    html = TABLE_TEMPLATE.format(
        rows=row(1, "追“锋”计划", "暂无", "2026-10-10 20:00", "2026-10-13 23:45")
    )
    item = parse_ercourse(html)[0]
    assert item["date"] == "2026-10-10"
    assert item["end_date"] == "2026-10-13"
    assert item["end_time"] == "23:45"


# ------------------------------------------------------------------ 接口返回（全量抓取）
#
# 页面表格是「按日期过滤」的（一次只给一天），所以真正的全量来自接口
# `/personal/scCalendar/getPageUserList`。这里测的就是那个接口的返回体。


def test_parse_api_records_from_real_endpoint_shape():
    records = [
        {
            "id": "1",
            "title": "追“锋”计划",
            "place": None,
            "startTime": "2026-10-10 20:00:00",
            "endTime": "2026-10-13 23:45:00",
        },
        {
            "id": "2",
            "title": "物理学院25级联合班会：本科生的学术科研",
            "place": "理化大楼西三报告厅",
            "startTime": "2026-10-10 19:00:00",
            "endTime": "2026-10-10 21:00:00",
        },
        {
            "id": "3",
            "title": "三五七 10.22",
            "place": "暂无",
            "startTime": "2026-10-22 17:30:00",
            "endTime": "2026-10-22 18:30:00",
        },
    ]
    items = parse_erc_api_records(records)
    assert [item["title"] for item in items] == [
        "物理学院25级联合班会：本科生的学术科研",
        "追“锋”计划",
        "三五七 10.22",
    ]
    first = items[1]
    assert first["date"] == "2026-10-10" and first["time"] == "20:00"
    assert first["end_date"] == "2026-10-13" and first["end_time"] == "23:45"
    # 结果按 (日期, 开始时刻) 排序：19:00 的联合班会在前，20:00 的追锋在后。
    # place 为 null 时要落成「暂无」，与页面表格的展示一致。
    assert items[0]["location"] == "理化大楼西三报告厅"
    assert items[1]["location"] == "暂无"


def test_parse_api_records_dedupes_and_skips_junk():
    records = [
        {"title": "重复", "place": "暂无", "startTime": "2026-10-10 20:00:00",
         "endTime": "2026-10-10 21:00:00"},
        {"title": "重复", "place": "暂无", "startTime": "2026-10-10 20:00:00",
         "endTime": "2026-10-10 21:00:00"},
        {"title": "", "startTime": "2026-10-10 20:00:00"},
        {"title": "没有时间", "startTime": ""},
        "不是字典",
    ]
    items = parse_erc_api_records(records)
    assert len(items) == 1 and items[0]["title"] == "重复"
    assert parse_erc_api_records(None) == []
    assert parse_erc_api_records("oops") == []


def test_parse_columns_by_header_name_not_by_fixed_order():
    """列顺序变了也能认（表头驱动，而不是「按第 3 列是地点」）。"""
    html = """
    <div class="ant-table"><table>
    <thead><tr><th>标题</th><th>开始时间</th><th>地点</th><th>结束时间</th><th>序号</th></tr></thead>
    <tbody>
    <tr class="ant-table-row"><td>名家讲座</td><td>2026-11-02 14:00</td><td>图书馆报告厅</td>
    <td>2026-11-02 16:00</td><td>7</td></tr>
    </tbody></table></div>
    """
    item = parse_ercourse(html)[0]
    assert item["title"] == "名家讲座"
    assert item["location"] == "图书馆报告厅"
    assert item["date"] == "2026-11-02"
    assert item["time"] == "14:00"
    assert item["end_time"] == "16:00"


def test_parse_multiple_rows_and_seconds_in_timestamp():
    html = TABLE_TEMPLATE.format(
        rows=row(1, "训练", "体育馆", "2026-10-21 19:30:00", "2026-10-21 20:30:00")
        + row(2, "志愿服务", "东区", "2026-10-25 09:00", "2026-10-25 11:30")
    )
    items = parse_ercourse(html)
    assert [item["title"] for item in items] == ["训练", "志愿服务"]
    assert items[1]["date"] == "2026-10-25" and items[1]["time"] == "09:00"


def test_rows_without_date_are_skipped():
    """只有 `14:00` 而没有日期的行无法排进日历，只能跳过。"""
    html = TABLE_TEMPLATE.format(
        rows=row(1, "没有日期", "某地", "14:00", "15:00")
        + row(2, "有日期", "某地", "2026-10-21 14:00", "2026-10-21 15:00")
    )
    items = parse_ercourse(html)
    assert [item["title"] for item in items] == ["有日期"]


def test_html_without_ant_table_returns_empty():
    assert parse_ercourse("<html><body><h1>登录</h1></body></html>") == []
    assert parse_ercourse("") == []
    assert parse_ercourse(None) == []


# ------------------------------------------------------------------ 假宿主（不起窗口）

class _Host:
    """只带 `store` 的宿主：把控制器的实例方法当普通函数用。

    控制器里会用到 `self._ensure_category` 这类小工具，所以宿主要把它们显式挂上，
    否则「未绑定方法 + 假宿主」的组合会缺一环。
    """

    def __init__(self, store):
        self.store = store

    def _ensure_category(self, next_state, category_id, name):
        return ImportService._ensure_category(self, next_state, category_id, name)


@pytest.fixture()
def store(tmp_path):
    return Store(data_dir=str(tmp_path / "data"), packaged=False)


def _future(days=7):
    from datetime import date, timedelta

    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


ITEMS = [
    {
        "title": "第二次训练",
        "date": _future(7),
        "time": "19:30",
        "end_time": "20:30",
        "location": "暂无",
    },
    {
        "title": "志愿服务",
        "date": _future(11),
        "time": "09:00",
        "end_time": "11:30",
        "location": "东区",
    },
]


def test_create_ercourse_tasks_builds_category_and_schedule(store):
    host = _Host(store)
    created = ImportService._create_ercourse_tasks(host, ITEMS)
    assert created == 2
    categories = {c["id"]: c for c in store.state["categories"]}
    assert categories["er-course"]["name"] == "二课"
    task = next(t for t in store.state["tasks"] if t["title"] == "第二次训练")
    assert task["category"] == "er-course"
    assert task["date"] == ITEMS[0]["date"] and task["time"] == "19:30"
    assert task["duration"] == 60
    assert task["reminder"] == 30
    assert "地点：暂无" in task["notes"]
    assert "来源：二课" in task["notes"]
    second = next(t for t in store.state["tasks"] if t["title"] == "志愿服务")
    assert second["duration"] == 150


def test_create_ercourse_tasks_is_idempotent(store):
    host = _Host(store)
    assert ImportService._create_ercourse_tasks(host, ITEMS) == 2
    assert ImportService._create_ercourse_tasks(host, ITEMS) == 0
    assert len(store.state["tasks"]) == 2
    assert [c["id"] for c in store.state["categories"]].count("er-course") == 1


def test_create_ercourse_tasks_defaults_duration_when_end_missing(store):
    host = _Host(store)
    items = [
        {"title": "无结束时间", "date": _future(5), "time": "19:00", "end_time": "", "location": ""}
    ]
    ImportService._create_ercourse_tasks(host, items)
    task = store.state["tasks"][0]
    assert task["duration"] == 90
    assert "地点：" not in task["notes"]


def test_past_ercourse_items_are_thrown_away(store):
    """用户要求：已经过去的项目直接当垃圾丢掉——不建任务、也不标成已完成。"""
    host = _Host(store)
    items = [
        {"title": "过去的活动", "date": "2020-01-01", "time": "19:00", "end_time": "20:00",
         "location": "暂无"},
        {"title": "明天的活动", "date": _future(1), "time": "19:00", "end_time": "20:00",
         "location": "暂无"},
    ]
    created = ImportService._create_ercourse_tasks(host, items)
    assert created == 1
    assert [t["title"] for t in store.state["tasks"]] == ["明天的活动"]
    assert host._last_import_skipped == 1


def test_cross_day_ercourse_duration_and_note(store):
    host = _Host(store)
    items = [
        {
            "title": "追“锋”计划",
            "date": _future(3),
            "time": "20:00",
            "end_time": "23:45",
            "end_date": _future(6),
            "location": "暂无",
        }
    ]
    ImportService._create_ercourse_tasks(host, items)
    task = store.state["tasks"][0]
    # 跨天会超过 24 小时，clean_task 上限 1440 分钟
    assert task["duration"] == 1440
    assert "结束：" in task["notes"]


# ------------------------------------------------------------------ 课程整体平移


def _add(store, title, day, category, time="14:00"):
    from nanqi.core import clean_category, next_category_color

    if not any(c["id"] == category for c in store.state["categories"]):
        def add_category(next_state):
            next_state["categories"].append(
                clean_category(
                    {
                        "id": category,
                        "name": "课程" if category == "course" else category,
                        "color": next_category_color(next_state["categories"]),
                    }
                )
            )

        store.change(add_category)

    draft = {
        "title": title,
        "date": day,
        "time": time,
        "duration": 60,
        "category": category,
        "reminder": 30,
        "notes": "",
        "kind": "schedule",
        "priority": "normal",
        "tags": [],
        "attachments": [],
        "done": False,
    }
    clean = clean_task(draft, categories=[c["id"] for c in store.state["categories"]])

    def mutate(next_state):
        next_state["tasks"].append(clean)

    store.change(mutate)
    return clean


def test_shift_courses_moves_only_course_tasks(store):
    from datetime import date, timedelta

    host = _Host(store)
    ImportService._create_ercourse_tasks(host, ITEMS)  # 建出 er-course 分类
    course_day = date.today() + timedelta(days=10)
    _add(store, "线性代数", course_day.strftime("%Y-%m-%d"), "course")
    ImportService._create_ercourse_tasks(host, [dict(ITEMS[0], title="二课A")])

    result = AppController.shift_courses(host, 7)
    by_title = {t["title"]: t for t in store.state["tasks"]}
    assert result["shiftedCourses"] == 1
    assert by_title["线性代数"]["date"] == (course_day + timedelta(days=7)).strftime("%Y-%m-%d")
    assert by_title["二课A"]["date"] == ITEMS[0]["date"]       # 二课是确定的一天，不平移
    assert by_title["第二次训练"]["date"] == ITEMS[0]["date"]


def test_shift_courses_supports_negative_days_and_zero(store):
    from datetime import date, timedelta

    host = _Host(store)
    day = date.today() + timedelta(days=10)
    _add(store, "线性代数", day.strftime("%Y-%m-%d"), "course")
    AppController.shift_courses(host, -7)
    expected = (day - timedelta(days=7)).strftime("%Y-%m-%d")
    assert store.state["tasks"][0]["date"] == expected
    # 0 天：什么也不做，也不报错
    assert AppController.shift_courses(host, 0)["shiftedCourses"] == 0
    assert store.state["tasks"][0]["date"] == expected


def test_shift_courses_rejects_bad_input(store):
    host = _Host(store)
    with pytest.raises(ValueError, match="整数"):
        AppController.shift_courses(host, "七天")
    with pytest.raises(ValueError, match="365"):
        AppController.shift_courses(host, 400)


# ------------------------------------------------------------------ 设置：天气


@pytest.fixture()
def bridge(tmp_path, monkeypatch):
    monkeypatch.delenv("NANQI_DATA_DIR", raising=False)
    monkeypatch.delenv("WEEK LIGHT_DATA_DIR", raising=False)
    store = Store(data_dir=str(tmp_path / "data"), packaged=False)
    return Bridge(store, type("Host", (), {})())


def test_weather_settings_roundtrip_through_bridge(bridge):
    state = bridge._handle_settings({"weather": {"enabled": True, "latitude": 39.9, "longitude": 116.4}})["value"]
    weather = state["settings"]["weather"]
    assert weather["enabled"] is True
    assert weather["latitude"] == 39.9 and weather["longitude"] == 116.4

    # 只改一个字段时其余保持（patch 语义），非法键被丢掉
    state = bridge._handle_settings({"weather": {"city": "北京", "evil": "x"}})["value"]
    weather = state["settings"]["weather"]
    assert weather["city"] == "北京"
    assert weather["enabled"] is True
    assert "evil" not in weather


def test_weather_settings_default_off_and_persisted(bridge):
    assert bridge.store.state["settings"]["weather"]["enabled"] is False
    bridge._handle_settings({"weather": {"enabled": True}})
    reloaded = Store(data_dir=str(bridge.store.data_dir), packaged=False)
    assert reloaded.state["settings"]["weather"]["enabled"] is True
