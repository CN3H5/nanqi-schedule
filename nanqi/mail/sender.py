r"""sender.py —— 真正把邮件发出去（SMTP）。

【为什么错误信息要自己翻译】
`smtplib` 抛上来的异常对普通用户等于天书：

    SMTPAuthenticationError(535, b'Error: authentication failed')

而用户真正需要知道的是「授权码不对」以及「去哪儿改」。所以这里把最常见的十几种
失败逐条映射成中文提示，并且**区分「配置问题」和「网络问题」**——前者用户能自己
立刻修好，后者只能等一会儿重试。

【三个协议细节】
  1. **SSL/TLS 与 STARTTLS 不是一回事**：465 端口用的是「一上来就是 TLS」
     （`SMTP_SSL`），587 端口是「明文连上再升级」（`starttls()`）。选错会直接
     连不上或卡住，所以加密方式必须让用户显式选，不能猜。
  2. **必须设超时**：不设的话，服务器没响应会让后台线程永久挂住，用户看到的就是
     「一直没反应」。
  3. **授权码不是邮箱密码**：QQ/163 这类服务商用邮箱密码登录会被拒绝，必须用
     单独生成的「授权码」。这条最容易踩，所以写进提示语里。
"""

from __future__ import annotations

import smtplib
import socket
import ssl
from email.message import EmailMessage

__all__ = ["MailError", "send_message"]


class MailError(RuntimeError):
    """发送失败（消息可直接展示给用户）。"""


def _describe_os_error(error: OSError, config: dict) -> str:
    host = config.get("host") or "发信服务器"
    port = config.get("port")
    if isinstance(error, socket.gaierror):
        return f"找不到发信服务器「{host}」，请检查服务器地址是否填写正确"
    if isinstance(error, ssl.SSLError):
        if config.get("security") == "ssl":
            return f"与「{host}:{port}」建立加密连接失败。若服务器用 STARTTLS，请把加密方式改成 STARTTLS（常见于 587 端口）"
        return f"与「{host}:{port}」的加密握手失败，请检查加密方式与端口是否匹配"
    if isinstance(error, (socket.timeout, TimeoutError)):
        return f"连接「{host}:{port}」超时。请检查网络、防火墙，或确认端口是否被运营商封锁"
    if isinstance(error, ConnectionRefusedError):
        return f"「{host}:{port}」拒绝连接，请检查服务器地址与端口"
    return f"连接「{host}:{port}」失败：{error}"


def send_message(config: dict, message: EmailMessage, password: str, timeout: float = 25.0) -> None:
    """登录并发出一封邮件；失败抛 `MailError`（消息为中文，可直接展示）。"""
    host = config.get("host") or ""
    port = int(config.get("port") or 0)
    security = config.get("security") or "ssl"
    user = config.get("user") or ""
    if not host or not port:
        raise MailError("还没有填写发信服务器与端口")
    if not (config.get("recipients") or []):
        raise MailError("还没有填写收件人")
    # 【顺序很重要】先查凭据再连网络：缺授权码是用户能立刻改的事，
    # 没必要先花几秒去连服务器，更不能因为「连不上」而报出与真正原因无关的错误。
    if user and not password:
        raise MailError("还没有保存授权码，请先在设置里填写并发信测试")

    # smtplib 的构造函数**当场就会连接**（不是等到 login），所以建连也必须包在
    # try 里，否则 ConnectionRefused / 超时 会以原始 OSError 的形式冒到界面上。
    try:
        if security == "ssl":
            client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(host, port, timeout=timeout)
    except OSError as error:
        raise MailError(_describe_os_error(error, config)) from error

    try:
        client.ehlo()
        if security == "starttls":
            try:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            except smtplib.SMTPException as error:
                raise MailError(
                    "服务器不接受 STARTTLS。若是 465 端口，请把加密方式改成 SSL/TLS"
                ) from error
        if user:
            try:
                client.login(user, password)
            except smtplib.SMTPAuthenticationError as error:
                code = getattr(error, "smtp_code", "")
                raise MailError(
                    "登录被拒绝（" + str(code) + "）。"
                    "QQ / 163 等邮箱需要用「授权码」而不是邮箱密码，"
                    "请到邮箱网页版的「设置 → 账户 → POP3/SMTP 服务」生成授权码后填回来"
                ) from error
            except smtplib.SMTPException as error:
                raise MailError(f"登录失败：{error}") from error
        try:
            client.send_message(message)
        except smtplib.SMTPRecipientsRefused as error:
            raise MailError(f"收件人被服务器拒绝：{error}") from error
        except smtplib.SMTPSenderRefused as error:
            raise MailError("发件人被服务器拒绝，请检查账号与发件人地址是否一致") from error
        except smtplib.SMTPDataError as error:
            raise MailError(f"服务器拒收这封邮件：{error}") from error
    except OSError as error:
        raise MailError(_describe_os_error(error, config)) from error
    except smtplib.SMTPException as error:
        raise MailError(f"发信失败：{error}") from error
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001 - 关闭失败不影响「已经发出去了」
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
