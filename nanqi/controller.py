r"""controller.py —— 应用控制器（移植自 src/main.cjs 的 `app.whenReady()` 那一段）。

【它把哪些东西接在一起】
    Store（数据/设置/更新状态）
      ↕
    Bridge（前端调用入口，注册进每个页面的 QWebChannel）
      ↕
    主窗口 / 桌面便签 / 提醒窗口 / 内置浏览器
      + 托盘图标与菜单、系统通知、提醒定时器、自动更新检查

原版这些都在 `main.cjs` 里平铺，这里按职责拆成方法，但**行为顺序刻意保持一致**：

  * 启动：迁移旧版开机启动项 → 建主窗口 → 建便签控制器 → 建托盘菜单 → 注册 IPC →
    按 `--widget` / 便签偏好决定是否显示便签 → 7 秒后首次检查更新（之后每 6 小时）→
    3 秒后首次提醒扫描（之后每 5 秒）；
  * 提醒：`dueTasks()` 找出到点的计划 → 写 `notifiedAt` 与 history（并推进名句）→
    发系统通知 → 弹提醒窗口；从休眠恢复后立刻补扫一次；
  * 退出：主窗口与便签都只是隐藏，只有托盘「退出南七日程」或设置里的退出才真的结束。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from . import __version__
from .browser import BrowserWindow, is_webengine_available
from .import_service import ImportService
from .reminder_service import ReminderService
from .attachments import validate_attachment
from .course_reminder import is_course
from .notify import Notifier
from .paths import icon_path, web_dir
from .store import Store, merge_import
from .widget_model import normalize_prefs
from .windows.main_window import MainWindow
from .windows.reminder_window import ReminderWindow
from .windows.widget_window import WidgetWindow

__all__ = ["AppController"]

# 与 Electron 版一致的时间常量。
FIRST_TICK_DELAY_MS = 3000
TICK_INTERVAL_MS = 5000
# 课表没写周次时，默认按整学期（18 周）展开，保证课程每周都出现。
# 「一键添加」的三个入口。考试与课表是**同一个入口**（用户确认：网址跟课表是同一个），
# 进去以后在页面上切到「考试安排」再点浏览器里那个「一键添加」即可。
SCHEDULE_URL = "https://jw.ustc.edu.cn/home"
EXAM_URL = SCHEDULE_URL
# 二课（团学活动）页面：「我报名的二课」就是一张 Ant Design 表格，无需点日期切换。
ERCOURSE_URL = "https://young.ustc.edu.cn/login/sc-wisdom-group-learning/personalInformation/mySchedule"

IMPORT_SOURCES = {
    "schedule": {"label": "课表", "url": SCHEDULE_URL},
    "ercourse": {"label": "二课", "url": ERCOURSE_URL},
    "exam": {"label": "考试", "url": EXAM_URL},
}
IMPORT_LABELS = {kind: spec["label"] for kind, spec in IMPORT_SOURCES.items()}
# 抓取页面的超时（毫秒）：runJavaScript 的回调有可能一直不回来，不能让它永远挂着。
IMPORT_CAPTURE_TIMEOUT_MS = 12000

# 二课全量抓取：接口一次只返回一天，所以按天并发拉。
# 天数是「从今天起算」，覆盖一整个学期绰绰有余；已经过去的日子不拉（反正会被丢弃）。
ERC_CRAWL_DAYS = 150
ERC_CRAWL_TIMEOUT_S = 90

#: 在**页面里**跑的二课接口抓取脚本。
#: 关键点：同源 + `credentials:'include'` 用的是浏览器里已登录的 cookie，
#: 不经过 Python 网络栈，所以不需要把任何凭据拿出来。
#: 结果写进 `window.__nanqiErc`，由 Python 侧轮询（Promise 不能通过 runJavaScript 返回）。
_ERC_CRAWL_JS = (
    "(function(){"
    # 用页面自己的 origin 拼接口地址：在团学平台上它与写死的绝对地址完全等价，
    # 但这样也可以在本地起一个假接口做端到端测试（见 tests/ui_erc_api_smoke.py）。
    "var ORIGIN=(location.origin&&location.origin.indexOf('http')===0)"
    "?location.origin:'https://young.ustc.edu.cn';"
    "var API=ORIGIN+'/login/wisdom-group-learning-bg/"
    "personal/scCalendar/getPageUserList';"
    "var DAYS=" + str(ERC_CRAWL_DAYS) + ",CONCURRENCY=6;"
    # 站点用 jeecg 的那套鉴权：axios 拦截器会从 localStorage 取 token 塞进
    # `X-Access-Token` 请求头。我们直接用 fetch，就得自己把同样的头带上，
    # 否则接口会回 401/「Token 失效」，让全量抓取白白退化成「只抓当天」。
    "function readToken(){var out='';try{"
    "for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i)||'';"
    "if(!/token/i.test(k))continue;var raw=localStorage.getItem(k)||'';"
    "try{var v=JSON.parse(raw);if(v&&typeof v==='object')"
    "raw=(v.value||v.token||v.accessToken||'');}catch(e){}"
    "if(typeof raw==='string'&&raw.length>10&&raw.length<4000)out=raw;}"
    "}catch(e){}return out;}"
    "var TOKEN=readToken();"
    "var HEADERS={'Accept':'application/json'};"
    "if(TOKEN)HEADERS['X-Access-Token']=TOKEN;"
    "var today=new Date();today.setHours(0,0,0,0);"
    "var dates=[];"
    "for(var i=0;i<DAYS;i++){var d=new Date(today.getTime()+i*86400000);"
    "dates.push(d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'"
    "+String(d.getDate()).padStart(2,'0'));}"
    "var out=[],seen={},errors=0,done=0,index=0;"
    "window.__nanqiErc={done:false,records:[],progress:0,total:dates.length,errors:0,message:''};"
    "function add(r){if(!r||!r.title)return;"
    "var k=[r.title,r.startTime,r.endTime].join('|');"
    "if(!seen[k]){seen[k]=1;out.push(r);}}"
    "function finish(msg){var s=window.__nanqiErc;s.done=true;s.records=out;"
    "s.errors=errors;s.message=msg||'';}"
    "function next(){"
    "if(index>=dates.length){"
    "finish(errors>=dates.length?'接口全部失败（可能没登录）':'');return;}"
    "var batch=[];"
    "for(var b=0;b<CONCURRENCY&&index<dates.length;b++,index++)batch.push(dates[index]);"
    "var pending=batch.length;"
    "batch.forEach(function(day){"
    "var url=API+'?now='+day+'&pageNo=1&pageSize=100';"
    "fetch(url,{credentials:'include',headers:HEADERS})"
    ".then(function(r){return r.json();})"
    ".then(function(json){var res=json&&(json.result||json.data);"
    "var recs=res&&(res.records||res.list||res.rows);"
    "if(Array.isArray(recs))recs.forEach(add);else errors++;})"
    ".catch(function(){errors++;})"
    ".then(function(){done++;window.__nanqiErc.progress=done;"
    "window.__nanqiErc.errors=errors;"
    "if(--pending===0)next();});});}"
    "next();return 'started';})()"
)

#: 只读进度/结果（done 之后才带 records，避免每 250ms 传一大堆 JSON）。
_ERC_CRAWL_READ_JS = (
    "(function(){var s=window.__nanqiErc;"
    "if(!s)return JSON.stringify({done:false,progress:0});"
    "if(!s.done)return JSON.stringify({done:false,progress:s.progress||0,total:s.total||0,"
    "errors:s.errors||0});"
    "return JSON.stringify({done:true,progress:s.progress||0,total:s.total||0,"
    "errors:s.errors||0,message:s.message||'',records:s.records||[]});})()"
)

# 抓取脚本：顶层文档 + 所有同源 iframe 的 outerHTML，用注释标记来源。
# 解析器对多份文档拼接是宽容的（HTMLParser 会继续往下读）。
_CAPTURE_PAGE_JS = (
    "(function(){"
    "function dump(doc, path){"
    "var out='<!--FRAME:'+path+'-->';"
    "out += doc && doc.documentElement ? doc.documentElement.outerHTML : '';"
    "try{var frames=doc.querySelectorAll('iframe,frame');"
    "for(var i=0;i<frames.length;i++){"
    "try{var inner=frames[i].contentDocument;"
    "if(inner&&inner.documentElement){out+=dump(inner, path+'/'+i);}}catch(e){}"
    "}}catch(e){}"
    "return out;}"
    "return dump(document,'top');})()"
)

RESUME_TICK_DELAY_MS = 1500

# Windows 电源广播（对应 powerMonitor 的 resume 事件）。
WM_POWERBROADCAST = 0x0218
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012

_WIDGET_FALLBACK_ERROR = "便签层级设置失败："


class _PowerResumeFilter(QAbstractNativeEventFilter):
    """系统从休眠/待机恢复时回调一次（原版用 `powerMonitor.on('resume')`）。

    恢复后必须尽快补扫一次提醒——关机或休眠期间到点的计划要在回来后立刻提醒，
    而不是等下一个 5 秒轮询（虽然差别很小，但这正是原版专门监听它的原因）。
    """

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        try:
            if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                from ctypes import wintypes

                msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == WM_POWERBROADCAST and int(msg.wParam) in (
                    PBT_APMRESUMESUSPEND,
                    PBT_APMRESUMEAUTOMATIC,
                ):
                    QTimer.singleShot(RESUME_TICK_DELAY_MS, self._callback)
        except Exception:  # noqa: BLE001 - 事件过滤器绝不能把异常抛回 Qt
            pass
        return False


class AppController(QObject):
    """应用运行时：窗口、托盘、通知、定时器与前端桥接。"""

    def __init__(
        self,
        app: QApplication,
        *,
        start_hidden: bool = False,
        start_with_widget: bool = False,
        version: str = __version__,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.version = version
        self.quitting = False

        self.store = Store(parent=self)
        self.importer = ImportService(self.store)
        self.reminders = ReminderService(self.store)
        self._picked_attachments = set()
        self.store.stateChanged.connect(self._on_state_changed)

        # 邮件服务：把「什么时候发」留在界面线程（tick 里判断），
        # 「怎么发」丢到后台线程（见 nanqi/mail/service.py 的说明）。
        from .mail import MailService

        self.mail = MailService(self.store.mail, parent=self) if self.store.mail else None
        if self.mail is not None:
            self.mail.finished.connect(self._on_mail_finished)

        # 循环导入（bridge 需要 host，host 需要 bridge）用分两步构造来避开：
        # 先建 Bridge 并把它自己的 host 指回 self。
        from .bridge import Bridge, MainSignals

        self.main_signals = MainSignals(self)
        self.bridge = Bridge(self.store, self, parent=self)

        self.main_window: MainWindow | None = None
        self.widget_window: WidgetWindow | None = None
        self.reminder_window: ReminderWindow | None = None
        self.browser_window: BrowserWindow | None = None
        self.mail_window = None
        self._browser_profile = None
        self._tray: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._tick_timer: QTimer | None = None
        self._power_filter: _PowerResumeFilter | None = None
        # 课程提醒的「已发阶段」去重集合（(日期, 任务ID, 阶段)），只活在本进程里：
        # 与应用重启后补发迟到提醒的既有取舍一致（见 core/reminder.py 的说明）。
        # 当日天气缓存：{"date": "YYYY-MM-DD", "data": dict|None}。一天最多查一次。
        self._weather_cache: dict | None = None
        self._weather_pending = False
        # 「一键添加」的抓取重入锁：连点按钮时只允许一轮在飞（避免叠加回调/重复写入）。
        self._import_busy = False
        # 用户这次选的导入类型（课表/二课/考试），用作页面判定的强证据。
        self._import_prefer = ""
        # 二课接口抓取的轮询定时器 + 本次点击是否已经试过接口。
        self._erc_timer: QTimer | None = None
        self._erc_api_attempted = False
        self._erc_api_state: dict = {}
        # 上一次导入因为「已经过去」而跳过的条数（用于结果文案）。

        self.notifier = Notifier()
        # 【启动顺序】先把主窗口建出来并显示，再在事件循环的下一轮做托盘、定时器、
        # 开机启动迁移、便签恢复这些「不挡视线」的事。这样用户看到窗口的时间只取决于
        # 建窗口本身（QtWebEngine 初始化约 0.9 秒），而不是全部初始化之和。
        self._build_main_window()
        if not start_hidden:
            self.show_main()
        QTimer.singleShot(0, lambda: self._finish_startup(start_with_widget))

    def _finish_startup(self, start_with_widget: bool) -> None:
        """主窗口显示之后才需要做的事（延后一轮事件循环执行）。"""
        self._build_tray()
        self._start_timers()

        if self.store.packaged:
            try:
                self.store.login["migrate_legacy_once"]()
            except Exception as error:  # noqa: BLE001 - 迁移失败不该阻止启动
                print("Startup shortcut migration skipped:", error, file=sys.stderr)

        # 便签：`--widget` 直接打开；否则按上次保存的「开着」状态恢复。
        if start_with_widget:
            self.open_widget()
        else:
            self._restore_widget()
        self.app.aboutToQuit.connect(self._on_about_to_quit)

    # =================================================================== 构建

    def _build_main_window(self) -> None:
        self.main_window = MainWindow(
            self.bridge,
            self.main_signals,
            web_dir(),
            packaged=self.store.packaged,
        )
        # 应用页面里的外链（例如更新说明里的链接）交给内置浏览器。
        self.main_window.view.set_external_opener(self.open_browser)

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon(str(icon_path()))
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("南七日程 · 提醒在后台运行")
        menu = QMenu()
        menu.addAction("打开南七日程", self.show_main)
        menu.addAction("桌面便签", self.open_widget)
        menu.addAction("内置浏览器", lambda: self.open_browser(""))
        menu.addAction("新建计划", self.request_new_task)
        menu.addSeparator()
        menu.addAction("退出南七日程（停止提醒）", self.request_quit)
        self._tray_menu = menu
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        self.notifier.set_tray(self._tray)

    def _start_timers(self) -> None:
        QTimer.singleShot(FIRST_TICK_DELAY_MS, self.tick)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_INTERVAL_MS)
        self._tick_timer.timeout.connect(self.tick)
        self._tick_timer.start()

        if os.name == "nt":
            try:
                self._power_filter = _PowerResumeFilter(self.tick)
                self.app.installNativeEventFilter(self._power_filter)
            except Exception:  # noqa: BLE001
                self._power_filter = None

    # =================================================================== 状态广播

    def _on_state_changed(self, payload: str) -> None:
        """Store 的快照直接转给所有页面（同一份 Bridge 被注册进每个页面的通道）。"""
        self.bridge.emit_state(payload)

    # =================================================================== 邮件

    def _on_mail_finished(self, ok: bool, message: str) -> None:
        """邮件发送结束：成功时静默（只在设置页显示），失败时给一次明确提示。"""
        if not ok:
            self._notify("邮件发送失败", message)
            self.bridge.emit_error("邮件发送失败：" + message)
        # 无论成败都刷新一次快照：界面上的「上次发送」提示靠它更新。
        self.store.broadcast()


    def _maybe_send_digest(self) -> None:
        """每次 tick 顺带判断「今天到点了吗」——比单独开一个 24 小时的定时器可靠
        （后者在休眠/改系统时间后会错位，而这里每次都用当前时间重新判断）。"""
        if self.mail is None:
            return
        try:
            self.mail.maybe_send_digest(
                self.store.state["tasks"], message_text=self.store.encouragement()
            )
        except Exception as error:  # noqa: BLE001 - 摘要失败不该影响提醒主流程
            self.bridge.emit_error("每日摘要发送失败：" + str(error))

    # =================================================================== 提醒

    def tick(self) -> None:
        try:
            self._maybe_send_digest()
            entries = self.reminders.collect(weather=self._today_weather())
            for entry in entries:
                self._notify(entry["title"], entry["body"])
            if entries:
                self.show_reminder()
                if self.mail is not None:
                    tasks = {e["task"]["id"]: e["task"] for e in entries if e["stage"] not in ("nap", "early", "change")}
                    self.mail.on_reminders(list(tasks.values()), self.store.encouragement())
        except Exception as error:
            self.bridge.emit_error("提醒失败：" + str(error))

    def _today_weather(self) -> dict | None:
        """当天天气（一天只查一次，**只在当天有课时才查**）。

        查询放在后台线程：`tick` 每 5 秒跑一次，绝不能在这里阻塞等网络。
        第一次调用只负责「把请求发出去」，本次返回 None；拿到结果后缓存当日，
        失败也缓存成 None（一天最多试一次，不做无意义的重试）。
        """
        try:
            from .weather import fetch_daily, normalize_settings

            today = _today_key()
            cache = self._weather_cache
            # 先看缓存：`tick()` 每 5 秒走一次，已经定过的日子直接返回，
            # 连设置解析都不做（开着天气的人也不用每 5 秒读一遍设置）。
            if isinstance(cache, dict) and cache.get("date") == today:
                return cache.get("data")
            if self._weather_pending:
                return None
            settings = normalize_settings(self.store.state["settings"].get("weather"))
            if not settings["enabled"]:
                return None
            # 没课的日子不需要天气：省掉一次对外请求。
            has_course = any(
                is_course(task) and task.get("date") == today and not task.get("done")
                for task in self.store.state["tasks"]
            )
            if not has_course:
                self._weather_cache = {"date": today, "data": None}
                return None

            latitude = settings["latitude"]
            longitude = settings["longitude"]
            self._weather_pending = True

            def work() -> None:
                try:
                    data = fetch_daily(latitude, longitude, today)
                except Exception:  # noqa: BLE001 - 天气查不到就按常规节奏提醒
                    data = None
                self._weather_cache = {"date": today, "data": data}
                self._weather_pending = False

            threading.Thread(target=work, name="weather", daemon=True).start()
            return None
        except Exception as error:  # noqa: BLE001
            self._weather_pending = False
            print("Weather skipped:", error, file=sys.stderr)
            return None

    def show_reminder(self) -> None:
        """弹出独立提醒窗口（关闭后销毁，下次重新创建，与原版一致）。"""
        if not self.store.state["settings"].get("popup"):
            return
        if self.reminder_window is None:
            from PySide6.QtCore import Qt

            window = ReminderWindow(self.bridge, web_dir(), packaged=self.store.packaged)
            # 关闭即销毁（原版 `popup.on('closed')` 也是这样把引用清掉的）。
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            window.destroyed.connect(self._on_reminder_destroyed)
            self.reminder_window = window
            window.show()
            return
        self.reminder_window.show_inactive()

    def _on_reminder_destroyed(self) -> None:
        self.reminder_window = None

    def _notify(self, title: str, body: str) -> None:
        self.notifier.notify(title, body, silent=not self.store.state["settings"].get("sound", True))

    def test_reminder(self) -> None:
        """设置页的「发送测试提醒」。"""
        self._notify("南七日程提醒已准备好", "这是一条测试通知。\n" + self.store.encouragement())
        self.show_reminder()

    # =================================================================== 窗口

    def show_main(self) -> None:
        if self.main_window is not None:
            self.main_window.show_main()

    def request_new_task(self) -> None:
        self.show_main()
        self.main_signals.newTask.emit("")

    def edit_task(self, task_id: str) -> None:
        self.show_main()
        self.main_signals.editTask.emit(str(task_id))

    def _on_tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_main()

    # =================================================================== 桌面便签

    def _widget_prefs(self) -> dict:
        return normalize_prefs(
            self.store.state["settings"].get("widget"),
            categories=[c["id"] for c in self.store.state.get("categories", [])],
        )

    def _save_widget_bounds(self, bounds: dict) -> None:
        def mutate(next_state: dict) -> None:
            next_state["settings"]["widget"] = normalize_prefs(
                {**next_state["settings"]["widget"], "bounds": bounds}
            )

        self.store.change(mutate)

    def open_mail(self) -> bool:
        """打开邮箱窗口（收件箱 / 读信 / 写信 / 网页登录）。"""
        if self.store.mail is None:
            return False
        if self.mail_window is None:
            from .windows.mail_window import MailWindow

            self.mail_window = MailWindow(self.store.mail, host=self)
        self.mail_window.show()
        self.mail_window.raise_()
        self.mail_window.activateWindow()
        return True

    def mail_available(self) -> bool:
        return self.store.mail is not None

    def open_widget(self) -> None:
        if not self._widget_prefs()["enabled"]:
            def mutate(next_state: dict) -> None:
                next_state["settings"]["widget"] = normalize_prefs(
                    {**next_state["settings"]["widget"], "enabled": True}
                )

            self.store.change(mutate)

        if self.widget_window is None:
            self.widget_window = WidgetWindow(
                self.bridge,
                web_dir(),
                self._widget_prefs,
                self._save_widget_bounds,
                packaged=self.store.packaged,
                report_error=lambda message: self.bridge.emit_error(_WIDGET_FALLBACK_ERROR + message),
            )
        self.widget_window.show_widget()

    def close_widget(self) -> None:
        if self.widget_window is not None:
            self.widget_window.save_bounds_now()
        if self._widget_prefs()["enabled"]:
            def mutate(next_state: dict) -> None:
                next_state["settings"]["widget"] = normalize_prefs(
                    {**next_state["settings"]["widget"], "enabled": False}
                )

            self.store.change(mutate)
        if self.widget_window is not None:
            self.widget_window.hide_widget()

    def _restore_widget(self) -> None:
        if self._widget_prefs()["enabled"]:
            self.open_widget()

    def update_widget_layer(self) -> None:
        if self.widget_window is not None:
            self.widget_window.settle_layer()

    def set_drag_regions(self, regions) -> None:
        if self.widget_window is not None:
            self.widget_window.set_drag_regions(regions)

    # =================================================================== 内置浏览器

    def browser_available(self) -> bool:
        return is_webengine_available()
    def browser_profile(self):
        """浏览器使用**持久化**配置：登录状态、Cookie 与缓存跨次启动保留。"""
        if self._browser_profile is not None:
            return self._browser_profile
        try:
            from PySide6.QtWebEngineCore import QWebEngineProfile

            root = Path(self.store.data_dir) / "browser"
            (root / "storage").mkdir(parents=True, exist_ok=True)
            (root / "cache").mkdir(parents=True, exist_ok=True)
            profile = QWebEngineProfile("orange-schedule-browser", self)
            profile.setPersistentStoragePath(str(root / "storage"))
            profile.setCachePath(str(root / "cache"))
            profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
            )
            self._browser_profile = profile
        except Exception:  # noqa: BLE001 - 建不了持久化配置就退回默认配置
            self._browser_profile = False
        return self._browser_profile or None

    def open_browser(self, url: str = "") -> bool:
        """打开内置浏览器；组件不可用时退回系统默认浏览器。"""
        if not is_webengine_available():
            if url:
                QDesktopServices.openUrl(QUrl(url))
            return False
        if self.browser_window is None:
            self.browser_window = BrowserWindow(
                profile=self.browser_profile(),
                icon_path=str(icon_path()),
            )
        if url:
            self.browser_window.open_url(url)
        else:
            self.browser_window.show_and_raise()
        return True

    # ============================================== 一键添加（课表 / 二课 / 考试）

    def start_import(self, kind: str = "schedule") -> bool:
        """「一键添加」：打开对应页面，并在浏览器右下角挂「一键添加」浮动按钮。

        【为什么合并成一个入口】
        以前是两个按钮（一键添加课表 / 一键添加二课），用户得先选对按钮，选错了就白点。
        现在主界面只有一个「一键添加」，点开让你选**这次要加什么**（课表 / 二课 / 考试），
        选完直接跳到对应页面；而浏览器里那个浮动按钮是**通用**的——它会自己判断
        当前页面是课表、二课还是考试（`course_import.describe_page` 的 `import` 字段），
        所以哪怕你先打开了课表页再点「二课」，也不会导错。
        """
        spec = IMPORT_SOURCES.get(str(kind or "").strip().lower())
        if spec is None:
            raise ValueError("不支持的导入类型（只支持课表 / 二课 / 考试）")
        if not is_webengine_available():
            self.bridge.emit_error("内置浏览器组件不可用，无法抓取页面")
            return False
        if self.browser_window is None:
            self.browser_window = BrowserWindow(
                profile=self.browser_profile(),
                icon_path=str(icon_path()),
            )
        window = self.browser_window
        window.open_url(spec["url"])
        window.hide_import_result()
        window.set_course_import_mode(True, self._grab_import_page, label="一键添加")
        self._import_busy = False
        # 记住用户这次选的是哪一类：页面上往往同时挂着课表和考试的框架，
        # 「他想要什么」是比「页面看起来像什么」更强的证据（见 describe_page 的 prefer）。
        self._import_prefer = str(kind or "").strip().lower()
        return True



    def _show_import_result(self, message: str, ok: bool = True, seconds: int = 9) -> None:
        """把导入结果告诉用户——**非模态**浮层，不会出现关不掉的窗口。"""
        window = self.browser_window
        if window is not None:
            try:
                window.show_import_result(message, ok=ok, seconds=seconds)
                return
            except Exception:  # noqa: BLE001
                pass
        if not ok:
            self.bridge.emit_error(message)

    def _grab_import_page(self) -> None:
        """浮动按钮点击：抓当前页（含同源 iframe）→ 判断页面类型 → 分派导入。

        三件事保证这个按钮「点了就一定有反应」：
          1. **重入锁**：上一轮还没回来时再点，只提示不叠加（用户连点很常见）；
          2. **超时兜底**：`runJavaScript` 的回调有可能一直不回来（页面正在导航、
             渲染进程还没起来），12 秒没回应就复位并提示，不让按钮永远「死」着；
          3. **非模态反馈**：结果走窗口内的浮层，绝不在脚本回调里弹模态对话框。
        """
        window = self.browser_window
        tab = window.browser.current_tab() if window else None
        page = tab.web_view.page() if tab and tab.web_view else None
        if page is None:
            self._show_import_result("请先在内置浏览器里打开要导入的页面。", ok=False)
            return
        if getattr(self, "_import_busy", False):
            self._show_import_result("上一次导入还在进行，请稍等一下再点。", ok=False)
            return

        self._import_busy = True
        # 每次点击重置「二课接口已试过」的标记：一次点击内只允许试一次接口。
        self._erc_api_attempted = False
        page_url = page.url().toString()
        finished = {"done": False}

        def on_timeout() -> None:
            if finished["done"]:
                return
            finished["done"] = True
            self._import_busy = False
            self._show_import_result(
                "页面没有响应（可能还在加载）。等页面显示完整后再点一次。", ok=False
            )

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(on_timeout)
        timer.start(IMPORT_CAPTURE_TIMEOUT_MS)

        def handle_html(html) -> None:
            if finished["done"]:
                return
            finished["done"] = True
            timer.stop()
            self._import_busy = False
            try:
                self._import_from_html(
                    html or "", url=page_url, prefer=getattr(self, "_import_prefer", "")
                )
            except Exception as error:  # noqa: BLE001 - 导入失败必须说出来，不能静默
                self._show_import_result("导入失败：" + str(error), ok=False)
                self.bridge.emit_error("导入失败：" + str(error))

        # 【关键】不能用 page.toHtml()：它只返回**顶层文档**。教务系统的课表常常
        # 嵌在 iframe 里（有的还套两层），只抓顶层就会「明明看得见、就是认不出」。
        # 这里递归把顶层与所有同源 iframe 的 HTML 拼在一起，用注释标记来源，
        # 解析器对多份文档拼接是宽容的（HTMLParser 会继续往下读）。
        page.runJavaScript(_CAPTURE_PAGE_JS, handle_html)

    # ---------------------------------------------------------- 二课：全量抓取

    def _start_ercourse_crawl(self, page, page_url: str, fallback_html: str) -> None:
        """走页面自己的接口，把**整个学期**的二课一次拉全。

        【为什么不能靠抓当前页的表格】
        团学平台的「我的日程」是**按所选日期过滤**的——抓一次页面只能得到那一天的行
        （用户实测：只点某天再点「一键添加」才有内容）。它前端里有现成接口：
            /personal/scCalendar/getPageUserList?now=YYYY-MM-DD&pageNo=1&pageSize=100
        所以这里在页面里按天并发调这个接口（同源、带登录 cookie），把结果汇总回来。
        接口不可用（改版/未登录）时自动退回「抓当前页表格」，只导入当前这一天。
        """
        self._import_busy = True
        self._erc_api_attempted = True
        self._erc_api_state = {"deadline": time.monotonic() + ERC_CRAWL_TIMEOUT_S}
        self._show_import_result("正在读取二课日程（0 / %d 天）…" % ERC_CRAWL_DAYS, seconds=120)

        def poll() -> None:
            if not self._import_busy:
                return
            if time.monotonic() > self._erc_api_state["deadline"]:
                self._finish_erc_crawl(None, page, page_url, fallback_html, "接口超时")
                return
            try:
                page.runJavaScript(_ERC_CRAWL_READ_JS, on_state)
            except Exception:  # noqa: BLE001 - 页面被关掉/渲染进程没了：别让按钮永远卡住
                self._finish_erc_crawl(None, page, page_url, fallback_html, "页面已关闭")

        def on_state(raw) -> None:
            if not self._import_busy:
                return
            payload = {}
            if isinstance(raw, str) and raw:
                try:
                    import json as _json

                    payload = _json.loads(raw) or {}
                except ValueError:
                    payload = {}
            if payload.get("done"):
                records = payload.get("records") or []
                self._finish_erc_crawl(records, page, page_url, fallback_html, payload.get("message") or "")
                return
            total = payload.get("total") or ERC_CRAWL_DAYS
            done = payload.get("progress") or 0
            self._show_import_result(
                f"正在读取二课日程（{done} / {total} 天）…", seconds=120
            )
            self._erc_timer.start(250)

        self._erc_timer = QTimer(self)
        self._erc_timer.setSingleShot(True)
        self._erc_timer.timeout.connect(poll)
        page.runJavaScript(_ERC_CRAWL_JS, lambda _started: self._erc_timer.start(300))

    def _finish_erc_crawl(self, records, page, page_url: str, fallback_html: str, message: str) -> None:
        from .course_erc import parse_erc_api_records

        try:
            self._erc_timer.stop()
        except Exception:  # noqa: BLE001
            pass
        items = parse_erc_api_records(records or [])
        if not items:
            # 接口没给东西（没登录 / 站点改版）：退回「抓当前页表格」，并说清楚只覆盖当天。
            self._import_busy = False
            from .course_erc import parse_ercourse

            day_items = parse_ercourse(fallback_html)
            if not day_items:
                self._show_import_result(
                    "没能从二课页面读到日程（接口和表格都没数据）。"
                    "请确认已登录、并且「我的日程」里能看到记录。"
                    + (f"（{message}）" if message else ""),
                    ok=False,
                )
                return
            created = self.importer._create_ercourse_tasks(day_items)
            skipped = self.importer._last_import_skipped
            tail = f"，跳过已过期 {skipped} 项" if skipped else ""
            self._show_import_result(
                f"接口不可用，只导入了当前选中日期的 {created} 项二课{tail}。"
                "（想在浏览器里看到全部，请确认已登录后重试）",
                ok=False,
            )
            return

        self._import_busy = False
        created = self.importer._create_ercourse_tasks(items)
        skipped = self.importer._last_import_skipped
        detail = ""
        if skipped:
            detail += f"，跳过已过期 {skipped} 项"
        self._show_import_result(
            f"已导入 {created} 项二课日程（共读到 {len(items)} 条{detail}）。"
        )

    def _import_from_html(self, html: str, url: str = "", prefer: str = "") -> None:
        """把抓到的整页 HTML 按「它到底是什么页面」导入，并给出结果文案。"""
        from .course_import import describe_page

        report = describe_page(html, url=url, prefer=prefer)
        kind = report.get("import") or ""
        label = IMPORT_LABELS.get(kind, "页面")

        from .jsjson import dumps
        diagnostic = {"kind": kind or "unknown", "htmlBytes": len(html.encode("utf-8")), "at": datetime.now().isoformat(timespec="seconds")}
        (Path(self.store.data_dir) / "import-diagnostic.json").write_text(dumps(diagnostic), encoding="utf-8")

        if not kind:
            self._show_import_result(report["hint"], ok=False)
            return

        if kind == "schedule":
            from .course_import import parse_course_html

            courses = parse_course_html(html)
            if not courses:
                self._show_import_result(
                    "识别到课表页面，但没能取出课程。" + report["hint"], ok=False
                )
                return
            created = self.importer._create_course_tasks(courses, self._resolve_semester_start())
            skipped = self.importer._last_import_skipped
            detail = f"，跳过已过期 {skipped} 项" if skipped else ""
            self._show_import_result(
                f"已导入 {created} 项课程日程（来自 {len(courses)} 个上课时段{detail}）。"
                ""
            )
            return

        if kind == "ercourse":
            # 二课页面是「按日期过滤」的：先走接口拉全量，接口不行才用这份 HTML 里那一天。
            # 只在团学平台（有那个接口）上才尝试，别的站点白白打 150 个请求没有意义。
            if not getattr(self, "_erc_api_attempted", False) and erc_api_allowed(url):
                window = self.browser_window
                tab = window.browser.current_tab() if window else None
                page = tab.web_view.page() if tab and tab.web_view else None
                if page is not None:
                    self._start_ercourse_crawl(page, url, html)
                    return
            from .course_erc import parse_ercourse

            items = parse_ercourse(html)
            if not items:
                self._show_import_result("识别到二课页面，但没能取出记录。", ok=False)
                return
            created = self.importer._create_ercourse_tasks(items)
            detail = "" if created else "（这些二课之前已经导入过）"
            self._show_import_result(
                f"已导入 {created} 项二课日程{detail}。"
            )
            return

        if kind == "exam":
            from .course_exam import parse_exam_html

            items = parse_exam_html(html)
            if not items:
                self._show_import_result(
                    "识别到考试页面，但没能取出考试安排（可能时间列是「第 N 周」这种相对写法）。"
                    "请检查页面中的日期和时间。",
                    ok=False,
                )
                return
            created = self.importer._create_exam_tasks(items)
            skipped = self.importer._last_import_skipped
            detail = "" if created else "（这些考试之前已经导入过）"
            if skipped:
                detail += f"，跳过已过期 {skipped} 项"
            self._show_import_result(
                f"已导入 {created} 项考试日程{detail}。"
            )
            return

        self._show_import_result(report["hint"], ok=False)




    def _resolve_semester_start(self):
        """自动确定「第一周周一」——**不再弹对话框让用户确认**。

        课表 HTML 里通常没有学期起点，而把「第一周周一是哪天」这种换算推给用户，
        正是这次反馈里最刺眼的一点。默认取**本周周一**作为第 1 周：
        这样导入的课程立刻落在当前这一周的日历里，用户马上能看到结果；
        若与实际教学周不符，后面可以整体平移，而不是在导入时卡住他。
        """
        from datetime import date, timedelta

        today = date.today()
        return today - timedelta(days=today.weekday())


    def shift_courses(self, days) -> dict:
        """把**课程**日程整体平移 N 天，用来对齐真实教学周。

        【为什么需要它】
        课表 HTML 里通常没有「学期第一周是哪天」，导入时默认把**本周周一**当第 1 周。
        如果实际教学周不是这样，整学期的课就会整体错位。与其让用户在导入时回答一个
        他多半也不知道的问题（旧版的「第一周周一」对话框就是这么被否掉的），
        不如导完之后让他用真实课表一对，错多少天就用这个按钮挪多少天。

        只动 `category == "course"` 的计划：二课的日期是**确定的某一天**（团学活动那天），
        平移它会凭空造出错误日程，所以排除。
        """
        from datetime import timedelta

        try:
            offset = int(days)
        except (TypeError, ValueError):
            raise ValueError("平移天数必须是整数") from None
        if offset == 0:
            return {**self.store.snapshot(), "shiftedCourses": 0}
        if abs(offset) > 365:
            raise ValueError("平移天数不能超过 365 天")
        delta = timedelta(days=offset)

        def mutate(next_state: dict) -> None:
            holder["moved"] = 0
            for task in next_state["tasks"]:
                if task.get("category") != "course":
                    continue
                try:
                    current = datetime.strptime(task["date"], "%Y-%m-%d")
                except (KeyError, TypeError, ValueError):
                    continue
                task["date"] = (current + delta).strftime("%Y-%m-%d")
                holder["moved"] += 1

        holder: dict[str, int] = {"moved": 0}
        result = self.store.change(mutate)
        return {**result, "shiftedCourses": holder["moved"]}

    # =================================================================== 备份

    def export_data(self):
        parent = self.main_window if self.main_window is not None else None
        path, _selected = QFileDialog.getSaveFileName(
            parent,
            "导出南七日程备份",
            self.store.suggested_export_path(),
            "JSON 备份 (*.json)",
        )
        if not path:
            return None
        return self.store.export_to(path)

    def import_data(self):
        parent = self.main_window if self.main_window is not None else None
        path, _selected = QFileDialog.getOpenFileName(
            parent,
            "导入南七日程备份",
            "",
            "JSON 备份 (*.json)",
        )
        if not path:
            return None
        incoming = self.store.load_backup(path)

        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("导入南七日程备份")
        box.setText(f"导入 {len(incoming['tasks'])} 项计划？")
        box.setInformativeText(
            "相同 ID 的计划将使用备份中的内容，其余现有计划保留。导入前会自动保存完整备份。"
        )
        cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        confirm = box.addButton("导入并合并", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is not confirm:
            return None

        self.store.write_side_backup("before-import")
        if self.store._load_failed:
            return self.store.restore_backup(incoming)
        return self.store.change(lambda next_state: merge_import(next_state, incoming))

    def open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.data_dir)))

    # =================================================================== 附件

    def pick_attachments(self) -> list[dict]:
        """文件选择框（计划的附件）；取消时返回空列表。"""
        parent = self.main_window if self.main_window is not None else None
        paths, _selected = QFileDialog.getOpenFileNames(parent, "选择附件")
        result = []
        for path in paths:
            name = os.path.basename(path)
            validate_attachment(path, [path])
            self._picked_attachments.add(path)
            result.append({"name": name, "path": path})
        return result

    def open_attachment(self, path) -> None:
        registered = list(self._picked_attachments) + [a["path"] for t in self.store.state["tasks"] for a in t.get("attachments", [])]
        target = validate_attachment(path, registered)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            raise ValueError("系统无法打开此附件")
        return None

    # =================================================================== 更新


    # =================================================================== 退出

    def request_quit(self) -> None:
        """彻底退出（托盘菜单与设置页的「彻底退出」都走这里）。

        【为什么不是简单一句 app.quit()】
        实测（PySide6 6.12 + QtWebEngine，Windows 11）：当半透明无边框便签窗口与
        内置浏览器窗口**同时打开**时，`app.quit()` 会返回但主事件循环不退出，
        进程留在后台不走（托盘图标一直挂着，而单实例机制会让用户再也打不开程序）。
        逐个排查后的结论是这一类窗口组合下的退出流程不可靠，因此这里做三件事：

          1. **先把便签位置落盘**——退出流程万一卡住，用户数据也已经保存；
          2. **先隐藏所有窗口再退出**：隐藏之后 `app.exit(0)` 能稳定结束
             （只做其中任何一件都不够，两件一起做才稳）；
          3. **arm 一个看门狗**：5 秒内进程还没结束就强制结束自己，
             绝不把「点了退出却还在后台」留给用户。

        用 `exit(0)` 而不是 `quit()`：两者在 Qt 文档里等价，但实测只有前者
        在上述窗口组合下真正让事件循环返回。
        """
        if self.quitting:
            return
        self.quitting = True

        try:
            if self.widget_window is not None:
                self.widget_window.save_bounds_now()
        except Exception:  # noqa: BLE001
            pass

        for window in (
            self.widget_window,
            self.browser_window,
            self.reminder_window,
            self.main_window,
        ):
            try:
                if window is not None:
                    window.hide()
            except Exception:  # noqa: BLE001
                pass

        if self._tray is not None:
            try:
                self._tray.hide()
            except Exception:  # noqa: BLE001
                pass

        self._arm_quit_watchdog()
        self.app.exit(0)

    def _arm_quit_watchdog(self, timeout_s: float = 5.0) -> None:
        """兜底：正常退出流程若在 timeout 内没结束，直接结束进程。"""

        def watch() -> None:
            time.sleep(timeout_s)
            try:
                print("[退出] 正常退出超时，强制结束进程", file=sys.stderr, flush=True)
            except Exception:  # noqa: BLE001
                pass
            os._exit(0)

        threading.Thread(target=watch, name="quit-watchdog", daemon=True).start()

    def _on_about_to_quit(self) -> None:
        self.quitting = True
        if self._tick_timer is not None:
            self._tick_timer.stop()
        try:
            if self.widget_window is not None:
                self.widget_window.save_bounds_now()
                self.widget_window.allow_close()
                self.widget_window.hide()
        except Exception:  # noqa: BLE001
            pass
        for window in (self.main_window, self.reminder_window, self.browser_window):
            try:
                if window is not None:
                    window.hide()
            except Exception:  # noqa: BLE001
                pass
        # 【不要在这里 close() 或 deleteLater() 浏览器窗口/配置】
        # 实测在 PySide6 6.12 + QtWebEngine 下，退出流程里销毁 QWebEngineView 或
        # 删除 QWebEngineProfile 会把 app.exec() 卡住不返回。隐藏就足够了：
        # 进程结束时会自然释放，代价是运行期间数据目录里的 browser/ 子目录被占用
        # （与 Electron 版占用 userData 的情况一样）。
        if self._tray is not None:
            self._tray.hide()
        if self._power_filter is not None:
            try:
                self.app.removeNativeEventFilter(self._power_filter)
            except Exception:  # noqa: BLE001
                pass
        # 让 Qt 处理掉上面的隐藏/销毁，减少带 WebEngine 退出时的收尾异常。
        self.app.processEvents()

    # =================================================================== 便签回调

    def allow_widget_close(self) -> None:
        if self.widget_window is not None:
            self.widget_window.allow_close()

    def mark_main_allowed(self) -> None:
        if self.main_window is not None:
            self.main_window.allow_close()


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _today_key() -> str:
    """本地日期 `YYYY-MM-DD`（与计划里的日期字段同一口径）。"""
    return datetime.now().strftime("%Y-%m-%d")


#: 允许走「二课接口全量抓取」的站点。生产上只有团学平台；
#: 测试可以用 `NANQI_ERC_API_HOSTS=127.0.0.1` 追加（见 tests/ui_erc_api_smoke.py），
#: 这样就能对着本地假接口把整条链路跑一遍，而不必登录真实站点。
ERC_API_HOSTS = ("young.ustc.edu.cn",)


def erc_api_allowed(url: str) -> bool:
    text = (url or "").lower()
    host = ""
    if "://" in text:
        host = text.split("://", 1)[1].split("/", 1)[0]
    extra = os.environ.get("NANQI_ERC_API_HOSTS", "")
    hosts = list(ERC_API_HOSTS) + [item.strip().lower() for item in extra.split(",") if item.strip()]
    return any(item and host.split(":", 1)[0] == item for item in hosts)






