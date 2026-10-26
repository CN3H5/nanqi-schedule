"""test_widget_model.py —— 移植自 orange-schedule-main/tests/widget.test.cjs。

每个测试函数的 docstring 里都写着对应的 JS 测试名，便于一一对照。

JS ↔ Python 的对应关系：
    dateKey        -> date_key
    normalizePrefs -> normalize_prefs
    getRange       -> get_range
    selectTasks    -> select_tasks
    validateState  -> validate_state（来自 nanqi.core）

注意：JS 的 `normalizePrefs()` 允许一个参数都不传（input 为 undefined 时退化成默认值）。
Python 的 `normalize_prefs(source)` 目前没有默认值，这个差异由
`test_legacy_settings_receive_usable_widget_defaults` 如实断言出来（见该用例的注释）。
"""

from __future__ import annotations

import copy
from datetime import datetime

from nanqi.core import validate_state
from nanqi.widget_model import DEFAULT_PREFS, date_key, get_range, normalize_prefs, select_tasks

# widget.test.cjs 里的 `defaults` 字面量，原样写出来，这样比较才有意义（不是同义反复）。
JS_DEFAULTS = {
    "enabled": False,
    "mode": "day",
    "anchor": None,
    "category": "all",
    "onTop": False,
    "theme": "sage",
    "showCompleted": True,
    "bounds": None,
}


def local_date(value):
    """JS `localDate(value)` = `new Date(value + 'T12:00:00')`。"""
    return datetime.fromisoformat(value + "T12:00:00")


def ids(tasks):
    return sorted(item["id"] for item in tasks)


def make_task(task_id, date, overrides=None):
    """JS `makeTask(id, date, overrides = {})`。"""
    item = {
        "id": task_id,
        "title": task_id,
        "date": date,
        "time": "09:00",
        "duration": 30,
        "category": "work",
        "reminder": -1,
        "done": False,
    }
    if overrides:
        item.update(overrides)
    return item


def test_widget_uses_the_local_calendar_day_near_midnight():
    """JS: widget uses the local calendar day near midnight"""
    assert date_key(datetime(2026, 9, 28, 0, 5)) == "2026-09-28"
    assert date_key(datetime(2026, 9, 28, 23, 55)) == "2026-09-28"


def test_legacy_settings_receive_usable_widget_defaults():
    """JS: legacy settings receive usable widget defaults"""
    assert DEFAULT_PREFS == JS_DEFAULTS, "nanqi.widget_model.DEFAULT_PREFS must equal the JS defaults literal"
    assert normalize_prefs(None) == JS_DEFAULTS
    assert normalize_prefs({}) == JS_DEFAULTS
    migrated = validate_state({"version": 1, "tasks": [], "settings": {"sound": False}})
    assert migrated["settings"]["sound"] is False
    assert migrated["settings"]["widget"] == JS_DEFAULTS
    # JS: assert.deepEqual(normalizePrefs(), defaults)（不传参数）。
    # 这一句放在最后，只是为了让本用例前面的断言在 Python 签名拒绝「不传参数」时
    # 仍然被求值；断言的强度与 JS 完全相同。
    assert normalize_prefs() == JS_DEFAULTS


def test_widget_preferences_reject_invalid_types_and_unknown_choices():
    """JS: widget preferences reject invalid types and unknown choices"""
    invalid = {
        "enabled": "yes",
        "mode": "month",
        "anchor": "2026-02-30",
        "category": "unknown",
        "onTop": 1,
        "theme": "black",
        "showCompleted": "false",
        "bounds": {"x": 1, "y": 1},
    }
    assert normalize_prefs(invalid) == JS_DEFAULTS
    for value in (False, "week", 3, []):
        assert normalize_prefs(value) == JS_DEFAULTS


def test_widget_anchors_accept_leap_days_and_reject_impossible_or_noncanonical_dates():
    """JS: widget anchors accept leap days and reject impossible or noncanonical dates"""
    assert normalize_prefs({"anchor": "2028-02-29"})["anchor"] == "2028-02-29"
    for anchor in (
        "2026-02-29",
        "2028-02-30",
        "2026-04-31",
        "2026-13-01",
        "2026-00-10",
        "2026-09-00",
        "2026-9-28",
        "2026-09-28T12:00:00",
        "",
        20260928,
    ):
        assert normalize_prefs({"anchor": anchor})["anchor"] is None, str(anchor)


def test_valid_widget_preferences_survive_backup_validation_without_mutating_input():
    """JS: valid widget preferences survive backup validation without mutating input"""
    prefs = {
        "enabled": True,
        "mode": "week",
        "anchor": "2026-12-31",
        "category": "study",
        "onTop": True,
        "theme": "lavender",
        "showCompleted": False,
        "bounds": {"x": 240, "y": 120, "width": 380, "height": 600},
    }
    saved = copy.deepcopy(prefs)
    assert normalize_prefs(prefs) == saved
    assert prefs == saved
    restored = validate_state({"version": 1, "tasks": [], "settings": {"widget": prefs}})
    assert restored["settings"]["widget"] == saved


def test_day_range_includes_its_day_and_excludes_the_next_local_day_across_year_and_leap_boundaries():
    """JS: day range includes its day and excludes the next local day across year and leap boundaries"""
    assert get_range({"mode": "day", "anchor": "2026-12-31"}, local_date("2026-09-28")) == {
        "start": "2026-12-31",
        "end": "2027-01-01",
    }
    assert get_range({"mode": "day", "anchor": "2028-02-29"}) == {
        "start": "2028-02-29",
        "end": "2028-03-01",
    }


def test_week_ranges_run_from_monday_to_the_following_monday_across_a_year():
    """JS: week ranges run from Monday to the following Monday across a year"""
    for anchor in ("2026-12-28", "2026-12-31", "2027-01-03"):
        assert get_range({"mode": "week", "anchor": anchor}) == {
            "start": "2026-12-28",
            "end": "2027-01-04",
        }
    assert get_range({"mode": "week", "anchor": "2027-01-04"}) == {
        "start": "2027-01-04",
        "end": "2027-01-11",
    }


def test_following_today_advances_day_and_week_while_a_pinned_anchor_stays_fixed():
    """JS: following today advances day and week while a pinned anchor stays fixed"""
    assert get_range({"mode": "day", "anchor": None}, local_date("2026-09-30")) == {
        "start": "2026-09-30",
        "end": "2026-10-01",
    }
    assert get_range({"mode": "week", "anchor": None}, local_date("2026-10-04")) == {
        "start": "2026-09-28",
        "end": "2026-10-05",
    }
    assert get_range({"mode": "week", "anchor": None}, local_date("2026-10-05")) == {
        "start": "2026-10-05",
        "end": "2026-10-12",
    }
    assert get_range({"mode": "day", "anchor": "2026-09-28"}, local_date("2026-10-05")) == {
        "start": "2026-09-28",
        "end": "2026-09-29",
    }


def test_task_selection_uses_scheduled_dates_and_splits_pending_and_completed_in_the_selected_range():
    """JS: task selection uses scheduled dates and splits pending and completed in the selected range"""
    tasks = [
        make_task("previous", "2026-12-27"),
        make_task("monday", "2026-12-28"),
        make_task(
            "completed",
            "2026-12-30",
            {"done": True, "updatedAt": int(local_date("2027-01-15").timestamp() * 1000)},
        ),
        make_task("sunday", "2027-01-03", {"category": "life"}),
        make_task("next-week", "2027-01-04"),
    ]
    before = copy.deepcopy(tasks)
    result = select_tasks(tasks, {"mode": "week", "anchor": "2027-01-01"})
    assert result["start"] == "2026-12-28"
    assert result["end"] == "2027-01-04"
    assert ids(result["tasks"]) == ["completed", "monday", "sunday"]
    assert ids(result["pending"]) == ["monday", "sunday"]
    assert ids(result["done"]) == ["completed"]
    assert tasks == before


def test_daily_and_category_filters_affect_both_pending_and_completed_without_including_other_dates():
    """JS: daily and category filters affect both pending and completed without including other dates"""
    tasks = [
        make_task("work-pending", "2026-09-28"),
        make_task("work-done", "2026-09-28", {"done": True}),
        make_task("study-pending", "2026-09-28", {"category": "study"}),
        make_task("study-done", "2026-09-28", {"category": "study", "done": True}),
        make_task("life-pending", "2026-09-28", {"category": "life"}),
        make_task("tomorrow-work", "2026-09-29"),
    ]
    result = select_tasks(tasks, {"mode": "day", "anchor": "2026-09-28", "category": "work"})
    assert ids(result["tasks"]) == ["work-done", "work-pending"]
    assert ids(result["pending"]) == ["work-pending"]
    assert ids(result["done"]) == ["work-done"]
    all_categories = select_tasks(tasks, {"mode": "day", "anchor": "2026-09-28", "category": "all"})
    assert len(all_categories["tasks"]) == 5
    assert len(all_categories["pending"]) == 3
    assert len(all_categories["done"]) == 2


def test_empty_periods_return_empty_lists():
    """JS: empty periods return empty lists"""
    result = select_tasks([make_task("outside", "2026-09-27")], {"mode": "day", "anchor": "2026-09-28"})
    assert result["tasks"] == []
    assert result["pending"] == []
    assert result["done"] == []


def test_hiding_completed_rows_retains_completed_counts_in_the_shared_task_model():
    """JS: hiding completed rows retains completed counts in the shared task model"""
    tasks = [make_task("pending", "2026-09-28"), make_task("done", "2026-09-28", {"done": True})]
    result = select_tasks(tasks, {"anchor": "2026-09-28", "showCompleted": False})
    assert ids(result["tasks"]) == ["done", "pending"]
    assert ids(result["done"]) == ["done"]
