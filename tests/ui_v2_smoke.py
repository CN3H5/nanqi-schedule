"""Actual WebEngine form roundtrips; synthetic data only, no real accounts."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
data = Path(tempfile.mkdtemp(prefix="nanqi-v2-"))
os.environ["NANQI_DATA_DIR"] = os.environ["WEEKLIGHT_DATA_DIR"] = str(data)
from nanqi.webengine import prepare_webengine
prepare_webengine()
from PySide6 import QtWebEngineWidgets
from PySide6.QtWidgets import QApplication
from nanqi.controller import AppController

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
controller = AppController(app, start_hidden=True)


def pump(seconds=0.1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def js(view, expression):
    result = []
    view.page().runJavaScript("JSON.stringify((" + expression + "))", result.append)
    deadline = time.monotonic() + 10
    while not result and time.monotonic() < deadline:
        pump()
    assert result, "JavaScript callback timeout"
    return json.loads(result[0]) if result[0] else None


def wait_for(predicate):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if predicate():
            return
        pump()
    raise AssertionError("roundtrip timeout")


try:
    view = controller.main_window.view
    wait_for(lambda: js(view, "typeof state!=='undefined'&&!!state"))
    controller.open_widget()
    widget = controller.widget_window.view
    wait_for(lambda: js(widget, "typeof state!=='undefined'&&!!state"))
    for i, reminder in enumerate([-1, 0, 10]):
        js(widget, "(()=>{const f=document.querySelector('#quick-form');f.elements.title.value='V2-widget-" + str(i) + "';f.elements.reminder.value='" + str(reminder) + "';f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));return true})()")
        wait_for(lambda: len(controller.store.state["tasks"]) == i + 1)
        assert controller.store.state["tasks"][-1]["reminder"] == reminder
        pump()
        print(f"[OK] widget reminder {reminder}: saved", flush=True)
    for duration in (120, 1440):
        payload = dict(title=f"V2-duration-{duration}", date="2030-01-01", time="23:00", duration=duration, reminder=-1, category="work")
        response = json.loads(controller.bridge.invoke("save-task", json.dumps([payload, 1])))
        assert response["ok"]
        task = controller.store.state["tasks"][-1]
        pump()
        js(view, "(()=>{openEditor(state.tasks.find(t=>t.id===" + json.dumps(task["id"]) + "));document.querySelector('#task-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));return true})()")
        wait_for(lambda: js(view, "!document.querySelector('#editor').open"))
        saved = next(t for t in json.loads(controller.store.data_file.read_text(encoding="utf-8"))["tasks"] if t["id"] == task["id"])
        assert saved["duration"] == duration
        print(f"[OK] overnight edit persisted: {duration}", flush=True)
    js(view, "(()=>{openEditor();const f=document.querySelector('#task-form');f.elements.quick.value='每天跑步';f.elements.quick.dispatchEvent(new Event('input',{bubbles:true}));return true})()")
    pump()
    assert js(view,"document.querySelector('#task-form').elements.repeat.value") == "daily"
    js(view, "(()=>{const f=document.querySelector('#task-form');f.elements.quick.value='跑步';f.elements.quick.dispatchEvent(new Event('input',{bubbles:true}));return true})()")
    pump()
    assert js(view,"document.querySelector('#task-form').elements.repeat.value") == "1"
    print("[OK] NLP removed repeat token resets form", flush=True)
    print("v2 form roundtrips: 6/6 passed", flush=True)
finally:
    controller.request_quit()
    pump()
