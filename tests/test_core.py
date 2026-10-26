"""test_core.py —— `orange-schedule-main/tests/core.test.cjs` 的 pytest 移植。

每个测试函数的 docstring 就是对应的 JS 测试名，方便与 `core.test.cjs` 一一对照。
断言全部照搬 JS，没有合并、没有弱化。只有三处 JS↔Python 的等价改写：

  1. `Object.freeze(x)` / `structuredClone(x)` 的不可变性检查 → 先 `copy.deepcopy`
     留快照，调用后再 `==` 比较（Python 里 dict 本来就可变，冻结要靠断言证明）；
  2. `JSON.parse(JSON.stringify(x))`：凡是 JS 注释/测试名明确在考察「序列化后仍然
     保留」的用例（snooze、备份重启、每日重复备份）就用 `json.loads(json.dumps(x))`；
     纯粹当「快照」用的地方用 `copy.deepcopy`；
  3. `assert.equal(x.notifiedAt, undefined)` → `"notifiedAt" not in x`（`undefined`
     在 JS 里就是「这个键根本没写回」，`core.py` 也用「键是否存在」区分）。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from unittest import mock

import pytest

from nanqi.core import (
    clean_task,
    date_key,
    due_tasks,
    local_ms,
    make_series,
    validate_state,
)

# core.test.cjs 第 3 行的 draft。
DRAFT = {
    "title": "阶段复盘",
    "date": "2026-09-28",
    "time": "10:00",
    "duration": 60,
    "category": "work",
    "reminder": 10,
}

MAX_SAFE_INTEGER = 9007199254740991


class _Undefined:
    """JS 的 `undefined`：与 `null` 不同，用它保留用例的原始数量。"""

    def __repr__(self) -> str:  # pragma: no cover - 只用于断言信息
        return "undefined"

    __str__ = __repr__


UNDEFINED = _Undefined()


def draft(**over) -> dict:
    """`{...draft, ...over}`。"""
    return {**DRAFT, **over}


def add_days(value: str, days: int) -> str:
    """等价于 JS 里 `d.setDate(d.getDate()+days)` 后取 `dateKey(d)`。"""
    return date_key(datetime.strptime(value, "%Y-%m-%d") + timedelta(days=days))


def weekday_index(value: str) -> int:
    """把 Python 的 `weekday()`（周一=0）换算成 JS `getDay()`（周日=0）。"""
    return (datetime.strptime(value, "%Y-%m-%d").weekday() + 1) % 7


def attributes(task: dict) -> dict:
    """JS: 去掉 id/date/seriesId/seriesIndex 之后的全部属性。"""
    return {k: v for k, v in task.items() if k not in ("id", "date", "seriesId", "seriesIndex")}


def saved_fields(task: dict) -> dict:
    """JS: `fields(task)`，其中 `notifiedAt ?? null` 在 Python 里就是 `.get(...)`。"""
    return {
        "id": task["id"],
        "title": task["title"],
        "date": task["date"],
        "time": task["time"],
        "duration": task["duration"],
        "category": task["category"],
        "reminder": task["reminder"],
        "priority": task["priority"],
        "notes": task["notes"],
        "done": task["done"],
        "createdAt": task["createdAt"],
        "notifiedAt": task.get("notifiedAt"),
        "snoozeUntil": task.get("snoozeUntil"),
    }


# --------------------------------------------------------------------------- 基础校验


def test_reject_invalid_dates_times_and_reminders():
    """reject invalid dates, times and reminders"""
    with pytest.raises(ValueError):
        clean_task(draft(date="2026-02-30"))
    with pytest.raises(ValueError):
        clean_task(draft(time="24:00"))
    with pytest.raises(ValueError):
        clean_task(draft(reminder=2))
    with pytest.raises(ValueError):
        clean_task(draft(title=" "))


def test_weekly_repetitions_preserve_local_dates_across_months_and_year():
    """weekly repetitions preserve local dates across months and year"""
    tasks = make_series(clean_task(draft(date="2026-12-28")), 4)
    assert [t["date"] for t in tasks] == ["2026-12-28", "2027-01-04", "2027-01-11", "2027-01-18"]
    assert len({t["id"] for t in tasks}) == 4


# --------------------------------------------------------------------------- 提醒判定


def test_remind_exactly_at_threshold_then_suppress_across_restart():
    """remind exactly at threshold, then suppress across restart"""
    t = clean_task(DRAFT)
    threshold = local_ms("2026-09-28", "09:50:00")
    assert len(due_tasks([t], threshold - 1)) == 0
    assert len(due_tasks([t], threshold)) == 1
    t["notifiedAt"] = threshold
    restored = validate_state({"version": 1, "tasks": [t]})
    assert len(due_tasks(restored["tasks"], threshold + 1000000)) == 0


def test_snooze_survives_serialization_and_fires_once_at_due_time():
    """snooze survives serialization and fires once at due time"""
    t = {**clean_task(DRAFT), "notifiedAt": 1, "snoozeUntil": 100000}
    restored = validate_state(json.loads(json.dumps({"version": 1, "tasks": [t]})))
    assert len(due_tasks(restored["tasks"], 99999)) == 0
    assert len(due_tasks(restored["tasks"], 100000)) == 1


def test_done_and_reminder_disabled_tasks_never_fire():
    """done and reminder-disabled tasks never fire"""
    t = clean_task(DRAFT)
    assert due_tasks([{**t, "done": True}, {**t, "reminder": -1}], MAX_SAFE_INTEGER) == []


def test_schedule_changes_rearm_reminders_but_note_only_edits_do_not():
    """schedule changes rearm reminders but note-only edits do not"""
    old = {**clean_task(DRAFT), "notifiedAt": 123}
    assert clean_task({**old, "notes": "new"}, old)["notifiedAt"] == 123
    # JS: assert.equal(cleanTask({...old,time:'11:00'},old).notifiedAt, undefined)
    assert "notifiedAt" not in clean_task({**old, "time": "11:00"}, old)


def test_missed_reminders_are_caught_after_resume_regardless_of_length_of_absence():
    """missed reminders are caught after resume regardless of length of absence"""
    t = clean_task(DRAFT)
    assert len(due_tasks([t], local_ms("2026-10-02", "12:00:00"))) == 1


# --------------------------------------------------------------------------- 备份校验


def test_backups_reject_duplicate_ids_without_changing_data():
    """backups reject duplicate ids without changing data"""
    t = clean_task(DRAFT)
    backup = {"version": 1, "tasks": [t, t]}
    before = copy.deepcopy(backup)
    with pytest.raises(ValueError):
        validate_state(backup)
    assert backup == before
    with pytest.raises(ValueError):
        validate_state({"version": 2, "tasks": []})


# --------------------------------------------------------------------------- 每日重复


def test_daily_repetition_includes_both_endpoints_including_a_one_day_range():
    """daily repetition includes both endpoints, including a one-day range"""
    original = clean_task(draft(date="2030-06-10"))
    options = {"frequency": "daily", "until": "2030-06-10"}
    original_before = copy.deepcopy(original)
    options_before = copy.deepcopy(options)
    tasks = make_series(original, options)
    assert len(tasks) == 1
    single = {k: v for k, v in tasks[0].items() if k not in ("seriesId", "seriesIndex")}
    assert tasks[0]["seriesId"]
    assert tasks[0]["seriesIndex"] == 0
    assert single == original
    # Even a single generated task must be independent of the draft
    assert tasks[0] is not original
    assert original == original_before
    assert options == options_before


def test_daily_repetition_crosses_month_leap_day_and_year_boundaries_using_calendar_dates():
    """daily repetition crosses month, leap-day and year boundaries using calendar dates"""
    cases = [
        ("2030-01-30", "2030-02-02", ["2030-01-30", "2030-01-31", "2030-02-01", "2030-02-02"]),
        ("2027-02-28", "2027-03-01", ["2027-02-28", "2027-03-01"]),
        ("2028-02-28", "2028-03-01", ["2028-02-28", "2028-02-29", "2028-03-01"]),
        ("2030-12-31", "2031-01-02", ["2030-12-31", "2031-01-01", "2031-01-02"]),
    ]
    for start, until, expected in cases:
        tasks = make_series(clean_task(draft(date=start)), {"frequency": "daily", "until": until})
        assert [task["date"] for task in tasks] == expected, f"{start} through {until}"
        assert all(task["time"] == DRAFT["time"] for task in tasks)


def test_legacy_numeric_weekly_repetition_counts_remain_compatible():
    """legacy numeric weekly repetition counts remain compatible"""
    original = clean_task(draft(date="2026-12-28"))
    original_before = copy.deepcopy(original)
    for count, last_date in [(1, "2026-12-28"), (4, "2027-01-18"), (8, "2027-02-15"), (12, "2027-03-15")]:
        tasks = make_series(original, count)
        assert len(tasks) == count
        assert tasks[0]["id"] == original["id"]
        assert tasks[0]["date"] == original["date"]
        assert tasks[-1]["date"] == last_date
        assert len({task["id"] for task in tasks}) == count
        assert all(weekday_index(task["date"]) == 1 for task in tasks)  # JS getDay()===1，周一
        assert all(task["time"] == original["time"] and task["reminder"] == original["reminder"] for task in tasks)
    assert make_series(original) == [{**original}]
    assert original == original_before


def test_daily_tasks_have_unique_ids_and_preserve_attributes_without_mutating_the_draft():
    """daily tasks have unique IDs and preserve attributes without mutating the draft"""
    original = clean_task(
        draft(
            date="2030-06-10",
            time="07:45",
            duration=45,
            reminder=30,
            category="study",
            priority="high",
            notes="每天单独记录进展",
        )
    )
    before = copy.deepcopy(original)
    options = {"frequency": "daily", "until": "2030-06-13"}
    options_before = copy.deepcopy(options)
    tasks = make_series(original, options)
    assert len(tasks) == 4
    assert len({task["id"] for task in tasks}) == 4
    # Generated tasks must be different objects
    assert len({id(task) for task in tasks}) == 4
    assert tasks[0]["id"] == original["id"]
    for task in tasks:
        assert task is not original
        assert attributes(task) == attributes(original)
    assert original == before
    assert options == options_before
    assert options == {"frequency": "daily", "until": "2030-06-13"}


def test_editing_and_completing_one_daily_task_leaves_every_other_day_unchanged():
    """editing and completing one daily task leaves every other day unchanged"""
    tasks = make_series(clean_task(draft(date="2030-06-10")), {"frequency": "daily", "until": "2030-06-12"})
    before = json.loads(json.dumps(tasks))
    tasks[1] = clean_task(
        {**tasks[1], "title": "仅修改第二天", "time": "11:30", "notes": "只属于第二天"},
        tasks[1],
    )
    tasks[1]["done"] = True
    tasks[1]["snoozeUntil"] = None
    assert tasks[1]["id"] == before[1]["id"]
    assert tasks[1]["date"] == "2030-06-11"
    assert tasks[1]["title"] == "仅修改第二天"
    assert tasks[1]["done"] is True
    assert tasks[0] == before[0]
    assert tasks[2] == before[2]
    assert [task["id"] for task in due_tasks(tasks, local_ms("2030-06-13", "12:00:00"))] == [
        tasks[0]["id"],
        tasks[2]["id"],
    ]


def test_each_daily_task_becomes_due_at_its_own_threshold_and_earlier_notifications_do_not_suppress_later_days():
    """each daily task becomes due at its own threshold and earlier notifications do not suppress later days"""
    tasks = make_series(
        clean_task(draft(date="2030-05-31", time="08:30", reminder=15)),
        {"frequency": "daily", "until": "2030-06-02"},
    )
    for task in tasks:
        threshold = local_ms(task["date"], "08:15:00")
        assert due_tasks(tasks, threshold - 1) == [], f"No early reminder on {task['date']}"
        assert [due["id"] for due in due_tasks(tasks, threshold)] == [task["id"]], f"Only {task['date']} is due"
        task["notifiedAt"] = threshold
        assert due_tasks(tasks, threshold) == [], "Already notified tasks must not repeat"


def test_daily_reminders_with_a_one_day_lead_fire_on_the_preceding_calendar_day():
    """daily reminders with a one-day lead fire on the preceding calendar day"""
    tasks = make_series(
        clean_task(draft(date="2030-06-01", time="09:00", reminder=1440)),
        {"frequency": "daily", "until": "2030-06-02"},
    )
    thresholds = [local_ms("2030-05-31", "09:00:00"), local_ms("2030-06-01", "09:00:00")]
    for index in range(len(tasks)):
        assert due_tasks(tasks, thresholds[index] - 1) == []
        assert [task["id"] for task in due_tasks(tasks, thresholds[index])] == [tasks[index]["id"]]
        tasks[index]["notifiedAt"] = thresholds[index]


def test_a_serialized_backup_preserves_every_daily_occurrence_and_its_independent_reminder_state():
    """a serialized backup preserves every daily occurrence and its independent reminder state"""
    tasks = make_series(
        clean_task(draft(date="2030-06-10", notes="重复计划备份验证")),
        {"frequency": "daily", "until": "2030-06-13"},
    )

    def threshold(value: str) -> int:
        return local_ms(value, "09:50:00")

    tasks[0]["done"] = True
    tasks[1]["notifiedAt"] = threshold(tasks[1]["date"])
    tasks[2]["notifiedAt"] = threshold(tasks[2]["date"])
    tasks[2]["snoozeUntil"] = threshold(tasks[2]["date"]) + 600000

    backup = {"version": 1, "tasks": tasks, "settings": {"sound": False, "popup": True}, "history": []}
    serialized = json.dumps(backup)
    restored = validate_state(json.loads(serialized))
    assert len(restored["tasks"]) == 4
    assert [saved_fields(task) for task in restored["tasks"]] == [saved_fields(task) for task in tasks]
    assert restored["settings"]["sound"] is False
    # Backup validation must not modify the source
    assert json.dumps(backup) == serialized
    assert due_tasks(restored["tasks"], tasks[2]["snoozeUntil"] - 1) == []
    assert [task["id"] for task in due_tasks(restored["tasks"], tasks[2]["snoozeUntil"])] == [tasks[2]["id"]]
    restored["tasks"][2]["snoozeUntil"] = None
    restored["tasks"][2]["notifiedAt"] = tasks[2]["snoozeUntil"]
    assert [task["id"] for task in due_tasks(restored["tasks"], threshold(tasks[3]["date"]))] == [tasks[3]["id"]]
    assert [saved_fields(task) for task in validate_state(json.loads(json.dumps(restored)))["tasks"]] == [
        saved_fields(task) for task in restored["tasks"]
    ]


def test_daily_repetition_rejects_missing_malformed_impossible_and_reversed_end_dates():
    """daily repetition rejects missing, malformed, impossible and reversed end dates"""
    original = clean_task(draft(date="2028-02-28"))
    original_before = copy.deepcopy(original)
    invalid_ends = [
        UNDEFINED,
        None,
        False,
        20280301,
        "",
        "2028-2-29",
        "2028-02-30",
        "2027-02-29",
        "2028-13-01",
        "2028-00-01",
        "2028-03-00",
        "2028-02-29T12:00:00",
        "not-a-date",
        "2028-02-27",
    ]
    for until in invalid_ends:
        # JS: assert.throws(..., `Must reject end date ${String(until)}`)
        with pytest.raises(ValueError):
            make_series(original, {"frequency": "daily", "until": until})
    assert original == original_before


def test_repetition_rejects_unknown_frequency_and_unsupported_legacy_counts():
    """repetition rejects unknown frequency and unsupported legacy counts"""
    original = clean_task(DRAFT)
    original_before = copy.deepcopy(original)
    for options in [
        {"frequency": "monthly", "until": "2026-10-28"},
        {"frequency": "hourly", "until": "2026-10-28"},
        {"frequency": "Daily", "until": "2026-10-28"},
    ]:
        with pytest.raises(ValueError):
            make_series(original, options)
    for count in [0, -1, 2, 3, 5, 13, 1.5, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            make_series(original, count)
    assert original == original_before


def test_daily_repetition_allows_10000_occurrences_but_rejects_a_larger_inclusive_range():
    """daily repetition allows 10000 occurrences but rejects a larger inclusive range"""
    original = clean_task(draft(date="2030-01-01"))
    original_before = copy.deepcopy(original)
    last_allowed = add_days(original["date"], 9999)
    tasks = make_series(original, {"frequency": "daily", "until": last_allowed})
    assert len(tasks) == 10000
    assert tasks[0]["date"] == original["date"]
    assert tasks[-1]["date"] == last_allowed
    assert len({task["id"] for task in tasks}) == 10000
    # '10001 daily occurrences must be rejected before allocating a series'
    with mock.patch("uuid.uuid4") as allocate:
        with pytest.raises(ValueError):
            make_series(original, {"frequency": "daily", "until": add_days(last_allowed, 1)})
        assert allocate.call_count == 0
    assert original == original_before
