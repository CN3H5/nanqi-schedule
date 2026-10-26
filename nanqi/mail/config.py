r"""config.py —— 邮件设置的读、写与校验（`mail.json`）。

【为什么单独一个文件，而不是塞进 plans.json】
`plans.json` 是「可导出、可导入、可复制到别的电脑」的用户数据。邮件设置放进去会
带来两个问题：

  1. 导入别人的备份会**悄悄改掉你的发信账号与收件人**（等于被改成给别人发信）；
  2. 备份文件里会带上你的邮箱地址与服务器。

所以邮件设置单独存 `mail.json`（不含密码），授权码另存为 DPAPI 密文
（见 `secret.py`）。三个文件各管一件事：计划数据 / 邮件配置 / 邮件凭据。

【校验的原则：只接受认识的值，其余一律报错】
发信配置错一个字符就是「点了没反应」或者「连不上」，而错误信息往往很难看懂。
所以这里在**保存时就**把能查的都查掉（端口范围、加密方式、地址格式、摘要时间格式），
并且给出中文提示，让用户在设置页当场就知道哪里不对。
"""

from __future__ import annotations

import json
import threading
from functools import wraps
import os
import re
from pathlib import Path

from .secret import SecretError, protect, unprotect

__all__ = [
    "DEFAULT_CONFIG",
    "PRESETS",
    "MailConfigStore",
    "normalize",
    "SECURITY_MODES",
]

SECURITY_MODES = ("ssl", "starttls", "none")

# 常见服务商的默认参数，用于「一键填好」；用户仍可手改任意值。
PRESETS = {
    "qq": {"host": "smtp.qq.com", "port": 465, "security": "ssl"},
    "163": {"host": "smtp.163.com", "port": 465, "security": "ssl"},
    "126": {"host": "smtp.126.com", "port": 465, "security": "ssl"},
    "gmail": {"host": "smtp.gmail.com", "port": 587, "security": "starttls"},
    "outlook": {"host": "smtp.office365.com", "port": 587, "security": "starttls"},
    "ustc": {"host": "mail.ustc.edu.cn", "port": 465, "security": "ssl"},
}

DEFAULT_CONFIG = {
    # 收信（IMAP）与发信（SMTP）用同一个账号与授权码，但服务器参数分开存：
    # 有些服务商的收发服务器不同域名，混在一起会让人只能二选一。
    "provider": "",           # 服务商标识（qq/163/...），仅用于界面回显
    "imapHost": "",
    "imapPort": 993,
    "imapSecurity": "ssl",
    "onReminder": False,      # 到期时把这一项发到邮箱
    "digest": False,          # 每日摘要
    "digestTime": "08:00",    # 摘要发送时间（本地时间 HH:MM）
    "host": "",
    "port": 465,
    "security": "ssl",
    "user": "",               # 登录账号（通常就是邮箱地址）
    "sender": "",             # 发件人显示地址；留空则用 user
    "recipients": [],         # 收件人列表
    "lastDigestAttemptDate": "",
    "lastDigestDate": "",     # 最近一次摘要日期（YYYY-MM-DD），用于每天只发一次
}

_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def _split_recipients(value) -> list[str]:
    """把「a@x.com; b@y.com」这类输入拆成列表（逗号/分号/空格都认）。"""
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,;\s]+", str(value or ""))
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def normalize(raw, *, require_complete: bool = False) -> dict:
    """校验并补全邮件设置；不合法时抛 ValueError（中文提示）。

    `require_complete=True` 用于「发送前」的检查：那时必须已经填好服务器、账号、
    收件人，否则发送必然失败，不如当场报错。
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("邮件设置格式不正确")

    config = dict(DEFAULT_CONFIG)
    config["onReminder"] = raw.get("onReminder") is True
    config["digest"] = raw.get("digest") is True

    provider = str(raw.get("provider") or "").strip().lower()
    if provider and not re.fullmatch(r"[a-z]{1,16}", provider):
        raise ValueError("服务商标识不合法")
    config["provider"] = provider

    imap_host = str(raw.get("imapHost") or "").strip()
    if imap_host and not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", imap_host):
        raise ValueError("收信服务器地址不合法")
    config["imapHost"] = imap_host

    imap_port = raw.get("imapPort", DEFAULT_CONFIG["imapPort"])
    if isinstance(imap_port, str):
        imap_port = imap_port.strip()
        imap_port = int(imap_port) if imap_port.isdigit() else -1
    if isinstance(imap_port, bool) or not isinstance(imap_port, int) or not 1 <= imap_port <= 65535:
        raise ValueError("收信端口应为 1–65535 之间的整数")
    config["imapPort"] = imap_port

    imap_security = str(raw.get("imapSecurity") or DEFAULT_CONFIG["imapSecurity"]).strip().lower()
    if imap_security not in ("ssl", "starttls"):
        raise ValueError("收信加密方式只能是 SSL/TLS 或 STARTTLS")
    config["imapSecurity"] = imap_security

    digest_time = str(raw.get("digestTime") or DEFAULT_CONFIG["digestTime"]).strip()
    if not _TIME_RE.match(digest_time):
        raise ValueError("每日摘要时间格式应为 HH:MM（例如 08:00）")
    config["digestTime"] = digest_time

    host = str(raw.get("host") or "").strip()
    if host and not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", host):
        raise ValueError("发信服务器地址不合法")
    config["host"] = host

    port = raw.get("port", DEFAULT_CONFIG["port"])
    if isinstance(port, str):
        port = port.strip()
        port = int(port) if port.isdigit() else -1
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("端口应为 1–65535 之间的整数")
    config["port"] = port

    security = str(raw.get("security") or DEFAULT_CONFIG["security"]).strip().lower()
    if security not in SECURITY_MODES:
        raise ValueError("加密方式只能是 SSL/TLS、STARTTLS 或 不加密")
    config["security"] = security

    user = str(raw.get("user") or "").strip()
    if user and not _EMAIL_RE.match(user):
        raise ValueError("账号应为邮箱地址")
    config["user"] = user

    sender = str(raw.get("sender") or "").strip()
    if sender and not _EMAIL_RE.match(sender):
        raise ValueError("发件人地址不合法")
    config["sender"] = sender

    recipients = _split_recipients(raw.get("recipients"))
    for address in recipients:
        if not _EMAIL_RE.match(address):
            raise ValueError(f"收件人地址不合法：{address}")
    config["recipients"] = recipients

    attempt = raw.get("lastDigestAttemptDate")
    config["lastDigestAttemptDate"] = attempt if isinstance(attempt, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", attempt) else ""
    last = raw.get("lastDigestDate")
    config["lastDigestDate"] = last if isinstance(last, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", last) else ""

    if require_complete:
        missing = []
        if not config["host"]:
            missing.append("发信服务器")
        if not config["user"]:
            missing.append("账号")
        if not config["recipients"]:
            missing.append("收件人")
        if missing:
            raise ValueError("请先填写" + "、".join(missing))
    return config


def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._file_lock:
            return method(self, *args, **kwargs)
    return wrapped


class MailConfigStore:
    """邮件配置 + 授权码的本地存储（三个文件各管一件事）。"""

    def __init__(self, data_dir: Path) -> None:
        self._file_lock = threading.RLock()
        self.data_dir = Path(data_dir)
        self.config_file = self.data_dir / "mail.json"
        self.secret_file = self.data_dir / "mail.secret"
        self._config = dict(DEFAULT_CONFIG)
        self.load()

    # ------------------------------------------------------------------ 读

    def load(self) -> dict:
        try:
            raw = json.loads(self.config_file.read_text(encoding="utf-8"))
            self._config = normalize(raw)
        except (OSError, ValueError):
            # 配置损坏就当没配过：邮件是可选功能，不该因此让应用起不来。
            self._config = dict(DEFAULT_CONFIG)
        return self._config

    @property
    @synchronized
    def config(self) -> dict:
        return dict(self._config)

    def has_secret(self) -> bool:
        """是否已保存授权码（只判断存在，不去解密）。"""
        try:
            return self.secret_file.is_file() and self.secret_file.stat().st_size > 0
        except OSError:
            return False

    def password(self) -> str:
        """解密取出授权码；失败时抛 SecretError。"""
        try:
            blob = self.secret_file.read_bytes()
        except OSError as error:
            raise SecretError(f"读取授权码失败：{error}") from error
        return unprotect(blob)

    # ------------------------------------------------------------------ 写

    def _write_config(self) -> None:
        with self._file_lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp = self.config_file.with_suffix(".json.tmp")
            temp.write_text(json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.config_file)

    @synchronized
    def update(self, patch: dict, *, password=None) -> dict:
        """保存设置；`password` 为 None 表示「不改动已保存的授权码」。"""
        merged = {**self._config, **(patch or {})}
        # 前端传来的 password 字段不是配置项，单独处理
        if isinstance(patch, dict):
            merged.pop("password", None)
        config = normalize({**merged, "password": None})
        if isinstance(password, str) and password:
            blob = protect(password)  # 失败会抛 SecretError，此时不写任何文件
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp = self.secret_file.with_suffix(".secret.tmp")
            temp.write_bytes(blob)
            os.replace(temp, self.secret_file)
        self._config = config
        self._write_config()
        return dict(config)

    def clear_secret(self) -> bool:
        """删除已保存的授权码；返回是否真的删掉了文件。"""
        try:
            self.secret_file.unlink()
            return True
        except OSError:
            return False

    @synchronized
    def mark_digest_sent(self, date_text: str) -> None:
        """记录摘要已发送的日期（每天只发一次）。"""
        if self._config.get("lastDigestDate") == date_text:
            return
        self._config["lastDigestDate"] = date_text
        try:
            self._write_config()
        except OSError:
            pass

    # ------------------------------------------------------------------ 给界面的公开视图

    def public(self) -> dict:
        """给界面的配置视图：**永远不含密码**，只告诉界面「有没有存过」。"""
        view = dict(self._config)
        view["hasSecret"] = self.has_secret()
        view["configured"] = bool(self._config["host"] and self._config["user"] and self._config["recipients"])
        return view
