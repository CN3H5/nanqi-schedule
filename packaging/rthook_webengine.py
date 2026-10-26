r"""rthook_webengine.py —— PyInstaller **运行时钩子**（QtWebEngine 打包修复）。

【出处】
与 `nanqi/webengine.py` 一样，移植自 ustc-assistant 项目的
`packaging/rthook_webengine.py`——那份文件是「打包后浏览器白屏」这个问题的结论沉淀。

【什么是运行时钩子】
PyInstaller 的 runtime hook 是一段被塞进 exe 启动流程最前面的代码，在你的
`main.py` 之前执行，所以它比 main.py 更适合做「必须在最早期完成」的事。

【为什么必须在这里做】
QtWebEngine 只在**第一次被初始化**时读取下面这些环境变量：

    QTWEBENGINEPROCESS_PATH      渲染子进程 QtWebEngineProcess.exe 的位置
    QTWEBENGINE_RESOURCES_PATH   .pak 资源包所在目录
    QTWEBENGINE_LOCALES_PATH     语言包目录
    QTWEBENGINE_CHROMIUM_FLAGS   Chromium 命令行开关

放到 main.py 里做有两个风险：
  1. 单文件模式解压目录是随机的（`%TEMP%\MEIxxxxxx`），必须在拿到 `sys._MEIPASS`
     之后**立刻**算出来，越早越稳；
  2. 任何在你之前 import 了 `PySide6.QtWebEngineCore` 的代码都会让这些变量来不及
     生效（Qt 只在首次初始化时读取）。

【这次改动：不再复制一份开关清单】
原版把 SAFE_FLAGS 在「源码运行（webengine.py）」和「打包钩子（本文件）」里各写了一遍，
注释里还互相提醒「改一处要改两处」——那是个迟早会漂移的隐患。现在本钩子优先
**直接调用 `nanqi.webengine.prepare_webengine()`**：那个模块只 import os/sys/pathlib，
不碰 Qt，所以在最早期调用它与内联写法一样安全。只有导入失败（打包漏收模块等极端情况）
才退回下面的内联实现，保证任何情况下环境变量都会被设好。
"""

import os
import sys


def _roots():
    """可能存放 QtWebEngine 运行期文件的目录（含各自的 PySide6/ 子目录）。"""
    candidates = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(bundle)
    if getattr(sys, "frozen", False):
        candidates.append(os.path.dirname(sys.executable))
    out = []
    for root in candidates:
        out.append(root)
        out.append(os.path.join(root, "PySide6"))
    return out


def _first_file(roots, relative):
    for root in roots:
        path = os.path.join(root, relative)
        if os.path.isfile(path):
            return path
    return None


def _first_dir(roots, relative):
    for root in roots:
        path = os.path.join(root, relative)
        if os.path.isdir(path):
            return path
    return None


def _shared_flags():
    """与 `nanqi/webengine.py` 的 SAFE_FLAGS 保持一致（仅兜底路径使用）。"""
    return [
        "--disable-gpu",
        "--disable-gpu-compositing",
        "--disable-software-rasterizer",
            "--disable-features=VizDisplayCompositor",
        "--disable-background-timer-throttling",
    ]


def _data_dir():
    """与 `nanqi/paths.py` 的 user_data_dir() 保持一致。"""
    for name in ("NANQI_DATA_DIR", "WEEKLIGHT_DATA_DIR"):
        override = os.environ.get(name)
        if isinstance(override, str) and override.strip():
            return os.path.abspath(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "nanqi_schedule")
    return os.path.join(os.path.expanduser("~"), ".nanqi_schedule")


def _apply_shared():
    """首选路径：调用应用自己的实现（单一来源，不会漂移）。"""
    from nanqi.webengine import prepare_webengine

    prepare_webengine()


def _apply_fallback():
    """兜底路径：万一应用包没被打进去，也要把环境变量设好。"""
    roots = _roots()

    process = _first_file(roots, "QtWebEngineProcess.exe")
    if process:
        os.environ.setdefault("QTWEBENGINEPROCESS_PATH", process)

    resources = _first_dir(roots, "resources")
    if resources and os.path.isfile(os.path.join(resources, "qtwebengine_resources.pak")):
        os.environ.setdefault("QTWEBENGINE_RESOURCES_PATH", resources)

    locales = _first_dir(roots, os.path.join("translations", "qtwebengine_locales"))
    if locales:
        os.environ.setdefault("QTWEBENGINE_LOCALES_PATH", locales)

    try:
        cache = os.path.join(_data_dir(), "webengine")
        os.makedirs(cache, exist_ok=True)
        os.environ.setdefault("QTWEBENGINE_DISK_CACHE_DIR", cache)
    except OSError:
        pass

    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    parts = existing.split() if existing else []
    gpu_requested = os.environ.get("NANQI_WEBENGINE_GPU", os.environ.get("ORANGESCHEDULE_WEBENGINE_GPU", "0")) == "1"
    wanted = ["--disable-background-timer-throttling"] if gpu_requested else _shared_flags()
    for flag in wanted:
        key = flag.split("=", 1)[0]
        if not any(item == key or item.startswith(key + "=") for item in parts):
            parts.append(flag)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(parts)

    os.environ.setdefault("QT_LOGGING_RULES", "qt.webenginecontext.info=true")


def _apply():
    try:
        _apply_shared()
    except Exception:
        _apply_fallback()


_apply()
