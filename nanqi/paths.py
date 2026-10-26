"""paths.py —— 运行期资源与数据目录的统一解析。

【为什么要单独一个模块】
源码运行时资源和数据都在项目目录里；PyInstaller 单文件 exe 运行时会把自己
解压到 `%TEMP%\\_MEIxxxxxx`，并把用 `--add-data` 带进去的资源放在 `sys._MEIPASS`
下。所有「资源在哪」和「用户数据在哪」的判断都收在这里，其它模块不再各自猜测，
这样打包前后行为一致。

【数据目录与改名】
南七日程 v1.0 的数据目录是 `%APPDATA%\\nanqi_schedule`（可用 `NANQI_DATA_DIR`
覆盖）。上一代「橙子日程 / weeklight」用的是 `%APPDATA%\\weeklight`——改名不应该
让用户已经录入的计划凭空消失，所以 `migrate_legacy_data_dir()` 会在新目录里还没有
`plans.json`、而旧目录里有的时候，把它**复制**过来（复制而不是移动：旧目录保持原样，
万一新版本有问题还能退回旧版）。环境变量同样兼容旧名。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

__all__ = [
    "is_packaged",
    "resource_base",
    "assets_dir",
    "web_dir",
    "user_data_dir",
    "legacy_data_dir",
    "migrate_legacy_data_dir",
    "executable_path",
    "icon_path",
    "APP_DIR_NAME",
    "LEGACY_DIR_NAME",
    "DATA_DIR_ENV",
    "LEGACY_DATA_DIR_ENV",
    "WEBENGINE_GPU_ENV",
    "LEGACY_WEBENGINE_GPU_ENV",
]

APP_DIR_NAME = "nanqi_schedule"
LEGACY_DIR_NAME = "weeklight"

DATA_DIR_ENV = "NANQI_DATA_DIR"
LEGACY_DATA_DIR_ENV = "WEEKLIGHT_DATA_DIR"
# 默认使用保守的软件渲染开关（保证任何机器都打得开网页）；
# 想要硬件加速的用户可以设 NANQI_WEBENGINE_GPU=1，启动与切换都会明显更快。
WEBENGINE_GPU_ENV = "NANQI_WEBENGINE_GPU"
LEGACY_WEBENGINE_GPU_ENV = "ORANGESCHEDULE_WEBENGINE_GPU"


def is_packaged() -> bool:
    """是否运行在 PyInstaller 打好的 exe 里（对应 Electron 的 `app.isPackaged`）。"""
    return bool(getattr(sys, "frozen", False))


def resource_base() -> Path:
    """打包资源根目录：单文件模式是解压目录，源码模式是项目根。"""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle)
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    return resource_base() / "assets"


def web_dir() -> Path:
    """前端页面目录（index.html / 三个窗口的 HTML 与样式脚本）。"""
    return assets_dir() / "web"


def executable_path() -> str:
    """当前可执行文件路径（打包后是 exe，源码运行时是 python.exe）。"""
    return os.path.abspath(sys.executable)


def _env_override(*names: str) -> str | None:
    """按顺序取第一个非空的环境变量值（新名字优先，旧名字兼容）。"""
    for name in names:
        value = os.environ.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _base_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata).resolve()
    return Path.home().resolve()


def user_data_dir() -> Path:
    """当前用户的数据目录：`%APPDATA%\\nanqi_schedule`，可用环境变量覆盖。"""
    override = _env_override(DATA_DIR_ENV, LEGACY_DATA_DIR_ENV)
    if override is not None:
        return Path(override).resolve()
    return _base_dir() / APP_DIR_NAME


def legacy_data_dir() -> Path | None:
    """上一代（橙子日程 / weeklight）的数据目录；不存在则返回 None。"""
    override = os.environ.get(LEGACY_DATA_DIR_ENV)
    if isinstance(override, str) and override.strip():
        candidate = Path(override).resolve()
    else:
        candidate = _base_dir() / LEGACY_DIR_NAME
    return candidate if candidate.is_dir() else None


def migrate_legacy_data_dir(target: Path) -> str | None:
    """把旧版数据目录里的用户数据搬一份到新目录；搬过则返回旧目录路径。

    只在「新目录还没有 `plans.json`」时动手，避免覆盖新数据；只复制用户数据
    （计划、备份副本、提醒轮换都在 plans.json 里），浏览器缓存与 WebEngine 缓存
    不复制——那些是纯缓存，重新生成即可。
    """
    if (target / "plans.json").exists():
        return None
    legacy = legacy_data_dir()
    if legacy is None or legacy == target:
        return None
    source = legacy / "plans.json"
    if not source.is_file():
        return None
    try:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target / "plans.json")
        backup = legacy / "plans.json.bak"
        if backup.is_file():
            shutil.copyfile(backup, target / "plans.json.bak")
    except OSError:
        return None
    return str(legacy)


def icon_path() -> Path:
    """窗口与任务栏图标；Windows 用 .ico 才能被 Shell 正确读取。"""
    name = "icon.ico" if os.name == "nt" else "icon.png"
    candidate = assets_dir() / name
    if candidate.is_file():
        return candidate
    return assets_dir() / "icon.png"
