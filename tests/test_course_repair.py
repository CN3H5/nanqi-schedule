r"""test_course_repair.py —— 旧版「拼接标题」课程日程的解析与修复。

用到的标题全部来自**用户真实数据**（`%APPDATA%\nanqi_schedule\plans.json`），
这样才不会写着写着又变成「按自己想象的格式解析」。
"""

from __future__ import annotations

import json

import pytest

from nanqi.course_repair import (
    COURSE_REMINDER_MINUTES,
    looks_glued,
    parse_glued_title,
    repair_state,
)
from nanqi.core import clean_task, initial_state, validate_state
from nanqi.store import Store
from nanqi.task_series import validate_series_tasks

#: 真实数据里的 8 个脏标题（原样抄下来，包括 `1,22210` 这种粘连）
REAL_TITLES = {
    "speak": "07:5009:25 FL1102A.02 [2] 科普英语交流A Thomas Yifang Xiao 3,6-14 1,22210",
    "speak2": "07:5009:25 FL1102A.02 [2] 科普英语交流A Thomas Yifang Xiao 4 1,22210",
    "linear": "07:5009:25 MATH1009.11 [4] 线性代数(B1) 叶郁 2-4,6-18 1,25204",
    "mech": "07:5009:25 PHYS1001A.06 [4] 力学A 杜佩之 2-5,7-18 1,25107",
    "mech2": "07:5009:25 PHYS1001A.06 [4] 力学A 杜佩之 6 1,25107",
    "pe": "14:0015:35 PE00001.15 [1] 基础体育 李少松 2-5,7-18 6,7西区操场（男生）",
    "cs": "19:3021:55 CS1003.H1 [3] 计算机程序设计 孙广中 4,7-15 11,12,13西区电三楼计院机房5...",
    "english": "21:1021:55 FL1100.01 [4] 大学英语 1 13不需选课，外语教学中...",
}


from datetime import date, timedelta


def future(days=30):
    """测试用的「将来」日期：清理逻辑会丢掉已经过去的课表/考试/二课。"""
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


def course(**overrides):
    draft = {
        "title": "力学A",
        "date": future(),
        "time": "07:50",
        "duration": 95,
        "category": "course",
        "reminder": COURSE_REMINDER_MINUTES,
        "notes": "地点：5107\n教师：杜佩之\n周次：2-5,7-18\n来源：教务系统课表导入",
        "kind": "schedule",
        "priority": "normal",
        "tags": [],
        "attachments": [],
        "done": False,
    }
    draft.update(overrides)
    task_id = draft.pop("id", None)
    task = clean_task(draft, categories=["course", "exam", "study", "er-course"])
    if task_id:
        task["id"] = task_id
    return task


def state_with(*tasks):
    state = initial_state()
    state["categories"].append(
        {"id": "course", "name": "课程", "color": "#5b8fc9", "bg": "#eef3f9", "ink": "#33506e"}
    )
    state["tasks"] = list(tasks)
    return state


# ------------------------------------------------------------------ 标题解析


def test_parses_real_glued_titles():
    info = parse_glued_title(REAL_TITLES["speak"])
    assert info["name"] == "科普英语交流A"
    assert (info["start"], info["end"], info["duration"]) == ("07:50", "09:25", 95)
    assert info["weeks_text"] == "3,6-14"
    assert info["teacher"] == "Thomas Yifang Xiao"
    # `1,22210` = 节次 1,2 + 教室 2210
    assert (info["timespan"], info["room"]) == ("1,2", "2210")
    assert (info["code"], info["section"]) == ("FL1102A.02", "2")


@pytest.mark.parametrize(
    "key,name,start,duration,room",
    [
        ("linear", "线性代数(B1)", "07:50", 95, "5204"),
        ("mech", "力学A", "07:50", 95, "5107"),
        ("pe", "基础体育", "14:00", 95, "西区操场（男生）"),
        ("cs", "计算机程序设计", "19:30", 145, "西区电三楼计院机房5..."),
        ("english", "大学英语", "21:10", 45, "不需选课，外语教学中..."),
    ],
)
def test_parses_remaining_real_titles(key, name, start, duration, room):
    info = parse_glued_title(REAL_TITLES[key])
    assert info["name"] == name
    assert info["start"] == start
    assert info["duration"] == duration
    assert info["room"] == room


def test_normal_titles_are_not_glued():
    for title in ("力学A", "三五七 10.22", "科普英语交流A", "复习：线代第六章", ""):
        assert looks_glued(title) is False
        assert parse_glued_title(title) is None
    assert looks_glued(None) is False
    assert parse_glued_title(None) is None
    # 只有一个时刻的正常标题不算
    assert parse_glued_title("07:50 开始 · 2210") is None


# ------------------------------------------------------------------ 就地修复


def test_repairs_tasks_when_no_good_import_exists():
    broken = course(title=REAL_TITLES["mech"], duration=45, reminder=10, notes="来源：教务系统课表导入")
    state = state_with(broken)
    stats = repair_state(state)

    assert stats == {"repaired": 1, "removed": 0, "reminders": 0}
    fixed = state["tasks"][0]
    assert fixed["title"] == "力学A"
    assert (fixed["time"], fixed["duration"]) == ("07:50", 95)
    assert fixed["reminder"] == COURSE_REMINDER_MINUTES
    assert "地点：5107" in fixed["notes"] and "教师：杜佩之" in fixed["notes"]
    assert "周次：2-5,7-18" in fixed["notes"]
    validate_series_tasks(state["tasks"])


def test_removes_old_copy_when_a_good_import_of_same_course_exists():
    good = course()  # 备注里带「来源：教务系统课表导入」
    broken_one = course(title=REAL_TITLES["mech"], date=future(10), duration=45, reminder=10,
                        notes="来源：教务系统课表导入", id="broken-1")
    broken_two = course(title=REAL_TITLES["mech2"], date="2026-10-27", duration=45, reminder=10,
                        notes="来源：教务系统课表导入", id="broken-2")
    state = state_with(broken_one, broken_two, good)
    stats = repair_state(state)

    assert stats["removed"] == 2 and stats["repaired"] == 0
    assert [t["title"] for t in state["tasks"]] == ["力学A"]
    assert state["tasks"][0]["date"] == good["date"]  # 留下的是正常那份


def test_dedupes_same_name_date_time_even_without_good_copy():
    first = course(title=REAL_TITLES["pe"], date="2026-10-21", time="14:00", duration=45,
                   reminder=10, notes="来源：教务系统课表导入", id="a")
    second = course(title=REAL_TITLES["pe"], date="2026-10-21", time="14:00", duration=45,
                    reminder=10, notes="来源：教务系统课表导入", id="b")
    state = state_with(first, second)
    stats = repair_state(state)
    assert stats["repaired"] == 1 and stats["removed"] == 1
    assert len(state["tasks"]) == 1


def test_repair_is_idempotent():
    state = state_with(
        course(title=REAL_TITLES["linear"], duration=45, reminder=10, notes="来源：教务系统课表导入"),
        course(title=REAL_TITLES["pe"], date=future(11), duration=45, reminder=10,
               notes="来源：教务系统课表导入", id="pe-1"),
    )
    first = repair_state(state)
    assert any(first.values())
    second = repair_state(state)
    assert second == {"repaired": 0, "removed": 0, "reminders": 0}


def test_non_course_categories_are_untouched():
    weird = course(title=REAL_TITLES["mech"], category="study", notes="用户自己写的")
    state = state_with(weird)
    assert repair_state(state) == {"repaired": 0, "removed": 0, "reminders": 0}
    assert state["tasks"][0]["title"] == REAL_TITLES["mech"]


# ------------------------------------------------------------------ 提醒时长归一化


def test_course_reminder_is_normalised_to_30_minutes_for_imports():
    imported = course(reminder=10)
    state = state_with(imported)
    stats = repair_state(state)
    assert stats["reminders"] == 1
    assert state["tasks"][0]["reminder"] == COURSE_REMINDER_MINUTES


def test_user_edited_reminder_is_respected():
    """用户自己改过提醒（备注里没有「导入」）就绝不覆盖。"""
    custom = course(reminder=10, notes="我想提前十分钟")
    state = state_with(custom)
    assert repair_state(state)["reminders"] == 0
    assert state["tasks"][0]["reminder"] == 10


# ------------------------------------------------------------------ 接到 Store 上


def test_store_repairs_on_load_and_keeps_a_backup(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state = state_with(
        course(title=REAL_TITLES["mech"], date=future(10), duration=45, reminder=10,
               notes="来源：教务系统课表导入", id="broken-1"),
    )
    (data_dir / "plans.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    store = Store(data_dir=str(data_dir), packaged=False)

    assert len(store.state["tasks"]) == 1
    assert store.state["tasks"][0]["title"] == "力学A"
    assert store.state["tasks"][0]["duration"] == 95
    assert "已整理导入的日程" in store.migration_note
    backups = list(data_dir.glob("before-repair-*.json"))
    assert len(backups) == 1
    # 备份里还是修复前的样子
    original = json.loads(backups[0].read_text(encoding="utf-8"))
    assert original["tasks"][0]["title"] == REAL_TITLES["mech"]
    # 落盘后的数据能通过整份校验
    validate_state(json.loads((data_dir / "plans.json").read_text(encoding="utf-8")))


def test_store_does_nothing_when_data_is_clean(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state = state_with(course())
    (data_dir / "plans.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    clean = Store(data_dir=str(data_dir), packaged=False)
    assert clean.state["tasks"][0]["title"] == "力学A"
    assert clean.migration_note == ""
    assert not list(data_dir.glob("before-repair-*.json"))


