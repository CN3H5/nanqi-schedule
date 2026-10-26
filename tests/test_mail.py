"""test_mail.py —— 邮件功能的单元测试（配置、凭据、内容、解析、真实收发）。

【为什么这几组非测不可】
邮件功能有三处「静默出错」的风险，全都是上线后用户才会踩的坑：

  1. **凭据落盘**：密码一旦被写进 `plans.json`，导出备份就等于泄漏；
  2. **配置校验**：端口写错、授权码当成密码用，现象都是「点了没反应」；
  3. **MIME 解析**：Base64 中文主题、只有 HTML 没有纯文本的邮件，解析不当会让
     整封邮件变成空白。

所以这里除了常规断言，还做了两件「真」事：用 Windows DPAPI 实际加密再解密一次，
以及起一个**本地假 SMTP 服务器**真实跑一遍收发流程——不使用网络，但走的是真实的
`smtplib` 代码路径。
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from nanqi.mail import compose, config as mail_config, imap_client, sender, secret


# --------------------------------------------------------------------------- 配置校验


class TestConfigValidation:
    def test_defaults_are_complete(self):
        cfg = mail_config.normalize({})
        assert cfg["security"] == "ssl" and cfg["port"] == 465
        assert cfg["imapSecurity"] == "ssl" and cfg["imapPort"] == 993
        assert cfg["recipients"] == [] and cfg["digestTime"] == "08:00"
        assert cfg["onReminder"] is False and cfg["digest"] is False

    def test_recipients_are_split_and_deduplicated(self):
        cfg = mail_config.normalize({"recipients": "a@x.com; b@x.com, a@x.com\nc@x.com"})
        assert cfg["recipients"] == ["a@x.com", "b@x.com", "c@x.com"]

    @pytest.mark.parametrize(
        "patch, keyword",
        [
            ({"port": 99999}, "端口"),
            ({"port": "abc"}, "端口"),
            ({"imapPort": 0}, "收信端口"),
            ({"security": "plain"}, "加密方式"),
            ({"imapSecurity": "none"}, "收信加密方式"),
            ({"user": "not-an-email"}, "账号"),
            ({"recipients": ["bad"]}, "收件人"),
            ({"digestTime": "25:00"}, "摘要时间"),
            ({"host": "has space.com"}, "服务器"),
        ],
    )
    def test_invalid_values_raise_with_chinese_hint(self, patch, keyword):
        with pytest.raises(ValueError) as info:
            mail_config.normalize(patch)
        assert keyword in str(info.value)

    def test_require_complete_lists_what_is_missing(self):
        with pytest.raises(ValueError) as info:
            mail_config.normalize({"host": "smtp.x.com"}, require_complete=True)
        message = str(info.value)
        assert "账号" in message and "收件人" in message


# --------------------------------------------------------------------------- 凭据与落盘


class TestCredentialStorage:
    def test_password_is_encrypted_and_never_written_to_mail_json(self, tmp_path):
        store = mail_config.MailConfigStore(tmp_path)
        store.update(
            {"provider": "qq", "host": "smtp.qq.com", "user": "me@qq.com", "recipients": ["you@qq.com"]},
            password="super-secret-code",
        )
        text = (tmp_path / "mail.json").read_text(encoding="utf-8")
        assert "super-secret-code" not in text, "授权码绝不能出现在 mail.json 里"
        assert (tmp_path / "mail.secret").is_file()
        assert store.has_secret() is True
        assert "password" not in store.public()
        assert store.public()["hasSecret"] is True

    def test_password_roundtrip_through_dpapi(self, tmp_path):
        store = mail_config.MailConfigStore(tmp_path)
        store.update({"user": "me@qq.com"}, password="授权码-abc-123")
        assert store.password() == "授权码-abc-123"

    def test_updating_without_password_keeps_the_saved_one(self, tmp_path):
        store = mail_config.MailConfigStore(tmp_path)
        store.update({"user": "me@qq.com"}, password="keep-me")
        store.update({"digest": True})
        assert store.password() == "keep-me"
        assert store.config["digest"] is True

    def test_clear_secret_removes_only_the_credential(self, tmp_path):
        store = mail_config.MailConfigStore(tmp_path)
        store.update({"user": "me@qq.com"}, password="bye")
        assert store.clear_secret() is True
        assert store.has_secret() is False
        assert store.config["user"] == "me@qq.com", "清除授权码不该顺手清掉配置"

    def test_config_is_survived_by_a_new_store_instance(self, tmp_path):
        mail_config.MailConfigStore(tmp_path).update(
            {"host": "smtp.qq.com", "imapHost": "imap.qq.com", "recipients": ["a@b.com"]}
        )
        again = mail_config.MailConfigStore(tmp_path)
        assert again.config["host"] == "smtp.qq.com"
        assert again.config["imapHost"] == "imap.qq.com"
        assert again.config["recipients"] == ["a@b.com"]

    def test_mark_digest_sent_is_persisted(self, tmp_path):
        store = mail_config.MailConfigStore(tmp_path)
        store.mark_digest_sent("2026-03-01")
        assert mail_config.MailConfigStore(tmp_path).config["lastDigestDate"] == "2026-03-01"

    def test_corrupted_config_falls_back_to_defaults(self, tmp_path):
        (tmp_path / "mail.json").write_text("{ not json", encoding="utf-8")
        store = mail_config.MailConfigStore(tmp_path)
        assert store.config["host"] == "" and store.config["port"] == 465


# --------------------------------------------------------------------------- 邮件内容


class TestCompose:
    def _config(self):
        return mail_config.normalize(
            {"user": "me@qq.com", "sender": "me@qq.com", "recipients": ["you@qq.com"]}
        )

    def _task(self, **overrides):
        task = {
            "id": "t1", "title": "写移植报告", "date": "2026-03-02", "time": "09:30",
            "duration": 45, "category": "work", "priority": "high", "notes": "第三章",
            "done": False,
        }
        task.update(overrides)
        return task

    def test_test_message_has_headers_and_both_bodies(self):
        message = compose.test_message(self._config())
        assert "测试邮件" in message["Subject"]
        assert message["To"] == "you@qq.com"
        assert message["From"].endswith("<me@qq.com>") or "me@qq.com" in message["From"]
        assert message.is_multipart(), "应同时带纯文本与 HTML 两份正文"

    def test_reminder_message_contains_the_details(self):
        message = compose.reminder_message(self._config(), self._task(), "加油")
        body = message.get_body(preferencelist=("plain",)).get_content()
        assert "写移植报告" in body
        assert "09:30" in body and "45 分钟" in body
        assert "第三章" in body and "加油" in body
        assert "提醒" in message["Subject"]

    def test_digest_separates_pending_and_done(self):
        tasks = [self._task(), self._task(id="t2", title="整理书桌", time="20:00", done=True, priority="normal")]
        message = compose.digest_message(self._config(), tasks, "2026-03-02")
        body = message.get_body(preferencelist=("plain",)).get_content()
        assert "写移植报告" in body
        assert "已完" in body and "整理书桌" in body
        assert "1 项待办" in message["Subject"], "主题里应给出待办数量"

    def test_empty_day_still_produces_a_readable_mail(self):
        message = compose.digest_message(self._config(), [], "2026-03-02")
        body = message.get_body(preferencelist=("plain",)).get_content()
        assert "没有" in body
        assert "没有计划" in message["Subject"]

    def test_html_escapes_user_content(self):
        message = compose.reminder_message(self._config(), self._task(title="<script>x</script>"))
        html = message.get_body(preferencelist=("html",)).get_content()
        assert "<script>" not in html and "&lt;script&gt;" in html


# --------------------------------------------------------------------------- IMAP 解析


def _raw_message(subject: str, text: str | None, html: str | None, filename: str | None = None) -> bytes:
    message = compose.build_message(
        {"user": "a@b.com", "recipients": ["c@d.com"]}, subject, text or "placeholder"
    )
    if html:
        message.add_alternative(html, subtype="html")
    if filename:
        message.add_attachment(b"binary", maintype="application", subtype="octet-stream", filename=filename)
    return message.as_bytes()


class TestImapParsing:
    def test_html_to_text_strips_tags_and_scripts(self):
        text = imap_client._html_to_text("<p>你好</p><script>alert(1)</script><div>世界</div>")
        assert "你好" in text and "世界" in text and "script" not in text

    def test_decodes_rfc2047_subject(self):
        assert "中文主题" in imap_client._decode("=?utf-8?b?5Lit5paH5Li76aKY?=")

    def test_extracts_plain_and_html_bodies(self):
        import email as email_module

        message = email_module.message_from_bytes(_raw_message("主题", "纯文本正文", "<p>HTML 正文</p>"))
        text, html = imap_client._extract_body(message)
        assert "纯文本正文" in text
        assert "HTML 正文" in html

    def test_html_only_mail_falls_back_to_text(self):
        import email as email_module

        message = email_module.message_from_bytes(_raw_message("主题", None, "<p>只有 HTML</p>"))
        message.set_payload([part for part in message.get_payload() if not part.get_content_type().startswith("text/plain")])
        _, html = imap_client._extract_body(message)
        assert "只有 HTML" in html

    def test_attachment_names_are_collected(self):
        import email as email_module

        message = email_module.message_from_bytes(_raw_message("主题", "正文", None, filename="报表.pdf"))
        assert "报表.pdf" in imap_client._attachment_names(message)


# --------------------------------------------------------------------------- 真实收发（本地假服务器）


class _FakeSmtp:
    """最小的 SMTP 服务器：只实现发信需要的几个命令，用来验证真实代码路径。"""

    def __init__(self) -> None:
        self.payload = b""
        self.commands: list[str] = []
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._server.accept()
        except OSError:
            return
        with conn:
            conn.sendall(b"220 local ESMTP\r\n")
            buffer = b""
            data_mode = False
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                if data_mode:
                    if b"\r\n.\r\n" in buffer:
                        self.payload += buffer
                        buffer = b""
                        data_mode = False
                        conn.sendall(b"250 queued\r\n")
                    continue
                while b"\r\n" in buffer:
                    line, buffer = buffer.split(b"\r\n", 1)
                    text = line.decode("utf-8", "replace")
                    self.commands.append(text)
                    upper = text.upper()
                    if upper.startswith("EHLO"):
                        conn.sendall(b"250-local\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME\r\n")
                    elif upper.startswith("AUTH"):
                        conn.sendall(b"235 ok\r\n")
                    elif upper.startswith(("MAIL FROM", "RCPT TO", "RSET", "NOOP")):
                        conn.sendall(b"250 ok\r\n")
                    elif upper.startswith("DATA"):
                        conn.sendall(b"354 go ahead\r\n")
                        data_mode = True
                        break
                    elif upper.startswith("QUIT"):
                        conn.sendall(b"221 bye\r\n")
                        return
                    else:
                        conn.sendall(b"250 ok\r\n")

    def close(self) -> None:
        try:
            self._server.close()
        except OSError:
            pass


@pytest.fixture()
def fake_smtp():
    server = _FakeSmtp()
    try:
        yield server
    finally:
        server.close()


class TestSend:
    def _config(self, port: int) -> dict:
        return {
            "host": "127.0.0.1", "port": port, "security": "none", "user": "me@local",
            "sender": "me@local", "recipients": ["you@local"],
        }

    def test_sends_a_real_message_through_the_smtp_path(self, fake_smtp):
        config = self._config(fake_smtp.port)
        sender.send_message(config, compose.test_message(config), "auth-code")
        deadline = time.time() + 5
        while b"\r\n.\r\n" not in fake_smtp.payload and time.time() < deadline:
            time.sleep(0.02)
        payload = fake_smtp.payload
        assert payload, "服务器没有收到正文"
        assert b"To: you@local" in payload or b"To: <you@local>" in payload
        assert any(command.upper().startswith("AUTH") for command in fake_smtp.commands), "应真的走过认证"
        assert any(command.upper().startswith("MAIL FROM") for command in fake_smtp.commands)

    def test_connection_refused_gives_a_readable_chinese_error(self):
        # 取一个几乎肯定没人监听的端口：连不上时要给中文提示，而不是抛原始 OSError。
        # 这里显式给一个授权码，才走得到「建连」这一步（缺授权码会先被拦下，见下一个用例）。
        config = self._config(9)
        with pytest.raises(sender.MailError) as info:
            sender.send_message(config, compose.test_message(config), "auth-code")
        text = str(info.value)
        assert "失败" in text or "超时" in text or "拒绝" in text
        assert "Errno" not in text and "ConnectionRefused" not in text

    def test_missing_recipients_is_rejected_before_connecting(self):
        config = {**self._config(9), "recipients": []}
        with pytest.raises(sender.MailError) as info:
            sender.send_message(config, compose.test_message(config), "")
        assert "收件人" in str(info.value)

    def test_missing_password_is_reported_clearly(self):
        config = self._config(9)
        with pytest.raises(sender.MailError) as info:
            sender.send_message(config, compose.test_message(config), "")
        assert "授权码" in str(info.value)


# --------------------------------------------------------------------------- DPAPI


def test_dpapi_roundtrip_and_tamper_detection(tmp_path):
    if not secret.available():
        pytest.skip("非 Windows 环境没有 DPAPI")
    blob = secret.protect("机密")
    assert blob != "机密".encode("utf-8")
    assert secret.unprotect(blob) == "机密"
    with pytest.raises(secret.SecretError):
        secret.unprotect(b"not a valid blob")
