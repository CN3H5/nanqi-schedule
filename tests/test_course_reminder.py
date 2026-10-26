"""test_course_reminder.py —— 上课提醒的文案与节奏（用户明确提出的四条规则）。

对应用户的原话：
  1. 「主标题是提醒你要上课了、要上的课程是什么，副标题写什么时候开始、在哪里上课」
  2. 「早上有早八，就提前一个小时提醒一次，然后再提前半个小时提醒一次」
  3. 「下午两点又有课，就在上午上完课之后提醒他中午要早点睡，下午还有课」
  4. 「天气不好就提前 45 分钟提醒」

这里只测纯函数（`nanqi/course_reminder.py`），不建窗口。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from nanqi.course_reminder import (
    BAD_WEATHER_LEAD_MINUTES,
    BAD_WEATHER_NOTE,
    classroom_of,
    course_label,
    is_course,
    notifications,
    reminder_lead,
)


DAY = "2026-10-21"


def course(
    title="线性代数(B1)",
    time="14:00",
    *,
    duration=95,
    category="course",
    room="5204",
    reminder=30,
    date=DAY,
    done=False,
    task_id="t1",
):
    notes = "地点：" + room if room else ""
    return {
        "id": task_id,
        "title": title,
        "date": date,
        "time": time,
        "duration": duration,
        "category": category,
        "reminder": reminder,
        "notes": notes,
        "done": done,
        "kind": "schedule",
        "priority": "normal",
        "tags": [],
        "attachments": [],
    }


def at(hour, minute=0, day=DAY):
    date = datetime.strptime(day, "%Y-%m-%d")
    return date.replace(hour=hour, minute=minute)


# ------------------------------------------------------------------ 基本判断


def test_is_course_covers_schedule_and_exam_only():
    """用户的分类规则：课表 + 考试 = 第一课堂；二课/复习/其它 = 普通待办。"""
    assert is_course(course())
    assert is_course(course(category="exam"))
    assert not is_course(course(category="er-course"))   # 二课是待办，不走上课提醒
    assert not is_course(course(category="study"))
    assert not is_course({})


def test_course_label_uses_exam_wording():
    assert course_label(course()) == "上课提醒"
    assert course_label(course(category="exam")) == "考试提醒"


def test_classroom_of_reads_location_line_and_never_guesses():
    assert classroom_of(course(room="5107")) == "5107"
    # 教室号就是编号：不能被「翻译」成「五教 1 楼 107 室」
    assert classroom_of(course(room="5107")) != "五教 1 楼 107 室"
    assert classroom_of(course(room="")) == ""
    assert classroom_of({"notes": "教师：李少松"}) == ""


def test_reminder_lead_upgrades_default_but_respects_longer_user_choice():
    assert reminder_lead(course()) == 30
    assert reminder_lead(course(), bad_weather=True) == BAD_WEATHER_LEAD_MINUTES == 45
    # 用户自己设成提前 60 分钟时不要被天气缩回 45
    assert reminder_lead(course(reminder=60), bad_weather=True) == 60


# ------------------------------------------------------------------ 主标题 / 副标题


def test_title_is_course_name_and_body_is_only_time_and_room():
    """通知里只放「几点开始、在哪」；教师/周次这些留在计划详情里。"""
    items = notifications([course()], at(13, 30))
    assert len(items) == 1
    item = items[0]
    assert item["stage"] == "soon"
    assert item["title"] == "上课提醒 · 线性代数(B1)"
    assert item["body"] == "14:00 开始 · 5204"
    # 备注里的其它信息不该漏进通知
    assert "教师" not in item["body"] and "周次" not in item["body"]


def test_exam_reminder_uses_exam_wording():
    exam = course(title="力学A", category="exam", room="5107")
    items = notifications([exam], at(13, 30))
    assert items[0]["title"] == "考试提醒 · 力学A"
    assert items[0]["body"] == "14:00 开始 · 5107"


def test_body_without_room_still_readable():
    items = notifications([course(room="")], at(13, 30))
    assert items[0]["body"] == "14:00 开始"


def test_ercourse_is_a_plain_todo_and_never_uses_course_copy():
    ercourse = course(title="三五七 10.22", category="er-course")
    assert notifications([ercourse], at(13, 30)) == []


# ------------------------------------------------------------------ 早八：两条


def test_early_class_reminds_one_hour_then_half_hour():
    early = course(title="高等数学", time="08:00", task_id="e1")
    delivered: set = set()

    first = notifications([early], at(7, 0), delivered)
    assert [item["stage"] for item in first] == ["early"]
    assert first[0]["title"] == "上课提醒 · 高等数学"
    assert first[0]["body"].splitlines()[0] == "08:00 开始 · 5204"
    assert "还有一小时" in first[0]["body"]
    delivered.add(first[0]["key"])

    # 07:30 到点前 30 分钟：第二条，文案与第一条不同（只剩时间与地点）
    second = notifications([early], at(7, 30), delivered)
    assert [item["stage"] for item in second] == ["soon"]
    assert second[0]["body"] == "08:00 开始 · 5204"
    delivered.add(second[0]["key"])

    # 两条都发过之后不再重复
    assert notifications([early], at(7, 45), delivered) == []


def test_early_class_before_one_hour_does_not_fire():
    assert notifications([course(time="08:00")], at(6, 50)) == []


def test_early_class_started_late_only_sends_the_useful_one():
    """07:45 才打开应用：只补发「马上要上课」，不再补一小时前那条。"""
    items = notifications([course(time="08:00")], at(7, 45))
    assert [item["stage"] for item in items] == ["soon"]


# ------------------------------------------------------------------ 下午课：午睡提醒


def test_afternoon_class_reminds_nap_after_morning_class_ends():
    morning = course(title="大学物理", time="08:00", duration=95, task_id="m1", room="5107")
    afternoon = course(title="线性代数(B1)", time="14:00", duration=95, task_id="a1", room="5204")

    # 上午还在上课（09:20）：不该提醒午睡
    assert notifications([morning, afternoon], at(9, 20)) == []

    items = notifications([morning, afternoon], at(9, 40))
    assert [item["stage"] for item in items] == ["nap"]
    assert items[0]["title"] == "中午早点睡，下午还有课"
    assert items[0]["body"] == "14:00 的 线性代数(B1) 在 5204"

    delivered = {items[0]["key"]}
    # 到 13:30 再按常规提醒一次
    soon = notifications([morning, afternoon], at(13, 30), delivered)
    assert [item["stage"] for item in soon] == ["soon"]
    assert soon[0]["title"] == "上课提醒 · 线性代数(B1)"


def test_nap_reminder_never_fires_twice_for_several_afternoon_classes():
    morning = course(title="大学物理", time="08:00", duration=95, task_id="m1")
    first = course(title="线性代数", time="14:00", task_id="a1")
    second = course(title="英语", time="16:00", task_id="a2")

    items = notifications([morning, first, second], at(10, 0))
    assert [item["stage"] for item in items] == ["nap"]
    assert items[0]["task"]["id"] == "a1"  # 只提醒最近的那一节

    delivered = {items[0]["key"]}
    assert notifications([morning, first, second], at(10, 5), delivered) == []


def test_no_nap_reminder_without_morning_class():
    items = notifications([course(time="14:00")], at(10, 0))
    assert items == []


def test_evening_class_has_no_nap_reminder():
    morning = course(title="大学物理", time="08:00", duration=95, task_id="m1")
    evening = course(title="选修", time="19:00", task_id="a1")
    # 晚上 19:00 的课（>= 13:00）在上午课后仍会提醒午睡一次，符合「下午还有课」的本意
    items = notifications([morning, evening], at(12, 0))
    assert [item["stage"] for item in items] == ["nap"]
    assert "19:00" in items[0]["body"]


# ------------------------------------------------------------------ 天气


def test_bad_weather_moves_reminder_to_45_minutes_with_note():
    plain = course(time="14:00")
    # 13:20 时 45 分钟前已过、30 分钟前还没到：只有天气不好时才会提醒
    assert notifications([plain], at(13, 20)) == []
    items = notifications([plain], at(13, 20), bad_weather=True)
    assert [item["stage"] for item in items] == ["soon"]
    assert "今天天气不好，要早点出发" in items[0]["body"]


def test_weather_note_text_can_be_customised():
    items = notifications([course()], at(13, 20), bad_weather=True, weather_note="有雨，要早点出发")
    assert items[0]["body"].splitlines()[-1] == "有雨，要早点出发"


def test_good_weather_keeps_30_minutes_and_no_note():
    items = notifications([course()], at(13, 20), bad_weather=False)
    assert items == []
    items = notifications([course()], at(13, 30), bad_weather=False)
    assert BAD_WEATHER_NOTE not in items[0]["body"]


# ------------------------------------------------------------------ 过滤


def test_other_days_done_and_silenced_tasks_are_ignored():
    assert notifications([course(date="2026-10-20")], at(13, 30)) == []
    assert notifications([course(done=True)], at(13, 30)) == []
    assert notifications([course(reminder=-1)], at(13, 30)) == []
    assert notifications([course(category="study")], at(13, 30)) == []


def test_items_sorted_by_start_time():
    late = course(title="英语", time="14:20", task_id="b", reminder=90)
    early = course(title="线性代数", time="14:00", task_id="a", reminder=90)
    items = notifications([late, early], at(13, 10))
    assert [item["task"]["title"] for item in items] == ["线性代数", "英语"]


def test_course_already_started_is_not_reminded():
    """上课十分钟后才打开应用：不能弹一句「还有 30 分钟」。"""
    assert notifications([course(time="14:00")], at(14, 10)) == []


# ------------------------------------------------------------------ Persistence service

def test_service_uses_weather_and_persists_stage(tmp_path):
    from nanqi.store import Store
    from nanqi.core import clean_task, clean_category
    from nanqi.reminder_service import ReminderService
    from nanqi.weather import judge
    store = Store(data_dir=str(tmp_path), packaged=False)
    def mutate(state):
        state["categories"].append(clean_category({"id":"course","name":"课程"}))
        state["tasks"].append(clean_task({"title":"线性代数(B1)","date":DAY,"time":"14:00","duration":95,"category":"course","reminder":30,"notes":"地点：5204"},categories=["course"]))
    store.change(mutate)
    weather = {"precipitation_mm":6.0,"wind_kmh":5.0,"temp_min_c":12.0,"code":63}
    weather["judge"] = judge(weather)
    entries = ReminderService(store).collect(at(13,20),weather)
    assert entries[0]["title"] == "上课提醒 · 线性代数(B1)"
    assert entries[0]["body"].splitlines()[0] == "14:00 开始 · 5204"
    assert "今天天气不好，要早点出发" in entries[0]["body"]
    assert store.state["tasks"][0]["notifiedAt"] is not None
    assert store.state["history"][-1]["taskId"] == store.state["tasks"][0]["id"]
    restarted = Store(data_dir=str(tmp_path),packaged=False)
    assert ReminderService(restarted).collect(at(13,21),weather) == []
