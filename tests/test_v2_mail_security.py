import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl


def test_privileged_page_only_allows_its_file(tmp_path):
    from nanqi.windows.webview import AppWebEnginePage
    trusted = tmp_path / "index.html"
    opened = []
    page = SimpleNamespace(_trusted_file=trusted.resolve(), _hand_off=opened.append)
    navigate = AppWebEnginePage.acceptNavigationRequest
    assert navigate(page, QUrl.fromLocalFile(str(trusted)), None, True)
    for url in (QUrl.fromLocalFile(str(tmp_path / "evil.html")), QUrl("data:text/html,evil"), QUrl("blob:https://evil/id")):
        assert not navigate(page, url, None, True)
    assert not navigate(page, QUrl.fromLocalFile(str(trusted)), None, False)


def test_smtp_queue_does_not_drop_busy_reminders(monkeypatch):
    from nanqi.mail import service as module
    first_started, release = threading.Event(), threading.Event()
    sent = []
    def send(cfg, message, password):
        first_started.set()
        assert release.wait(3)
        sent.append(message)
    monkeypatch.setattr(module.sender, "send_message", send)
    monkeypatch.setattr(module.compose, "reminder_message", lambda c,t,m:t["id"])
    store = SimpleNamespace(config={"onReminder":True,"user":"test"}, public=lambda:{"configured":True}, password=lambda:"dummy")
    service = module.MailService(store)
    service.on_reminders([{"id":"first"}])
    assert first_started.wait(2)
    service.on_reminders([{"id":"second"},{"id":"third"}])
    release.set()
    service.wait_idle()
    assert sent == ["first","second","third"]
    service.on_reminders([{"id":"second"}])
    service.wait_idle()
    assert len(sent) == 3


def test_failed_digest_persists_attempt_but_not_success(tmp_path, monkeypatch):
    from nanqi.mail.config import MailConfigStore
    from nanqi.mail.service import MailService
    from nanqi.mail import sender
    store = MailConfigStore(tmp_path)
    store.update({"host":"smtp.example.com","user":"test@example.com","recipients":["test@example.com"],"digest":True,"digestTime":"00:00"})
    monkeypatch.setattr(store,"password",lambda:"dummy")
    monkeypatch.setattr(sender,"send_message",lambda *a:(_ for _ in ()).throw(sender.MailError("offline")))
    service = MailService(store)
    assert service.maybe_send_digest([])
    service.wait_idle()
    assert store.config["lastDigestDate"] == ""
    restarted = MailConfigStore(tmp_path)
    assert restarted.config["lastDigestAttemptDate"]
    assert MailService(restarted).maybe_send_digest([]) is False


def test_imap_worker_serializes_and_invalidates_inflight():
    from nanqi.mail.requests import MailRequests
    started, release = threading.Event(), threading.Event()
    calls = []
    requests = MailRequests()
    def slow():
        calls.append("first-start")
        started.set()
        assert release.wait(3)
        calls.append("first-end")
    first = requests.submit("body",slow)
    assert started.wait(2)
    second = requests.submit("body",lambda:calls.append("second"))
    release.set()
    requests.wait_idle()
    assert calls == ["first-start","first-end","second"]
    assert not requests.current("body",first)
    assert requests.current("body",second)
    requests.invalidate()
    assert not requests.current("body",second)


def test_all_logout_cleanup_jobs_are_executed():
    from nanqi.mail.requests import MailRequests
    requests = MailRequests()
    calls = []
    for i in range(4):
        requests.submit("logout",lambda i=i:calls.append(i))
    requests.wait_idle()
    assert calls == [0,1,2,3]


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_compose_and_safe_body(app, monkeypatch):
    from nanqi.windows.mail_window import _ComposeDialog, SafeMailBody
    dialog = _ComposeDialog()
    assert dialog.subject_edit is not None
    body = SafeMailBody()
    opened = []
    from nanqi.windows import mail_window
    monkeypatch.setattr(mail_window.QDesktopServices,"openUrl",lambda url:opened.append(url.toString()))
    for url in ("file:///C:/bad.exe", "javascript:alert(1)", "data:text/html,bad", "https://example.com"):
        body._open_link(QUrl(url))
    assert opened == ["https://example.com"]
    assert body.loadResource(2,QUrl("file:///C:/secret.png")).isEmpty()
    dialog.close()
    body.close()
