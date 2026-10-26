r"""mail_window.py —— 邮箱窗口（收件箱 / 读信 / 写信 / 登录各种邮箱）。

【它和内置浏览器是什么关系】
两者是互补的，不是重复：

  * **这个窗口**走 IMAP/SMTP，把主题、发件人、正文稳定地取回来，读起来快、
    不受网页改版影响，还能直接写信发出去；
  * **网页登录**按钮把对应服务商的网页版交给内置浏览器（QtWebEngine，
    带持久 Cookie），用来处理「必须网页操作」的事：开启 IMAP 服务、生成授权码、
    看被 IMAP 隐藏的文件夹、走 OAuth 的账号。

所以「登录各种邮箱」有两条路：有授权码的用 IMAP 直连；没有的（或 Gmail/Outlook
这类强制 OAuth 的）用网页登录，一次登录后 Cookie 会留着。

【线程模型】
IMAP 是一次真实网络往返（登录 + 取列表可能好几秒），全部放进后台线程；结果通过
Qt 信号回到界面线程再更新控件。界面线程里**只**做 UI 操作，绝不碰 socket——
否则窗口会白屏几秒，用户会以为卡死了。
"""

from __future__ import annotations

import threading
from html import escape
from PySide6.QtCore import QUrl, QByteArray
from PySide6.QtGui import QDesktopServices
from ..mail.requests import MailRequests

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..mail import compose, imap_client, sender
from ..mail.config import PRESETS
from ..mail.secret import SecretError

__all__ = ["MailWindow"]

# 网页版邮箱入口（登录/开启 IMAP/生成授权码都从这里去）。
WEBMAIL = {
    "qq": "https://mail.qq.com",
    "163": "https://mail.163.com",
    "126": "https://mail.126.com",
    "gmail": "https://mail.google.com",
    "outlook": "https://outlook.live.com/mail",
    "ustc": "https://mail.ustc.edu.cn",
}

_PROVIDER_LABELS = [
    ("qq", "QQ 邮箱"),
    ("163", "网易 163"),
    ("126", "网易 126"),
    ("gmail", "Gmail"),
    ("outlook", "Outlook"),
    ("ustc", "中国科大邮箱"),
    ("custom", "其它（手动填写）"),
]


class _Bridge(QObject):
    """后台线程 → 界面线程的信号中转（Qt 会跨线程排队投递）。"""

    sendFinished = Signal(bool, str)
    status = Signal(str)
    folders = Signal(list)
    messages = Signal(list)
    body = Signal(dict)
    error = Signal(str)


class MailWindow(QMainWindow):
    """邮箱窗口。`host` 需要提供 `open_browser(url)`（网页登录用）。"""

    def __init__(self, mail_store, host=None, parent=None) -> None:
        super().__init__(parent)
        self.mail_store = mail_store
        self.host = host
        self.setWindowTitle("邮箱 · 南七日程")
        self.resize(1180, 760)
        self._client = None
        self._current_folder = "INBOX"
        self._busy = False
        self._mail_closed = False
        self._sending = False
        self.requests = MailRequests(self)
        self.requests.finished.connect(self._mail_result)

        self.signals = _Bridge()
        self.signals.sendFinished.connect(self._send_finished)
        self.signals.status.connect(self._set_status)
        self.signals.folders.connect(self._show_folders)
        self.signals.messages.connect(self._show_messages)
        self.signals.body.connect(self._show_body)
        self.signals.error.connect(self._show_error)

        self._build_ui()
        self._load_config_into_form()

    # ================================================================== 界面

    def _build_ui(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        # ---- 登录条 ----
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.provider_box = QComboBox()
        for key, label in _PROVIDER_LABELS:
            self.provider_box.addItem(label, key)
        self.provider_box.currentIndexChanged.connect(self._on_provider_changed)
        bar.addWidget(self.provider_box)

        self.account_edit = QLineEdit()
        self.account_edit.setPlaceholderText("邮箱账号，例如 you@qq.com")
        self.account_edit.setMinimumWidth(220)
        bar.addWidget(self.account_edit)

        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.Password)
        self.secret_edit.setPlaceholderText("授权码（不是邮箱密码）")
        self.secret_edit.setMinimumWidth(180)
        bar.addWidget(self.secret_edit)

        self.login_button = QPushButton("登录 / 连接")
        self.login_button.setDefault(True)
        self.login_button.clicked.connect(self._login)
        bar.addWidget(self.login_button)

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self._refresh)
        bar.addWidget(self.refresh_button)

        self.compose_button = QPushButton("写邮件")
        self.compose_button.clicked.connect(self._compose)
        bar.addWidget(self.compose_button)

        self.web_button = QPushButton("网页登录 ↗")
        self.web_button.setToolTip("在应用内置浏览器里打开网页版邮箱（开启 IMAP、生成授权码）")
        self.web_button.clicked.connect(self._open_webmail)
        bar.addWidget(self.web_button)
        outer.addLayout(bar)

        hint = QLabel(
            "QQ / 163 / 126 等邮箱需要先在网页版「设置 → 账户」中开启 IMAP 服务，"
            "再生成授权码，用它代替邮箱密码。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6b7280;font-size:12px")
        outer.addWidget(hint)

        # ---- 三栏 ----
        splitter = QSplitter(Qt.Horizontal)
        self.folder_list = QListWidget()
        self.folder_list.setMaximumWidth(190)
        self.folder_list.currentItemChanged.connect(self._on_folder_changed)
        splitter.addWidget(self.folder_list)

        self.message_list = QListWidget()
        self.message_list.currentItemChanged.connect(self._on_message_changed)
        splitter.addWidget(self.message_list)

        self.body_view = SafeMailBody()
        splitter.addWidget(self.body_view)
        splitter.setSizes([180, 380, 620])
        outer.addWidget(splitter, 1)

        self.setCentralWidget(central)
        self.status_label = QLabel("未连接")
        self.statusBar().addWidget(self.status_label, 1)

    def _load_config_into_form(self) -> None:
        config = self.mail_store.config
        self.account_edit.setText(config.get("user") or "")
        if self.mail_store.has_secret():
            self.secret_edit.setPlaceholderText("已保存授权码（留空则沿用）")
        host = (config.get("provider") or "").lower()
        if host:
            index = self.provider_box.findData(host)
            if index >= 0:
                self.provider_box.setCurrentIndex(index)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _show_error(self, text: str) -> None:
        self._busy = False
        self._set_status("出错")
        QMessageBox.warning(self, "邮箱", text)

    # ================================================================== 登录

    def _on_provider_changed(self) -> None:
        key = self.provider_box.currentData()
        if key in ("custom", None):
            return
        imap = imap_client.IMAP_PRESETS.get(key)
        smtp = PRESETS.get(key)
        if not imap:
            return
        self.status_label.setText(
            f"将使用 {imap['host']}:{imap['port']} 收信、"
            f"{(smtp or {}).get('host', '（待填）')} 发信"
        )

    def _login(self) -> None:
        if self._busy:
            return
        account = self.account_edit.text().strip()
        secret = self.secret_edit.text()
        key = self.provider_box.currentData()
        if not account:
            QMessageBox.information(self, "邮箱", "请填写邮箱账号")
            return
        password = secret
        if not password:
            try:
                password = self.mail_store.password()
            except SecretError as error:
                QMessageBox.warning(self, "邮箱", str(error))
                return
        if not password:
            QMessageBox.information(self, "邮箱", "请填写授权码（QQ / 163 需在网页版生成）")
            return

        imap = imap_client.IMAP_PRESETS.get(key, {})
        smtp = PRESETS.get(key, {})
        config = {
            **self.mail_store.config,
            "provider": key,
            "user": account,
            "sender": account,
            "imapHost": imap.get("host", self.mail_store.config.get("imapHost", "")),
            "imapPort": imap.get("port", 993),
            "imapSecurity": "ssl" if imap.get("ssl", True) else "starttls",
            "host": smtp.get("host", self.mail_store.config.get("host", "")),
            "port": smtp.get("port", self.mail_store.config.get("port", 465)),
            "security": smtp.get("security", self.mail_store.config.get("security", "ssl")),
        }
        # 收件人没填时用自己，保证每日摘要/提醒邮件有明确去处
        if not config.get("recipients"):
            config["recipients"] = [account]
        try:
            self.mail_store.update(config, password=secret or None)
        except SecretError as error:
            QMessageBox.warning(self, "邮箱", str(error))
            return
        except ValueError as error:
            QMessageBox.warning(self, "邮箱", str(error))
            return

        self._busy = True
        self.secret_edit.clear()
        self.secret_edit.setPlaceholderText("已保存授权码（留空则沿用）")
        self._set_status("正在连接…")
        config_snapshot = dict(self.mail_store.config)
        self.requests.invalidate()
        self.requests.submit("login", lambda: self._worker_login(config_snapshot, password))

    def _worker_login(self, config, password):
        client = imap_client.connect(config, password)
        try:
            return client, imap_client.list_folders(client)
        except Exception:
            client.logout()
            raise


    # ================================================================== 列表

    def _show_folders(self, folders: list) -> None:
        self._busy = False
        self.folder_list.clear()
        for folder in folders:
            item = QListWidgetItem(folder["name"])
            item.setData(Qt.UserRole, folder.get("raw", folder["name"]))
            self.folder_list.addItem(item)
        if self.folder_list.count():
            self.folder_list.setCurrentRow(0)

    def _on_folder_changed(self, current, _previous=None) -> None:
        if current is None:
            return
        self._current_folder = str(current.data(Qt.UserRole))
        self._load_messages()

    def _refresh(self) -> None:
        if self._client is None:
            self._login()
            return
        self._load_messages()

    def _load_messages(self):
        if self._client is None:
            return
        self.requests.invalidate("body")
        self.message_list.clear()
        self.body_view.clear()
        self._busy = True
        self._set_status(f"正在读取「{self._current_folder}」…")
        client, folder, account = self._client, self._current_folder, self.mail_store.config.get("user")
        self.requests.submit("messages", lambda: (account, folder, imap_client.fetch_messages(client, folder, 40)))


    def _show_messages(self, messages: list) -> None:
        self._busy = False
        self.message_list.clear()
        for message in messages:
            marker = "" if message.get("seen") else "● "
            item = QListWidgetItem(f"{marker}{message.get('sender', '')}\n{message.get('subject', '')}")
            item.setData(Qt.UserRole, message.get("uid"))
            item.setToolTip(f"{message.get('date', '')}\n{message.get('subject', '')}")
            self.message_list.addItem(item)
        if self.message_list.count() and self.message_list.currentRow() < 0:
            self.message_list.setCurrentRow(0)

    # ================================================================== 正文

    def _on_message_changed(self, current, _previous=None):
        if current is None or self._client is None:
            return
        uid = str(current.data(Qt.UserRole) or "")
        if not uid:
            return
        client, folder, account = self._client, self._current_folder, self.mail_store.config.get("user")
        self.body_view.setPlainText("正在读取…")
        self.requests.submit("body", lambda: (account, folder, uid, imap_client.fetch_message(client, folder, uid)))


    def _show_body(self, body: dict) -> None:
        header = (
            f"<div style='font-size:13px;color:#6b7280'>"
            f"<b>{escape(str(body.get('subject', '')))}</b><br>{escape(str(body.get('sender', '')))}<br>"
            f"{escape(str(body.get('date', '')))}</div><hr>"
        )
        if body.get("html"):
            # QTextBrowser 只做静态渲染：不执行脚本、不加载远程图片，避免跟踪像素与脚本
            self.body_view.setHtml(header + body["html"])
        else:
            self.body_view.setPlainText(
                f"{escape(str(body.get('subject', '')))}\n{escape(str(body.get('sender', '')))}  {escape(str(body.get('date', '')))}\n\n"
                f"{body.get('text', '')}"
            )

    # ================================================================== 写信

    def _compose(self) -> None:
        if self._sending:
            return
        dialog = _ComposeDialog(self, default_to=self.account_edit.text().strip())
        if dialog.exec() != QDialog.Accepted:
            return
        to, subject, text = dialog.result_values()
        if not to:
            QMessageBox.information(self, "写邮件", "请填写收件人")
            return
        config = {**self.mail_store.config, "recipients": [item.strip() for item in to.replace(";", ",").split(",") if item.strip()]}
        try:
            password = self.mail_store.password()
        except SecretError as error:
            QMessageBox.warning(self, "写邮件", str(error))
            return
        if not config.get("host"):
            QMessageBox.information(self, "写邮件", "还没有配置发信服务器（SMTP），请先在设置里填写")
            return
        self._sending = True
        self.compose_button.setEnabled(False)
        self._set_status("正在发送…")

        def worker() -> None:
            try:
                message = compose.build_message(config, subject or "（无主题）", text)
                sender.send_message(config, message, password)
                self.signals.sendFinished.emit(True, "邮件已发送")
            except (sender.MailError, SecretError) as error:
                self.signals.sendFinished.emit(False, str(error))
            except Exception as error:  # noqa: BLE001
                self.signals.sendFinished.emit(False, f"发送失败：{error}")

        threading.Thread(target=worker, daemon=True).start()

    def _open_webmail(self) -> None:
        key = self.provider_box.currentData()
        url = WEBMAIL.get(key, "https://mail.qq.com")
        opener = getattr(self.host, "open_browser", None)
        if opener is None:
            QMessageBox.information(self, "网页登录", f"请用系统浏览器打开：{url}")
            return
        opener(url)

    # ================================================================== 生命周期

    def closeEvent(self, event):
        self._mail_closed = True
        self.requests.invalidate()
        client, self._client = self._client, None
        if client is not None:
            self.requests.submit("logout", client.logout)
        self._busy = False
        super().closeEvent(event)


    def showEvent(self, event):
        self._mail_closed = False
        super().showEvent(event)

    def _mail_result(self, kind, token, value, error):
        if self._mail_closed or not self.requests.current(kind, token):
            if kind == "login" and value:
                self.requests.submit("logout", value[0].logout)
            return
        if kind == "logout":
            return
        if error:
            self._show_error(error)
            return
        if kind == "login":
            previous = self._client
            self._client, folders = value
            if previous is not None and previous is not self._client:
                self.requests.submit("logout", previous.logout)
            self._set_status("已连接")
            self._show_folders(folders)
        elif kind == "messages":
            account, folder, messages = value
            if account == self.mail_store.config.get("user") and folder == self._current_folder:
                self._show_messages(messages)
                self._set_status(f"「{folder}」共 {len(messages)} 封（最近 40 封）")
        elif kind == "body":
            account, folder, uid, body = value
            selected = self.message_list.currentItem()
            if account == self.mail_store.config.get("user") and folder == self._current_folder and selected and str(selected.data(Qt.UserRole)) == uid:
                self._show_body(body)

    def _send_finished(self, success, message):
        self._sending = False
        self.compose_button.setEnabled(True)
        self._set_status(message)
        if not success:
            QMessageBox.warning(self, "发送失败", message)



class _ComposeDialog(QDialog):
    """写信对话框（收件人 / 主题 / 正文）。"""

    def __init__(self, parent=None, default_to: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("写邮件")
        self.resize(680, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.to_edit = QLineEdit(default_to)
        self.to_edit.setPlaceholderText("多个收件人用逗号分隔")
        self.subject_edit = QLineEdit()
        form.addRow("收件人", self.to_edit)
        form.addRow("主题", self.subject_edit)
        layout.addLayout(form)
        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText("正文…")
        layout.addWidget(self.body_edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("发送")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_values(self) -> tuple[str, str, str]:
        return self.to_edit.text().strip(), self.subject_edit.text().strip(), self.body_edit.toPlainText()


class SafeMailBody(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._open_link)

    def loadResource(self, kind, url):
        return QByteArray()

    def _open_link(self, url):
        if url.scheme().lower() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
