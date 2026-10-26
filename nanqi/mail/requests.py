"""One IMAP worker, generation-tagged results, no socket access on UI thread."""
import threading
from queue import Queue
from PySide6.QtCore import QObject, Signal


class MailRequests(QObject):
    finished = Signal(str, int, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation = {}
        self._queue = Queue()
        self._lock = threading.Lock()
        threading.Thread(target=self._run, name="nanqi-imap", daemon=True).start()

    def invalidate(self, kind=None):
        with self._lock:
            for key in ([kind] if kind else ["login", "messages", "body", "logout"]):
                self._generation[key] = self._generation.get(key, 0) + 1

    def current(self, kind, token):
        with self._lock:
            return token == self._generation.get(kind, 0)

    def submit(self, kind, function):
        with self._lock:
            token = self._generation.get(kind, 0) + 1
            self._generation[kind] = token
        self._queue.put((kind, token, function))
        return token

    def _run(self):
        while True:
            kind, token, function = self._queue.get()
            try:
                if kind != "logout" and not self.current(kind, token):
                    continue
                value, error = None, ""
                try:
                    value = function()
                except Exception as exc:
                    error = str(exc) or "邮箱请求失败"
                try:
                    self.finished.emit(kind, token, value, error)
                except RuntimeError:  # owner destroyed during application shutdown
                    pass
            finally:
                self._queue.task_done()

    def wait_idle(self):
        self._queue.join()
