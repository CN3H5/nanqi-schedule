"""test_login_settings.py —— 移植自 orange-schedule-main/tests/login-settings.test.cjs。

每个测试函数的 docstring 里都写着对应的 JS 测试名，便于一一对照。

JS 注入的是一个 Electron `app` 桩对象（`getLoginItemSettings` / `setLoginItemSettings`）；
Python 版注入的是 `registry` 对象：

    registry.read(path, args) -> {"openAtLogin": bool, "launchItems": [...], "executableWillLaunchAtLogin": bool}
    registry.write(options) -> None

`FakeRegistry` 就是那个桩：`responses` 相当于 JS 的 `Map`（按 path 取结果），
`reads` / `writes` 记录调用，`read_error` / `write_error` 用来复现注册表 API 抛错，
`on_write` 对应 JS 里改写 `setLoginItemSettings` 的那个「写入后刷新 responses」的钩子。

JS ↔ Python 的对应关系：
    createLoginSettings -> create_login_settings
    getOpenAtLogin      -> get_open_at_login
    setOpenAtLogin      -> set_open_at_login
    migrateLegacyOnce   -> migrate_legacy_once
"""

from __future__ import annotations

import copy
import ntpath

import pytest

from nanqi.login_settings import create_login_settings

CURRENT = "C:\\Apps\\Weeklight\\南七日程.exe"
OLD = "C:\\Apps\\Weeklight\\周光.exe"
ITEM_NAME = "local.nanqi.schedule"


def entry(file, enabled=True, overrides=None):
    """JS `entry(file, enabled = true, overrides = {})`。"""
    item = {"name": ITEM_NAME, "path": file, "args": ["--hidden"], "scope": "user", "enabled": enabled}
    if overrides:
        item.update(overrides)
    return item


def settings(open_at_login=False, launch_items=None):
    """JS `settings(openAtLogin = false, launchItems = [])`。"""
    items = list(launch_items or [])
    return {
        "openAtLogin": open_at_login,
        "launchItems": items,
        "executableWillLaunchAtLogin": any(item.get("enabled") for item in items),
    }


class _BooleanObject:
    """JS `new Boolean(false)`：一个对象，绝不是 Python 的 bool。"""

    def __bool__(self):
        return False


class FakeRegistry:
    """替代 Electron `app` 的注册表桩（`mockApp` 里的那个 app 对象）。"""

    def __init__(self, responses=None, is_packaged=True):
        self.responses = responses if responses is not None else {}
        self.is_packaged = is_packaged
        self.reads = []
        self.writes = []
        self.read_error = None
        self.write_error = None
        self.on_write = None

    def read(self, path, args):
        # JS: reads.push(structuredClone(options)); return responses.get(options.path) || settings();
        self.reads.append({"path": path, "args": list(args)})
        if self.read_error is not None:
            raise self.read_error
        found = self.responses.get(path)
        return copy.deepcopy(found) if found else settings()

    def write(self, options):
        # JS: writes.push(structuredClone(options)); 之后由测试自己的钩子刷新 responses。
        self.writes.append(copy.deepcopy(options))
        if self.write_error is not None:
            raise self.write_error
        if self.on_write is not None:
            self.on_write(options)


def mock_app(responses=None, is_packaged=True):
    """JS `mockApp(responses = new Map(), isPackaged = true)`。"""
    return FakeRegistry(responses, is_packaged)


def helper(mock, **overrides):
    """JS `helper(mock, overrides = {})`。"""
    options = {
        "registry": mock,
        "exec_path": CURRENT,
        "platform": "win32",
        "packaged": mock.is_packaged,
    }
    options.update(overrides)
    return create_login_settings(**options)


def test_current_startup_reads_always_compare_the_executable_and_hidden_arguments():
    """JS: current startup reads always compare the executable and --hidden arguments"""
    mock = mock_app({CURRENT: settings(True, [entry(CURRENT)])})
    assert helper(mock)["get_open_at_login"]() is True
    assert mock.reads == [{"path": CURRENT, "args": ["--hidden"]}]
    assert mock.writes == []


def test_an_enabled_same_directory_legacy_entry_migrates_once_without_requiring_an_existing_exe():
    """JS: an enabled same-directory legacy entry migrates once without requiring an existing EXE"""
    mock = mock_app({OLD: settings(True, [entry(OLD)])})
    login = helper(mock)
    assert login["migrate_legacy_once"]() == {"migrated": True, "from": OLD, "to": CURRENT}
    assert mock.reads == [
        {"path": CURRENT, "args": ["--hidden"]},
        {"path": OLD, "args": ["--hidden"]},
    ]
    assert mock.writes == [
        {"openAtLogin": True, "path": CURRENT, "args": ["--hidden"], "name": ITEM_NAME}
    ]
    assert login["migrate_legacy_once"]() == {"migrated": False, "reason": "already-checked"}
    assert len(mock.reads) == 2
    assert len(mock.writes) == 1


def test_missing_removed_and_task_manager_disabled_legacy_startup_entries_stay_disabled():
    """JS: missing, removed and Task Manager-disabled legacy startup entries stay disabled"""
    for legacy in (
        settings(),
        settings(False, [entry(OLD)]),
        settings(True, [entry(OLD, False)]),
        settings(True),
    ):
        mock = mock_app({OLD: legacy})
        assert helper(mock)["migrate_legacy_once"]()["migrated"] is False
        assert mock.writes == []


def test_an_existing_current_command_is_left_unchanged_even_if_task_manager_disabled_it():
    """JS: an existing current command is left unchanged even if Task Manager disabled it"""
    for enabled in (True, False):
        mock = mock_app(
            {
                CURRENT: settings(True, [entry(CURRENT, enabled)]),
                OLD: settings(True, [entry(OLD)]),
            }
        )
        assert helper(mock)["migrate_legacy_once"]() == {
            "migrated": False,
            "reason": "current-configured",
        }
        assert mock.reads == [{"path": CURRENT, "args": ["--hidden"]}]
        assert mock.writes == []


def test_development_builds_and_other_platforms_never_inspect_or_change_startup_settings():
    """JS: development builds and other platforms never inspect or change startup settings"""
    for options in (
        {"packaged": False, "platform": "win32"},
        {"packaged": True, "platform": "linux"},
        {"packaged": True, "platform": "darwin"},
    ):
        mock = mock_app({OLD: settings(True, [entry(OLD)])}, options["packaged"])
        login = helper(mock, platform=options["platform"])
        assert login["get_open_at_login"]() is False
        assert login["migrate_legacy_once"]() == {"migrated": False, "reason": "unsupported"}
        assert mock.reads == []
        assert mock.writes == []


def test_another_installation_directory_is_neither_inspected_nor_migrated():
    """JS: another installation directory is neither inspected nor migrated"""
    elsewhere = "C:\\OtherInstallation\\周光.exe"
    mock = mock_app({elsewhere: settings(True, [entry(elsewhere)])})
    assert helper(mock)["migrate_legacy_once"]()["migrated"] is False
    assert [read["path"] for read in mock.reads] == [CURRENT, OLD]
    assert mock.writes == []
    misleading = mock_app({OLD: settings(True, [entry(elsewhere)])})
    assert helper(misleading)["migrate_legacy_once"]()["migrated"] is False
    assert misleading.writes == []


def test_an_enabled_unrelated_or_machine_wide_entry_cannot_override_the_app_entry_being_disabled():
    """JS: an enabled unrelated or machine-wide entry cannot override the app entry being disabled"""
    for alternative in (
        entry(OLD, True, {"name": "another-startup-entry"}),
        entry(OLD, True, {"scope": "machine"}),
    ):
        mock = mock_app({OLD: settings(True, [entry(OLD, False), alternative])})
        assert helper(mock)["migrate_legacy_once"]()["migrated"] is False
        assert mock.writes == []


def test_spaced_installation_paths_can_obtain_enabled_evidence_using_a_quoted_registry_comparison():
    """JS: spaced installation paths can obtain enabled evidence using a quoted registry comparison"""
    target = "C:\\Program Files\\Weeklight\\南七日程.exe"
    legacy = ntpath.join(ntpath.dirname(target), "周光.exe")
    mock = mock_app(
        {
            legacy: settings(True),
            '"' + legacy + '"': settings(True, [entry(legacy)]),
        }
    )
    assert helper(mock, exec_path=target)["migrate_legacy_once"]()["migrated"] is True
    assert [read["path"] for read in mock.reads] == [target, legacy, '"' + legacy + '"']
    assert mock.writes[0]["path"] == target


def test_a_custom_old_filename_remains_inside_the_same_directory_and_path_traversal_is_rejected():
    """JS: a custom old filename remains inside the same directory and path traversal is rejected"""
    legacy = ntpath.join(ntpath.dirname(CURRENT), "FormerPlanner.exe")
    mock = mock_app({legacy: settings(True, [entry(legacy)])})
    assert (
        helper(mock, legacy_executable_name="FormerPlanner.exe")["migrate_legacy_once"]()["migrated"]
        is True
    )
    assert mock.reads[1]["path"] == legacy
    for invalid in ("..\\周光.exe", "C:\\Other\\周光.exe", "..", "old/current.exe"):
        rejected = mock_app()
        with pytest.raises(ValueError, match="filename in the same"):
            helper(rejected, legacy_executable_name=invalid)["migrate_legacy_once"]()
        assert rejected.reads == []
        assert rejected.writes == []


def test_unchanged_executable_names_do_not_migrate_their_own_startup_registration():
    """JS: unchanged executable names do not migrate their own startup registration"""
    mock = mock_app()
    assert helper(mock, exec_path=OLD)["migrate_legacy_once"]() == {
        "migrated": False,
        "reason": "unchanged-executable",
    }
    assert mock.reads == []
    assert mock.writes == []


def test_registry_api_failures_propagate_for_the_startup_caller_to_catch_and_are_not_retried_in_the_same_launch():
    """JS: registry API failures propagate for the startup caller to catch and are not retried in the same launch"""
    mock = mock_app()
    mock.read_error = RuntimeError("Read failed")
    login = helper(mock)
    with pytest.raises(RuntimeError, match="Read failed"):
        login["migrate_legacy_once"]()
    assert login["migrate_legacy_once"]() == {"migrated": False, "reason": "already-checked"}
    write_failure = mock_app({OLD: settings(True, [entry(OLD)])})
    write_failure.write_error = RuntimeError("Write failed")
    with pytest.raises(RuntimeError, match="Write failed"):
        helper(write_failure)["migrate_legacy_once"]()


def test_setting_startup_writes_the_exact_current_command_and_verifies_enabling_and_disabling():
    """JS: setting startup writes the exact current command and verifies enabling and disabling"""
    responses = {}
    mock = mock_app(responses)

    def verify_write(options):
        # JS：调用原来的 setLoginItemSettings 记一次写入，然后刷新 responses。
        responses[CURRENT] = settings(
            options["openAtLogin"], [entry(CURRENT)] if options["openAtLogin"] else []
        )

    mock.on_write = verify_write
    login = helper(mock)
    assert login["set_open_at_login"](True) is True
    assert login["set_open_at_login"](False) is False
    assert mock.writes == [
        {"openAtLogin": True, "path": CURRENT, "args": ["--hidden"], "name": ITEM_NAME},
        {"openAtLogin": False, "path": CURRENT, "args": ["--hidden"], "name": ITEM_NAME},
    ]
    assert mock.reads == [
        {"path": CURRENT, "args": ["--hidden"]},
        {"path": CURRENT, "args": ["--hidden"]},
    ]


def test_a_silently_ignored_startup_write_is_reported_instead_of_claiming_success():
    """JS: a silently ignored startup write is reported instead of claiming success"""
    for requested in (True, False):
        mock = mock_app({CURRENT: settings(not requested)})
        with pytest.raises(ValueError, match="Windows 未保存.*权限.*安全软件"):
            helper(mock)["set_open_at_login"](requested)
        assert mock.writes == [
            {"openAtLogin": requested, "path": CURRENT, "args": ["--hidden"], "name": ITEM_NAME}
        ]
        assert mock.reads == [{"path": CURRENT, "args": ["--hidden"]}]


def test_startup_writes_reject_non_boolean_values_before_calling_any_registry_api():
    """JS: startup writes reject non-boolean values before calling any registry API"""
    # JS 的列表：undefined, null, 0, 1, '', 'true', 'false', [], {}, new Boolean(false)
    for value in (None, None, 0, 1, "", "true", "false", [], {}, _BooleanObject()):
        mock = mock_app()
        with pytest.raises(TypeError, match="开机自动启动设置必须为布尔值"):
            helper(mock)["set_open_at_login"](value)
        assert mock.writes == []
        assert mock.reads == []


def test_unsupported_startup_writes_reject_without_reading_or_changing_registry_settings():
    """JS: unsupported startup writes reject without reading or changing registry settings"""
    for options in (
        {"packaged": False, "platform": "win32"},
        {"packaged": True, "platform": "linux"},
        {"packaged": True, "platform": "darwin"},
    ):
        mock = mock_app({}, options["packaged"])
        login = helper(mock, platform=options["platform"])
        for requested in (True, False):
            with pytest.raises(ValueError, match="仅支持打包后的 Windows 应用"):
                login["set_open_at_login"](requested)
        assert login["get_open_at_login"]() is False
        assert mock.writes == []
        assert mock.reads == []


def test_startup_verification_read_failures_propagate_instead_of_returning_a_requested_state():
    """JS: startup verification read failures propagate instead of returning a requested state"""
    for requested in (True, False):
        mock = mock_app()
        error = RuntimeError("Registry read failed")
        mock.read_error = error
        login = helper(mock)
        with pytest.raises(RuntimeError) as raise_info:
            login["set_open_at_login"](requested)
        assert raise_info.value is error
        assert mock.writes == [
            {"openAtLogin": requested, "path": CURRENT, "args": ["--hidden"], "name": ITEM_NAME}
        ]
        assert mock.reads == [{"path": CURRENT, "args": ["--hidden"]}]
        with pytest.raises(RuntimeError) as second_raise_info:
            login["get_open_at_login"]()
        assert second_raise_info.value is error


def test_startup_write_failures_propagate_without_trying_to_confirm_success():
    """JS: startup write failures propagate without trying to confirm success"""
    mock = mock_app()
    error = RuntimeError("Registry write failed")
    mock.write_error = error
    with pytest.raises(RuntimeError) as raise_info:
        helper(mock)["set_open_at_login"](True)
    assert raise_info.value is error
    assert len(mock.writes) == 1
    assert mock.reads == []
