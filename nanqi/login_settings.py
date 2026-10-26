"""login_settings.py —— 开机自动启动（移植自 src/login-settings.cjs）。

【原版在做什么】
Electron 的 `app.getLoginItemSettings/setLoginItemSettings` 在 Windows 上对应
`HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` 里的一条命令，并且
「有没有被任务管理器禁用」要看
`...\\Explorer\\StartupApproved\\Run` 里同名值的第一个字节。
Python 版用 `winreg` 直接实现这两件事，并保留原来全部的判断顺序与中文提示：

  * 只有**打包后的 Windows 应用**才允许读写（源码运行时勾选框是灰的）；
  * 写入后必须回读确认，Windows 没保存就报错，而不是假装成功；
  * 从旧版「周光.exe」迁移时，必须拿到「当前命令 + 已启用」两项证据，
    缺一项就什么都不做；同目录之外的启动项一概不碰。

【为什么把注册表抽成可注入对象】
原版的单元测试是给 `app` 注入桩对象来覆盖各种注册表状态。这里保留同样的形状
（`read(path, args)` / `write(options)`），所以那套边界用例可以原样移植过来，
不必真的去改这台机器的启动项。
"""

from __future__ import annotations

import os
import re
import sys

__all__ = [
    "create_login_settings",
    "WindowsRunRegistry",
    "LOGIN_ARGS",
    "LOGIN_ITEM_NAME",
]

LOGIN_ARGS = ["--hidden"]
LOGIN_ITEM_NAME = "local.nanqi.schedule"
LEGACY_EXECUTABLE_NAME = "周光.exe"

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"


def _normalize(path_text: str) -> str:
    """Windows 路径比较用的规范化：统一分隔符与大小写。"""
    return os.path.normcase(os.path.normpath(str(path_text)))


def _same_path(left, right) -> bool:
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and _normalize(left) == _normalize(right)
    )


def quote_windows_argument(value: str) -> str:
    r"""按 Windows 命令行规则加引号：路径里的空格与结尾反斜杠都要处理。

    与 Electron 的 `quoteWindowsArgument` 规则一致：引号前的连续反斜杠加倍，
    结尾的连续反斜杠也加倍（否则 `"C:\dir\"` 会把引号转义掉）。
    """
    escaped = re.sub(r'(\\*)"', lambda match: match.group(1) * 2 + '\\"', value)
    escaped = re.sub(r"(\\+)$", lambda match: match.group(1) * 2, escaped)
    return '"' + escaped + '"'


def build_command(path_text: str, args) -> str:
    return " ".join([quote_windows_argument(path_text)] + list(args or []))


def _split_command(command: str):
    """把一条启动命令拆成 (可执行文件路径, 参数列表)。"""
    text = command.strip()
    if not text:
        return "", []
    if text.startswith('"'):
        end = text.find('"', 1)
        if end == -1:
            return text.strip('"'), []
        return text[1:end], text[end + 1 :].split()
    head, _, tail = text.partition(" ")
    return head, tail.split()


class WindowsRunRegistry:
    """真实的注册表实现；接口与 Electron 的 loginItemSettings 对齐。"""

    def __init__(self, login_item_name: str = LOGIN_ITEM_NAME) -> None:
        self._name = login_item_name

    # ------------------------------------------------------------ 读

    def read(self, path: str, args) -> dict:
        import winreg

        entries: dict[str, str] = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    entries[name] = str(value)
                    index += 1
        except FileNotFoundError:
            entries = {}
        except OSError:
            raise

        approved = self._read_approved()
        launch_items = []
        for name, command in entries.items():
            executable, item_args = _split_command(command)
            launch_items.append(
                {
                    "name": name,
                    "path": executable,
                    "args": item_args,
                    "scope": "user",
                    "enabled": approved.get(name, True),
                }
            )

        own = next((item for item in launch_items if item["name"] == self._name), None)
        open_at_login = bool(
            own
            and _same_path(own["path"], path)
            and list(own["args"]) == list(args or [])
            and own["enabled"]
        )
        return {
            "openAtLogin": open_at_login,
            "launchItems": launch_items,
            "executableWillLaunchAtLogin": any(item["enabled"] for item in launch_items),
        }

    def _read_approved(self) -> dict:
        import winreg

        result: dict[str, bool] = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _APPROVED_KEY) as key:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    # 12 字节的二进制值：首字节偶数表示启用，奇数表示被禁用。
                    if isinstance(value, (bytes, bytearray)) and value:
                        result[name] = value[0] % 2 == 0
                    else:
                        result[name] = True
                    index += 1
        except OSError:
            return {}
        return result

    # ------------------------------------------------------------ 写

    def write(self, options: dict) -> None:
        import winreg

        enabled = bool(options.get("openAtLogin"))
        path_text = options.get("path") or ""
        args = options.get("args") or []
        name = options.get("name") or self._name
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, build_command(path_text, args))
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        # 同步清理/恢复「任务管理器 → 启动」的启用状态，否则禁用过的条目重新勾选也不会生效。
        try:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, _APPROVED_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                if enabled:
                    winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, bytes([2] + [0] * 11))
                else:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
        except OSError:
            # StartupApproved 不可写不影响主流程：Run 键已经写成功了。
            pass


def create_login_settings(
    *,
    registry=None,
    exec_path: str | None = None,
    platform: str | None = None,
    packaged: bool | None = None,
    legacy_executable_name: str = LEGACY_EXECUTABLE_NAME,
    login_item_name: str = LOGIN_ITEM_NAME,
):
    """构建开机启动设置对象（`get_open_at_login` / `set_open_at_login` / `migrate_legacy_once`）。"""
    system = platform or ("win32" if sys.platform == "win32" else sys.platform)
    frozen = packaged
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    executable = exec_path or os.path.abspath(sys.executable)
    backend = registry or WindowsRunRegistry(login_item_name)
    migration_checked = False

    def supported() -> bool:
        return system == "win32" and frozen is True

    def read(target: str) -> dict:
        return backend.read(target, list(LOGIN_ARGS))

    def matching_item(settings: dict, target: str):
        items = settings.get("launchItems")
        if not isinstance(items, list):
            return None
        for item in items:
            if (
                isinstance(item, dict)
                and item.get("name") == login_item_name
                and item.get("scope") == "user"
                and _same_path(item.get("path"), target)
            ):
                return item
        return None

    def get_open_at_login() -> bool:
        if not supported():
            return False
        return read(executable).get("openAtLogin") is True

    def set_open_at_login(enabled) -> bool:
        if not isinstance(enabled, bool):
            raise TypeError("开机自动启动设置必须为布尔值")
        if not supported():
            raise ValueError("开机自动启动仅支持打包后的 Windows 应用")
        backend.write(
            {"openAtLogin": enabled, "path": executable, "args": list(LOGIN_ARGS), "name": login_item_name}
        )
        saved = get_open_at_login()
        if saved is not enabled:
            raise ValueError(
                "Windows 未保存开机自动启动设置。请检查当前账户权限或安全软件是否拦截后重试。"
            )
        return saved

    def migrate_legacy_once() -> dict:
        nonlocal migration_checked
        if not supported():
            return {"migrated": False, "reason": "unsupported"}
        if migration_checked:
            return {"migrated": False, "reason": "already-checked"}
        migration_checked = True

        if not os.path.isabs(executable):
            raise ValueError("Current executable path must be absolute")
        if (
            not isinstance(legacy_executable_name, str)
            or os.path.basename(legacy_executable_name) != legacy_executable_name
            or any(char in legacy_executable_name for char in (":", "\\", "/"))
            or not legacy_executable_name.lower().endswith(".exe")
        ):
            raise ValueError("Legacy executable name must be a filename in the same installation directory")

        legacy_path = os.path.join(os.path.dirname(executable), legacy_executable_name)
        if _same_path(executable, legacy_path):
            return {"migrated": False, "reason": "unchanged-executable"}
        # 当前命令已经配置过（即使被任务管理器禁用）就不动它。
        if read(executable).get("openAtLogin") is True:
            return {"migrated": False, "reason": "current-configured"}

        legacy = read(legacy_path)
        if legacy.get("openAtLogin") is not True:
            return {"migrated": False, "reason": "legacy-not-configured"}
        item = matching_item(legacy, legacy_path)
        # Electron 的解析器也接受带引号的路径，这里同样补一次对比。
        if item is None and " " in legacy_path:
            legacy = read('"' + legacy_path + '"')
            item = matching_item(legacy, legacy_path)
        # openAtLogin 证明命令完全一致；enabled 才能证明没有被任务管理器禁用。
        if legacy.get("openAtLogin") is not True or not item or item.get("enabled") is not True:
            return {"migrated": False, "reason": "legacy-not-enabled"}

        backend.write(
            {"openAtLogin": True, "path": executable, "args": list(LOGIN_ARGS), "name": login_item_name}
        )
        return {"migrated": True, "from": legacy_path, "to": executable}

    return {
        "get_open_at_login": get_open_at_login,
        "set_open_at_login": set_open_at_login,
        "migrate_legacy_once": migrate_legacy_once,
        "exec_path": executable,
    }
