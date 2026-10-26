"""test_data_path.py —— `orange-schedule-main/tests/data-path.test.cjs` 的 pytest 移植。

四个用例逐一对应，包括相对路径覆盖与纯空白覆盖两种边界。
JS 的 `path.resolve` 在 Python 里是 `os.path.abspath`（同样以当前工作目录为基准），
`path.join(os.tmpdir(), ...)` 对应 `os.path.join(tempfile.gettempdir(), ...)`。
"""

from __future__ import annotations

import json
import os
import tempfile

from nanqi.data_path import resolve_data_path
from nanqi.paths import migrate_legacy_data_dir, user_data_dir
from nanqi.store import Store


class _Undefined:
    """JS 的 `undefined`：与 `null`/省略参数不同，用它保留用例的原始数量。"""

    def __repr__(self) -> str:  # pragma: no cover - 只用于断言信息
        return "undefined"

    __str__ = __repr__


UNDEFINED = _Undefined()


def user_directory(name: str) -> str:
    """JS: `path.join(os.tmpdir(), 'weeklight-path-tests', name, 'weeklight')`。"""
    return os.path.join(tempfile.gettempdir(), "weeklight-path-tests", name, "weeklight")


def test_default_data_belongs_to_the_current_user_directory_provided_by_electron():
    """default data belongs to the current user directory provided by Electron"""
    first_user = user_directory("first-user")
    second_user = user_directory("second-user")
    assert resolve_data_path(first_user) == first_user
    assert resolve_data_path(second_user) == second_user
    assert resolve_data_path(first_user) != resolve_data_path(second_user)


def test_an_explicit_absolute_data_override_takes_precedence_and_preserves_spaces_and_unicode():
    """an explicit absolute data override takes precedence and preserves spaces and Unicode"""
    custom = os.path.join(tempfile.gettempdir(), "周光自选目录", "My Plans")
    assert resolve_data_path(user_directory("first-user"), custom) == custom
    assert resolve_data_path(user_directory("second-user"), custom) == custom


def test_an_empty_environment_override_falls_back_to_the_per_user_default():
    """an empty environment override falls back to the per-user default"""
    default_path = user_directory("current-user")
    for override in (UNDEFINED, "", " ", "\t"):
        assert resolve_data_path(default_path, override) == default_path


def test_an_explicit_relative_override_resolves_to_an_absolute_directory_for_electron_setpath():
    """an explicit relative override resolves to a directory usable for Electron setPath"""
    override = os.path.join("_work", "custom-weeklight-data")
    actual = resolve_data_path(user_directory("current-user"), override)
    assert actual == os.path.abspath(override)
    assert os.path.isabs(actual) is True


# ---------------------------------------------------------------------------
# 以下是本版新增的部分（上游没有对应用例）：改名后的数据目录与环境变量兼容。
# 「改名不能丢数据」是硬要求，所以这几条必须被钉住。
# ---------------------------------------------------------------------------


class TestRenamedDataDirectory:
    """`%APPDATA%\\nanqi_schedule` + 从 `%APPDATA%\\weeklight` 迁移。"""

    def test_default_directory_uses_the_new_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        monkeypatch.delenv("NANQI_DATA_DIR", raising=False)
        monkeypatch.delenv("WEEKLIGHT_DATA_DIR", raising=False)
        assert user_data_dir() == tmp_path.resolve() / "nanqi_schedule"

    def test_new_variable_wins_and_old_one_still_works(self, tmp_path, monkeypatch):
        new_dir = tmp_path / "new"
        old_dir = tmp_path / "old"

        monkeypatch.setenv("WEEKLIGHT_DATA_DIR", str(old_dir))
        assert user_data_dir() == old_dir.resolve()

        monkeypatch.setenv("NANQI_DATA_DIR", str(new_dir))
        assert user_data_dir() == new_dir.resolve()

    def test_migration_copies_plans_when_the_new_directory_is_empty(self, tmp_path, monkeypatch):
        legacy = tmp_path / "weeklight"
        target = tmp_path / "nanqi_schedule"
        legacy.mkdir()
        (legacy / "plans.json").write_text('{"version":1,"tasks":[]}', encoding="utf-8")
        (legacy / "plans.json.bak").write_text('{"version":1,"tasks":[]}', encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(tmp_path))

        migrated = migrate_legacy_data_dir(target)

        assert migrated == str(legacy)
        assert (target / "plans.json").read_text(encoding="utf-8") == '{"version":1,"tasks":[]}'
        assert (target / "plans.json.bak").is_file()
        # 复制而不是移动：旧目录必须原样保留，万一新版本有问题还能退回旧版。
        assert (legacy / "plans.json").is_file()

    def test_migration_never_overwrites_existing_data(self, tmp_path, monkeypatch):
        legacy = tmp_path / "weeklight"
        target = tmp_path / "nanqi_schedule"
        legacy.mkdir()
        target.mkdir()
        (legacy / "plans.json").write_text("旧数据", encoding="utf-8")
        (target / "plans.json").write_text("新数据", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(tmp_path))

        assert migrate_legacy_data_dir(target) is None
        assert (target / "plans.json").read_text(encoding="utf-8") == "新数据"

    def test_migration_is_a_no_op_without_legacy_data(self, tmp_path, monkeypatch):
        target = tmp_path / "nanqi_schedule"
        (tmp_path / "weeklight").mkdir()
        monkeypatch.setenv("APPDATA", str(tmp_path))

        assert migrate_legacy_data_dir(target) is None
        assert not (target / "plans.json").exists()

    def test_store_migrates_and_disables_the_inherited_update_check(self, tmp_path, monkeypatch):
        """迁移过来的旧数据会带着「自动检查更新=开」，但那条通道对本产品无意义。"""
        legacy = tmp_path / "weeklight"
        target = tmp_path / "nanqi_schedule"
        legacy.mkdir()
        (legacy / "plans.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "tasks": [],
                    "settings": {"sound": True, "popup": True, "checkUpdates": True},
                    "history": [],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("NANQI_DATA_DIR", str(target))
        monkeypatch.setenv("APPDATA", str(tmp_path))

        store = Store(login=_FakeLogin())

        assert "checkUpdates" not in store.state["settings"]
        assert "橙子日程" in store.migration_note
        assert store.snapshot()["migrationNote"] == store.migration_note


class _FakeLogin:
    """不碰注册表的开机启动桩（迁移测试只关心数据目录）。"""

    def get_open_at_login(self) -> bool:
        return False

    def set_open_at_login(self, enabled: bool) -> bool:
        return bool(enabled)

    def migrate_legacy_once(self) -> dict:
        return {"migrated": False, "reason": "test"}
