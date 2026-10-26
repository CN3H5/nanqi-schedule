r"""build.py —— 一键打包 `nanqi_schedule.exe`（文件夹形式 + 单文件形式）。

【打包命令（本脚本实际执行的等价命令）】
    pyinstaller --onedir  --windowed --name nanqi_schedule main.py   # 启动快，推荐
    pyinstaller --onefile --windowed --name nanqi_schedule main.py   # 一个文件，启动慢

脚本额外帮你处理了四件容易出错的事：

1. **图标**：用 `assets/icon.ico`（和 Electron 版同一个图标），同时作为 exe 内嵌图标
   ——任务栏、开始菜单、Alt+Tab 显示的都是它。
2. **前端资源**：`assets/` 整个目录用 `--add-data` 带进去（`index.html`、三个窗口的
   CSS/JS、名句 JSON、图标）。PyInstaller 只自动打包被 import 的模块，不会管数据文件，
   漏掉就会变成「exe 能启动但界面空白」。
3. **QtWebEngine 运行期钩子**：`packaging/rthook_webengine.py` 在 exe 启动最前面把
   `QTWEBENGINEPROCESS_PATH` 等环境变量指向解压目录，否则内置浏览器白屏。
4. **打完包立刻自查**：检查 PyInstaller 有没有把 QtWebEngine 的依赖模块排除掉、
   关键运行期文件有没有进包；然后再真正跑一遍 exe 的 `--selfcheck`、`--doctor`
   与 `--selftest-ui`，把结果打印出来（这是「防止旧问题复发」的自动闸门）。

【怎么用】
    python build.py                # 两种形态都打（默认）
    python build.py --onedir       # 只打文件夹形式
    python build.py --onefile      # 只打单文件形式
    python build.py --no-verify    # 跳过打包后的自动自查

【产物在哪】
    dist/nanqi_schedule/nanqi_schedule.exe   文件夹形式（--onedir）
    dist/nanqi_schedule.exe                  单文件形式（--onefile）

【单文件 vs 文件夹 —— 这是启动速度的关键】
单文件每次启动都要把自己解压到 `%TEMP%\\_MEIxxxxxx`（本应用含 QtWebEngine，解压量约
250 MB），实测「双击 → 主窗口出现」约 19 秒；文件夹形式没有解压开销，约 3 秒。
所以**日常使用推荐文件夹形式**，单文件形式的价值只在于「一个文件拷走就能用」。
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
APP_NAME = "nanqi_schedule"

# 用不到的 Qt 模块，排除掉能显著减小体积。
#
# ★★★ 千万不要排除 QtWebEngine 的依赖模块（这是「打包后浏览器打不开网页」的头号原因）★★★
# QtWebEngineCore 自己会 import 下面这几个模块：
#     PySide6.QtWebChannel    —— 网页与宿主之间的消息通道（同时也是本项目
#                                前端页面 ↔ Python 的 QWebChannel 依赖！）
#     PySide6.QtPositioning   —— 网页地理位置 API 的底层
#     PySide6.QtQuick         —— QtWebEngine 内部用到的场景图运行时
#     PySide6.QtQml           —— QtQuick 的依赖（上一行的下一层）
# 一旦放进 EXCLUDES，PyInstaller 会照办，并在
#     build/work/nanqi_schedule/warn-nanqi_schedule.txt
# 里留下 `excluded module named PySide6.QtWebChannel - imported by ...`，
# 结果就是：exe 能启动、窗口也在，但网页永远渲染不出来（白屏）。
EXCLUDES = [
    "PySide6.QtQuick3D", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtWebSockets",
    "tkinter", "unittest", "pytest", "numpy", "matplotlib",
]

# 项目自己的模块：其中不少是「用到才 import」，显式列出避免被静态分析漏掉。
HIDDEN_IMPORTS = [
    "nanqi.bridge",
    "nanqi.controller",
    "nanqi.core",
    # controller.py 在方法体内才 `from .course_import import parse_course_html`（课表导入），
    # 静态分析不一定跟得到；漏掉它 exe 里「一键添加课表」会直接报错。
    "nanqi.course_import",
    # 同理：二课解析、考试解析、上课提醒节奏、天气都只在方法体内 import
    # （避免启动时联网/多读文件）。
    "nanqi.course_erc",
    "nanqi.course_exam",
    "nanqi.course_reminder",
    "nanqi.weather",
    "nanqi.data_path",
    "nanqi.jsjson",
    "nanqi.login_settings",
    "nanqi.notify",
    "nanqi.paths",
    "nanqi.reminder_copy",
    "nanqi.selftest",
    "nanqi.store",
    "nanqi.import_service",
    "nanqi.reminder_service",
    "nanqi.attachments",
    "nanqi.mail.requests",
    "nanqi.windows.mail_window",
    "nanqi.task_series",
    "nanqi.webengine",
    "nanqi.widget_model",
    "nanqi.window_branding",
    "nanqi.window_layer",
    "nanqi.browser",
    "nanqi.browser.view",
    "nanqi.browser.palette",
    "nanqi.windows",
    "nanqi.windows.main_window",
    "nanqi.windows.reminder_window",
    "nanqi.windows.webview",
    "nanqi.windows.widget_window",
    # QtWebEngine 本体与它的依赖（详见 EXCLUDES 上方的说明）。
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebChannel",
    "PySide6.QtPositioning",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.QtNetwork",
    "PySide6.QtPrintSupport",
    "PySide6.QtOpenGL",
    "PySide6.QtSvg",
]

# QtWebEngine 运行期**必须**存在的文件；打包后逐个复查，缺任何一个都会白屏。
WEBENGINE_REQUIRED = [
    "QtWebEngineProcess.exe",
    "Qt6WebEngineCore.dll",
    "Qt6WebEngineWidgets.dll",
    "Qt6WebChannel.dll",
    "qtwebengine_resources.pak",
    "icudtl.dat",
]


def write_version_file(target: Path) -> str:
    """生成 Windows 版本资源（exe 属性里看到的「文件说明 / 产品名称 / 版本号」）。

    原版用 electron-builder 的 `signAndEditExecutable` 写了这些信息，文件属性与
    任务管理器里能直接看出是「南七日程 1.1.12」；PyInstaller 默认只嵌图标、不写版本
    资源，所以这里补上，免得看起来像个来路不明的程序。

    版本号从 `app/__init__.py` 读取，保持单一来源。
    """
    import sys as _sys

    _sys.path.insert(0, str(PROJECT_ROOT))
    from nanqi import __version__

    numbers: list[int] = []
    for part in str(__version__).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        numbers.append(int(digits) if digits else 0)
    while len(numbers) < 4:
        numbers.append(0)
    version_tuple = tuple(numbers[:4])

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', 'OrangeCatzhang'),
        StringStruct('FileDescription', '南七日程 · Windows 桌面日历与待办清单'),
        StringStruct('FileVersion', '{__version__}'),
        StringStruct('InternalName', 'nanqi_schedule'),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', 'nanqi_schedule.exe'),
        StringStruct('ProductName', '南七日程'),
        StringStruct('ProductVersion', '{__version__}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0804, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return str(__version__)


def run_step(title: str, args: list[str]) -> int:
    print(f"\n===== {title} =====")
    print("$ " + " ".join(f'"{a}"' if " " in a else a for a in args))
    # Codex/other tools may prepend Poppler or Conda DLLs to PATH. In particular
    # their ICU exports differ from Windows ICU used by Qt. Never collect them.
    import sysconfig
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(map(str, [Path(sys.executable).parent,
        Path(sysconfig.get_paths()["purelib"]) / "PySide6",
        Path(sysconfig.get_paths()["purelib"]) / "shiboken6",
        windows / "System32", windows]))
    result = subprocess.run(args, cwd=PROJECT_ROOT, env=env)
    if result.returncode != 0:
        print(f"[ERROR] 这一步失败了（退出码 {result.returncode}），已停止。")
    return result.returncode


def verify_webengine_bundle(onefile: bool) -> bool:
    """打包后复查 QtWebEngine 是否真的被收进去了（白屏问题的自动闸门）。"""
    work = work_dir(onefile) / APP_NAME
    kind = "单文件 exe" if onefile else "文件夹形式"
    print(f"\n===== 复查 QtWebEngine（{kind}）=====")
    ok = True

    warn = work / f"warn-{APP_NAME}.txt"
    if warn.exists():
        bad = [
            line.strip()
            for line in warn.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.startswith("excluded module named") and "QtWebEngine" in line
        ]
        if bad:
            ok = False
            print("[ERROR] 下列模块被排除了，但 QtWebEngine 需要它们 —— 浏览器会白屏：")
            for line in bad:
                print("        " + line)
            print("        请把它们从 build.py 的 EXCLUDES 里删掉。")
        else:
            print("[OK] 没有排除 QtWebEngine 依赖的模块")
    else:
        print(f"[警告] 找不到 {warn.name}，跳过排除项检查")

    toc = work / "Analysis-00.toc"
    if toc.exists():
        text = toc.read_text(encoding="utf-8", errors="replace")
        missing = [name for name in WEBENGINE_REQUIRED if name not in text]
        if missing:
            ok = False
            print("[ERROR] 下列 QtWebEngine 运行期文件没有打进包（会白屏）：")
            for name in missing:
                print(f"        - {name}")
        else:
            print(f"[OK] QtWebEngine 关键文件齐全（{len(WEBENGINE_REQUIRED)} 项）")
    else:
        print(f"[警告] 找不到 {toc.name}，跳过打包内容检查")

    # 前端资源也必须进包，否则界面空白。
    added = toc.read_text(encoding="utf-8", errors="replace") if toc.exists() else ""
    for relative in ("index.html", "widget.html", "reminder.html", "bridge.js", "editor-model.js", "reminder-classics.json"):
        if relative not in added:
            ok = False
            print(f"[ERROR] 前端资源没有进包：{relative}")
    if ok:
        print("[OK] 前端资源（三个页面 + 通道脚本 + 名句库）已在包内")

    return ok


def verify_packaged_app(exe: Path) -> bool:
    """真正跑一遍打好的 exe：自检、浏览器体检、界面自检都必须返回 0。

    这里刻意用**本地代码页**解码子进程输出（不指定 encoding）：exe 在中文 Windows 上
    按代码页输出，用 UTF-8 去解码只会得到乱码；而报告文件本身是 UTF-8 写的，
    下面按 UTF-8 单独读取，两条流各按自己的编码处理。
    """
    print("\n===== 运行打包结果自查 =====")
    ok = True
    checks = (
        (["--selfcheck"], "自检"),
        (["--doctor"], "浏览器体检"),
        (["--selftest-ui"], "界面自检"),
    )
    for args, label in checks:
        print(f"$ \"{exe}\" {' '.join(args)}")
        # 【必须换一个单实例名】用户很可能正开着南七日程；默认实例名下 `--selftest-ui`
        # 会连上那个实例、把参数发过去、自己静默退出（退出码 0，其实什么都没测）。
        from nanqi.core import initial_state
        check_dir = PROJECT_ROOT / "build" / "verification" / ("onefile" if exe.parent == dist_dir() else "onedir") / args[0][2:]
        check_dir.mkdir(parents=True, exist_ok=True)
        (check_dir / "plans.json").write_text(json.dumps(initial_state()), encoding="utf-8")
        env = {**os.environ, "NANQI_INSTANCE_NAME": "nanqi-schedule-buildcheck", "NANQI_DATA_DIR": str(check_dir), "WEEKLIGHT_DATA_DIR": str(check_dir)}
        try:
            result = subprocess.run(
                [str(exe), *args],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=600,
                env=env,
            )
        except subprocess.TimeoutExpired:
            ok = False
            print(f"[ERROR] {label} 超时未结束（超过 600 秒）")
            continue
        output = (result.stdout or "") + (result.stderr or "")
        for line in output.splitlines():
            if any(
                noise in line
                for noise in ("gpu_channel_manager", "ContextResult", "GLES", "Failed to create shared")
            ):
                continue
            print("   " + line)
        if result.returncode != 0:
            ok = False
            print(f"[ERROR] {label} 返回 {result.returncode}（windowed 版控制台输出可能不全，")
            print("        详情见数据目录里的 doctor-report.txt）")
        else:
            print(f"[OK] {label} 通过（退出码 0）")

        report = check_dir / "doctor-report.txt"
        if report.is_file():
            print(f"   —— {report} ——")
            for line in report.read_text(encoding="utf-8", errors="replace").splitlines():
                print("   " + line)
    return ok


def report_path() -> Path:
    """与 nanqi.paths.py 的 user_data_dir() 保持一致：`%APPDATA%\\nanqi_schedule`（兼容旧名）。"""
    override = os.environ.get("NANQI_DATA_DIR") or os.environ.get("WEEKLIGHT_DATA_DIR")
    if isinstance(override, str) and override.strip():
        return Path(os.path.abspath(override)) / "doctor-report.txt"
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "nanqi_schedule" / "doctor-report.txt"
    return Path.home() / ".nanqi_schedule" / "doctor-report.txt"


def pyinstaller_args(onefile: bool, version_file: Path) -> list[str]:
    """组装一次 PyInstaller 调用的完整参数。"""
    assets = PROJECT_ROOT / "assets"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        # 两种模式各自有独立的工作目录，否则后一次 --clean 会把前一次的
        # warn/Analysis 清单删掉，静态复查就无从查起。
        "--clean",
        "--name", APP_NAME,
        "--icon", str(PROJECT_ROOT / "assets" / "icon.ico"),
        # ★ 前端资源必须显式带上（PyInstaller 不会自动打包数据文件）
        "--add-data", f"{assets}{';' if sys.platform == 'win32' else ':'}assets",
        # Windows 版本资源：文件属性与任务管理器里显示的产品名与版本号
        "--version-file", str(version_file),
        # 运行期钩子：在 QApplication 之前设好 QTWEBENGINEPROCESS_PATH 等变量
        "--runtime-hook", str(PROJECT_ROOT / "packaging" / "rthook_webengine.py"),
        "--distpath", str(dist_dir()),
        "--workpath", str(work_dir(onefile)),
        "--specpath", str(PROJECT_ROOT / "build"),
        "--onefile" if onefile else "--onedir",
        "--windowed",
    ]
    for module in HIDDEN_IMPORTS:
        args += [f"--hidden-import={module}"]
    for module in EXCLUDES:
        args += ["--exclude-module", module]
    args.append(str(PROJECT_ROOT / "main.py"))
    return args


def dist_dir() -> Path:
    """产物目录。默认 `dist/`；`NANQI_DIST_DIR` 可改到别处。

    【为什么需要这个开关】打包要写 `dist/nanqi_schedule/_internal/*.dll`，而用户正开着
    应用时那些文件被占用，PyInstaller 会 PermissionError。有了它就能先把新包打到旁边
    （例如 `NANQI_DIST_DIR=dist_staged`），等用户关掉应用再整体替换，不必打断他。
    """
    import os

    override = os.environ.get("NANQI_DIST_DIR", "").strip()
    return Path(override) if override else PROJECT_ROOT / "dist"


def work_dir(onefile: bool) -> Path:
    return PROJECT_ROOT / "build" / ("work-onefile" if onefile else "work-onedir")


def exe_path(onefile: bool) -> Path:
    if onefile:
        return dist_dir() / f"{APP_NAME}.exe"
    return dist_dir() / APP_NAME / f"{APP_NAME}.exe"


def build_one(onefile: bool, version_file: Path, version: str) -> tuple[bool, Path]:
    """打包一种形态并做静态复查；返回 (是否成功, exe 路径)。"""
    label = "单文件 exe" if onefile else "文件夹形式（启动快）"
    if run_step(f"PyInstaller 打包 —— {label}", pyinstaller_args(onefile, version_file)) != 0:
        return False, exe_path(onefile)
    if not verify_webengine_bundle(onefile):
        return False, exe_path(onefile)

    exe = exe_path(onefile)
    if not exe.is_file():
        print(f"[ERROR] 没找到打包结果：{exe}")
        return False, exe

    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"[OK] {label} 打包完成：{exe}")
    print(f"     版本 {version}（已写入 exe 版本资源与图标），主程序 {size_mb:.1f} MB")
    return True, exe


def main() -> int:
    argv = sys.argv[1:]
    skip_verify = "--no-verify" in argv
    # 默认两种都打：文件夹形式启动快（推荐日常使用），单文件形式方便拷走。
    if "--onedir" in argv:
        modes = [False]
    elif "--onefile" in argv:
        modes = [True]
    else:
        modes = [False, True]

    print(f"使用解释器：{sys.executable}")

    icon_path = PROJECT_ROOT / "assets" / "icon.ico"
    if not icon_path.is_file():
        print(f"[ERROR] 找不到图标：{icon_path}")
        return 1

    web_dir = PROJECT_ROOT / "assets" / "web"
    for required in ("index.html", "widget.html", "reminder.html", "bridge.js"):
        if not (web_dir / required).is_file():
            print(f"[ERROR] 缺少前端文件：{web_dir / required}")
            return 1

    version_file = PROJECT_ROOT / "build" / "version_info.txt"
    version = write_version_file(version_file)

    built: list[tuple[bool, Path]] = []
    for onefile in modes:
        ok, exe = build_one(onefile, version_file, version)
        if not ok:
            return 1
        built.append((onefile, exe))

    print("\n===== 打包完成 =====")
    for onefile, exe in built:
        kind = "单文件（拷走即用，每次启动需解压）" if onefile else "文件夹（启动快，推荐）"
        print(f"  {kind}")
        print(f"    {exe}")
    print("\n提示：文件夹形式的启动速度明显更快（无需解压 250 MB）；")
    print("      单文件形式的优点是只有一个文件，代价是每次启动都要先解压十几秒。")

    if skip_verify:
        print("\n（已按 --no-verify 跳过打包后自查）")
        return 0

    all_ok = True
    for onefile, exe in built:
        kind = "单文件 exe" if onefile else "文件夹形式"
        print(f"\n########## 运行打包结果自查 —— {kind} ##########")
        all_ok = verify_packaged_app(exe) and all_ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
