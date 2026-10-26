r"""main.py —— 南七日程（Python 重编译版）入口。

【启动顺序为什么不能变】
QtWebEngine 有两条硬性要求，违反了就会「窗口在、网页永远出不来」（白屏）：

  1. `prepare_webengine()` 必须在**构造 QApplication 之前**调用——下面那些
     `QTWEBENGINEPROCESS_PATH` / `QTWEBENGINE_CHROMIUM_FLAGS` 只在首次初始化时读一次；
  2. `PySide6.QtWebEngineWidgets` 必须在**构造 QApplication 之前**被 import。

所以真正的顺序是：修环境 → import WebEngine → 建 QApplication → 建窗口。

【命令行】
    orange-schedule              正常启动（显示主窗口，托盘常驻）
    orange-schedule --hidden     开机自动启动用：不显示主窗口，只在托盘提醒
    orange-schedule --widget     只打开桌面便签
    orange-schedule --browser URL 直接打开内置浏览器并访问 URL
    orange-schedule --doctor     体检：打印 QtWebEngine 与内置浏览器所需文件的实际情况
    orange-schedule --selfcheck  自检：数据目录、计划数量、打包状态、组件可用性
    orange-schedule --selftest-ui 界面自检：真窗口里验证三个页面的加载与前端通道
    orange-schedule --reset-webengine  清掉 WebEngine 缓存（网页打不开时的自救）
    orange-schedule --version    版本号

`--doctor` / `--selfcheck` / `--selftest-ui` 除了打印，还会在数据目录写一份
`doctor-report.txt`：打包成 `--windowed` 的 exe 没有控制台，没有文件就无从查看。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 允许以 `python main.py` 直接运行（项目根加入 sys.path）。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nanqi import __version__  # noqa: E402
from nanqi.paths import is_packaged, user_data_dir  # noqa: E402

# QtCore 只提供 QTimer/QLocalServer 这类非图形设施，在 QApplication 之前 import 是安全的，
# 而且在模块顶层拿到引用可以避免在热路径函数里反复 import。
from PySide6.QtCore import QTimer  # noqa: E402

# 单实例通道名。`NANQI_INSTANCE_NAME` 可以换一个名字，让「诊断/自检」和
# **正在运行的正常实例**并存：否则 `--selftest-ui` 会连上已有实例、把参数发过去、
# 自己静默退出（退出码 0 但什么都没测），看起来像「自检没反应」。
SERVER_NAME = os.environ.get("NANQI_INSTANCE_NAME") or "nanqi-schedule-single-instance"
WINDOW_MODE = "--widget"
# 硬件加速下等页面加载的上限：超过就判定这台机器渲染不出来，退回软件渲染。
RENDER_WATCHDOG_MS = 10000


def _make_output_never_crash() -> None:
    r"""让标准输出/错误在遇到编不出来的字符时退化，而不是抛异常。

    中文 Windows 上 Python 的默认输出编码是 GBK（代码页 936），而打包后的
    `--windowed` exe 又可能被父进程用管道接走。这时候只要写一个 GBK 编不出的字符
    （例如 `⚠`、某些生僻字），`print` 就会抛 UnicodeEncodeError——诊断信息本身
    反而成了新的故障点。这里把 errors 改成 replace，最坏情况只是显示成 `?`。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass


def _print(line: str = "") -> None:
    """向控制台输出。

    `--windowed` 打包后进程没有控制台，`sys.stdout` 可能是 None：这时直接跳过，
    结果仍然会写进 `doctor-report.txt`，所以不会丢失任何诊断信息。
    """
    stream = getattr(sys, "stdout", None)
    if stream is None:
        return
    try:
        print(line, file=stream, flush=True)
    except Exception:  # noqa: BLE001
        pass


def _write_report(title: str, lines: list[str]) -> None:
    """把体检/自检结果写到数据目录，方便打包后查看。"""
    try:
        target = user_data_dir()
        target.mkdir(parents=True, exist_ok=True)
        path = target / "doctor-report.txt"
        path.write_text("\n".join([title, "=" * len(title), *lines, ""]), encoding="utf-8")
        _print(f"报告已写入：{path}")
    except OSError as error:
        _print(f"报告写入失败：{error}")


# --------------------------------------------------------------------------- 体检

def run_doctor() -> int:
    from nanqi import webengine

    webengine.prepare_webengine(verbose=True)
    report = webengine.diagnose()

    lines = [
        f"版本        : {__version__}",
        f"打包运行    : {report['frozen']}",
        f"解压目录    : {report['meipass'] or '(非单文件模式)'}",
        f"渲染子进程  : {report['process'] or '(未找到)'}",
        f"资源包目录  : {report['resources'] or '(未找到)'}",
        f"语言包目录  : {report['locales'] or '(未找到)'}",
        f"缓存目录    : {report['cache']}",
        f"Chromium 开关: {report['flags']}",
        f"WebEngine 可导入: {report['webengine_import']}",
    ]
    if not report["webengine_import"]:
        lines.append(f"    导入错误: {report.get('webengine_import_error', '')}")

    browser_ok = False
    try:
        from nanqi.browser import BOOKMARKS, is_webengine_available

        browser_ok = is_webengine_available()
        lines.append(f"内置浏览器可用: {browser_ok}（书签 {len(BOOKMARKS)} 条）")
    except Exception as error:  # noqa: BLE001
        lines.append(f"内置浏览器不可用: {error}")

    ok = bool(report["process"]) and report["webengine_import"] and browser_ok
    lines.append("")
    lines.append("结论：" + ("内置浏览器所需组件已就位。" if ok else "缺少组件，内置浏览器可能打不开网页。"))
    for line in lines:
        _print(line)
    _write_report("南七日程 · 浏览器体检", lines)
    return 0 if ok else 1


def run_selfcheck() -> int:
    from nanqi.browser import BOOKMARKS, is_webengine_available
    from nanqi.core import initial_state, validate_state
    from nanqi.paths import assets_dir, web_dir
    from nanqi.reminder_copy import message_count
    from nanqi.store import Store

    lines: list[str] = []
    ok = True

    def check(label: str, condition: bool, extra: str = "") -> None:
        nonlocal ok
        ok = ok and condition
        lines.append(f"[{'OK' if condition else '!!'}] {label}{(' — ' + extra) if extra else ''}")

    check("资源目录存在", assets_dir().is_dir(), str(assets_dir()))
    for name in ("index.html", "widget.html", "reminder.html", "bridge.js", "renderer.js", "widget.js"):
        check(f"前端文件 {name}", (web_dir() / name).is_file())
    check("名句文案库", message_count() >= 100, f"{message_count()} 条")
    check("内置浏览器可用", is_webengine_available(), f"书签 {len(BOOKMARKS)} 条")

    try:
        validate_state(initial_state())
        check("空状态可通过校验", True)
    except Exception as error:  # noqa: BLE001
        check("空状态可通过校验", False, str(error))

    try:
        store = Store()
        lines.append(f"数据目录    : {store.data_dir}")
        lines.append(f"数据文件    : {store.data_file}（存在：{store.data_file.exists()}）")
        lines.append(f"计划数量    : {len(store.state['tasks'])}")
        lines.append(f"打包运行    : {store.packaged}")
        if store.startup_error:
            lines.append(f"启动提示    : {store.startup_error}")
    except Exception as error:  # noqa: BLE001
        check("数据层可初始化", False, str(error))

    lines.append("")
    lines.append("结论：" + ("自检通过。" if ok else "自检发现问题，请查看上面的 !! 行。"))
    for line in lines:
        _print(line)
    _write_report("南七日程 · 自检", lines)
    return 0 if ok else 1


# --------------------------------------------------------------------------- 单实例

def _acquire_single_instance():
    """返回 (是否本进程是唯一实例, server, 触发方式)。

    先尝试连接已有实例；连得上说明已经有一个在跑，把本次参数发过去让它响应即可。
    只有连不上时才清掉残留的 socket 再监听——顺序反了会让第二个实例成功监听，
    变成两个进程同时运行（托盘出现两个图标）。
    """
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    probe = QLocalSocket()
    probe.connectToServer(SERVER_NAME)
    if probe.waitForConnected(300):
        payload = WINDOW_MODE if WINDOW_MODE in sys.argv else "show"
        probe.write(payload.encode("utf-8"))
        probe.flush()
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return False, None, payload

    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    if not server.listen(SERVER_NAME):
        # 监听失败（例如权限问题）时宁可继续启动，也不要让用户打不开程序。
        return True, None, ""
    return True, server, ""


def _attach_single_instance_handler(server, controller) -> None:
    def on_new_connection() -> None:
        socket = server.nextPendingConnection()
        if socket is None:
            return

        def on_ready() -> None:
            payload = bytes(socket.readAll()).decode("utf-8", "replace")
            if WINDOW_MODE in payload:
                controller.open_widget()
            else:
                controller.show_main()
            socket.disconnectFromServer()

        socket.readyRead.connect(on_ready)
        # 对方可能已经断开，超时后清理即可。
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1000, socket.deleteLater)

    server.newConnection.connect(on_new_connection)


# --------------------------------------------------------------------------- 主流程

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    _make_output_never_crash()

    if "--version" in args:
        _print(f"南七日程 {__version__}")
        return 0
    if "--help" in args or "-h" in args:
        _print(__doc__ or "")
        return 0

    # 1) 修 QtWebEngine 的启动环境（必须在 QApplication 之前）。
    from nanqi import webengine

    if "--reset-webengine" in args:
        _print("已清理 WebEngine 缓存目录：" + webengine.reset_webengine_settings_dir())
        return 0

    if "--doctor" in args:
        return run_doctor()

    webengine.prepare_webengine()

    # 2) 必须在 QApplication 之前 import。
    from PySide6 import QtWebEngineWidgets  # noqa: F401

    # 3) 现在才建 QApplication。
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication(args)
    app.setApplicationName("南七日程")
    app.setApplicationDisplayName("南七日程")
    app.setOrganizationName("XJ.Jiang")
    app.setApplicationVersion(__version__)
    # 关键：关掉主窗口只是隐藏，托盘与提醒要继续工作（对应 Electron 的
    # `window-all-closed` 不退出）。
    app.setQuitOnLastWindowClosed(False)
    try:
        from nanqi.paths import icon_path

        if icon_path().is_file():
            app.setWindowIcon(QIcon(str(icon_path())))
    except Exception:  # noqa: BLE001
        pass

    # 进程级任务栏身份（开发版与安装版用不同 ID，互不抢占）。
    from nanqi.window_branding import apply_app_user_model_id, resolve_app_id

    apply_app_user_model_id(resolve_app_id(is_packaged()))

    if "--selfcheck" in args:
        return run_selfcheck()

    # 4) 单实例。
    is_primary, server, _payload = _acquire_single_instance()
    if not is_primary:
        return 0

    # 5) 建控制器（窗口、托盘、提醒、浏览器都在这里装配）。
    from nanqi.controller import AppController

    # `--browser URL` 是「直接看网页」，这时不弹主日历，避免两个窗口一起抢焦点。
    browser_url = _browser_argument(args)
    controller = AppController(
        app,
        start_hidden="--hidden" in args or browser_url is not None or "--selftest-ui" in args,
        start_with_widget=WINDOW_MODE in args,
    )
    if server is not None:
        _attach_single_instance_handler(server, controller)

    if "--selftest-ui" in args:
        return run_ui_selftest(app, controller)

    # 渲染看门狗：默认用硬件加速（实测启动快约 45%、切换快约 20%），
    # 万一这台机器上 GL 上下文卡住导致页面出不来，就自动退回软件渲染并重启一次。
    _arm_render_watchdog(controller, args)

    if browser_url is not None:
        controller.open_browser(browser_url)

    return app.exec()


def _arm_render_watchdog(controller, args: list[str]) -> None:
    """硬件加速「打不开网页」时的自动回退（只会发生一次）。

    ustc-assistant 排查过一类机器（独显 / 虚拟化 / 远程桌面）上创建 GL 上下文会失败，
    渲染进程反复重试并卡住——表现就是窗口在、页面永远出不来。默认开硬件加速能明显
    提速，但必须给这类机器留退路，做法是：

      1. 启动后 10 秒内没等到 `loadFinished`，就判定「这台机器渲染不出来」；
      2. 把 `software` 写进数据目录的 `webengine_mode.txt`；
      3. 重启自己（`os._exit`，因为本进程的 WebEngine 可能已经卡住，正常退出会挂在
         退出流程里——这是实测过的）。

    下次启动直接走软件渲染，不会再卡；即使用户拒绝了自动重启，手动再开一次也已经是
    软件渲染。用 `NANQI_WEBENGINE_GPU` / `NANQI_WEBENGINE_SOFTWARE` 显式指定过模式的
    用户不受影响（尊重用户的选择，不做回退）。
    """
    from nanqi import webengine as _webengine

    if _webengine.render_mode_forced() or _webengine.render_mode() != "gpu":
        return
    loaded = {"done": False}
    try:
        controller.main_window.view.loadFinished.connect(lambda ok: loaded.update(done=True))
    except Exception:  # noqa: BLE001
        return

    def check() -> None:
        if loaded["done"]:
            return
        if not _webengine.set_render_mode("software"):
            # 写不进数据目录就什么都不做：宁可让用户看到一次异常，也不要陷入重启循环。
            return
        _print("检测到硬件加速无法正常渲染，已改为软件渲染并重新启动……")
        _relaunch(args)

    QTimer.singleShot(RENDER_WATCHDOG_MS, check)


def _relaunch(args: list[str]) -> None:
    """重新启动自己（用于渲染模式回退）。"""
    import subprocess

    command = [sys.executable, *(args[1:] if is_packaged() else args)]
    try:
        subprocess.Popen(command)
    except Exception:  # noqa: BLE001 - 重启失败也要把当前进程结束掉，避免两个实例互相挡
        pass
    # 本进程的渲染可能已卡住，正常退出会挂在退出流程里，所以直接结束。
    os._exit(0)


def run_ui_selftest(app, controller) -> int:
    """在真窗口里验证三个页面的加载与前端通道（只读探测，不改动用户数据）。"""
    from nanqi.selftest import run_ui_selftest as _run

    # 记录「自检前有没有数据文件」：自检本身不写数据，但为了还原被它临时改过的
    # 便签偏好，会走一次正常的落盘路径，从而可能创建出「全新默认状态」的 plans.json。
    # 结束时若是这种情况就把文件删掉，让自检做到真正的零痕迹。
    data_file = controller.store.data_file
    existed_before = data_file.exists()

    _run(app, controller)
    code = app.exec()
    results = getattr(controller, "_selftest_results", [])

    if not existed_before and data_file.exists():
        try:
            from nanqi.core import initial_state

            if controller.store.state == initial_state():
                data_file.unlink()
                backup = data_file.parent / (data_file.name + ".bak")
                if backup.exists():
                    backup.unlink()
                _print("（自检没有留下任何数据文件）")
        except Exception:  # noqa: BLE001 - 清理失败不影响自检结论
            pass

    passed = sum(1 for _, ok, _ in results if ok)

    lines = [
        f"版本        : {__version__}",
        f"打包运行    : {is_packaged()}",
        "",
    ]
    for label, ok, extra in results:
        lines.append(f"[{'OK' if ok else '!!'}] {label}" + (f" — {extra}" if extra else ""))
    lines.append("")
    lines.append(f"结论：{passed}/{len(results)} 项通过。")
    for line in lines:
        _print(line)
    _write_report("南七日程 · 界面自检", lines)
    return code


def _browser_argument(args: list[str]) -> str | None:
    """取出 `--browser URL` 或 `--browser=URL` 的取值；没给就返回 None。"""
    for index, arg in enumerate(args):
        if arg == "--browser":
            return args[index + 1] if index + 1 < len(args) else ""
        if arg.startswith("--browser="):
            return arg.split("=", 1)[1]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
