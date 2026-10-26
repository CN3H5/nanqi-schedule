"""
view.py —— 内置浏览器（基于 QtWebEngine）
=========================================

【移植说明】
本文件从 ustc-assistant 的 `ui/browser_view.py` 移植并做了轻量适配，保留原来的
结构、注释风格与交互行为，只改了三类东西：
  1. 主题依赖：原来 `from ui import theme`，现在 `from . import palette as theme`，
     浏览器不再依赖日历 UI 代码；
  2. 首页文案与书签：标题/副标题换成南七日程的说法，书签在原 12 个科大站点之外
     补了 4 个常用站点（共 16 个）；
  3. 新增 `BrowserWindow`：日历应用需要一个"自己独立开一个窗口、关掉时只是隐藏、
     下次还能复用"的浏览器外壳，原文件只有内嵌的 BrowserView。

【这个文件解决什么问题】
在应用里内置一个浏览器，用于打开网页。科大师生日常要访问教务系统、图书馆、邮件
系统等页面，内置浏览器可以不用离开应用就快速查看，省去切窗口的麻烦。

【实现方案】
使用 PySide6 的 QtWebEngine 模块（基于 Chromium），提供：
- 地址栏：输入 URL 或搜索词并导航
- 前进/后退/刷新/主页：标准浏览器导航按钮
- 书签：常用网页快速访问（科大教务、图书馆等）
- 多标签页：支持同时打开多个网页，可拖动排序
- 主页：快速访问常用链接
- 独立窗口外壳：可指定持久化 Profile、快捷键、用系统浏览器打开当前页

【为什么用 QtWebEngine】
QtWebEngine 是 Qt 6 推荐的 Web 引擎，基于最新 Chromium，支持现代 Web 标准，
PySide6 提供了完整的绑定。注意 QtWebEngine 的渲染跑在独立子进程
（QtWebEngineProcess.exe）里，主进程只是壳：打包或换机器时如果渲染进程起不来、
或者 GL 上下文创建失败（远程桌面/虚拟化/驱动问题很常见），表现就是"窗口在、
网页永远白屏"。所以启动前通常要设好 Chromium 开关（如 --disable-gpu /
--no-sandbox）。这部分不是本文件的职责——南七日程在别处统一处理启动环境，
这里只负责界面与行为。

【导入期无副作用】
本模块在任何地方都不要求先有 QApplication、也不要求有显示器：QtWebEngine 的
导入是防御式的（缺失时降级成一页提示），所有控件都在函数体里创建。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QWidget, QLineEdit, QPushButton,
    QTabBar, QStackedWidget, QFrame, QLabel, QScrollArea,
    QGridLayout, QMainWindow,
)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    WEBENGINE_AVAILABLE = True
except ImportError:
    QWebEngineView = None
    QWebEnginePage = None
    QWebEngineProfile = None
    WEBENGINE_AVAILABLE = False

from . import palette as theme


def is_webengine_available() -> bool:
    """QtWebEngine 是否可用（缺 PySide6-WebEngine 时为 False）。

    调用方可以据此决定要不要给用户一句"请安装 PySide6-WebEngine"的提示，
    而不是让窗口默默开出一片空白。
    """
    return WEBENGINE_AVAILABLE


# 书签：前 12 条来自原 ustc-assistant 版本（「科大教务在线」的地址已按用户反馈
# 更正为 https://jw.ustc.edu.cn/home），后面 4 条是南七日程新增的通用站点。
BOOKMARKS = [
    {"name": "科大教务在线", "url": "https://jw.ustc.edu.cn/home", "icon": "📚"},
    {"name": "科大图书馆", "url": "https://lib.ustc.edu.cn", "icon": "📖"},
    {"name": "科大邮件", "url": "https://mail.ustc.edu.cn", "icon": "✉"},
    {"name": "科大网盘", "url": "https://pan.ustc.edu.cn", "icon": "💾"},
    {"name": "中国科大", "url": "https://www.ustc.edu.cn", "icon": "🏫"},
    {"name": "教务处", "url": "https://www.teach.ustc.edu.cn", "icon": "🎓"},
    {"name": "研究生院", "url": "https://gradschool.ustc.edu.cn", "icon": "🎯"},
    {"name": "信息门户", "url": "https://portal.ustc.edu.cn", "icon": "🔑"},
    {"name": "Blackboard", "url": "https://blackboard.ustc.edu.cn", "icon": "📝"},
    {"name": "GitHub", "url": "https://github.com", "icon": "🐙"},
    {"name": "百度", "url": "https://www.baidu.com", "icon": "🔍"},
    {"name": "知乎", "url": "https://www.zhihu.com", "icon": "💡"},
    {"name": "必应", "url": "https://cn.bing.com", "icon": "🔎"},
    {"name": "豆瓣", "url": "https://www.douban.com", "icon": "🎬"},
    {"name": "中国知网", "url": "https://www.cnki.net", "icon": "📑"},
    {"name": "天气", "url": "https://weather.cma.cn", "icon": "⛅"},
]


class BrowserTab(QWidget):
    """单个浏览器标签页（地址栏 + WebEngine 视图）。"""

    title_changed = Signal(str, str)      # (标题, URL)：外层拿来更新标签页文字
    home_requested = Signal()             # 点了 ⌂：外层切回书签主页

    def __init__(self, url: str = "", parent=None):
        super().__init__(parent)
        self._build_ui()
        if url:
            self.navigate(url)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QFrame()
        toolbar.setObjectName("BrowserToolbar")
        toolbar.setFixedHeight(44)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 4, 8, 4)
        tb_layout.setSpacing(6)

        self.back_btn = QPushButton("◀")
        self.back_btn.setObjectName("BrowserNavButton")
        self.back_btn.setFixedSize(32, 32)
        self.back_btn.setToolTip("后退")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.clicked.connect(self._go_back)
        tb_layout.addWidget(self.back_btn)

        self.forward_btn = QPushButton("▶")
        self.forward_btn.setObjectName("BrowserNavButton")
        self.forward_btn.setFixedSize(32, 32)
        self.forward_btn.setToolTip("前进")
        self.forward_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.forward_btn.clicked.connect(self._go_forward)
        tb_layout.addWidget(self.forward_btn)

        self.reload_btn = QPushButton("⟳")
        self.reload_btn.setObjectName("BrowserNavButton")
        self.reload_btn.setFixedSize(32, 32)
        self.reload_btn.setToolTip("刷新")
        self.reload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reload_btn.clicked.connect(self._reload)
        tb_layout.addWidget(self.reload_btn)

        self.home_btn = QPushButton("⌂")
        self.home_btn.setObjectName("BrowserNavButton")
        self.home_btn.setFixedSize(32, 32)
        self.home_btn.setToolTip("主页")
        self.home_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.home_btn.clicked.connect(self._go_home)
        tb_layout.addWidget(self.home_btn)

        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("BrowserUrlBar")
        self.url_edit.setPlaceholderText("输入网址或搜索内容后回车访问...")
        self.url_edit.setFixedHeight(32)
        self.url_edit.returnPressed.connect(self._on_url_entered)
        tb_layout.addWidget(self.url_edit, 1)

        self.go_btn = QPushButton("访问")
        self.go_btn.setObjectName("PrimaryButton")
        self.go_btn.setFixedHeight(32)
        self.go_btn.setFixedWidth(64)
        self.go_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go_btn.clicked.connect(self._on_url_entered)
        tb_layout.addWidget(self.go_btn)

        layout.addWidget(toolbar)

        if WEBENGINE_AVAILABLE:
            self.web_view = QWebEngineView()
            self.web_view.urlChanged.connect(self._on_url_changed)
            self.web_view.titleChanged.connect(self._on_title_changed)
            self.web_view.loadProgress.connect(self._on_load_progress)
            layout.addWidget(self.web_view, 1)
        else:
            fallback = QLabel(
                "浏览器组件不可用\n\n"
                "请确保已安装 PySide6-WebEngine：\n"
                "pip install PySide6-WebEngine\n\n"
                "或在 requirements.txt 中添加依赖后重新安装。"
            )
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet(
                f"color: {theme.TEXT_WEAK}; font-size: 14px;"
                f" background: {theme.SURFACE}; padding: 40px;"
            )
            layout.addWidget(fallback, 1)
            self.web_view = None

        # 顶部那条 2px 的细进度条：比整块遮罩轻，加载时给一点"在动"的反馈
        self.progress_bar = QFrame()
        self.progress_bar.setFixedHeight(2)
        self.progress_bar.setStyleSheet(
            f"background: transparent;"
        )
        self._progress_indicator = QLabel(self.progress_bar)
        self._progress_indicator.setFixedHeight(2)
        self._progress_indicator.setFixedWidth(0)
        self._progress_indicator.setStyleSheet(
            f"background: {theme.PRIMARY};"
        )
        layout.addWidget(self.progress_bar)

    def navigate(self, url: str) -> None:
        """把用户输入的一串文本变成可访问的 URL 并加载。

        判断顺序（在 ustc-assistant 原版基础上只把本地地址提前，其余不动）：
          - localhost / 127. 开头 → 本地开发服务，用 http://
            原版这一条排在"带点"分支之后，而 127.0.0.1:5000 本身带点、又不带空格，
            会先被判成域名补上 https://，本地服务多半没开 TLS 就打不开了；
            所以这里把本地地址提到最前面（只影响 127.* 与 localhost* 这两类输入）。
          - 带点且不含空格 → 当域名，补 https://
          - 已经带协议头（http/https/file/about/data）→ 原样使用
          - 其余（裸词 python、带空格的句子）→ 交给百度搜索
        """
        if not url:
            return
        if not url.startswith(("http://", "https://", "file://", "about:", "data:")):
            if url.startswith("localhost") or url.startswith("127."):
                url = "http://" + url                    # 本地开发服务用 http
            elif "." in url and " " not in url:
                url = "https://" + url
            else:
                url = f"https://www.baidu.com/s?wd={url}"
        self.url_edit.setText(url)
        if self.web_view:
            self.web_view.setUrl(QUrl(url))

    def _on_url_entered(self) -> None:
        url = self.url_edit.text().strip()
        self.navigate(url)

    def _on_url_changed(self, url: QUrl) -> None:
        self.url_edit.setText(url.toString())
        title = self.web_view.title() if self.web_view else "新标签页"
        self.title_changed.emit(title or url.host(), url.toString())

    def _on_title_changed(self, title: str) -> None:
        url = self.web_view.url().toString() if self.web_view else ""
        self.title_changed.emit(title or "新标签页", url)

    def _on_load_progress(self, progress: int) -> None:
        if 0 < progress < 100:
            width = int(self.progress_bar.width() * progress / 100)
            self._progress_indicator.setFixedWidth(max(0, width))
        else:
            self._progress_indicator.setFixedWidth(0)

    def _go_back(self) -> None:
        if self.web_view and self.web_view.history().canGoBack():
            self.web_view.back()

    def _go_forward(self) -> None:
        if self.web_view and self.web_view.history().canGoForward():
            self.web_view.forward()

    def _reload(self) -> None:
        if self.web_view:
            self.web_view.reload()

    def _go_home(self) -> None:
        self.url_edit.clear()
        self.home_requested.emit()         # 让外层切回书签主页，而不是对着空白页发呆

    def current_url(self) -> str:
        """当前页面的 URL（没有 WebEngine 或还没导航过时返回空串）。

        给"用系统浏览器打开"这类外壳功能用，省得外面到处判空。
        """
        if not self.web_view:
            return ""
        return self.web_view.url().toString()


class BrowserHomePage(QWidget):
    """浏览器主页（快速访问书签）。"""

    navigate_to = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {theme.BG}; border: none; }}"
            f"QScrollBar {{ background: transparent; width: 8px; }}"
            f"QScrollBar::handle {{ background: {theme.BORDER}; border-radius: 4px; }}"
        )

        container = QWidget()
        container.setStyleSheet(f"background: {theme.BG};")
        inner = QVBoxLayout(container)
        inner.setContentsMargins(40, 30, 40, 30)
        inner.setSpacing(20)

        title = QLabel("南七日程 · 内置浏览器")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"font-size: 28px; font-weight: bold; color: {theme.PRIMARY};"
            f" background: transparent;"
        )
        inner.addWidget(title)

        subtitle = QLabel("快速访问常用网站，含科大校内服务")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            f"font-size: 14px; color: {theme.TEXT_WEAK};"
            f" background: transparent;"
        )
        inner.addWidget(subtitle)

        inner.addSpacing(20)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索或输入网址...")
        self.search_edit.setFixedHeight(42)
        self.search_edit.returnPressed.connect(self._on_search)
        self.search_edit.setStyleSheet(
            f"QLineEdit {{ background: {theme.SURFACE}; color: {theme.TEXT};"
            f" border: 2px solid {theme.BORDER}; border-radius: 21px;"
            f" padding: 0 20px; font-size: 15px; }}"
            f"QLineEdit:focus {{ border-color: {theme.PRIMARY}; }}"
        )
        search_row.addWidget(self.search_edit, 1)

        search_btn = QPushButton("搜索")
        search_btn.setFixedHeight(42)
        search_btn.setFixedWidth(80)
        search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        search_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PRIMARY}; color: white;"
            f" border: none; border-radius: 21px; font-size: 14px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {theme.PRIMARY_LIGHT}; }}"
        )
        search_btn.clicked.connect(self._on_search)
        search_row.addWidget(search_btn)
        inner.addLayout(search_row)

        inner.addSpacing(20)

        grid_label = QLabel("常用网站")
        grid_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {theme.TEXT};"
            f" background: transparent;"
        )
        inner.addWidget(grid_label)

        # 固定 4 列的响应式网格：窗口变宽时卡片自己拉伸，列数不变
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, bm in enumerate(BOOKMARKS):
            card = self._bookmark_card(bm)
            grid.addWidget(card, i // 4, i % 4)
        inner.addLayout(grid)

        inner.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

    def _bookmark_card(self, bm: dict) -> QWidget:
        card = QFrame()
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setFixedHeight(80)
        card.setToolTip(bm["url"])          # 卡片窄的时候 URL 会被截断，悬停能看全
        card.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER};"
            f" border-radius: 10px; }}"
            f"QFrame:hover {{ background: {theme.SURFACE_HOVER};"
            f" border-color: {theme.PRIMARY}; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(4)

        icon_label = QLabel(bm.get("icon", "🌐"))
        icon_label.setStyleSheet("font-size: 20px; background: transparent;")
        card_layout.addWidget(icon_label)

        name_label = QLabel(bm["name"])
        name_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {theme.TEXT};"
            f" background: transparent;"
        )
        card_layout.addWidget(name_label)

        url_label = QLabel(bm["url"])
        url_label.setStyleSheet(
            f"font-size: 10px; color: {theme.TEXT_FAINT};"
            f" background: transparent;"
        )
        url_label.setWordWrap(True)
        card_layout.addWidget(url_label)

        card.mousePressEvent = lambda e, url=bm["url"]: self.navigate_to.emit(url)
        return card

    def _on_search(self) -> None:
        """主页搜索框：和地址栏一样的判断，只是没有协议头分支（输入更短更简单）。"""
        text = self.search_edit.text().strip()
        if text:
            if text.startswith("localhost") or text.startswith("127."):
                self.navigate_to.emit("http://" + text)     # 本地开发服务用 http
            elif "." in text and " " not in text:
                self.navigate_to.emit("https://" + text)
            else:
                self.navigate_to.emit(f"https://www.baidu.com/s?wd={text}")


class BrowserView(QWidget):
    """内置浏览器主视图（多标签页 + 主页）。

    `_tabs` 与 `tab_bar` 的下标严格一一对应，`page_stack` 里额外多一个主页。
    关闭最后一个标签页时回到主页（而不是留个空壳窗口）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tabs: list[BrowserTab] = []
        self._profile = None                # 由 set_profile() 注入的持久化 Profile
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        tab_bar_row = QFrame()
        tab_bar_row.setFixedHeight(36)
        tab_bar_row.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE};"
            f" border-bottom: 1px solid {theme.BORDER}; }}"
        )
        tab_bar_layout = QHBoxLayout(tab_bar_row)
        tab_bar_layout.setContentsMargins(4, 2, 4, 2)
        tab_bar_layout.setSpacing(2)

        self.tab_bar = QTabBar()
        self.tab_bar.setDocumentMode(True)
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setMovable(True)
        # 标签太多时从右侧省略，而不是把后面的标签挤出可视区
        self.tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.tab_bar.setStyleSheet(
            f"QTabBar {{ background: transparent; }}"
            f"QTabBar::tab {{ background: {theme.SURFACE_ALT}; color: {theme.TEXT_WEAK};"
            f" padding: 6px 16px; border-radius: 6px 6px 0 0; margin-right: 2px;"
            f" font-size: 12px; max-width: 180px; }}"
            f"QTabBar::tab:selected {{ background: {theme.SURFACE}; color: {theme.TEXT};"
            f" font-weight: bold; }}"
            f"QTabBar::tab:hover {{ background: {theme.SURFACE_HOVER}; }}"
            f"QTabBar::close-button {{ subcontrol-position: right;"
            f" color: {theme.TEXT_FAINT}; }}"
            f"QTabBar::close-button:hover {{ color: {theme.DANGER}; }}"
        )
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        self.tab_bar.tabCloseRequested.connect(self._close_tab)
        tab_bar_layout.addWidget(self.tab_bar, 1)

        new_tab_btn = QPushButton("＋")
        new_tab_btn.setFixedSize(28, 28)
        new_tab_btn.setToolTip("新建标签页")
        new_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_tab_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.TEXT_WEAK};"
            f" border: none; border-radius: 4px; font-size: 16px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {theme.SURFACE_HOVER};"
            f" color: {theme.PRIMARY}; }}"
        )
        new_tab_btn.clicked.connect(self._add_tab)
        tab_bar_layout.addWidget(new_tab_btn)

        # 原版没有这个按钮：内核里偶尔会遇到登录/兼容性问题的页面，
        # 留一个"丢给系统默认浏览器"的出口，比让用户复制地址强。
        self.system_btn = QPushButton("用系统浏览器打开")
        self.system_btn.setFixedHeight(28)
        self.system_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.system_btn.setToolTip("用系统默认浏览器打开当前页面")
        self.system_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.TEXT_WEAK};"
            f" border: 1px solid {theme.BORDER}; border-radius: 4px;"
            f" padding: 0 10px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {theme.SURFACE_HOVER};"
            f" color: {theme.PRIMARY}; border-color: {theme.PRIMARY}; }}"
        )
        self.system_btn.clicked.connect(self._open_in_system_browser)
        tab_bar_layout.addWidget(self.system_btn)

        layout.addWidget(tab_bar_row)

        self.page_stack = QStackedWidget()
        self.page_stack.setStyleSheet("background: transparent;")

        self.home_page = BrowserHomePage()
        self.home_page.navigate_to.connect(self._navigate_from_home)
        self.page_stack.addWidget(self.home_page)

        layout.addWidget(self.page_stack, 1)

        self._add_tab()

    # ---------------------------------------------------------------- 标签页

    def _add_tab(self, url: str = "") -> None:
        if not WEBENGINE_AVAILABLE:
            return
        tab = BrowserTab(url)
        tab.title_changed.connect(lambda title, u, t=tab: self._on_tab_title(t, title, u))
        tab.home_requested.connect(self._show_home)
        self._tabs.append(tab)
        self.page_stack.addWidget(tab)
        idx = self.tab_bar.addTab("新标签页")
        self.tab_bar.setCurrentIndex(idx)
        self.page_stack.setCurrentWidget(tab)
        # 新建的标签页也要用持久化 Profile（否则新开的标签又没有 Cookie）
        self._apply_profile_to_tab(tab)
        return tab

    def _close_tab(self, index: int) -> None:
        if index < 0 or index >= len(self._tabs):
            return
        tab = self._tabs.pop(index)
        self.page_stack.removeWidget(tab)
        tab.deleteLater()
        self.tab_bar.removeTab(index)
        if not self._tabs:
            self._show_home()               # 关光了就回主页，别留一扇空窗

    def _on_tab_changed(self, index: int) -> None:
        if index < 0:
            self._show_home()
            return
        if index < len(self._tabs):
            self.page_stack.setCurrentWidget(self._tabs[index])

    def _on_tab_title(self, tab: BrowserTab, title: str, url: str) -> None:
        idx = self._tabs.index(tab) if tab in self._tabs else -1
        if idx >= 0:
            display = title[:20] + "..." if len(title) > 20 else title
            self.tab_bar.setTabText(idx, display or "新标签页")

    def _show_home(self) -> None:
        self.page_stack.setCurrentWidget(self.home_page)

    def _navigate_from_home(self, url: str) -> None:
        self._add_tab(url)

    def open_url(self, url: str) -> None:
        self._add_tab(url)
        self.show()
        self.raise_()

    def refresh(self) -> None:
        pass

    # ---------------------------------------------------------------- Profile

    def set_profile(self, profile) -> None:
        """注入持久化 QWebEngineProfile（Cookie/登录态可跨启动保留）。

        必须在已有标签页上也生效：它们在 set_profile() 之前建成，用的还是默认
        Profile（默认是 off-the-record，关掉就丢登录态），所以要把页面换掉。
        """
        self._profile = profile
        for tab in list(self._tabs):
            self._apply_profile_to_tab(tab)

    def _apply_profile_to_tab(self, tab: BrowserTab) -> None:
        """把一个标签页的页面换成指定 Profile 的页面，并保持当前 URL。

        这里刻意整段包 try/except：Profile 不匹配（比如页面已被销毁、或 Profile
        属于别的线程）只能在运行时暴露，而"换个 Profile 失败"绝不该让整个浏览器
        崩掉或者白屏——失败就退回原来的页面，用户顶多丢一次登录态。
        """
        if self._profile is None or getattr(tab, "web_view", None) is None:
            return
        if QWebEnginePage is None:
            return
        try:
            old_url = tab.web_view.url()     # 换页面前先记住当前地址
            page = QWebEnginePage(self._profile, tab.web_view)
            tab.web_view.setPage(page)
            if not old_url.isEmpty():
                tab.web_view.setUrl(old_url)  # 换完再把地址加载回来
        except Exception:
            # Profile 相关失败一律忽略：宁可继续用旧页面，也不要打断浏览
            pass

    # ---------------------------------------------------------------- 其它

    def current_tab(self) -> BrowserTab | None:
        """当前选中的标签页（停在主页时返回 None）。"""
        idx = self.tab_bar.currentIndex()
        if 0 <= idx < len(self._tabs):
            return self._tabs[idx]
        return None

    def focus_address_bar(self) -> None:
        """快捷键 Ctrl+L / 新标签页：把焦点送进地址栏并全选，直接覆盖输入。"""
        tab = self.current_tab()
        if tab is None:
            tab = self._add_tab()
        if tab is None:
            return
        tab.url_edit.setFocus()
        tab.url_edit.selectAll()

    def reload_current(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab._reload()

    def back_current(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab._go_back()

    def forward_current(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab._go_forward()

    def close_current(self) -> None:
        idx = self.tab_bar.currentIndex()
        if idx >= 0:
            self._close_tab(idx)

    def _open_in_system_browser(self) -> None:
        """用系统默认浏览器打开当前标签页的地址（没标签/没地址就什么都不做）。"""
        tab = self.current_tab()
        if tab is None:
            return
        url = tab.current_url()
        if not url:
            return
        QDesktopServices.openUrl(QUrl(url))


class BrowserWindow(QMainWindow):
    """独立窗口形态的内置浏览器（日历应用用 this 打开"浏览器"窗口）。

    设计要点：
      - 生命周期由调用方掌握：默认 closeEvent 只 hide()，窗口留着下次复用；
        真要退出程序时调 allow_close() 再 close()。
      - 可注入 QWebEngineProfile，让所有标签页共用一份 Cookie/缓存。
      - 快捷键、工具栏按钮这类"浏览器该有的东西"都收在这里，
        BrowserView 保持和内嵌版本一致。
    """

    def __init__(self, profile=None, parent=None, icon_path: str | None = None):
        super().__init__(parent)
        self._profile = profile
        self._allow_close = False

        self.setWindowTitle("南七日程 · 内置浏览器")
        self.resize(1200, 820)
        self.setMinimumSize(760, 520)

        # 图标文件可能不存在（源码运行/打包路径差异），不存在就沿用系统默认图标
        if icon_path and os.path.isfile(icon_path):
            try:
                self.setWindowIcon(QIcon(icon_path))
            except Exception:
                pass

        self.browser = BrowserView(self)
        self.setCentralWidget(self.browser)
        if profile is not None:
            self.browser.set_profile(profile)

        # 「一键添加」浮动按钮：导入模式开启时显示（控制器开启），固定在窗口右下角，
        # 点击后由控制器抓取当前页 HTML，**自动判断**是课表 / 二课 / 考试并导入。
        self.course_btn = QPushButton("一键添加", self)
        self.course_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.course_btn.setFixedHeight(40)
        self.course_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PRIMARY}; color: white; border: none;"
            f" border-radius: 20px; padding: 0 20px; font-size: 14px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {theme.PRIMARY_LIGHT}; }}"
        )
        self.course_btn.hide()
        self.course_btn.clicked.connect(self._on_course_btn_clicked)
        self._course_callback = None

        # 导入结果条：**非模态**、自动消失、可点 × 关掉。
        # 【为什么不用 QMessageBox】见 `show_import_result` 的注释——从 WebEngine 的
        # 脚本回调里弹模态框，实测会出现「点了按钮后弹出一个关不掉的窗口」，
        # 而一个不能关的对话框在桌面应用里是最糟的故障形态。
        self.result_bar = QFrame(self)
        self.result_bar.setObjectName("importResult")
        self.result_bar.hide()
        bar_layout = QHBoxLayout(self.result_bar)
        bar_layout.setContentsMargins(14, 9, 8, 9)
        bar_layout.setSpacing(10)
        self.result_label = QLabel("", self.result_bar)
        self.result_label.setWordWrap(True)
        self.result_label.setMaximumWidth(520)
        self.result_close = QPushButton("×", self.result_bar)
        self.result_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.result_close.setFixedSize(22, 22)
        self.result_close.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #5b6470;"
            " font-size: 16px; } QPushButton:hover { color: #1f2933; }"
        )
        self.result_close.clicked.connect(self.hide_import_result)
        bar_layout.addWidget(self.result_label)
        bar_layout.addWidget(self.result_close, 0, Qt.AlignmentFlag.AlignTop)
        self._result_timer = QTimer(self)
        self._result_timer.setSingleShot(True)
        self._result_timer.timeout.connect(self.hide_import_result)

        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        """一组最常用的浏览器快捷键（挂在窗口上，切到哪个标签都生效）。"""
        def add(seq: str, slot) -> None:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)

        add("Ctrl+T", lambda: self.browser._add_tab())
        add("Ctrl+W", self.browser.close_current)
        add("Ctrl+L", self.browser.focus_address_bar)
        add("F5", self.browser.reload_current)
        add("Ctrl+R", self.browser.reload_current)
        add("Alt+Left", self.browser.back_current)
        add("Alt+Right", self.browser.forward_current)

    # ---------------------------------------------------------------- 课表导入

    def set_course_import_mode(self, enabled: bool, callback=None, label: str | None = None) -> None:
        """开启/关闭导入模式：显示（或隐藏）右下角的「一键添加」浮动按钮。"""
        self._course_callback = callback if enabled else None
        if label:
            self.course_btn.setText(label)
        self.course_btn.setVisible(bool(enabled))
        if enabled:
            self._place_course_btn()
            self.course_btn.raise_()

    def show_import_result(self, message: str, ok: bool = True, seconds: int = 9) -> None:
        """在窗口顶部显示一条**非模态**的导入结果（自动消失，可点 × 关闭）。

        【为什么不是 QMessageBox】
        这个结果是在 `runJavaScript` 的回调里产生的（也就是 WebEngine 正在派发页面事件
        的时候）。在这个时机弹**模态**对话框，用户实测会遇到「弹出一个怎么点都关不掉的
        窗口」——模态嵌套把事件循环和页面的事件派发绞在一起，属于无法自愈的故障。
        非模态浮层不阻塞任何东西：最坏情况也只是它自己超时消失。
        """
        if not message:
            self.hide_import_result()
            return
        accent = "#1f7a4d" if ok else "#b3541e"
        self.result_bar.setStyleSheet(
            "QFrame#importResult {"
            f" background: {'#eef7f1' if ok else '#fdf2ec'};"
            f" border: 1px solid {accent}; border-radius: 10px; }}"
        )
        self.result_label.setStyleSheet(f"color: {accent}; font-size: 13px;")
        self.result_label.setText(message)
        self.result_bar.adjustSize()
        self._place_result_bar()
        self.result_bar.show()
        self.result_bar.raise_()
        self._result_timer.start(max(2, int(seconds)) * 1000)

    def hide_import_result(self) -> None:
        try:
            self._result_timer.stop()
        except Exception:  # noqa: BLE001
            pass
        self.result_bar.hide()

    def _place_result_bar(self) -> None:
        """顶部居中（避开右上角的窗口按钮）；太窄时铺满可用宽度。"""
        margin = 18
        available = max(200, self.width() - margin * 2)
        width = min(self.result_bar.sizeHint().width(), available)
        self.result_bar.setFixedWidth(width)
        self.result_bar.adjustSize()
        self.result_bar.move(
            max(margin, (self.width() - self.result_bar.width()) // 2),
            margin,
        )

    def _on_course_btn_clicked(self) -> None:
        if self._course_callback is not None:
            self._course_callback()

    def _place_course_btn(self) -> None:
        margin = 24
        self.course_btn.adjustSize()
        self.course_btn.move(
            max(margin, self.width() - self.course_btn.width() - margin),
            max(margin, self.height() - self.course_btn.height() - margin),
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.course_btn.isVisible():
            self._place_course_btn()
        if self.result_bar.isVisible():
            self._place_result_bar()

    # ---------------------------------------------------------------- 对外接口

    def open_url(self, url: str) -> None:
        """在（可能已经存在的）浏览器窗口里新开一个标签页并显示窗口。"""
        self.browser._add_tab(url)
        self.show_and_raise()

    def show_and_raise(self) -> None:
        """显示并把窗口拉到最前（含从最小化恢复）。"""
        self.show()
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowMinimized
            | Qt.WindowState.WindowActive
        )
        self.raise_()
        self.activateWindow()

    def allow_close(self) -> None:
        """允许真正关闭窗口（程序退出前调用；否则 close() 只会隐藏）。"""
        self._allow_close = True

    def closeEvent(self, event) -> None:
        """默认不销毁窗口，只隐藏——日历应用会把它留着复用。"""
        if self._allow_close:
            super().closeEvent(event)
            return
        event.ignore()
        self.hide()


if __name__ == "__main__":                 # python -m nanqi.browser.view
    from PySide6.QtWidgets import QApplication

    _app = QApplication([])
    _win = BrowserWindow()
    _win.show()
    _app.exec()
