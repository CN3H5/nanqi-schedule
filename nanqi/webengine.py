r"""webengine.py —— QtWebEngine 启动环境修复（移植自 ustc-assistant/services/browser_fix.py）。

【这个文件继承的问题与结论】
内核浏览器（QtWebEngine，基于 Chromium）在**源码运行**时一切正常，但打包成单文件
exe 后经常「窗口在、网页永远出不来」。原项目已经把根因查清，这里全部沿用：

根因 1 —— 打包时很容易顺手排除 `PySide6.QtWebChannel / QtPositioning / QtQuick`，
    但它们是 QtWebEngineCore 自己 import 的；缺了 QtWebChannel（网页与宿主之间的
    消息通道）初始化就会失败，表现就是白屏。**打包时不能排除它们。**

根因 2 —— 单文件 exe 会把自己解压到 `%TEMP%\MEIxxxxxx`，而 Qt 默认按「库目录」
    去找渲染子进程 `QtWebEngineProcess.exe`；路径推不出来，渲染进程就起不来。
    所以必须在 **QApplication 之前**把 `QTWEBENGINEPROCESS_PATH` 等变量指向真实位置。

根因 3 —— 部分机器（独显 / 虚拟化 / 远程桌面）创建 GL 上下文失败，页面加载会卡住。
    默认写入一组保守的 Chromium 开关（关掉 GPU 合成、软件渲染兜底）最稳；
    想用硬件加速的用户可以设 `ORANGESCHEDULE_WEBENGINE_GPU=1` 重新打开。

本模块负责「源码运行 + 打包后兜底」（`packaging/rthook_webengine.py` 负责 exe 启动最早期），
两边的开关清单必须保持一致。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .paths import WEBENGINE_GPU_ENV, user_data_dir

__all__ = [
    "SAFE_FLAGS",
    "find_webengine_files",
    "prepare_webengine",
    "describe",
    "diagnose",
    "reset_webengine_settings_dir",
    "cache_dir",
    "render_mode",
    "set_render_mode",
    "render_mode_forced",
]

# 强制软件渲染（最稳，最慢）：网页打不开时的自救开关。
WEBENGINE_SOFTWARE_ENV = "NANQI_WEBENGINE_SOFTWARE"
# 上一代用过的名字，继续认，免得老脚本/文档失效。
_LEGACY_GPU_ENV = "ORANGESCHEDULE_WEBENGINE_GPU"

# 稳妥优先的一组 Chromium 开关，只在「用户没设过」时才补进去。
#   --disable-gpu                 关掉 GPU 合成，绕开 GL 上下文创建失败
#   --disable-gpu-compositing     同上，针对部分驱动的补充
#   --disable-software-rasterizer 不用软件光栅化（2D 页面足够，省 CPU）
#   --disable-features=...        关掉 VizDisplayCompositor，规避黑屏/白屏
SAFE_FLAGS = [
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disable-software-rasterizer",
    "--disable-features=VizDisplayCompositor",
    "--disable-background-timer-throttling",
]

# 用户明确要硬件加速时只保留这两项（沙箱与后台节流与 GPU 无关）。
_MINIMAL_FLAGS = ["--disable-background-timer-throttling"]


def cache_dir() -> Path:
    """浏览器缓存与用户数据目录：放在应用数据目录里，单文件模式退出也不会丢。"""
    return user_data_dir() / "webengine"


# ---------------------------------------------------------------- 渲染模式

def _mode_file() -> Path:
    return user_data_dir() / "webengine_mode.txt"


def render_mode() -> str:
    """当前要用的渲染模式：`'gpu'`（默认，快）或 `'software'`（最稳）。

    决定顺序：
      1. 环境变量 `NANQI_WEBENGINE_SOFTWARE=1` → 强制软件渲染；
      2. 环境变量 `NANQI_WEBENGINE_GPU=1` → 强制硬件加速；
      3. 数据目录里的 `webengine_mode.txt`（由 main.py 的启动看门狗在「这台机器
         渲染不出来」时写成 software）→ 用记录的结果；
      4. 都没有 → **默认硬件加速**。
    """
    if os.environ.get(WEBENGINE_SOFTWARE_ENV, "0") == "1":
        return "software"
    if os.environ.get(WEBENGINE_GPU_ENV, "0") == "1" or os.environ.get(_LEGACY_GPU_ENV, "0") == "1":
        return "gpu"
    try:
        recorded = _mode_file().read_text(encoding="utf-8").strip().lower()
    except OSError:
        return "gpu"
    return "software" if recorded == "software" else "gpu"


def set_render_mode(mode: str) -> bool:
    """把渲染模式记到数据目录（下次启动生效）；成功返回 True。"""
    if mode not in ("gpu", "software"):
        raise ValueError("渲染模式只能是 gpu 或 software")
    try:
        target = _mode_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(mode + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def render_mode_forced() -> bool:
    """用户是否用环境变量显式指定了渲染模式（此时不做自动回退）。"""
    return os.environ.get(WEBENGINE_SOFTWARE_ENV, "0") == "1" or os.environ.get(WEBENGINE_GPU_ENV, "0") == "1"


def _candidate_roots() -> list[Path]:
    """列出 `QtWebEngineProcess.exe` 等文件可能所在的目录。

    三类来源：单文件解压目录（`sys._MEIPASS`）、exe 所在目录、PySide6 包目录。
    每一类下面还可能有一层 `PySide6/`（PyInstaller 收集 Qt 依赖时的常见布局），
    所以每个根目录都要再看一眼它的 PySide6 子目录。
    """
    roots: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append(Path(bundle))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parent.parent)
    try:
        # 用 find_spec 定位包目录，而不是 `import PySide6`：后者要执行 PySide6 的
        # __init__（实测约 100 ms），而这时离「显示窗口」只差几百毫秒，能省就省。
        import importlib.util

        spec = importlib.util.find_spec("PySide6")
        if spec and spec.submodule_search_locations:
            roots.append(Path(next(iter(spec.submodule_search_locations))).resolve())
    except Exception:  # noqa: BLE001 - 没装 PySide6 时交给调用方判断
        pass

    expanded: list[Path] = []
    for root in roots:
        if root not in expanded:
            expanded.append(root)
        sub = root / "PySide6"
        if sub not in expanded:
            expanded.append(sub)
    return expanded


def find_webengine_files() -> dict:
    """探测三个关键运行期文件/目录的真实位置（找不到为 None）。"""
    roots = _candidate_roots()
    found: dict = {"process": None, "resources": None, "locales": None, "root": None}

    for root in roots:
        candidate = root / "QtWebEngineProcess.exe"
        if candidate.is_file():
            found["process"] = candidate
            found["root"] = root
            break

    for root in roots:
        resources = root / "resources"
        if found["resources"] is None and (resources / "qtwebengine_resources.pak").is_file():
            found["resources"] = resources
        locales = root / "translations" / "qtwebengine_locales"
        if found["locales"] is None and locales.is_dir():
            found["locales"] = locales

    return found


def _chromium_flags() -> str:
    """组装 `QTWEBENGINE_CHROMIUM_FLAGS`，保留用户已经设过的开关不覆盖。

    渲染模式的优先级：显式环境变量 > 上次自动探测的结果 > 默认（硬件加速）。
    实测硬件加速能让切换视图快约 20%、重渲染快约 45%、启动快约 45%，
    所以默认开；只有探测到「这台机器渲染不出来」时才会自动退回软件渲染。
    """
    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    parts = existing.split() if existing else []
    wanted = _MINIMAL_FLAGS if render_mode() == "gpu" else SAFE_FLAGS
    for flag in wanted:
        key = flag.split("=", 1)[0]
        if not any(item == key or item.startswith(key + "=") for item in parts):
            parts.append(flag)
    return " ".join(parts)


def prepare_webengine(verbose: bool = False) -> dict:
    """**必须在构造 QApplication 之前调用**：修好 QtWebEngine 的启动环境。"""
    info: dict = {"applied": [], "missing": [], "flags": "", "root": ""}

    files = find_webengine_files()
    info["root"] = str(files["root"]) if files["root"] else ""

    # 1) 渲染子进程的绝对路径：白屏最常见的原因。
    if files["process"]:
        os.environ["QTWEBENGINEPROCESS_PATH"] = str(files["process"])
        info["applied"].append("QTWEBENGINEPROCESS_PATH")
    else:
        info["missing"].append("QtWebEngineProcess.exe")

    # 2) 资源包与语言包。
    if files["resources"]:
        os.environ.setdefault("QTWEBENGINE_RESOURCES_PATH", str(files["resources"]))
        info["applied"].append("QTWEBENGINE_RESOURCES_PATH")
    else:
        info["missing"].append("resources/qtwebengine_resources.pak")

    if files["locales"]:
        os.environ.setdefault("QTWEBENGINE_LOCALES_PATH", str(files["locales"]))
        info["applied"].append("QTWEBENGINE_LOCALES_PATH")
    else:
        info["missing"].append("translations/qtwebengine_locales")

    # 3) 持久化缓存：单文件模式解压目录退出即删，指到用户数据目录可以少下载、少白屏。
    try:
        target = cache_dir()
        target.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("QTWEBENGINE_DISK_CACHE_DIR", str(target))
        info["applied"].append("QTWEBENGINE_DISK_CACHE_DIR")
    except OSError as error:
        info["missing"].append(f"webengine 缓存目录({error})")

    # 4) Chromium 开关。
    info["flags"] = _chromium_flags()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = info["flags"]

    # 5) 让 Qt 的关键日志进入应用日志：打包后没有控制台时，白屏只能靠日志排查。
    os.environ.setdefault(
        "QT_LOGGING_RULES", "qt.webenginecontext.info=true;qt.webenginecontext.debug=false"
    )

    if verbose:
        for line in describe(info):
            _safe_print(line)
    return info


def _safe_print(line: str) -> None:
    """输出诊断信息，但绝不让编码问题把体检变成崩溃（见 `describe()` 的说明）。"""
    stream = getattr(sys, "stdout", None)
    if stream is None:
        return
    try:
        print(line, file=stream, flush=True)
    except Exception:  # noqa: BLE001
        pass


def describe(info: dict) -> list[str]:
    """把 `prepare_webengine()` 的结果变成人能读的几行字。

    只用 ASCII 的 `[!]` 标出缺失项：打包后的 `--windowed` exe 在中文 Windows 上
    标准输出往往是 GBK 代码页，写 `⚠` 这类字符会直接抛 UnicodeEncodeError，
    把「体检」变成「崩溃」——诊断信息本身绝不能成为新的故障点。
    """
    return [
        "[浏览器] QtWebEngine 环境准备：",
        f"    渲染进程根目录 : {info.get('root') or '(未找到)'}",
        f"    已设置环境变量 : {', '.join(info.get('applied') or []) or '(无)'}",
        f"    [!] 缺失       : {', '.join(info.get('missing') or []) or '(无)'}",
        f"    Chromium 开关  : {info.get('flags') or '(无)'}",
    ]


def diagnose() -> dict:
    """结构化体检结果：供 `orange-schedule.exe --doctor` 使用。"""
    files = find_webengine_files()
    report = {
        "root": str(files["root"]) if files["root"] else "",
        "process": str(files["process"]) if files["process"] else "",
        "resources": str(files["resources"]) if files["resources"] else "",
        "locales": str(files["locales"]) if files["locales"] else "",
        "cache": str(cache_dir()),
        "frozen": bool(getattr(sys, "frozen", False)),
        "meipass": getattr(sys, "_MEIPASS", ""),
        "flags": os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", ""),
        "render_mode": render_mode(),
        "render_mode_recorded": _mode_file().is_file(),
    }
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

        report["webengine_import"] = True
    except Exception as error:  # noqa: BLE001
        report["webengine_import"] = False
        report["webengine_import_error"] = str(error)
    return report


def reset_webengine_settings_dir() -> str:
    """清掉 WebEngine 的缓存/用户数据目录（缓存损坏导致打不开网页时的自救手段）。"""
    import shutil

    target = cache_dir()
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


if __name__ == "__main__":  # python -m nanqi.webengine
    import json

    prepare_webengine(verbose=True)
    _safe_print(json.dumps(diagnose(), ensure_ascii=False, indent=2))
