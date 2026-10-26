"""bench_startup.py —— 启动慢在哪一步（cProfile 版，开发时用，不属于 pytest）。

前面用 `mark()` 只能看到「阶段边界」，看不出 AppController 构造那 600 多毫秒花在哪。
这里用 cProfile 把整条启动路径摊开，按累计耗时排序，找真正的大头。

用法：
    python tests\\bench_startup.py
    python tests\\bench_startup.py --top 30
"""

from __future__ import annotations

import cProfile
import io
import os
import pstats
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TEMP_DATA_DIR = tempfile.mkdtemp(prefix="nanqi-startup-")
os.environ.setdefault("NANQI_DATA_DIR", _TEMP_DATA_DIR)

TOP = 25
for index, argument in enumerate(sys.argv):
    if argument == "--top" and index + 1 < len(sys.argv):
        TOP = int(sys.argv[index + 1])

from nanqi import webengine  # noqa: E402

T0 = time.perf_counter()
webengine.prepare_webengine()
T1 = time.perf_counter()

from PySide6 import QtWebEngineWidgets  # noqa: E402,F401

T2 = time.perf_counter()

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
T3 = time.perf_counter()

from nanqi.controller import AppController  # noqa: E402

print(f"prepare_webengine      {(T1 - T0) * 1000:7.1f} ms")
print(f"import QtWebEngine     {(T2 - T1) * 1000:7.1f} ms")
print(f"QApplication           {(T3 - T2) * 1000:7.1f} ms")
print()

profiler = cProfile.Profile()
profiler.enable()
T4 = time.perf_counter()
controller = AppController(app, start_hidden=True)
T5 = time.perf_counter()
profiler.disable()

print(f"AppController 构造     {(T5 - T4) * 1000:7.1f} ms")
print()
stream = io.StringIO()
stats = pstats.Stats(profiler, stream=stream)
stats.sort_stats("cumulative").print_stats(TOP)
lines = stream.getvalue().splitlines()
for line in lines:
    # 只留下调用栈里的函数名与两个耗时列，去掉文件路径里的长前缀
    print(line.replace(str(ROOT) + os.sep, ""))

QTimer.singleShot(0, controller.request_quit)
app.exec()
shutil.rmtree(_TEMP_DATA_DIR, ignore_errors=True)
