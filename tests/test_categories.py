"""test_categories.py —— 自定义分类与新任务字段（类型/标签/附件）的测试。

覆盖三层：
  * core：clean_category / normalize_categories / clean_task 的新字段；
  * recurrence：make_series 的每周重复（{"frequency": "weekly", "until": ...}）；
  * IPC：save-category / delete-category 处理器（含删除时的任务改派）。
"""

from __future__ import annotations

import pytest

from nanqi.core import (
    clean_category,
    clean_task,
    initial_state,
    make_series,
    next_category_color,
    normalize_categories,
    validate_state,
)
from nanqi.core.categories import DEFAULT_CATEGORIES, MAX_CATEGORIES
from nanqi.store import Store
from nanqi.ipc.bridge import Bridge


# ------------------------------------------------------------------ core：分类

def test_clean_category_accepts_valid_and_derives_shades():
    c = clean_category({"id": "course", "name": "课程", "color": "#5B8FC9"})
    assert c["id"] == "course"
    assert c["color"] == "#5b8fc9"  # 统一小写
    assert c["bg"].startswith("#") and c["ink"].startswith("#")
    assert len(c["bg"]) == 7 and len(c["ink"]) == 7


def test_clean_category_rejects_bad_id_and_name():
    with pytest.raises(ValueError):
        clean_category({"id": "课程", "name": "x"})  # id 必须小写字母/数字/连字符
    with pytest.raises(ValueError):
        clean_category({"id": "ok", "name": "  "})  # 名称不能为空
    with pytest.raises(ValueError):
        clean_category({"id": "ok", "name": "很" * 21})  # 最长 20 字
    with pytest.raises(ValueError):
        clean_category({"id": "work", "name": "x"}, existing_ids=["work"])  # 查重


def test_clean_category_without_color_picks_from_palette():
    c = clean_category({"id": "x1", "name": "无颜色"})
    assert c["color"] == next_category_color([])


def test_normalize_categories_falls_back_and_skips_bad_items():
    assert [c["id"] for c in normalize_categories(None)] == ["work", "study", "life"]
    cleaned = normalize_categories(
        [
            {"id": "mine", "name": "我的", "color": "#123456"},
            {"id": "BAD ID", "name": "坏条目"},
            {"id": "mine", "name": "重复 id"},
            {"name": "缺 id"},
        ]
    )
    assert [c["id"] for c in cleaned] == ["mine"]
    assert cleaned[0]["color"] == "#123456"
    # 全部坏掉时回退默认三分类
    assert [c["id"] for c in normalize_categories([{"id": "??"}])] == ["work", "study", "life"]


def test_validate_state_roundtrips_custom_categories():
    state = validate_state(
        {
            "version": 1,
            "categories": [{"id": "course", "name": "课程", "color": "#5b8fc9"}],
            "tasks": [
                {
                    "id": "t1",
                    "title": "上课",
                    "date": "2026-10-20",
                    "time": "08:00",
                    "duration": 60,
                    "category": "course",
                    "reminder": 10,
                }
            ],
        }
    )
    assert [c["id"] for c in state["categories"]] == ["course"]
    assert state["tasks"][0]["category"] == "course"


def test_validate_state_rejects_task_with_unknown_category():
    with pytest.raises(ValueError, match="请选择有效分类"):
        validate_state(
            {
                "version": 1,
                "tasks": [
                    {
                        "id": "t1",
                        "title": "x",
                        "date": "2026-10-20",
                        "time": "08:00",
                        "duration": 60,
                        "category": "ghost",
                        "reminder": 10,
                    }
                ],
            }
        )


def test_max_categories_constant_is_twenty():
    assert MAX_CATEGORIES == 20
    assert len(DEFAULT_CATEGORIES) == 3


# ------------------------------------------------------------------ core：新任务字段

def _base_task(**overrides):
    task = {
        "id": "t1",
        "title": "示例",
        "date": "2026-10-20",
        "time": "09:00",
        "duration": 60,
        "category": "work",
        "reminder": 10,
    }
    task.update(overrides)
    return task


def test_clean_task_writes_kind_tags_attachments_only_when_given():
    legacy = clean_task(_base_task())
    assert "kind" not in legacy and "tags" not in legacy and "attachments" not in legacy

    modern = clean_task(
        _base_task(
            kind="review",
            tags=["项目", "英语", "项目"],
            attachments=[{"name": "提纲.pdf", "path": "D:/docs/提纲.pdf"}],
        )
    )
    assert modern["kind"] == "review"
    assert modern["tags"] == ["项目", "英语"]  # 去重
    assert modern["attachments"] == [{"name": "提纲.pdf", "path": "D:/docs/提纲.pdf"}]


def test_clean_task_rejects_bad_kind_and_cleans_tags():
    with pytest.raises(ValueError, match="计划类型不正确"):
        clean_task(_base_task(kind="milestone"))
    with pytest.raises(ValueError):
        clean_task(_base_task(tags=["很" * 21]))  # 单个标签最长 20 字
    with pytest.raises(ValueError):
        clean_task(_base_task(tags=[f"tag{i}" for i in range(11)]))  # 最多 10 个标签（去重后）
    # 附件必须是 {name, path}
    with pytest.raises(ValueError):
        clean_task(_base_task(attachments=[{"name": "x"}]))


# ------------------------------------------------------------------ recurrence：每周重复

def test_make_series_weekly_until():
    task = clean_task(_base_task(date="2026-10-19"))  # 周一
    series = make_series(task, {"frequency": "weekly", "until": "2026-11-09"})
    assert [t["date"] for t in series] == ["2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09"]
    assert len({t["seriesId"] for t in series}) == 1


def test_make_series_weekly_until_before_start_raises():
    task = clean_task(_base_task(date="2026-10-19"))
    with pytest.raises(ValueError, match="重复结束日期不能早于开始日期"):
        make_series(task, {"frequency": "weekly", "until": "2026-10-18"})


# ------------------------------------------------------------------ IPC：分类管理

@pytest.fixture()
def bridge(tmp_path, monkeypatch):
    monkeypatch.delenv("NANQI_DATA_DIR", raising=False)
    monkeypatch.delenv("ORANGE_SCHEDULE_DATA_DIR", raising=False)
    store = Store(data_dir=str(tmp_path / "data"), packaged=False)
    host = type("Host", (), {})()
    return Bridge(store, host)


def _save_task(bridge, **overrides):
    payload = _base_task(**overrides)
    payload.pop("id")
    return bridge._handle_save_task(payload, 1)["value"]


def test_save_category_create_and_rename(bridge):
    state = bridge._handle_save_category({"name": "课程"})["value"]
    created = next(c for c in state["categories"] if c["name"] == "课程")
    assert created["id"].startswith("c")
    assert created["color"].startswith("#")

    state = bridge._handle_save_category(
        {"id": created["id"], "name": "我的课程", "color": "#123456"}
    )["value"]
    renamed = next(c for c in state["categories"] if c["id"] == created["id"])
    assert renamed["name"] == "我的课程"
    assert renamed["color"] == "#123456"


def test_save_category_rejects_unknown_id(bridge):
    with pytest.raises(ValueError, match="已被删除"):
        bridge._handle_save_category({"id": "ghost", "name": "x"})


def test_delete_category_reassigns_tasks_and_respects_minimum(bridge):
    state = bridge._handle_save_category({"name": "课程"})["value"]
    course = next(c for c in state["categories"] if c["name"] == "课程")
    _save_task(bridge, title="上课", category=course["id"])

    state = bridge._handle_delete_category(course["id"])["value"]
    assert all(c["id"] != course["id"] for c in state["categories"])
    assert state["tasks"][0]["category"] == state["categories"][0]["id"]

    # 一路删到只剩一个后，再删就报错
    while len(state["categories"]) > 1:
        state = bridge._handle_delete_category(state["categories"][0]["id"])["value"]
    with pytest.raises(ValueError, match="至少保留一个分类"):
        bridge._handle_delete_category(state["categories"][0]["id"])


def test_delete_category_resets_widget_filter(bridge):
    state = bridge._handle_save_category({"name": "课程"})["value"]
    course = next(c for c in state["categories"] if c["name"] == "课程")
    bridge._handle_widget_settings({"category": course["id"]})
    state = bridge._handle_delete_category(course["id"])["value"]
    assert state["settings"]["widget"]["category"] == "all"


def test_save_task_with_new_fields_roundtrip(bridge):
    state = _save_task(
        bridge, title="复习数学", kind="review", tags=["期中"], attachments=[]
    )
    task = state["tasks"][0]
    assert task["kind"] == "review" and task["tags"] == ["期中"]
    # 落盘后再读回来保持一致
    reloaded = validate_state(bridge.store.state)
    assert reloaded["tasks"][0]["kind"] == "review"


def test_initial_state_has_default_categories():
    assert [c["id"] for c in initial_state()["categories"]] == ["work", "study", "life"]
