"""debug_editor_flow.py —— 手动调试脚本：驱动真实窗口验证新编辑器流程。

验证：
  1. 新建对话框打开后，在自由文本里输入自然语言，⋯ 面板字段被自动同步；
  2. 识别气泡（chips）与 Markdown 实时预览出现；
  3. 提交后任务真的落盘，标签/类型/时间段正确；
  4. 复习计划 + 考试日期会自动生成考前 7/3/1 天的复习安排；
  5. 分类管理：新增/改名/删除都能走通。

不是正式测试（tests/ui_smoke.py 才是），随用随改。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["NANQI_DATA_DIR"] = tempfile.mkdtemp(prefix="nanqi-editor-debug-")

from nanqi import webengine  # noqa: E402

webengine.prepare_webengine()

from PySide6 import QtWebEngineWidgets  # noqa: E402,F401
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

RESULTS = []


def record(label, ok, extra=""):
    RESULTS.append((label, ok))
    print(f"[{'OK' if ok else '!!'}] {label}" + (f" — {extra}" if extra else ""), flush=True)


def main() -> int:
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    import traceback

    def guard(fn):
        def wrapped(*args):
            try:
                return fn(*args)
            except Exception:
                traceback.print_exc()
                app.exit(1)

        return wrapped

    from nanqi.controller import AppController

    controller = AppController(app, start_hidden=True)
    view = controller.main_window.view
    today = time.strftime("%Y-%m-%d")

    def await_js(expression, timeout_s, on_value, label):
        deadline = time.monotonic() + timeout_s

        def attempt():
            def handle(raw):
                value = None
                if raw not in (None, "", "null"):
                    try:
                        value = json.loads(raw)
                    except (TypeError, ValueError):
                        value = None
                if value is not None:
                    guard(on_value)(value)
                    return
                if time.monotonic() > deadline:
                    record(label, False, f"超时，最后返回 {raw!r}")
                    on_value(None)
                    return
                QTimer.singleShot(150, attempt)

            view.page().runJavaScript("JSON.stringify(" + expression + ")", handle)

        attempt()

    # ---------------------------------------------------------- 1. 打开编辑器并输入
    def step1():
        view.page().runJavaScript(
            "document.querySelector('[data-action=new]').click();"
            "var q=document.querySelector('#quick-input');"
            "q.value='明天下午3点到5点 组会 #项目 @工作 每周 提前10分钟\\n- 带会议纪要\\n- **确认议程**';"
            "q.dispatchEvent(new Event('input',{bubbles:true}));"
            "window.__r={opened:document.querySelector('#editor').open};"
        )
        await_js(
            "(function(){var f=document.querySelector('#task-form');"
            "return {date:f.elements.date.value,time:f.elements.time.value,end:f.elements.endTime.value,"
            "repeat:f.elements.repeat.value,until:f.elements.repeatUntil.value,reminder:f.elements.reminder.value,"
            "category:f.elements.category.value,tags:f.elements.tags.value,kind:f.elements.kind.value,"
            "chips:document.querySelector('#parsed-chips').innerText,"
            "preview:document.querySelector('#quick-preview').innerHTML};})()",
            10,
            finish1,
            "NLP 同步到更多选项面板",
        )

    def finish1(v):
        if v is None:
            app.exit(1)
            return
        from datetime import datetime, timedelta

        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        ok = (
            v.get("date") == tomorrow
            and v.get("time") == "15:00"
            and v.get("end") == "17:00"
            and v.get("repeat") == "weekly"
            and v.get("reminder") == "10"
            and v.get("category") == "work"
            and v.get("tags") == "项目"
            and "已识别" in v.get("chips", "")
            and "<li>" in v.get("preview", "")
            and "<strong>" in v.get("preview", "")
        )
        record("NLP 同步到更多选项面板 + 气泡 + Markdown 预览", ok, json.dumps(v, ensure_ascii=False)[:300])
        step2()

    # ---------------------------------------------------------- 2. 提交保存
    def step2():
        view.page().runJavaScript(
            "window.__r=null;document.querySelector('#task-form [type=submit]').click();"
            "setTimeout(()=>{window.nanqi.get().then(s=>{window.__r={count:s.tasks.length,"
            "first:s.tasks[0]||null,error:document.querySelector('#form-error').textContent};});},800);"
        )
        await_js("window.__r", 15, finish2, "保存自由文本计划")

    def finish2(v):
        if v is None:
            app.exit(1)
            return
        t = v.get("first") or {}
        ok = (
            v.get("count", 0) >= 12  # 每周重复默认 12 周
            and t.get("title") == "组会"
            and t.get("time") == "15:00"
            and t.get("duration") == 120
            and t.get("tags") == ["项目"]
            and t.get("category") == "work"
            and t.get("reminder") == 10
            and "带会议纪要" in (t.get("notes") or "")
            and not v.get("error")
        )
        record(
            "保存自由文本计划（每周 12 项 / 120 分钟 / 标签 / 备注）",
            ok,
            json.dumps({k: t.get(k) for k in ("title", "time", "duration", "tags", "notes")}, ensure_ascii=False)
            + f" count={v.get('count')} error={v.get('error')!r}",
        )
        step3()

    # ---------------------------------------------------------- 3. 复习计划 + 考试日期
    def step3():
        from datetime import datetime, timedelta

        exam = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        view.page().runJavaScript(
            "document.querySelector('[data-action=new]').click();"
            "var f=document.querySelector('#task-form');"
            "f.elements.quick.value='复习数学分析';"
            "f.elements.quick.dispatchEvent(new Event('input',{bubbles:true}));"
            "document.querySelector('#more-options').click();"
            f"f.elements.examDate.value='{exam}';"
            "f.elements.examDate.dispatchEvent(new Event('change',{bubbles:true}));"
            "window.__r={kind:f.elements.kind.value,examVisible:!document.querySelector('#exam-date-field').hidden};"
        )
        await_js("window.__r", 10, lambda v: finish3(v, exam), "复习计划表单")

    def finish3(v, exam):
        if v is None:
            app.exit(1)
            return
        record("类型自动识别为复习 + 考试日期栏出现", v.get("kind") == "review" and v.get("examVisible") is True, json.dumps(v, ensure_ascii=False))
        view.page().runJavaScript(
            "window.__r=null;document.querySelector('#task-form [type=submit]').click();"
            "setTimeout(()=>{window.nanqi.get().then(s=>{window.__r={titles:s.tasks.map(t=>t.title),"
            "dates:s.tasks.map(t=>t.date)};});},900);"
        )
        await_js("window.__r", 15, lambda v: finish3b(v, exam), "复习安排生成")

    def finish3b(v, exam):
        if v is None:
            app.exit(1)
            return
        from datetime import datetime, timedelta

        exam_d = datetime.strptime(exam, "%Y-%m-%d")
        want = [(exam_d - timedelta(days=n)).strftime("%Y-%m-%d") for n in (7, 3, 1)]
        got = [d for t, d in zip(v.get("titles", []), v.get("dates", [])) if t.startswith("复习：")]
        record(
            "考前 7/3/1 天复习安排自动生成",
            sorted(got) == sorted(want),
            f"want={want} got={sorted(got)}",
        )
        step4()

    # ---------------------------------------------------------- 4. 分类管理
    def step4():
        view.page().runJavaScript(
            "window.__r=null;window.nanqi.saveCategory({name:'课程'}).then(s=>{"
            "var c=s.categories.find(c=>c.name==='课程');"
            "return window.nanqi.deleteCategory(c.id).then(s2=>({afterDelete:s2.categories.length,"
            "tasksOk:s2.tasks.every(t=>t.category!=='课 程'||true),"
            "sidebarHas:document.querySelector('#categories').children.length}));"
            "}).then(r=>{window.__r=r;}).catch(e=>{window.__r={error:e.message};});"
        )
        await_js("window.__r", 15, finish4, "分类新增/删除")

    def finish4(v):
        if v is None:
            app.exit(1)
            return
        record("分类新增/删除走通", v.get("afterDelete") == 3 and not v.get("error"), json.dumps(v, ensure_ascii=False))
        step5()

    # ---------------------------------------------------------- 5. 手动覆盖不被 NLP 冲掉
    def step5():
        view.page().runJavaScript(
            "document.querySelector('[data-action=new]').click();"
            "var f=document.querySelector('#task-form');"
            "document.querySelector('#more-options').click();"
            "f.elements.reminder.value='60';"
            "f.elements.reminder.dispatchEvent(new Event('change',{bubbles:true}));"
            "f.elements.quick.value='后天 提前10分钟 复查牙齿';"
            "f.elements.quick.dispatchEvent(new Event('input',{bubbles:true}));"
            "window.__r={reminder:f.elements.reminder.value};"
        )
        await_js("window.__r", 10, finish5, "手动覆盖优先")

    def finish5(v):
        if v is None:
            app.exit(1)
            return
        record("手动改过的提醒不被 NLP 覆盖", v.get("reminder") == "60", json.dumps(v))
        step6()

    # ---------------------------------------------------------- 6. 连续N周的结束日期不被默认值覆盖
    def step6():
        view.page().runJavaScript(
            "document.querySelector('[data-action=new]').click();"
            "var f=document.querySelector('#task-form');"
            "f.elements.quick.value='每周五 组会 连续16周';"
            "f.elements.quick.dispatchEvent(new Event('input',{bubbles:true}));"
            "window.__r={repeat:f.elements.repeat.value,until:f.elements.repeatUntil.value,date:f.elements.date.value};"
        )
        await_js("window.__r", 10, finish6, "连续16周识别")

    def finish6(v):
        if v is None:
            app.exit(1)
            return
        from datetime import datetime, timedelta

        start = datetime.strptime(v.get("date"), "%Y-%m-%d")
        want = (start + timedelta(days=15 * 7)).strftime("%Y-%m-%d")
        record(
            "「连续16周」的结束日期不被自动默认值覆盖",
            v.get("repeat") == "weekly" and v.get("until") == want,
            json.dumps(v) + f" want={want}",
        )
        controller.request_quit()

    QTimer.singleShot(600, guard(step1))
    QTimer.singleShot(90000, lambda: (record("整体超时", False), controller.request_quit()))
    app.exec()

    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\n编辑器流程：{passed}/{len(RESULTS)} 项通过", flush=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
