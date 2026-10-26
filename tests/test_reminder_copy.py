"""test_reminder_copy.py —— 移植自 orange-schedule-main/tests/reminder-copy.test.cjs。

每个测试函数的 docstring 里都写着对应的 JS 测试名，便于一一对照。

JS ↔ Python 的对应关系：
    normalizeRotation -> normalize_rotation
    getCurrentMessage -> get_current_message
    advanceMessage    -> advance_message（第二个参数是注入的选择函数 choose_index(limit) -> int）
    messages          -> messages()（带缓存，返回元组）
    validateState     -> validate_state（来自 nanqi.core）

说明：
  * JS 的 `clone`（JSON 往返）对应 `copy.deepcopy`。
  * JS 的 `freezeDeep`（Object.freeze）没有 Python 等价物：`freeze_deep()` 原样返回，
    「没有被改动」用前后两份深拷贝比较来断言。
  * 最后一个用例（默认选择函数）没有 JS 对照：JS 默认是 `crypto.randomInt`，
    这里只断言「在范围内、不重复」。
"""

from __future__ import annotations

import copy
import math
from urllib.parse import urlparse

from nanqi.core import validate_state
from nanqi.reminder_copy import advance_message, get_current_message, messages, normalize_rotation

MESSAGES = messages()
CATALOGUE_IDS = [message["id"] for message in MESSAGES]
EMPTY_ROTATION = {"seen": [], "currentId": None}


def sorted_values(values):
    return sorted(values)


def clone(value):
    """JS `clone(value)` = `JSON.parse(JSON.stringify(value))`。"""
    return copy.deepcopy(value)


def freeze_deep(value):
    """JS `freezeDeep(value)`；Python 没有等价物，原样返回。"""
    return value


def test_reminder_catalogue_has_at_least_100_distinct_messages_with_usable_display_fields():
    """JS: reminder catalogue has at least 100 distinct messages with usable display fields"""
    assert len(MESSAGES) >= 100, f"Only {len(MESSAGES)} reminder messages are available"
    assert len(set(CATALOGUE_IDS)) == len(MESSAGES), "Message IDs must be unique"
    assert len({message["text"].strip() for message in MESSAGES}) == len(MESSAGES), (
        "Message text must be unique"
    )
    for message in MESSAGES:
        for key in ("id", "text", "source"):
            assert isinstance(message[key], str), f"{message['id']}: {key} must be text"
            assert message[key].strip(), f"{message['id']}: {key} cannot be empty"
            assert message[key] == message[key].strip(), f"{message['id']}: {key} has outer whitespace"
        assert message.get("url"), f"{message['id']}: every classic needs its original source link"
        assert message["source"] != "南七日程"
        if message.get("url") is not None:
            url = urlparse(message["url"])
            assert url.scheme in ("http", "https"), f"{message['id']}: invalid source link"


def test_missing_and_malformed_legacy_rotation_values_receive_an_empty_rotation():
    """JS: missing and malformed legacy rotation values receive an empty rotation"""
    # JS 的列表以 undefined、null 开头；Python 里两者都是 None。
    for raw in (
        None,
        False,
        True,
        0,
        12,
        "old quote",
        [],
        {},
        {"seen": "old quote"},
        {"seen": None, "currentId": "removed-message"},
    ):
        assert normalize_rotation(raw) == EMPTY_ROTATION


def test_normalization_removes_unknown_and_duplicate_ids_while_preserving_valid_progress():
    """JS: normalization removes unknown and duplicate IDs while preserving valid progress"""
    a = CATALOGUE_IDS[4]
    b = CATALOGUE_IDS[1]
    raw = {
        "seen": [a, "removed-message", b, a, None, 1, {"id": a}, b],
        "currentId": b,
        "oldIndex": 42,
    }
    before = clone(raw)
    assert normalize_rotation(raw) == {"seen": [a, b], "currentId": b}
    assert raw == before
    assert normalize_rotation({"seen": [a], "currentId": "removed-message"}) == {
        "seen": [a],
        "currentId": None,
    }
    assert normalize_rotation({"seen": [a], "currentId": b}) == {"seen": [a, b], "currentId": b}
    assert normalize_rotation({"currentId": a}) == {"seen": [a], "currentId": a}


def test_current_message_survives_a_saved_rotation_and_invalid_current_ids_use_the_first_message():
    """JS: current message survives a saved rotation and invalid current IDs use the first message"""
    assert get_current_message(None) == MESSAGES[0]
    assert get_current_message({"currentId": "removed-message"}) == MESSAGES[0]
    current = MESSAGES[-1]
    raw = freeze_deep({"seen": [current["id"]], "currentId": current["id"]})
    assert get_current_message(raw) == current
    # JS 还断言了不传参数的调用：assert.deepEqual(getCurrentMessage(), messages[0])。
    # 这一句放在最后，只是为了让前面的断言在 Python 签名拒绝「不传参数」时仍然被求值；
    # 断言的强度与 JS 完全相同。
    assert get_current_message() == MESSAGES[0]


def test_one_complete_rotation_visits_every_message_exactly_once():
    """JS: one complete rotation visits every message exactly once"""
    rotation = None
    visited = set()
    for index in range(len(MESSAGES)):

        def picker(limit, index=index):
            assert limit == len(MESSAGES) - index
            return limit // 2

        next_state = advance_message(rotation, picker)
        assert next_state["message"]["id"] not in visited, (
            f"Repeated before the round completed: {next_state['message']['id']}"
        )
        visited.add(next_state["message"]["id"])
        assert next_state["rotation"]["currentId"] == next_state["message"]["id"]
        assert len(next_state["rotation"]["seen"]) == len(visited)
        assert sorted_values(next_state["rotation"]["seen"]) == sorted_values(visited)
        rotation = next_state["rotation"]
    assert sorted_values(visited) == sorted_values(CATALOGUE_IDS)


def test_consecutive_rounds_cover_the_whole_catalogue_without_repeating_at_their_boundary():
    """JS: consecutive rounds cover the whole catalogue without repeating at their boundary"""
    rotation = None
    previous_id = None
    for round_index in range(2):
        visited = set()
        for index in range(len(MESSAGES)):
            # JS 第二轮故意挑剩余列表的另一端。
            choose_index = (
                (lambda limit: 0) if round_index == 0 else (lambda limit: limit - 1)
            )
            next_state = advance_message(rotation, choose_index)
            assert next_state["message"]["id"] != previous_id, (
                "Adjacent reminders must differ, including a new round"
            )
            assert next_state["message"]["id"] not in visited, (
                f"Round {round_index + 1} repeated {next_state['message']['id']}"
            )
            visited.add(next_state["message"]["id"])
            assert len(next_state["rotation"]["seen"]) == index + 1
            rotation = next_state["rotation"]
            previous_id = next_state["message"]["id"]
        assert sorted_values(visited) == sorted_values(CATALOGUE_IDS)


def test_a_completed_round_cannot_immediately_repeat_any_possible_last_message():
    """JS: a completed round cannot immediately repeat any possible last message"""
    for current_id in CATALOGUE_IDS:
        previous = freeze_deep({"seen": list(CATALOGUE_IDS), "currentId": current_id})
        for choose_index in (lambda limit: 0, lambda limit: limit - 1):
            next_state = advance_message(previous, choose_index)
            assert next_state["message"]["id"] != current_id, f"New round repeated {current_id}"
            assert next_state["rotation"] == {
                "seen": [next_state["message"]["id"]],
                "currentId": next_state["message"]["id"],
            }


def test_json_persistence_resumes_the_unused_messages_instead_of_starting_the_round_again():
    """JS: JSON persistence resumes the unused messages instead of starting the round again"""
    choose_index = lambda limit: math.floor(limit * 0.6)  # noqa: E731
    visited = set()
    uninterrupted = None
    before_restart = min(37, len(MESSAGES) - 1)
    for _ in range(before_restart):
        next_state = advance_message(uninterrupted, choose_index)
        uninterrupted = next_state["rotation"]
        visited.add(next_state["message"]["id"])
    restored = normalize_rotation(clone(uninterrupted))
    assert get_current_message(restored) == get_current_message(uninterrupted)
    for _ in range(before_restart, len(MESSAGES)):
        direct_next = advance_message(uninterrupted, choose_index)
        restored_next = advance_message(restored, choose_index)
        assert restored_next == direct_next, "Restoring changed the available messages"
        assert restored_next["message"]["id"] not in visited, (
            "Restart repeated an already used message"
        )
        visited.add(restored_next["message"]["id"])
        uninterrupted = direct_next["rotation"]
        restored = clone(restored_next["rotation"])
    assert sorted_values(visited) == sorted_values(CATALOGUE_IDS)


def test_rotation_functions_do_not_mutate_caller_state_or_catalogue_content():
    """JS: rotation functions do not mutate caller state or catalogue content"""
    raw = freeze_deep(
        {"seen": [CATALOGUE_IDS[0], CATALOGUE_IDS[2]], "currentId": CATALOGUE_IDS[2]}
    )
    before = clone(raw)
    catalogue_before = clone(list(MESSAGES))
    normalize_rotation(raw)
    get_current_message(raw)
    next_state = advance_message(raw, lambda limit: 0)
    assert next_state["rotation"] is not raw
    assert raw == before
    assert list(MESSAGES) == catalogue_before


def test_core_validation_preserves_reminder_progress_through_a_saved_backup():
    """JS: core validation preserves reminder progress through a saved backup"""
    rotation = {
        "seen": [CATALOGUE_IDS[2], CATALOGUE_IDS[7], CATALOGUE_IDS[1]],
        "currentId": CATALOGUE_IDS[1],
    }
    backup = freeze_deep(
        {
            "version": 1,
            "tasks": [],
            "settings": {"sound": False, "popup": False, "reminderRotation": rotation},
            "history": [],
        }
    )
    before = clone(backup)
    restored = validate_state(clone(backup))
    assert restored["settings"]["reminderRotation"] == rotation
    assert restored["settings"]["sound"] is False
    assert restored["settings"]["popup"] is False
    assert validate_state(clone(restored))["settings"]["reminderRotation"] == rotation
    assert backup == before
    assert advance_message(restored["settings"]["reminderRotation"], lambda limit: 0) == advance_message(
        rotation, lambda limit: 0
    )


def test_core_validation_accepts_old_backups_and_cleans_broken_rotation_fields_without_losing_valid_ids():
    """JS: core validation accepts old backups and cleans broken rotation fields without losing valid IDs"""
    legacy = validate_state({"version": 1, "tasks": []})
    assert legacy["settings"]["reminderRotation"] == EMPTY_ROTATION
    for reminder_rotation in (None, False, "previous version", {"seen": {}, "currentId": 9}):
        restored = validate_state(
            {"version": 1, "tasks": [], "settings": {"reminderRotation": reminder_rotation}}
        )
        assert restored["settings"]["reminderRotation"] == EMPTY_ROTATION
    current_id = CATALOGUE_IDS[3]
    reminder_rotation = {
        "seen": [current_id, "deleted", current_id, CATALOGUE_IDS[5]],
        "currentId": current_id,
    }
    restored = validate_state(
        {"version": 1, "tasks": [], "settings": {"sound": False, "reminderRotation": reminder_rotation}}
    )
    assert restored["settings"]["reminderRotation"] == {
        "seen": [current_id, CATALOGUE_IDS[5]],
        "currentId": current_id,
    }
    assert restored["settings"]["sound"] is False


def test_upgrading_the_original_copy_catalogue_keeps_classic_progress_and_discards_retired_ids():
    """JS: upgrading the original-copy catalogue keeps classic progress and discards retired IDs"""
    rotation = normalize_rotation(
        {
            "seen": ["original-001", "classic-001", "original-099", "classic-020"],
            "currentId": "original-099",
        }
    )
    assert rotation == {"seen": ["classic-001", "classic-020"], "currentId": None}
    assert get_current_message(rotation)["id"] == "classic-001"
    next_state = advance_message(rotation, lambda limit: 0)
    assert next_state["message"]["id"] not in rotation["seen"]
    assert next_state["message"]["url"]
    classic_current = normalize_rotation({"seen": ["original-001"], "currentId": "classic-020"})
    assert classic_current == {"seen": ["classic-020"], "currentId": "classic-020"}


def test_default_picker_returns_an_in_range_message_without_repeating_within_a_round():
    """No JS counterpart: the JS default picker is crypto.randomInt; only range/no-repeat is asserted."""
    rotation = {"seen": [CATALOGUE_IDS[0]], "currentId": CATALOGUE_IDS[0]}
    seen_ids = {CATALOGUE_IDS[0]}
    for _ in range(20):
        next_state = advance_message(rotation)
        assert isinstance(next_state["message"]["id"], str)
        assert next_state["message"]["id"] in CATALOGUE_IDS
        assert next_state["message"]["id"] not in seen_ids
        seen_ids.add(next_state["message"]["id"])
        rotation = next_state["rotation"]
    assert len(set(rotation["seen"])) == len(rotation["seen"])
