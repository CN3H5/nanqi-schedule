"""test_weekday.py —— `orange-schedule-main/tests/weekday.test.cjs` 的 pytest 移植。

每个测试函数的 docstring 就是对应的 JS 测试名，方便与 `weekday.test.cjs` 一一对照。
JS↔Python 的等价改写与 `test_core.py` 完全一致：

  * `Object.freeze` / `structuredClone` → `copy.deepcopy` 快照 + `==` 比较；
  * `JSON.parse(JSON.stringify(...))` 用在「备份重启」这类序列化用例上时保留
    `json.loads(json.dumps(...))`；
  * `context.mock.method(crypto, 'randomUUID', ...)` → `mock.patch("uuid.uuid4")`
    并断言 `call_count == 0`（`nanqi.core.py` 里用的是 `uuid.uuid4()`）；
  * `new Date(task.date+'T12:00:00').getDay()` → `weekday_index()`（周日=0 的 JS 口径）。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from unittest import mock

import pytest

from nanqi.core import clean_task, date_key, due_tasks, local_ms, make_series, validate_state

# weekday.test.cjs 第 6 行的 draft。
WEEKDAY_DRAFT = {
    "title": "工作日阅读",
    "date": "2026-10-12",
    "time": "09:00",
    "duration": 30,
    "category": "study",
    "reminder": 10,
    "priority": "high",
    "notes": "每个工作日独立记录",
}


class _Undefined:
    """JS 的 `undefined`：与 `null` 不同，用它保留用例的原始数量。"""

    def __repr__(self) -> str:  # pragma: no cover - 只用于断言信息
        return "undefined"

    __str__ = __repr__


UNDEFINED = _Undefined()


def weekdays(start: str, until):
    """JS: `weekdays = (start, until) => makeSeries(cleanTask({...draft, date: start}), {frequency:'weekdays', until})`。"""
    return make_series(clean_task({**WEEKDAY_DRAFT, "date": start}), {"frequency": "weekdays", "until": until})


def dates(tasks: list[dict]) -> list[str]:
    return [task["date"] for task in tasks]


def add_days(value: str, days: int) -> str:
    """JS: `const date=new Date(value+'T12:00:00'); date.setDate(date.getDate()+days); return dateKey(date);`"""
    return date_key(datetime.strptime(value, "%Y-%m-%d") + timedelta(days=days))


def weekday_index(value: str) -> int:
    """JS `getDay()`：周日=0 … 周六=6。"""
    return (datetime.strptime(value, "%Y-%m-%d").weekday() + 1) % 7


def attributes(task: dict) -> dict:
    return {k: v for k, v in task.items() if k not in ("id", "date", "seriesId", "seriesIndex")}


def saved_fields(task: dict) -> dict:
    """JS: `savedFields(task)`，其中 `?? null` 在 Python 里就是 `.get(...)`。"""
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


def test_weekdays_include_monday_through_friday_and_both_weekday_endpoints():
    """weekdays include Monday through Friday and both weekday endpoints"""
    assert dates(weekdays("2026-10-12", "2026-10-16")) == [
        "2026-10-12",
        "2026-10-13",
        "2026-10-14",
        "2026-10-15",
        "2026-10-16",
    ]
    assert dates(weekdays("2026-10-14", "2026-10-14")) == ["2026-10-14"]


def test_weekdays_skip_weekends_across_weeks_month_ends_leap_days_and_years():
    """weekdays skip weekends across weeks, month ends, leap days and years"""
    cases = [
        ("2026-10-16", "2026-10-20", ["2026-10-16", "2026-10-19", "2026-10-20"]),
        ("2027-01-29", "2027-02-02", ["2027-01-29", "2027-02-01", "2027-02-02"]),
        ("2028-02-25", "2028-03-01", ["2028-02-25", "2028-02-28", "2028-02-29", "2028-03-01"]),
        ("2026-12-31", "2027-01-04", ["2026-12-31", "2027-01-01", "2027-01-04"]),
    ]
    for start, until, expected in cases:
        tasks = weekdays(start, until)
        assert dates(tasks) == expected, f"{start} through {until}"
        assert all(weekday_index(task["date"]) in (1, 2, 3, 4, 5) for task in tasks)


def test_weekend_starts_move_the_first_occurrence_to_monday_without_exceeding_the_end_date():
    """weekend starts move the first occurrence to Monday without exceeding the end date"""
    for start in ("2026-10-17", "2026-10-18"):
        base = clean_task({**WEEKDAY_DRAFT, "date": start})
        base_before = copy.deepcopy(base)
        options = {"frequency": "weekdays", "until": "2026-10-20"}
        options_before = copy.deepcopy(options)
        tasks = make_series(base, options)
        assert dates(tasks) == ["2026-10-19", "2026-10-20"]
        # The first actual occurrence must retain the draft ID
        assert tasks[0]["id"] == base["id"]
        # Moving the first occurrence must not mutate the draft
        assert base["date"] == start
        assert base == base_before
        assert options == options_before
    assert dates(weekdays("2026-10-16", "2026-10-17")) == ["2026-10-16"]
    assert dates(weekdays("2026-10-16", "2026-10-18")) == ["2026-10-16"]


def test_a_range_containing_only_saturday_or_sunday_reports_that_no_weekdays_exist():
    """a range containing only Saturday or Sunday reports that no weekdays exist"""
    for start, until in [("2026-10-17", "2026-10-17"), ("2026-10-18", "2026-10-18"), ("2026-10-17", "2026-10-18")]:
        with pytest.raises(ValueError, match="没有工作日"):
            weekdays(start, until)


def test_weekday_occurrences_have_independent_objects_and_ids_while_retaining_all_task_attributes():
    """weekday occurrences have independent objects and IDs while retaining all task attributes"""
    base = clean_task({**WEEKDAY_DRAFT, "date": "2026-10-17"})
    before = copy.deepcopy(base)
    options = {"frequency": "weekdays", "until": "2026-10-23"}
    options_before = copy.deepcopy(options)
    tasks = make_series(base, options)
    assert len(tasks) == 5
    assert len({id(task) for task in tasks}) == 5
    assert len({task["id"] for task in tasks}) == 5
    assert tasks[0]["id"] == base["id"]
    for task in tasks:
        assert task is not base
        assert attributes(task) == attributes(base)
    assert base == before
    assert options == options_before
    assert options == {"frequency": "weekdays", "until": "2026-10-23"}


def test_editing_or_completing_one_weekday_does_not_modify_the_other_occurrences():
    """editing or completing one weekday does not modify the other occurrences"""
    tasks = weekdays("2026-10-12", "2026-10-16")
    before = copy.deepcopy(tasks)
    tasks[1] = clean_task(
        {**tasks[1], "title": "仅修改星期二", "time": "11:30", "notes": "当天备注"},
        tasks[1],
    )
    tasks[1]["done"] = True
    assert tasks[1]["id"] == before[1]["id"]
    assert tasks[1]["date"] == before[1]["date"]
    assert tasks[1]["time"] == "11:30"
    others = [task for index, task in enumerate(tasks) if index != 1]
    before_others = [task for index, task in enumerate(before) if index != 1]
    assert others == before_others
    assert [task["id"] for task in due_tasks(tasks, local_ms("2026-10-17", "12:00:00"))] == [
        task["id"] for task in before_others
    ]


def test_weekday_reminders_fire_at_separate_thresholds_and_have_no_saturday_or_sunday_occurrence():
    """weekday reminders fire at separate thresholds and have no Saturday or Sunday occurrence"""
    tasks = weekdays("2026-10-16", "2026-10-20")
    for task in tasks:
        threshold = local_ms(task["date"], "08:50:00")
        assert due_tasks(tasks, threshold - 1) == []
        assert [item["id"] for item in due_tasks(tasks, threshold)] == [task["id"]]
        task["notifiedAt"] = threshold
        assert due_tasks(tasks, threshold) == []
        if task["date"] == "2026-10-16":
            for weekend in ("2026-10-17", "2026-10-18"):
                assert due_tasks(tasks, local_ms(weekend, "23:59:00")) == []


def test_backup_restart_preserves_every_weekday_and_its_separate_completion_and_reminder_state():
    """backup restart preserves every weekday and its separate completion and reminder state"""
    tasks = weekdays("2026-10-16", "2026-10-21")
    tasks[0]["done"] = True
    tasks[1]["notifiedAt"] = local_ms("2026-10-19", "08:50:00")
    tasks[2]["notifiedAt"] = local_ms("2026-10-20", "08:50:00")
    tasks[2]["snoozeUntil"] = local_ms("2026-10-20", "09:00:00")
    backup = {"version": 1, "tasks": tasks, "settings": {"sound": False}, "history": []}
    serialized = json.dumps(backup)
    restored = validate_state(json.loads(serialized))
    assert len(restored["tasks"]) == 4
    assert [saved_fields(task) for task in restored["tasks"]] == [saved_fields(task) for task in tasks]
    assert restored["settings"]["sound"] is False
    assert json.dumps(backup) == serialized
    assert due_tasks(restored["tasks"], tasks[2]["snoozeUntil"] - 1) == []
    assert [task["id"] for task in due_tasks(restored["tasks"], tasks[2]["snoozeUntil"])] == [tasks[2]["id"]]
    restored["tasks"][2]["snoozeUntil"] = None
    assert [task["id"] for task in due_tasks(restored["tasks"], local_ms("2026-10-21", "08:50:00"))] == [tasks[3]["id"]]
    assert [saved_fields(task) for task in validate_state(json.loads(json.dumps(restored)))["tasks"]] == [
        saved_fields(task) for task in restored["tasks"]
    ]


def test_weekdays_reject_missing_malformed_impossible_and_reversed_end_dates():
    """weekdays reject missing, malformed, impossible and reversed end dates"""
    invalid_ends = [
        UNDEFINED,
        None,
        False,
        20261016,
        "",
        "2026-10-11",
        "2026-2-13",
        "2026-02-30",
        "2027-02-29",
        "2026-13-01",
        "2026-10-16T12:00:00",
        "invalid",
    ]
    for until in invalid_ends:
        # JS: assert.throws(() => weekdays('2026-10-12', until), `Must reject ${String(until)}`)
        with pytest.raises(ValueError):
            weekdays("2026-10-12", until)


def test_the_10000_limit_counts_actual_weekdays_and_rejects_excess_before_allocating_task_ids():
    """the 10000 limit counts actual weekdays and rejects excess before allocating task IDs"""
    base = clean_task({**WEEKDAY_DRAFT, "date": "2026-10-12"})
    base_before = copy.deepcopy(base)
    # 2000 complete calendar weeks contain exactly 10000 Monday-Friday occurrences.
    last_friday = add_days(base["date"], 1999 * 7 + 4)
    last_sunday = add_days(base["date"], 1999 * 7 + 6)
    tasks = make_series(base, {"frequency": "weekdays", "until": last_sunday})
    assert len(tasks) == 10000
    assert tasks[-1]["date"] == last_friday
    assert len({task["id"] for task in tasks}) == 10000
    # JS 在这之后才 mock crypto.randomUUID —— 所以 10000 项那一次用的是真 UUID。
    with mock.patch("uuid.uuid4") as allocate:
        with pytest.raises(ValueError, match="10000"):
            make_series(base, {"frequency": "weekdays", "until": add_days(last_sunday, 1)})
        # Over-limit generation must reject before materializing tasks
        assert allocate.call_count == 0
    assert base == base_before


def test_weekday_support_preserves_daily_weekends_daily_capacity_and_numeric_weekly_calls():
    """weekday support preserves daily weekends, daily capacity and numeric weekly calls"""
    base = clean_task({**WEEKDAY_DRAFT, "date": "2026-10-16"})
    assert dates(make_series(base, {"frequency": "daily", "until": "2026-10-19"})) == [
        "2026-10-16",
        "2026-10-17",
        "2026-10-18",
        "2026-10-19",
    ]
    with pytest.raises(ValueError, match="10000"):
        make_series(base, {"frequency": "daily", "until": add_days(base["date"], 10000)})
    for count in (1, 4, 8, 12):
        tasks = make_series(base, count)
        assert len(tasks) == count
        assert tasks[0]["date"] == base["date"]
        assert tasks[-1]["date"] == add_days(base["date"], (count - 1) * 7)
