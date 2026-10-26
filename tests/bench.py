"""bench.py —— 启动 / 切换 / 运行 三类性能的测量工具（可反复运行做回归对比）。

用户能感觉到的慢只有三种，所以这里就量这三种：

  1. **打开**：从 import 到主窗口出现、到页面加载完成，各阶段分别计时；
  2. **切换**：点一下导航按钮到**下一帧真的画出来**（click → requestAnimationFrame），
     这才是手感上的「卡不卡」；
  3. **运行**：一次「新建计划」的完整往返（Python 序列化 + 落盘 + 广播 + 前端重渲染）。

用法：
    python tests\\bench.py                 # 当前配置
    python tests\\bench.py --tasks 300     # 指定压测任务数（默认 300）

对比软件渲染与硬件渲染：
    $env:NANQI_WEBENGINE_SOFTWARE=1; python tests\\bench.py
    $env:NANQI_WEBENGINE_GPU=1;      python tests\\bench.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 【必须最先做】数据目录要在 import nanqi.webengine 之前定好：QtWebEngine 的缓存
# 目录是 prepare_webengine() 时确定的，顺序反了会污染真实用户数据。
_TEMP_DATA_DIR = tempfile.mkdtemp(prefix="nanqi-bench-")
os.environ.setdefault("NANQI_DATA_DIR", _TEMP_DATA_DIR)

TASKS = 300
NO_CACHE = "--nocache" in sys.argv
NO_COALESCE = "--nocoalesce" in sys.argv
for index, argument in enumerate(sys.argv):
    if argument == "--tasks" and index + 1 < len(sys.argv):
        TASKS = max(0, int(sys.argv[index + 1]))

T0 = time.perf_counter()
STAGES: list[tuple[str, float]] = []


def mark(label: str) -> None:
    STAGES.append((label, (time.perf_counter() - T0) * 1000))
    print(f"  {label:<32} {(time.perf_counter() - T0) * 1000:8.1f} ms", flush=True)


mark("import 开始")

from nanqi import webengine  # noqa: E402

mark("import nanqi.webengine")
webengine.prepare_webengine()
mark("prepare_webengine")

from PySide6 import QtWebEngineWidgets  # noqa: E402,F401

mark("import QtWebEngineWidgets")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
mark("QApplication 构造")

from nanqi.controller import AppController  # noqa: E402

controller = AppController(app, start_hidden=True)
mark("AppController 构造")

view = controller.main_window.view
controller.main_window.show()
mark("主窗口 visible")

# --------------------------------------------------------------------- 页面脚本

SEED = (
    """
(function(){
  window.__seed = null;
  var today = new Date().toISOString().slice(0,10);
  var base = new Date(today + 'T12:00:00');
  var chain = Promise.resolve();
  for (var i = 0; i < """
    + str(TASKS)
    + """; i++) {
    (function(i){
      // 把任务摊到未来 28 天里（约 11 项/天），这才是真实的使用密度；
      // 全塞在一天会让「周视图的事件排道算法」变成 O(n²) 的最坏情况，掩盖真实差异。
      var day = new Date(base.getTime() + (i %% 28) * 86400000);
      chain = chain.then(function(){ return window.nanqi.saveTask({
        title: '压测计划 ' + i, date: day.toISOString().slice(0,10),
        time: ('0' + (i %% 24)).slice(-2) + ':00',
        duration: 30, category: ['work','study','life'][i %% 3], reminder: -1,
        priority: 'normal', notes: 'x' }, 1); });
    })(i);
  }
  chain.then(function(){ window.__seed = 'ok'; })
       .catch(function(e){ window.__seed = 'ERR ' + e.message; });
})()
"""
)

PAINT = """
(function(){
  var seq = Array.prototype.slice.call(document.querySelectorAll('.segmented button[data-mode]'))
    .concat(Array.prototype.slice.call(document.querySelectorAll('.nav-item[data-view]')));
  window.__paint = null;
  var samples = [];
  function step(i){
    if (i >= 24) {
      samples.sort(function(a,b){ return a-b; });
      window.__paint = JSON.stringify({
        median: samples[Math.floor(samples.length/2)],
        p90: samples[Math.floor(samples.length*0.9)],
        max: samples[samples.length-1]
      });
      return;
    }
    var button = seq[i % seq.length];
    var start = performance.now();
    button.click();
    requestAnimationFrame(function(){ requestAnimationFrame(function(){
      samples.push(performance.now() - start);
      step(i + 1);
    });});
  }
  step(0);
})()
"""

RERENDER = """
(function(){
  window.__re = null;
  var box = document.querySelector('#show-completed');
  if (!box) { window.__re = 'no box'; return; }
  var samples = [];
  function step(i){
    if (i >= 12) {
      samples.sort(function(a,b){ return a-b; });
      window.__re = JSON.stringify({median: samples[Math.floor(samples.length/2)],
                                    max: samples[samples.length-1]});
      return;
    }
    var start = performance.now();
    box.checked = !box.checked;
    box.dispatchEvent(new Event('change'));
    requestAnimationFrame(function(){ requestAnimationFrame(function(){
      samples.push(performance.now() - start);
      step(i + 1);
    });});
  }
  step(0);
})()
"""

SAVE = """
(function(){
  window.__save = null;
  var base = new Date(new Date().toISOString().slice(0,10) + 'T12:00:00');
  var start = performance.now();
  var chain = Promise.resolve();
  for (var i = 0; i < 30; i++) {
    (function(i){
      var day = new Date(base.getTime() + (i % 28) * 86400000);
      chain = chain.then(function(){ return window.nanqi.saveTask({
        title: '往返测试 ' + i, date: day.toISOString().slice(0,10),
        time: ('0' + (i % 24)).slice(-2) + ':00',
        duration: 30, category: 'work', reminder: -1, priority: 'normal', notes: '' }, 1); });
    })(i);
  }
  chain.then(function(){
    window.nanqi.get().then(function(s){
      window.__save = JSON.stringify({ms: performance.now() - start, tasks: s.tasks.length});
    });
  }).catch(function(e){ window.__save = JSON.stringify({error: e.message}); });
})()
"""


BURST = """
(function(){
  // 连续点 20 次「勾选完成」，**不等待**前一次返回——这就是用户快速连点、
  // 或者一次操作触发多次状态广播时的真实情况。按帧合并重渲染的意义全在这里：
  // 20 次广播如果每次都整表重建，页面会排队重绘 20 次；合并后每帧最多一次。
  window.__burst = null;
  var items = Array.prototype.slice.call(document.querySelectorAll('[data-task]')).slice(0, 20);
  if (!items.length) { window.__burst = JSON.stringify({skipped: true}); return; }
  var start = performance.now();
  var done = 0, total = items.length;
  items.forEach(function(el){
    var id = el.getAttribute('data-task');
    window.nanqi.applyTaskAction({id:id,action:"complete",scope:"single"}).then(function(){ done++; },
      function(){ done++; }).finally(function(){
      if (done === total) {
        requestAnimationFrame(function(){ requestAnimationFrame(function(){
          window.__burst = JSON.stringify({ops: total, ms: performance.now() - start});
        });});
      }
    });
  });
})()
"""


def poll(script: str, key: str, done) -> None:
    def attempt() -> None:
        view.page().runJavaScript(
            f"window.{key} || ''",
            lambda raw: done(raw) if raw else QTimer.singleShot(100, attempt),
        )

    attempt()


def report_paint(label: str, raw) -> None:
    try:
        data = json.loads(raw)
    except Exception as error:  # noqa: BLE001
        print(f"  {label:<32} 解析失败 {raw!r} ({error})", flush=True)
        return
    median = data.get("median", 0)
    p90 = data.get("p90", median)
    print(
        f"  {label:<32} 中位 {median:6.1f} ms   P90 {p90:6.1f} ms   最差 {data.get('max', 0):6.1f} ms",
        flush=True,
    )


loaded = {"done": False}
view.loadFinished.connect(lambda ok: loaded.update(done=True))


def phase_wait() -> None:
    if not loaded["done"]:
        QTimer.singleShot(50, phase_wait)
        return
    mark("页面 loadFinished")
    QTimer.singleShot(500, phase_switch)


def phase_switch() -> None:
    mark("首帧渲染完成")
    if NO_CACHE:
        # A/B 对照：把渲染期缓存关掉，就等价于优化前的实现
        # （renderer.js 是普通脚本，顶层函数就是 window 上的属性，可以直接替换）。
        view.page().runJavaScript("window.beginFrame=function(){};window.endFrame=function(){};")
    if NO_COALESCE:
        # A/B 对照：去掉「按帧合并」，每次 render() 立刻同步重绘（优化前的行为）。
        view.page().runJavaScript("window.render=function(){renderNow();};")
    view.page().runJavaScript(PAINT)
    poll(PAINT, "__paint", lambda raw: (report_paint("切换视图 click→paint", raw), phase_rerender()))


def phase_rerender() -> None:
    view.page().runJavaScript(RERENDER)
    poll(RERENDER, "__re", lambda raw: (report_paint("重渲染（勾选已办）", raw), phase_save()))


def phase_save() -> None:
    view.page().runJavaScript(SAVE)
    poll(SAVE, "__save", finish_save)


def phase_burst() -> None:
    view.page().runJavaScript(BURST)
    poll(BURST, "__burst", finish_burst)


def finish_save(raw) -> None:
    try:
        data = json.loads(raw)
        if "error" in data:
            print(f"  保存往返失败：{data['error']}", flush=True)
        else:
            print(
                f"  {'30 次保存往返（含渲染）':<32} 合计 {data['ms']:8.1f} ms"
                f"   平均 {data['ms'] / 30:6.1f} ms/次   共 {data['tasks']} 项",
                flush=True,
            )
    except Exception as error:  # noqa: BLE001
        print(f"  保存往返解析失败：{raw!r} ({error})", flush=True)
    phase_burst()


def finish_burst(raw) -> None:
    try:
        data = json.loads(raw)
        if data.get("skipped"):
            print("  连续操作压测：页面没有可勾选的计划，跳过", flush=True)
        else:
            ops = data["ops"]
            print(
                f"  {'连续 20 次勾选（不等待）':<32} 合计 {data['ms']:8.1f} ms"
                f"   平均 {data['ms'] / ops:6.1f} ms/次",
                flush=True,
            )
    except Exception as error:  # noqa: BLE001
        print(f"  连续操作解析失败：{raw!r} ({error})", flush=True)
    mark("全部完成")
    controller.request_quit()


if __name__ == "__main__":
    print("Chromium 开关：", os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "(未设置)")[:150])
    print(f"压测任务数：{TASKS}")
    print()
    print("① 启动阶段：")
    QTimer.singleShot(600, phase_wait)
    QTimer.singleShot(300000, controller.request_quit)
    app.exec()

    visible = next((ms for name, ms in STAGES if name == "主窗口 visible"), None)
    loaded_ms = next((ms for name, ms in STAGES if name == "页面 loadFinished"), None)
    print()
    if visible is not None:
        print(f"→ 打开：主窗口 {visible:.0f} ms，页面就绪 {loaded_ms or 0:.0f} ms")
    shutil.rmtree(_TEMP_DATA_DIR, ignore_errors=True)
