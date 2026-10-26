r"""imap_client.py —— 收信（IMAP）。纯同步实现，由窗口放到后台线程里调用。

【为什么收信要用 IMAP 而不是「打开网页版」】
网页版邮箱（例如 QQ 邮箱的 h5 页面）依赖大量前端脚本与登录态，把它塞进应用里
既能用、又脆弱。而 IMAP 是标准协议：只要用户开好「IMAP 服务」并拿到授权码，
就能稳定地把主题、发件人、正文取回来，**不需要联网渲染**，也不受网页改版影响。

【登录为什么常常失败，以及怎么提示】
QQ / 163 / 126 这类邮箱默认**关闭** IMAP 服务，且不接受邮箱密码，必须用
「授权码」。所以认证失败时这里给的不是 `imaplib.error: b'AUTHENTICATIONFAILED'`，
而是「请到网页版开启 IMAP 并生成授权码」的明确指引——这是用户唯一能自己解决的事。

【解析的兜底】
真实邮件五花八门：Base64 编码的中文主题、multipart/alternative、只有 HTML 没有
纯文本、内嵌图片……所以解析一律走「尽力而为」：
  * 主题/发件人用 `decode_header` 还原编码；
  * 正文优先取 text/plain，没有就用 HTML 去标签；
  * 单个部件解码失败就跳过，绝不让整封信读不出来。
"""

from __future__ import annotations

import email
import imaplib
import re
import socket
import ssl
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

__all__ = ["ImapError", "IMAP_PRESETS", "connect", "list_folders", "fetch_messages", "fetch_message"]

# 常用邮箱的 IMAP 参数（与 config.PRESETS 的 SMTP 参数配套使用）。
IMAP_PRESETS = {
    "qq": {"host": "imap.qq.com", "port": 993, "ssl": True},
    "163": {"host": "imap.163.com", "port": 993, "ssl": True},
    "126": {"host": "imap.126.com", "port": 993, "ssl": True},
    "gmail": {"host": "imap.gmail.com", "port": 993, "ssl": True},
    "outlook": {"host": "outlook.office365.com", "port": 993, "ssl": True},
    "ustc": {"host": "mail.ustc.edu.cn", "port": 993, "ssl": True},
}

MAX_MESSAGES = 200


class ImapError(RuntimeError):
    """收信失败（消息可直接展示给用户）。"""


def _decode(value) -> str:
    """还原邮件头的编码（RFC 2047）；失败时原样返回。"""
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:  # noqa: BLE001
        return str(value)


def _html_to_text(html: str) -> str:
    """把 HTML 正文压成纯文本（只用于「没有纯文本部件」的兜底）。"""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def connect(config: dict, password: str, timeout: float = 25.0):
    """登录 IMAP 服务器，返回已登录的 `IMAP4` 对象。"""
    host = str(config.get("imapHost") or "").strip()
    port = int(config.get("imapPort") or 993)
    if not host:
        raise ImapError("还没有填写收信服务器（IMAP）")
    user = str(config.get("user") or "").strip()
    if not user:
        raise ImapError("还没有填写账号")
    if not password:
        raise ImapError("还没有保存授权码，请先填写授权码并登录")

    try:
        if config.get("imapSecurity", "ssl") == "starttls":
            client = imaplib.IMAP4(host, port, timeout=timeout)
            client.starttls(ssl_context=ssl.create_default_context())
        else:
            client = imaplib.IMAP4_SSL(host, port, timeout=timeout, ssl_context=ssl.create_default_context())
    except socket.gaierror as error:
        raise ImapError(f"找不到收信服务器「{host}」，请检查地址是否填写正确") from error
    except (socket.timeout, TimeoutError) as error:
        raise ImapError(f"连接「{host}:{port}」超时，请检查网络或防火墙") from error
    except ssl.SSLError as error:
        raise ImapError(f"与「{host}:{port}」的加密握手失败，请检查端口与加密方式") from error
    except OSError as error:
        raise ImapError(f"连接「{host}:{port}」失败：{error}") from error

    try:
        client.login(user, password)
    except imaplib.IMAP4.error as error:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass
        text = str(error)
        raise ImapError(
            "收信登录被拒绝。QQ / 163 等邮箱需要先在网页版「设置 → 账户」里"
            "**开启 IMAP 服务**，再用生成的授权码登录（不是邮箱密码）。"
            + (f"（服务器原话：{text}）" if text and len(text) < 120 else "")
        ) from error
    except OSError as error:
        raise ImapError(f"登录时连接中断：{error}") from error
    return client


def list_folders(client) -> list[dict]:
    """列出邮箱里的文件夹（收件箱、已发送等）。"""
    try:
        typ, data = client.list()
    except imaplib.IMAP4.error as error:
        raise ImapError(f"读取文件夹列表失败：{error}") from error
    folders: list[dict] = []
    if typ != "OK":
        return folders
    for raw in data or []:
        if not isinstance(raw, (bytes, bytearray)):
            continue
        line = raw.decode("utf-8", "replace")
        # 形如：(\\HasNoChildren) "/" "INBOX"
        match = re.match(r'\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.*)$', line)
        if not match:
            continue
        name = match.group("name").strip().strip('"')
        try:
            name = _decode(name)
        except Exception:  # noqa: BLE001
            pass
        if not name:
            continue
        folders.append({"name": name, "raw": match.group("name").strip('"')})
    # 收件箱排在最前面，其余按名字排序
    folders.sort(key=lambda item: (item["name"].upper() != "INBOX", item["name"].lower()))
    return folders


def _select(client, folder: str) -> int:
    try:
        typ, data = client.select(f'"{folder}"', readonly=True)
    except imaplib.IMAP4.error as error:
        raise ImapError(f"打开文件夹「{folder}」失败：{error}") from error
    if typ != "OK":
        raise ImapError(f"打开文件夹「{folder}」失败")
    try:
        return int(data[0] or 0)
    except (TypeError, ValueError):
        return 0


def fetch_messages(client, folder: str = "INBOX", limit: int = 30) -> list[dict]:
    """取最近 `limit` 封邮件的头部信息，最新的排在最前面。"""
    total = _select(client, folder)
    if total == 0:
        return []
    limit = max(1, min(int(limit), MAX_MESSAGES, total))
    start = max(1, total - limit + 1)
    try:
        typ, data = client.fetch(f"{start}:{total}", "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE TO)])")
    except imaplib.IMAP4.error as error:
        raise ImapError(f"读取邮件列表失败：{error}") from error
    if typ != "OK":
        return []

    messages: list[dict] = []
    pending_meta = None
    for item in data or []:
        if isinstance(item, tuple) and len(item) == 2:
            meta_text = item[0].decode("utf-8", "replace") if isinstance(item[0], bytes) else str(item[0])
            uid_match = re.search(r"UID (\d+)", meta_text)
            size_match = re.search(r"RFC822\.SIZE (\d+)", meta_text)
            flags_match = re.search(r"FLAGS \(([^)]*)\)", meta_text)
            pending_meta = {
                "uid": uid_match.group(1) if uid_match else "",
                "size": int(size_match.group(1)) if size_match else 0,
                "seen": "\\Seen" in (flags_match.group(1) if flags_match else ""),
            }
            try:
                message = email.message_from_bytes(item[1])
            except Exception:  # noqa: BLE001 - 单封解析失败不能拖垮整个列表
                message = None
            if message is None:
                continue
            date_text = ""
            try:
                parsed = parsedate_to_datetime(message.get("Date"))
                date_text = parsed.strftime("%Y-%m-%d %H:%M") if parsed else ""
            except Exception:  # noqa: BLE001
                date_text = _decode(message.get("Date"))
            messages.append(
                {
                    "uid": pending_meta["uid"],
                    "subject": _decode(message.get("Subject")) or "（无主题）",
                    "sender": _decode(message.get("From")),
                    "recipients": _decode(message.get("To")),
                    "date": date_text,
                    "size": pending_meta["size"],
                    "seen": pending_meta["seen"],
                }
            )
    messages.reverse()
    return messages


def fetch_message(client, folder: str, uid: str) -> dict:
    """按 UID 取一封邮件的内容（纯文本 + HTML）。"""
    _select(client, folder)
    try:
        typ, data = client.uid("FETCH", str(uid), "(RFC822)")
    except imaplib.IMAP4.error as error:
        raise ImapError(f"读取邮件内容失败：{error}") from error
    if typ != "OK":
        raise ImapError("没有找到这封邮件（可能已被移动或删除）")
    raw = None
    for item in data or []:
        if isinstance(item, tuple) and len(item) == 2:
            raw = item[1]
            break
    if raw is None:
        raise ImapError("没有找到这封邮件的内容")
    message = email.message_from_bytes(raw)
    text_part, html_part = _extract_body(message)
    return {
        "uid": str(uid),
        "subject": _decode(message.get("Subject")) or "（无主题）",
        "sender": _decode(message.get("From")),
        "recipients": _decode(message.get("To")),
        "date": _decode(message.get("Date")),
        "text": text_part or _html_to_text(html_part or ""),
        "html": html_part or "",
        "attachments": _attachment_names(message),
    }


def _extract_body(message: Message) -> tuple[str, str]:
    """取出纯文本与 HTML 正文（含 multipart 递归）。"""
    text_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: Message) -> None:
        if part.is_multipart():
            for child in part.get_payload():
                if isinstance(child, Message):
                    walk(child)
            return
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition") or "")
        if "attachment" in disposition.lower():
            return
        if content_type not in ("text/plain", "text/html"):
            return
        try:
            payload = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001
            return
        if payload is None:
            return
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, "replace")
        except (LookupError, UnicodeDecodeError):
            decoded = payload.decode("utf-8", "replace")
        (html_parts if content_type == "text/html" else text_parts).append(decoded)

    walk(message)
    return "\n".join(text_parts).strip(), "\n".join(html_parts).strip()


def _attachment_names(message: Message) -> list[str]:
    names: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = str(part.get("Content-Disposition") or "")
        filename = part.get_filename()
        if filename and ("attachment" in disposition.lower() or filename):
            names.append(_decode(filename))
    return names
