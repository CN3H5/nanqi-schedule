r"""course_repair.py —— 修复旧版导入留下的「拼接标题」课程日程。

【为什么需要这个模块】
v1.0 的某一版 exe 里，课表导入走的是「把整格文本拼成一行」的兜底解析，于是任务的
标题变成了这样（真实数据）：

    07:5009:25 FL1102A.02 [2] 科普英语交流A Thomas Yifang Xiao 3,6-14 1,22210
    14:0015:35 PE00001.15 [1] 基础体育 李少松 2-5,7-18 6,7西区操场（男生）
    21:1021:55 FL1100.01 [4] 大学英语 1 13不需选课，外语教学中…

这些任务的其它字段也是错的：`duration` 固定 45（真实 95/145）、`time` 是节次默认时刻
（真实时刻在标题里）、`reminder` 是 10、备注只有一行「来源」。用户升级之后：
  * 日历里同一门课出现两份（旧的脏的 + 新导入的正确的）；
  * 提醒弹出来是「上课提醒 · 07:5009:25 FL1102A.02 [2] …」这一整串。

所以启动时做一次**一次性修复**：能从标题里解析出来的就修好，已经存在同一门课同一天
同一时刻的正确任务就把脏的那条删掉。修复前先写一份旁路备份（`before-repair-*.json`）。

【修复原则】
  * **只碰看起来确实是旧格式的课程任务**（category=course 且标题以两个粘连时刻开头）；
  * **能修就修，不轻易删**；只有在「同名 + 同日期 + 同一时刻」的正确任务已经存在时才删；
  * **幂等**：修完再跑一次不会再有变化（所以不会每次启动都写文件）。
"""

from __future__ import annotations

import re

__all__ = ["looks_glued", "parse_glued_title", "repair_state"]

#: `07:5009:25 …`：两个时刻粘在一起，这是旧兜底解析的指纹。
_GLUED_RE = re.compile(r"^(\d{1,2}:\d{2})(\d{1,2}:\d{2})\s+(.+)$", re.S)
#: `FL1102A.02 [2] 科普英语交流A …`：课程代码 + 方括号节次
_CODE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9._-]{1,19})\s+\[(\d{1,3})\]\s*(.+)$", re.S)
#: `3,6-14` / `2-5,7-18` / `4`：周次（可能带单双周后缀）
_WEEKS_RE = re.compile(r"^\d{1,2}(?:\s*[-–—~～]\s*\d{1,2})?(?:\s*[,，]\s*\d{1,2}(?:\s*[-–—~～]\s*\d{1,2})?)*\s*(?:单|双)?$")
#: `1,2` / `11,12,13`：节次。它常常和教室粘在一起（`1,22210` → 节次 1,2 + 教室 2210），
#: 所以先试「节次 + 四位数字教室」，再试「节次 + 非数字开头的教室」，最后才整体当教室。
_SPAN_ROOM_DIGITS_RE = re.compile(r"^(\d{1,2}(?:[,，]\d{1,2})+)(\d{4}[A-Za-z]?)$")
_SPAN_ROOM_TEXT_RE = re.compile(r"^(\d{1,2}(?:[,，]\d{1,2})+)(\D.*)$", re.S)
_TIMESPAN_RE = re.compile(r"^(\d{1,2}(?:[,，]\d{1,2})*)\s*(.*)$", re.S)

MIN_DURATION = 15
MAX_DURATION = 1440


def _clock(value: str) -> str | None:
    try:
        hour, minute = (int(part) for part in value.split(":"))
    except (AttributeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _minutes(start: str, end: str) -> int:
    """两个时刻之间的分钟数（夹在 15–1440，与 clean_task 的规则一致）。"""
    try:
        start_minutes = int(start[:2]) * 60 + int(start[3:5])
        end_minutes = int(end[:2]) * 60 + int(end[3:5])
    except (TypeError, ValueError):
        return 60
    if end_minutes <= start_minutes:  # 跨天/解析异常：给一个能用的默认值
        return 90
    return max(MIN_DURATION, min(MAX_DURATION, end_minutes - start_minutes))


def looks_glued(title) -> bool:
    """这个标题是不是旧版那种「两个时刻粘在一起」的垃圾标题。"""
    return isinstance(title, str) and bool(_GLUED_RE.match(title.strip()))


def parse_glued_title(text) -> dict | None:
    """把旧版的拼接标题拆回结构化字段；认不出返回 None。

    返回 `{name, start, end, duration, weeks_text, timespan, room, code, section, teacher}`。
    课程名取「代码/节次」之后的第一个词，教师取到周次之前的若干词——真实数据里这两段
    都是空格分隔的（`科普英语交流A Thomas Yifang Xiao 3,6-14 1,22210`）。
    """
    if not isinstance(text, str):
        return None
    match = _GLUED_RE.match(text.strip())
    if not match:
        return None
    start = _clock(match.group(1))
    end = _clock(match.group(2))
    if start is None:
        return None
    tail = match.group(3).strip()
    if not tail:
        return None

    code = ""
    section = ""
    code_match = _CODE_RE.match(tail)
    if code_match:
        code, section, tail = code_match.group(1), code_match.group(2), code_match.group(3).strip()
    if not tail:
        return None

    tokens = tail.split()
    name = tokens[0]
    index = 1
    teacher_parts: list[str] = []
    while index < len(tokens) and not _WEEKS_RE.match(tokens[index]):
        teacher_parts.append(tokens[index])
        index += 1
    weeks_text = tokens[index] if index < len(tokens) else ""
    rest = " ".join(tokens[index + 1 :]) if index + 1 < len(tokens) else ""

    timespan = ""
    room = ""
    if rest:
        digits = _SPAN_ROOM_DIGITS_RE.match(rest)
        text = _SPAN_ROOM_TEXT_RE.match(rest)
        if digits:
            timespan = digits.group(1).replace("，", ",")
            room = digits.group(2)
        elif text:
            timespan = text.group(1).replace("，", ",")
            room = text.group(2).strip()
        elif _TIMESPAN_RE.match(rest):
            span = _TIMESPAN_RE.match(rest)
            timespan = span.group(1).replace("，", ",")
            room = span.group(2).strip()
        else:
            room = rest

    if not name:
        return None
    return {
        "name": name,
        "start": start,
        "end": end or "",
        "duration": _minutes(start, end or ""),
        "weeks_text": weeks_text,
        "timespan": timespan,
        "room": room,
        "code": code,
        "section": section,
        "teacher": " ".join(teacher_parts).strip(),
    }


def _notes_of(info: dict) -> str:
    lines = []
    if info.get("room"):
        lines.append("地点：" + info["room"])
    if info.get("teacher"):
        lines.append("教师：" + info["teacher"])
    if info.get("weeks_text"):
        lines.append("周次：" + info["weeks_text"])
    if info.get("timespan"):
        lines.append("节次：" + info["timespan"])
    if info.get("code"):
        code = info["code"] + (f" [{info['section']}]" if info.get("section") else "")
        lines.append("课程代码：" + code)
    lines.append("来源：教务系统课表导入（已修复旧版解析）")
    return "\n".join(lines)


#: 新导入写的课程提醒是提前 30 分钟；旧版写的是 10。只把「导入来的」课程/考试补到 30，
#: 用户自己改过的（备注里没有「导入」字样）一律不动。
COURSE_REMINDER_MINUTES = 30
_REMINDER_FIX_CATEGORIES = ("course", "exam")


def repair_state(state: dict) -> dict:
    """就地修复 `state["tasks"]`，返回统计 `{repaired, removed, reminders}`。

    统计全为 0 时表示「没有任何变化」——调用方据此决定要不要落盘。
    """
    stats = {"repaired": 0, "removed": 0, "reminders": 0}
    tasks = state.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return stats

    parsed: dict[str, dict] = {}
    for task in tasks:
        if task.get("category") != "course":
            continue
        info = parse_glued_title(task.get("title"))
        if info is not None:
            parsed[task["id"]] = info
    if not parsed:
        # 没有旧格式任务，也别急着返回：提醒时长的归一化对全体课程都适用。
        return _normalise_course_reminders(tasks, stats)

    # 正常（非旧格式）任务已有的「同名 + 同日期 + 同时刻」集合：脏的那条可以直接删。
    good = {
        (task.get("title"), task.get("date"), task.get("time"))
        for task in tasks
        if task.get("id") not in parsed
    }
    # 【更重要的一条】如果这门课已经有一份**正常导入**的日程（备注里带「导入」，
    # 说明是修好解析之后重新导入的），那么旧格式那一整份就是过期的：它用的是另一套
    # 「第一周从哪天开始」的猜测，日期整体错位。这种情况整份删掉，只留新的那份。
    good_names = {
        task.get("title")
        for task in tasks
        if task.get("id") not in parsed and "导入" in str(task.get("notes") or "")
    }
    kept: list[dict] = []
    seen: set[tuple] = set()
    for task in tasks:
        info = parsed.get(task.get("id"))
        if info is None:
            kept.append(task)
            continue
        if info["name"] in good_names:
            stats["removed"] += 1
            continue
        key = (info["name"], task.get("date"), info["start"])
        if key in good or key in seen:
            stats["removed"] += 1
            continue
        task["title"] = info["name"]
        task["time"] = info["start"]
        task["duration"] = info["duration"]
        task["reminder"] = COURSE_REMINDER_MINUTES
        task["notes"] = _notes_of(info)
        kept.append(task)
        seen.add(key)
        stats["repaired"] += 1

    state["tasks"] = kept
    return _normalise_course_reminders(state["tasks"], stats)


def _normalise_course_reminders(tasks: list[dict], stats: dict) -> dict:
    for task in tasks:
        if task.get("category") not in _REMINDER_FIX_CATEGORIES:
            continue
        if "导入" not in str(task.get("notes") or ""):
            continue
        reminder = task.get("reminder")
        if not isinstance(reminder, int) or isinstance(reminder, bool):
            continue
        if 0 <= reminder < COURSE_REMINDER_MINUTES:
            task["reminder"] = COURSE_REMINDER_MINUTES
            stats["reminders"] += 1
    return stats


