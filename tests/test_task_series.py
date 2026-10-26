"""test_task_series.py —— 移植自 orange-schedule-main/tests/task-series.test.cjs。

每个测试函数的 docstring 里都写着对应的 JS 测试名，便于一一对照。

JS ↔ Python 的对应关系（本文件用到的部分）：
    previewTaskAction   -> preview_task_action
    applyTaskAction     -> apply_task_action
    validateSeriesTasks -> validate_series_tasks
    cleanTask           -> clean_task
    makeSeries          -> make_series
    validateState       -> validate_state

说明：
  * JS 的 `assert.throws` 只断言「抛错」，不校验文案，因此这里用
    `pytest.raises(Exception)` 对应，不加 match。
  * JS 的 `structuredClone` 对应 `copy.deepcopy`。
  * JS 的 `Object.freeze` 在 Python 里没有等价物：`freeze()` 只原样返回，
    「没有被改动」由冻结前后各留一份深拷贝来断言（JS 也是这么做的）。
"""

from __future__ import annotations

import copy
import json

import pytest

from nanqi.core import clean_task, make_series, validate_state
from nanqi.task_series import apply_task_action, preview_task_action, validate_series_tasks

STAMP = 1700000000000

DRAFT = {
    "title": "重复阅读",
    "date": "2030-06-10",
    "time": "09:00",
    "duration": 30,
    "category": "study",
    "reminder": 10,
    "priority": "normal",
    "notes": "每天独立记录",
}


def task(task_id, date, patch=None):
    """JS `task(id, date, patch = {})`。patch 覆盖默认字段（与 JS 的展开顺序一致）。"""
    item = dict(DRAFT)
    item.update(
        {
            "id": task_id,
            "date": date,
            "done": False,
            "createdAt": STAMP,
            "updatedAt": STAMP,
            "notifiedAt": None,
            "snoozeUntil": None,
        }
    )
    if patch:
        item.update(patch)
    return item


def series(series_id="series-a", count=4):
    """JS `series(id = 'series-a', count = 4)`。"""
    return [
        task(
            f"{series_id}-{index}",
            f"2030-06-{10 + index:02d}",
            {"seriesId": series_id, "seriesIndex": index},
        )
        for index in range(count)
    ]


def legacy():
    """JS `legacy()`：4 条没有分组信息、只有创建时间证据的旧事项。"""
    return [task(f"legacy-{index}", f"2030-06-{10 + index:02d}") for index in range(4)]


def state_of(tasks):
    """JS `stateOf(tasks)`。"""
    history = []
    for index, item in enumerate(tasks):
        history.append(
            {"id": f"history-{item['id']}", "taskId": item["id"], "at": STAMP + index, "dismissed": False}
        )
        history.append(
            {
                "id": f"old-history-{item['id']}",
                "taskId": item["id"],
                "at": STAMP - 100 + index,
                "dismissed": True,
            }
        )
    return {
        "version": 1,
        "tasks": tasks,
        "settings": {"sound": False, "popup": False},
        "history": history,
    }


def ids(items):
    return [item["id"] for item in items]


def freeze(value):
    """JS `freeze()`（Object.freeze 递归冻结）；Python 没有等价物，原样返回。"""
    return value


def view(item):
    """JS `view(item)`：预览里展示给用户的五个字段。"""
    return {
        "id": item["id"],
        "title": item["title"],
        "date": item["date"],
        "time": item["time"],
        "done": item["done"],
    }


def apply_following(state, task_id, action):
    """JS `applyFollowing(state, id, action)`。"""
    preview = preview_task_action(state["tasks"], task_id, action)
    apply_task_action(
        state, {"id": task_id, "action": action, "scope": "following", "token": preview["token"]}
    )
    return preview


def reject_without_mutation(state, request):
    """JS `rejectWithoutMutation(state, request)`。

    先深拷贝，断言操作抛错，再断言状态与拷贝逐字节一致——被拒绝的操作
    不允许部分修改 tasks / history / settings。
    """
    before = copy.deepcopy(state)
    with pytest.raises(Exception):
        apply_task_action(state, request)
    assert state == before, "Rejected operations must not partially change tasks, history or settings"


def test_series_validation_allows_legacy_single_tasks_and_gaps_in_immutable_occurrence_indices():
    """JS: series validation allows legacy single tasks and gaps in immutable occurrence indices"""
    tasks = [task("plain", "2030-06-10")]
    tasks.extend(item for item in series() if item["seriesIndex"] != 1)
    tasks.append(task("last", "2030-07-01", {"seriesId": "series-a", "seriesIndex": 9999}))
    before = copy.deepcopy(tasks)
    validate_series_tasks(freeze(tasks))
    assert tasks == before
    validate_series_tasks([*series("group-a", 2), *series("group-b", 2)])


def test_series_validation_rejects_malformed_metadata_pairs_ids_and_occurrence_indices():
    """JS: series validation rejects malformed metadata pairs, IDs and occurrence indices"""
    invalid = [
        {"seriesId": "group"},
        {"seriesIndex": 0},
        {"seriesId": None, "seriesIndex": 0},
        {"seriesId": "group", "seriesIndex": None},
        {"seriesId": "", "seriesIndex": 0},
        {"seriesId": 12, "seriesIndex": 0},
        {"seriesId": "bad/id", "seriesIndex": 0},
        {"seriesId": "contains space", "seriesIndex": 0},
        {"seriesId": "x" * 81, "seriesIndex": 0},
        *[
            {"seriesId": "group", "seriesIndex": index}
            for index in (-1, 10000, 0.5, "0", float("nan"), float("inf"))
        ],
    ]
    for metadata in invalid:
        with pytest.raises(Exception):
            validate_series_tasks([task("bad", DRAFT["date"], metadata)])
    with pytest.raises(Exception):
        validate_series_tasks(
            [
                task("one", DRAFT["date"], {"seriesId": "group", "seriesIndex": 2}),
                task("two", "2030-06-11", {"seriesId": "group", "seriesIndex": 2}),
            ]
        )


def test_new_daily_weekday_and_weekly_batches_receive_separate_identities_and_sequential_indices():
    """JS: new daily, weekday and weekly batches receive separate identities and sequential indices"""
    configurations = [
        4,
        8,
        12,
        {"frequency": "daily", "until": "2030-06-12"},
        {"frequency": "weekdays", "until": "2030-06-14"},
        {"frequency": "daily", "until": DRAFT["date"]},
    ]
    group_ids = set()
    for repeat in configurations:
        original = freeze(clean_task(dict(DRAFT)))
        tasks = make_series(original, repeat)
        assert isinstance(tasks[0].get("seriesId"), str) and len(tasks[0]["seriesId"]) > 0
        assert tasks[0]["seriesId"] not in group_ids, "Separate creations must never share a series identity"
        group_ids.add(tasks[0]["seriesId"])
        assert len({item["seriesId"] for item in tasks}) == 1
        assert [item["seriesIndex"] for item in tasks] == list(range(len(tasks)))
        assert len({item["id"] for item in tasks}) == len(tasks)
        assert tasks[0]["id"] == original["id"]
        assert "seriesId" not in original
        validate_series_tasks(tasks)

    single = make_series(clean_task(dict(DRAFT)), 1)[0]
    assert "seriesId" not in single
    assert "seriesIndex" not in single

    weekend_base = clean_task({**DRAFT, "date": "2030-06-15"})
    weekend_tasks = make_series(weekend_base, {"frequency": "weekdays", "until": "2030-06-18"})
    assert weekend_tasks[0]["date"] == "2030-06-17"
    assert weekend_tasks[0]["id"] == weekend_base["id"]
    assert [item["seriesIndex"] for item in weekend_tasks] == [0, 1]


def test_ordinary_task_input_cannot_forge_a_series_and_edits_preserve_trusted_series_identity():
    """JS: ordinary task input cannot forge a series and edits preserve trusted series identity"""
    fresh = clean_task({**DRAFT, "seriesId": "forged-group", "seriesIndex": 9000})
    assert "seriesId" not in fresh
    assert "seriesIndex" not in fresh

    original = series()[2]
    edited = clean_task(
        {
            **original,
            "title": "改名后的事项",
            "date": "2030-05-01",
            "time": "11:00",
            "seriesId": "forged-group",
            "seriesIndex": 0,
        },
        original,
    )
    assert edited["seriesId"] == original["seriesId"]
    assert edited["seriesIndex"] == 2
    assert edited["date"] == "2030-05-01"

    without_input_metadata = {**original, "title": "不发送隐藏字段"}
    del without_input_metadata["seriesId"]
    del without_input_metadata["seriesIndex"]
    saved = clean_task(without_input_metadata, original)
    assert saved["seriesId"] == original["seriesId"]
    assert saved["seriesIndex"] == 2


def test_series_preview_follows_original_indices_after_moves_and_ignores_identically_named_other_groups():
    """JS: series preview follows original indices after moves and ignores identically named other groups"""
    a = series()
    a[0]["date"] = "2030-12-31"
    a[1]["date"] = "2030-08-01"
    a[2]["date"] = "2030-05-01"
    a[3]["title"] = "同组已经改名"
    tasks = [a[3], *series("series-b"), a[0], task("same-name-single", DRAFT["date"]), a[2], a[1]]
    before = copy.deepcopy(tasks)
    preview = preview_task_action(freeze(tasks), a[1]["id"], "complete")
    assert preview["source"] == "series"
    assert preview["task"] == view(a[1])
    assert preview["items"] == [view(item) for item in (a[1], a[2], a[3])]
    assert preview["followingCount"] == 3
    assert isinstance(preview["token"], str) and len(preview["token"]) > 0
    assert tasks == before


def test_single_scope_changes_exactly_the_selected_task_even_when_a_following_group_exists():
    """JS: single scope changes exactly the selected task even when a following group exists"""
    for action in ("complete", "reopen", "delete"):
        state = state_of(series())
        state["tasks"][1]["done"] = action == "reopen"
        selected = state["tasks"][1]["id"]
        before = copy.deepcopy(state)
        token = None
        if action == "delete":
            token = preview_task_action(state["tasks"], state["tasks"][1]["id"], action)["singleToken"]
        apply_task_action(state, {"id": selected, "action": action, "scope": "single", "token": token})
        assert [item for item in state["tasks"] if item["id"] != selected] == [
            item for item in before["tasks"] if item["id"] != selected
        ]
        assert [entry for entry in state["history"] if entry["taskId"] != selected] == [
            entry for entry in before["history"] if entry["taskId"] != selected
        ]
        if action == "delete":
            assert not any(item["id"] == selected for item in state["tasks"])
        else:
            assert next(item for item in state["tasks"] if item["id"] == selected)["done"] == (
                action == "complete"
            )


def test_following_completion_is_idempotent_for_mixed_done_states_and_clears_every_affected_reminder():
    """JS: following completion is idempotent for mixed done states and clears every affected reminder"""
    tasks = [*series(), *series("series-b", 2)]
    tasks[2]["done"] = True
    for item in tasks:
        item["snoozeUntil"] = STAMP + 100000
    state = state_of(tasks)
    before = copy.deepcopy(state)
    selected = tasks[1]["id"]
    affected = {item["id"] for item in tasks[1:4]}
    apply_following(state, selected, "complete")
    assert all(item["done"] and item["snoozeUntil"] is None for item in state["tasks"] if item["id"] in affected)
    assert all(entry["dismissed"] for entry in state["history"] if entry["taskId"] in affected)
    assert [item for item in state["tasks"] if item["id"] not in affected] == [
        item for item in before["tasks"] if item["id"] not in affected
    ]
    assert [entry for entry in state["history"] if entry["taskId"] not in affected] == [
        entry for entry in before["history"] if entry["taskId"] not in affected
    ]
    apply_following(state, selected, "complete")
    assert all(item["done"] for item in state["tasks"] if item["id"] in affected)
    apply_following(state, selected, "reopen")
    assert all(not item["done"] for item in state["tasks"] if item["id"] in affected)
    assert [item for item in state["tasks"] if item["id"] not in affected] == [
        item for item in before["tasks"] if item["id"] not in affected
    ]


def test_following_deletion_respects_immutable_indices_and_removes_only_matching_task_history():
    """JS: following deletion respects immutable indices and removes only matching task history"""
    a = [item for item in series("series-a", 5) if item["seriesIndex"] != 3]
    a[0]["date"] = "2031-01-01"
    a[2]["date"] = "2030-05-01"
    state = state_of([*a, *series("series-b", 2)])
    deleted = {a[2]["id"], a[3]["id"]}
    before = copy.deepcopy(state)
    apply_following(state, a[2]["id"], "delete")
    assert state["tasks"] == [item for item in before["tasks"] if item["id"] not in deleted]
    assert state["history"] == [entry for entry in before["history"] if entry["taskId"] not in deleted]
    assert [item["seriesIndex"] for item in state["tasks"] if item["seriesId"] == "series-a"] == [0, 1]
    assert state["settings"] == before["settings"]


def test_legacy_candidate_previews_require_exact_creation_evidence_and_never_establish_groups_automatically():
    """JS: legacy candidate previews require exact creation evidence and never establish groups automatically"""
    candidates = legacy()
    patches = [
        {"createdAt": STAMP + 1},
        {"title": "另一标题"},
        {"time": "10:00"},
        {"duration": 60},
        {"category": "work"},
        {"reminder": 30},
        {"priority": "high"},
        {"notes": "另一备注"},
        {"seriesId": "already-linked", "seriesIndex": 0},
    ]
    mismatches = [
        task(f"different-{index}", "2030-06-20", patch) for index, patch in enumerate(patches)
    ]
    tasks = [candidates[3], *mismatches, candidates[0], candidates[2], candidates[1]]
    before = copy.deepcopy(tasks)
    preview = preview_task_action(freeze(tasks), candidates[1]["id"], "delete")
    assert preview["source"] == "legacy"
    assert preview["followingCount"] == 3
    assert ids(preview["items"]) == [candidates[1]["id"], candidates[2]["id"], candidates[3]["id"]]
    assert tasks == before
    last = preview_task_action(tasks, candidates[3]["id"], "complete")
    assert last["source"] == "legacy", "The last member still belongs to a candidate group of at least two"
    assert last["followingCount"] == 1


def test_legacy_following_requires_confirmation_acts_only_on_the_confirmed_suffix_and_leaves_metadata_absent():
    """JS: legacy following requires confirmation, acts only on the confirmed suffix and leaves metadata absent"""
    candidates = legacy()
    unrelated = task("unrelated", "2030-06-14", {"createdAt": STAMP + 1})
    state = state_of([*candidates, unrelated])
    reject_without_mutation(state, {"id": candidates[1]["id"], "action": "complete", "scope": "following"})
    before = copy.deepcopy(state)
    apply_following(state, candidates[1]["id"], "complete")
    assert state["tasks"][0]["done"] is False
    assert all(item["done"] for item in state["tasks"][1:4])
    assert state["tasks"][4] == before["tasks"][4]
    assert all("seriesId" not in item and "seriesIndex" not in item for item in state["tasks"])
    after_complete = copy.deepcopy(state)
    apply_following(state, candidates[2]["id"], "delete")
    removed = {candidates[2]["id"], candidates[3]["id"]}
    assert state["tasks"] == [item for item in after_complete["tasks"] if item["id"] not in removed]
    assert state["history"] == [entry for entry in after_complete["history"] if entry["taskId"] not in removed]


def test_same_titles_without_valid_matching_timestamps_remain_unrelated_single_tasks():
    """JS: same titles without valid matching timestamps remain unrelated single tasks"""
    single = task("single", DRAFT["date"])
    other = task("other", "2030-06-11", {"createdAt": STAMP + 1})
    assert preview_task_action([single, other], single["id"], "complete")["source"] == "single"
    # JS 的列表是 [undefined, 0, -1, NaN, Infinity, String(stamp)]：undefined/null 在 Python 里都是 None。
    for created_at in (None, 0, -1, float("nan"), float("inf"), str(STAMP)):
        tasks = [
            task("one", DRAFT["date"], {"createdAt": created_at}),
            task("two", "2030-06-11", {"createdAt": created_at}),
        ]
        preview = preview_task_action(tasks, "one", "complete")
        assert preview["source"] == "single"
        assert preview["followingCount"] == 1
        assert ids(preview["items"]) == ["one"]


def test_following_refuses_missing_tampered_and_stale_preview_tokens_without_partial_writes():
    """JS: following refuses missing, tampered and stale preview tokens without partial writes"""
    original = state_of(series())
    task_id = original["tasks"][1]["id"]
    preview = preview_task_action(original["tasks"], task_id, "complete")
    # JS 逐个试 undefined、null、'' 和篡改过的令牌；undefined/null 在 Python 里都是 None。
    for token in (None, None, "", "tampered-" + preview["token"]):
        reject_without_mutation(
            copy.deepcopy(original),
            {"id": task_id, "action": "complete", "scope": "following", "token": token},
        )

    def append_following(state):
        state["tasks"].append(
            task("new-following", "2030-06-20", {"seriesId": "series-a", "seriesIndex": 4})
        )

    def drop_last_occurrence(state):
        state["tasks"] = [item for item in state["tasks"] if item["id"] != "series-a-3"]

    def rename_following(state):
        state["tasks"][2]["title"] = "确认后被修改"

    def complete_following(state):
        state["tasks"][2]["done"] = True

    for mutate in (append_following, drop_last_occurrence, rename_following, complete_following):
        state = copy.deepcopy(original)
        mutate(state)
        reject_without_mutation(
            state,
            {"id": task_id, "action": "complete", "scope": "following", "token": preview["token"]},
        )


def test_a_confirmation_token_is_bound_to_its_action_and_selected_occurrence():
    """JS: a confirmation token is bound to its action and selected occurrence"""
    state = state_of(series())
    token = preview_task_action(state["tasks"], "series-a-1", "complete")["token"]
    reject_without_mutation(
        state, {"id": "series-a-1", "action": "delete", "scope": "following", "token": token}
    )
    reject_without_mutation(
        state, {"id": "series-a-2", "action": "complete", "scope": "following", "token": token}
    )


def test_unknown_action_scope_and_task_ids_are_rejected_rather_than_broadening_an_operation():
    """JS: unknown action, scope and task IDs are rejected rather than broadening an operation"""
    state = state_of(series())
    with pytest.raises(Exception):
        preview_task_action(state["tasks"], "missing", "complete")
    with pytest.raises(Exception):
        preview_task_action(state["tasks"], state["tasks"][0]["id"], "archive")
    for request in (
        {"id": "missing", "action": "delete", "scope": "single"},
        {"id": "series-a-0", "action": "archive", "scope": "single"},
        {"id": "series-a-0", "action": "complete", "scope": "everything"},
        {"id": "series-a-0", "action": "complete", "scope": ""},
        {"id": "series-a-0", "action": "delete", "scope": "following", "token": "not-confirmed"},
    ):
        reject_without_mutation(state, request)


def test_backup_restart_preserves_series_identity_and_rejects_malformed_or_conflicting_metadata():
    """JS: backup restart preserves series identity and rejects malformed or conflicting metadata"""
    original = state_of(
        [
            *[item for item in series() if item["seriesIndex"] != 1],
            task("old-single", "2030-06-20"),
        ]
    )
    original["tasks"][1]["date"] = "2030-05-01"
    original["tasks"][1]["done"] = True
    restored = validate_state(json.loads(json.dumps(original)))

    def fields(items):
        return [
            [item["id"], item.get("seriesId"), item.get("seriesIndex"), item["date"], item["done"]]
            for item in items
        ]

    assert fields(restored["tasks"]) == fields(original["tasks"])
    assert ids(preview_task_action(restored["tasks"], "series-a-2", "delete")["items"]) == [
        "series-a-2",
        "series-a-3",
    ]
    for patch in (
        {"seriesId": "group"},
        {"seriesIndex": 0},
        {"seriesId": "group", "seriesIndex": -1},
        {"seriesId": "group", "seriesIndex": "0"},
    ):
        with pytest.raises(Exception):
            validate_state({"version": 1, "tasks": [task("bad-backup", DRAFT["date"], patch)]})
    with pytest.raises(Exception):
        validate_state(
            {
                "version": 1,
                "tasks": [
                    task("one", DRAFT["date"], {"seriesId": "same", "seriesIndex": 1}),
                    task("two", "2030-06-11", {"seriesId": "same", "seriesIndex": 1}),
                ],
            }
        )


def test_backups_without_real_creation_times_cannot_fabricate_a_legacy_batch():
    """JS: backups without real creation times cannot fabricate a legacy batch"""
    base = {
        "title": "旧记录",
        "date": "2027-01-04",
        "time": "09:00",
        "duration": 30,
        "category": "work",
        "reminder": -1,
    }
    omitted = object()  # 代表 JS 的 `createdAt: undefined`（字段存在但值为 undefined）

    def with_created_at(task_id, date, created_at):
        item = {**base, "id": task_id, "date": date}
        if created_at is not omitted:
            item["createdAt"] = created_at
        return item

    for created_at in (omitted, None, 0, -1, "123", float("nan"), float("inf")):
        restored = validate_state(
            {
                "version": 1,
                "tasks": [
                    with_created_at("unknown-a", "2027-01-04", created_at),
                    with_created_at("unknown-b", "2027-01-05", created_at),
                ],
            }
        )
        assert all(item["createdAt"] == 0 for item in restored["tasks"])
        assert preview_task_action(restored["tasks"], "unknown-a", "delete")["source"] == "single"
        edited = clean_task({**restored["tasks"][0], "title": "编辑后"}, restored["tasks"][0])
        assert edited["createdAt"] == 0, "Editing must keep the creation time unknown"
    assert clean_task(base)["createdAt"] > 0, "Only a newly created task receives the current creation time"


def test_single_deletion_rejects_missing_or_stale_selected_task_confirmation_without_changing_data():
    """JS: single deletion rejects missing or stale selected-task confirmation without changing data"""
    state = state_of(series())
    task_id = state["tasks"][1]["id"]
    preview = preview_task_action(state["tasks"], task_id, "delete")
    for token in (None, "invalid", preview["token"]):
        reject_without_mutation(
            state, {"id": task_id, "action": "delete", "scope": "single", "token": token}
        )
    state["tasks"][1]["title"] = "确认后改名"
    reject_without_mutation(
        state,
        {"id": task_id, "action": "delete", "scope": "single", "token": preview["singleToken"]},
    )


def test_single_deletion_remains_valid_when_only_other_following_tasks_change():
    """JS: single deletion remains valid when only other following tasks change"""
    state = state_of(series())
    task_id = state["tasks"][1]["id"]
    preview = preview_task_action(state["tasks"], task_id, "delete")
    state["tasks"][2]["title"] = "后续事项改名"
    state["tasks"].append(task("later", "2030-06-20", {"seriesId": "series-a", "seriesIndex": 4}))
    before = copy.deepcopy(state)
    apply_task_action(
        state, {"id": task_id, "action": "delete", "scope": "single", "token": preview["singleToken"]}
    )
    assert state["tasks"] == [item for item in before["tasks"] if item["id"] != task_id]
    assert state["history"] == [entry for entry in before["history"] if entry["taskId"] != task_id]
