"""v2 regressions: observable persistence, scheduling and permission boundaries."""
import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from nanqi.core import clean_task, initial_state, validate_state
from nanqi.core.categories import clean_category
from nanqi.store import Store, merge_import


def draft(**patch):
    return dict(title="Regression", date="2030-01-01", time="09:00", duration=60,
                reminder=30, category="work", **patch)


def test_category_merge_rejects_21_without_modifying_input():
    state = initial_state()
    state["categories"] = [clean_category(dict(id=f"c{i}", name=f"C{i}")) for i in range(20)]
    incoming = initial_state()
    incoming["categories"] = [clean_category(dict(id="extra", name="Extra"))]
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="20"):
        merge_import(state, incoming)
    assert state == before


def test_failed_write_keeps_memory_and_bytes(tmp_path):
    store = Store(data_dir=str(tmp_path), packaged=False)
    store.change(lambda s: s["tasks"].append(clean_task(draft())))
    before = copy.deepcopy(store.state)
    disk = store.data_file.read_bytes()
    with pytest.raises(ValueError):
        store.change(lambda s: s["tasks"][0].update(category="missing"))
    assert store.state == before
    assert store.data_file.read_bytes() == disk


def test_corrupt_file_is_read_only_until_valid_import(tmp_path):
    path = tmp_path / "plans.json"
    path.write_text("broken", encoding="utf-8")
    store = Store(data_dir=str(tmp_path), packaged=False)
    with pytest.raises(ValueError, match="备份"):
        store.change(lambda s: s["tasks"].append(clean_task(draft())))
    assert path.read_text(encoding="utf-8") == "broken"
    assert list(tmp_path.glob("plans-recovery-*.json"))
    store.restore_backup(initial_state())
    store.change(lambda s: s["tasks"].append(clean_task(draft())))
    assert len(validate_state(json.loads(path.read_text(encoding="utf-8")))["tasks"]) == 1


def test_startup_keeps_past_course(tmp_path):
    state = initial_state()
    state["categories"].append(clean_category(dict(id="course", name="Course")))
    task = clean_task({**draft(), "date": "2020-01-01", "category": "course"}, categories=[c["id"] for c in state["categories"]])
    state["tasks"].append(task)
    (tmp_path / "plans.json").write_text(json.dumps(state), encoding="utf-8")
    assert Store(data_dir=str(tmp_path), packaged=False).state["tasks"][0]["id"] == task["id"]


def test_course_snooze_and_stage_survive_restart(tmp_path):
    from nanqi.reminder_service import ReminderService
    state = initial_state()
    state["categories"].append(clean_category(dict(id="course", name="Course")))
    task = clean_task({**draft(), "category": "course", "time": "14:00"}, categories=[c["id"] for c in state["categories"]])
    state["tasks"].append(task)
    store = Store(data_dir=str(tmp_path), packaged=False)
    store.persist(state)
    service = ReminderService(store)
    assert service.collect(datetime(2030, 1, 1, 13, 30))
    store = Store(data_dir=str(tmp_path), packaged=False)
    service = ReminderService(store)
    assert service.collect(datetime(2030, 1, 1, 13, 31)) == []
    due = int(datetime(2030, 1, 1, 13, 41).timestamp() * 1000)
    store.change(lambda s: s["tasks"][0].update(snoozeUntil=due))
    assert service.collect(datetime(2030, 1, 1, 13, 40)) == []
    assert len(service.collect(datetime(2030, 1, 1, 13, 41))) == 1
    assert service.collect(datetime(2030, 1, 1, 13, 42)) == []


def test_review_creation_rolls_back_as_one_write(tmp_path, monkeypatch):
    from nanqi.bridge import Bridge
    store = Store(data_dir=str(tmp_path), packaged=False)
    bridge = Bridge(store, object())
    monkeypatch.setattr(store, "persist", lambda s: (_ for _ in ()).throw(OSError("disk full")))
    response = json.loads(bridge.invoke("save-task", json.dumps([{**draft(), "kind": "review"}, 1, "2030-01-10"])))
    assert response["ok"] is False
    assert store.state["tasks"] == []


def test_update_channel_and_dynamic_handlers_are_absent(tmp_path):
    from nanqi.bridge import Bridge
    store = Store(data_dir=str(tmp_path), packaged=False)
    assert "update" not in store.snapshot()
    bridge = Bridge(store, object())
    for name in ("check-updates", "download-update", "__dict__", "mail-state", "toggle-task"):
        assert json.loads(bridge.invoke(name, "[]"))["ok"] is False


def test_registered_safe_attachments_only(tmp_path):
    from nanqi.attachments import validate_attachment
    safe = tmp_path / "notes.txt"
    safe.write_text("notes")
    bad = tmp_path / "bad.cmd"
    bad.write_text("do not execute")
    assert validate_attachment(str(safe), [str(safe)]) == safe.resolve()
    for path, registered in [(str(safe), []), (str(bad), [str(bad)]), (r"\\host\share\file.txt", [])]:
        with pytest.raises(ValueError):
            validate_attachment(path, registered)


def test_legacy_check_updates_ignored():
    state = initial_state()
    state["settings"]["checkUpdates"] = True
    assert "checkUpdates" not in validate_state(state)["settings"]


def test_import_auto_category_limit_rolls_back(tmp_path):
    from nanqi.import_service import ImportService
    store = Store(data_dir=str(tmp_path), packaged=False)
    state = initial_state()
    state["categories"] = [clean_category(dict(id=f"c{i}", name=f"C{i}")) for i in range(20)]
    store.persist(state)
    before = store.data_file.read_bytes()
    with pytest.raises(ValueError, match="20"):
        ImportService(store)._create_exam_tasks([])
    assert store.data_file.read_bytes() == before
    assert len(store.state["categories"]) == 20


def test_validation_and_reload_preserve_timestamps(tmp_path):
    store = Store(data_dir=str(tmp_path), packaged=False)
    task = clean_task(draft())
    task["createdAt"], task["updatedAt"] = 1000, 2000
    store.change(lambda s: s["tasks"].append(task))
    reloaded = Store(data_dir=str(tmp_path), packaged=False)
    assert reloaded.state["tasks"][0]["createdAt"] == 1000
    assert reloaded.state["tasks"][0]["updatedAt"] == 2000


def test_snooze_crossing_course_stage_does_not_double_notify(tmp_path):
    from nanqi.reminder_service import ReminderService
    store = Store(data_dir=str(tmp_path), packaged=False)
    state = initial_state()
    state["categories"].append(clean_category(dict(id="course", name="Course")))
    task = clean_task({**draft(), "category":"course", "time":"08:00"}, categories=["course"])
    state["tasks"].append(task)
    store.persist(state)
    service = ReminderService(store)
    assert service.collect(datetime(2030,1,1,7,25))[0]["stage"] == "early"
    due = int(datetime(2030,1,1,7,35).timestamp()*1000)
    store.change(lambda s:s["tasks"][0].update(snoozeUntil=due))
    assert service.collect(datetime(2030,1,1,7,30)) == []
    assert service.collect(datetime(2030,1,1,7,35))[0]["stage"] == "snooze"
    assert service.collect(datetime(2030,1,1,7,36)) == []


def test_source_contains_no_retired_update_network_calls():
    root = Path(__file__).resolve().parents[1]
    assert not (root/"nanqi"/"update_checker.py").exists()
    assert not (root/"assets"/"web"/"updates.js").exists()
    for source in (root/"nanqi").rglob("*.py"):
        assert "raw.githubusercontent.com/OrangeCatzhang" not in source.read_text(encoding="utf-8")
