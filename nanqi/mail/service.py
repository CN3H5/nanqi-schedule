"""Serialized SMTP delivery. Accepted queue entries are never dropped when busy."""
from __future__ import annotations
import threading
import time
from queue import Queue
from PySide6.QtCore import QObject, Signal
from . import compose, sender
from .secret import SecretError

class MailService(QObject):
    finished = Signal(bool, str)

    def __init__(self, config_store, parent=None):
        super().__init__(parent)
        self._store = config_store
        self._lock = threading.Lock()
        self._queue = Queue()
        self._sent_reminders = {}
        self.last_result = (True, "")
        self._worker = threading.Thread(target=self._run, name="nanqi-mail-queue", daemon=True)
        self._worker.start()

    @property
    def config(self):
        return self._store.config

    def configured(self):
        return self._store.public()["configured"]

    def on_reminders(self, tasks, message_text=""):
        if not self.config.get("onReminder") or not self.configured():
            return
        now = time.monotonic()
        for task in tasks:
            key = str(task.get("id") or "")
            with self._lock:
                last = self._sent_reminders.get(key)
                if last is not None and now - last < 300:
                    continue
                if self._dispatch("reminder", lambda cfg, password, task=dict(task): compose.reminder_message(cfg, task, message_text)):
                    self._sent_reminders[key] = now
        with self._lock:
            self._sent_reminders = {k:v for k,v in self._sent_reminders.items() if now-v < 300}

    def send_digest(self, tasks, day, message_text=""):
        return self._dispatch("digest", lambda cfg, password: compose.digest_message(cfg, tasks, day, message_text), day=day)

    def maybe_send_digest(self, tasks, now=None, message_text=""):
        cfg = self.config
        if not cfg.get("digest"):
            return False
        moment = now or time.localtime()
        today = time.strftime("%Y-%m-%d", moment)
        if today in (cfg.get("lastDigestDate"), cfg.get("lastDigestAttemptDate")):
            return False
        if time.strftime("%H:%M", moment) < cfg.get("digestTime", "08:00") or not self.configured():
            return False
        return self.send_digest([t for t in tasks if t.get("date") == today], today, message_text)

    def _dispatch(self, kind, factory, day=None):
        config = self.config
        try:
            password = self._store.password() if config.get("user") else ""
            if day:
                self._store.update({"lastDigestAttemptDate": day})
        except (SecretError, OSError, ValueError) as error:
            self._report(False, str(error))
            return False
        self._queue.put((kind, factory, config, password, day))
        return True

    def _run(self):
        while True:
            kind, factory, config, password, day = self._queue.get()
            try:
                sender.send_message(config, factory(config, password), password)
                if day:
                    self._store.mark_digest_sent(day)
                self._report(True, "邮件已发送")
            except Exception as error:
                self._report(False, str(error) or "邮件发送失败")
            finally:
                self._queue.task_done()

    def wait_idle(self):
        """For deterministic shutdown/tests, never call on the UI thread."""
        self._queue.join()

    def _report(self, ok, message):
        self.last_result = (ok, message)
        self.finished.emit(ok, message)
