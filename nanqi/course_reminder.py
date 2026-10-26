r"""course_reminder.py —— 上课 / 考试提醒的文案与节奏（纯函数，便于单测）。

适用范围：**第一课堂**，即 `course`（课表）与 `exam`（考试）两个分类。
二课、复习和其它待办属于「更普通的待办」，走 `core/reminder.py` 的通用提醒。

【为什么单独成模块】
用户对这一块的要求非常具体，而且是「产品文案」而不是窗口代码：

  1. **主标题**是这门课（或这一科）本身：`上课提醒 · 线性代数(B1)`、
     `考试提醒 · 力学A`；
  2. **副标题只有两件事**：几点开始、在哪 —— `14:00 开始 · 5204`；
     教师、周次、节次这类信息**不进通知**，只留在计划详情里
     （用户原话：「其他的无关信息全部放到不用展示那么详细…只有点进去看详情的时候才展示」）；
  3. **早八（08:00 前后）**：先提前 **60** 分钟提醒一次，再提前 **30** 分钟提醒一次，
     两条文案不同（第一条多一句「早八，还有一小时，记得吃早饭再出发」）；
  4. **下午第一节课**：只在**上午最后一节下课之后**提醒一次「中午早点睡，下午还有课」，
     到点前再按常规提醒；
  5. **天气不好**（见 `weather.py`）：把常规的 30 分钟提前到 **45** 分钟，
     并在副标题下面加一句「今天天气不好，要早点出发」。

这些都是「给定任务与当前时刻，应该弹哪几条通知」的判断，与 Qt 无关，所以放在这里用
纯函数实现——`controller.tick()` 只负责取结果、发通知。**不要**把这些规则写进定时器里，
否则每加一条文案都要动主流程。

【为什么课程要脱离 `due_tasks` 单独调度】
通用的到点提醒是「一个任务一条通知，写掉 `notifiedAt` 就不再提醒」，而早八要**两条**、
下午课要**一条前置的午睡提醒**。如果硬塞进 `due_tasks`，第一条通知后 `notifiedAt` 就被
写掉，第二条永远发不出来。所以这里用「阶段（stage）」建模，由调用方用
`(日期, 任务ID, 阶段)` 去重：同一阶段一天只弹一次。

【阶段与触发时刻】

    早八课（上课时刻 <= 08:15）
        stage=early   S-60 分钟      「08:00 开始 · 5107 / 早八，还有一小时…」
        stage=soon    S-lead         「07:50 开始 · 2210」
    下午第一节（上课时刻 >= 13:00，且当天上午有课）
        stage=nap     上午最后一节的下课时刻  「中午早点睡，下午还有课」
        stage=soon    S-lead
    其余课 / 考试
        stage=soon    S-lead

应用启动得晚（比如 07:50 才开）时**只补发最有用的那一条**：此时 `soon` 已经到点，
就只发 `soon`，不再补一条意义不大的 `early`。
"""

from __future__ import annotations

from datetime import datetime

__all__ = [
    "COURSE_CATEGORIES",
    "EARLY_CLASS_UNTIL",
    "DEFAULT_LEAD_MINUTES",
    "BAD_WEATHER_LEAD_MINUTES",
    "EARLY_LEAD_MINUTES",
    "AFTERNOON_START_MINUTES",
    "BAD_WEATHER_NOTE",
    "is_course",
    "course_label",
    "classroom_of",
    "reminder_lead",
    "notifications",
]

# 走「上课/考试提醒」文案的分类：**第一课堂**（课表 + 考试）。
# 二课（second classroom）属于「待办」，走通用提醒——用户明确要求：
# 「课表和考试属于一类；复习、二课、其他添加的待办属于一类，也就是更普通的待办」。
COURSE_CATEGORIES = ("course", "exam")

# 上课时刻不晚于这个值就算「早八」（07:50 这种 7 字头的课同样按早八处理）。
EARLY_CLASS_UNTIL = "08:15"
# 下午课的下界：13:00 之后开始的课才谈得上「中午早点睡」。
AFTERNOON_START_MINUTES = 13 * 60

DEFAULT_LEAD_MINUTES = 30
BAD_WEATHER_LEAD_MINUTES = 45
EARLY_LEAD_MINUTES = 60

BAD_WEATHER_NOTE = "今天天气不好，要早点出发"

_STAGE_EARLY = "early"
_STAGE_NAP = "nap"
_STAGE_SOON = "soon"


# --------------------------------------------------------------------- 字段读取


def is_course(task) -> bool:
    """这个任务是不是「课程类」任务（课表导入的课 / 二课）。"""
    return isinstance(task, dict) and task.get("category") in COURSE_CATEGORIES


def course_label(task) -> str:
    """通知主标题的前半段：考试与上课分开叫。

    用户的原话是「主标题就是这个课程」——考试也一样，写清考的是哪一科最要紧。
    """
    return "考试提醒" if task.get("category") == "exam" else "上课提醒"


def classroom_of(task) -> str:
    """从备注里取教室。

    备注是课表导入时写的多行文本，地点一定以 `地点：` 开头（见 controller 里的
    `notes_lines`）。这里**原样返回**，不猜「5107 是五教 1 楼 107 室」——
    教室编号就是编号，臆测只会制造错误信息。
    """
    for line in str(task.get("notes") or "").splitlines():
        text = line.strip()
        if text.startswith("地点："):
            return text[3:].strip()
        if text.startswith("地点:"):
            return text[3:].strip()
    return ""


def _clock_minutes(value) -> int | None:
    """`'14:05'` → 845；认不出返回 None。"""
    text = str(value or "")
    if len(text) < 5:
        return None
    try:
        hour, minute = int(text[:2]), int(text[3:5])
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def reminder_lead(task, *, bad_weather: bool = False) -> int:
    """这节课提前多少分钟提醒（分钟数；`-1` 表示用户关掉了提醒）。

    天气不好时把**默认的 30** 提到 45；如果用户自己把提醒改成了更早的
    （比如 60 分钟），那就尊重用户的设置，不再往回缩。
    """
    raw = task.get("reminder")
    base = DEFAULT_LEAD_MINUTES
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        base = raw
    if bad_weather and base <= DEFAULT_LEAD_MINUTES:
        return BAD_WEATHER_LEAD_MINUTES
    return base


def _reminder_off(task) -> bool:
    raw = task.get("reminder")
    return isinstance(raw, int) and not isinstance(raw, bool) and raw < 0


# --------------------------------------------------------------------- 文案


def _subtitle(task) -> str:
    """副标题：`14:00 开始 · 5204`（没教室就只写时间）。"""
    start = str(task.get("time") or "")
    room = classroom_of(task)
    return f"{start} 开始" + (f" · {room}" if room else "")


def _title(task) -> str:
    return f"{course_label(task)} · {task.get('title', '')}".rstrip(" ·")


def _entry(task, stage: str, key: str, body: str, title: str | None = None) -> dict:
    return {
        "task": task,
        "stage": stage,
        "key": key,
        "title": _title(task) if title is None else title,
        "body": body,
    }


# --------------------------------------------------------------------- 主逻辑


def notifications(
    tasks,
    now: datetime | None = None,
    delivered: set | None = None,
    *,
    bad_weather: bool = False,
    weather_note: str = "",
    today: str | None = None,
) -> list[dict]:
    """算出「此刻该弹的课程提醒」。

    参数：
        tasks      全部计划（未过滤）
        now        当前本地时间（默认 `datetime.now()`，测试可注入）
        delivered  已发过的 `(日期, 任务ID, 阶段)` 集合（调用方负责加入）
        bad_weather / weather_note  来自 `weather.py` 的当日判断

    返回按上课时刻排序的列表，每项：`{task, stage, key, title, body}`。
    """
    moment = now or datetime.now()
    today_key = today or moment.strftime("%Y-%m-%d")
    now_minutes = moment.hour * 60 + moment.minute
    seen = delivered if delivered is not None else set()

    courses = []
    for task in tasks or []:
        if not is_course(task) or task.get("done"):
            continue
        if str(task.get("date") or "") != today_key:
            continue
        if _reminder_off(task):
            continue
        if _clock_minutes(task.get("time")) is None:
            continue
        courses.append(task)
    if not courses:
        return []
    courses.sort(key=lambda item: str(item.get("time") or ""))

    # 上午最后一节的下课时刻（用 duration 算），「中午早点睡」以它为准。
    morning_end: int | None = None
    for task in courses:
        start = _clock_minutes(task.get("time"))
        if start is None or start >= AFTERNOON_START_MINUTES:
            continue
        end = start + max(0, int(task.get("duration") or 0))
        if morning_end is None or end > morning_end:
            morning_end = end

    # 下午第一节（最早的那节下午课）就是「中午早点睡」要提醒的那一节：
    # 一天只提醒一次，后面的课到点前直接按常规提醒，不重复喊他睡觉。
    first_afternoon = None
    for task in courses:
        start = _clock_minutes(task.get("time"))
        if start is not None and start >= AFTERNOON_START_MINUTES:
            first_afternoon = str(task.get("id") or task.get("title") or "")
            break

    note = (weather_note or BAD_WEATHER_NOTE) if bad_weather else ""
    output: list[dict] = []

    for task in courses:
        start = _clock_minutes(task.get("time"))
        if start is None:
            continue
        # 已经开始的课不再提醒：通用的「补发迟到提醒」对课程是反效果的
        # （「还有 30 分钟」在上课十分钟后弹出只会让人困惑）。
        if now_minutes >= start:
            continue
        identifier = str(task.get("id") or task.get("title") or "")
        lead = reminder_lead(task, bad_weather=bad_weather)
        key_soon = f"{today_key}:{identifier}:{_STAGE_SOON}"
        soon_due = now_minutes >= start - lead

        # ---- 早八：60 分钟 + 30 分钟两条 -------------------------------
        if str(task.get("time") or "") <= EARLY_CLASS_UNTIL:
            key_early = f"{today_key}:{identifier}:{_STAGE_EARLY}"
            if soon_due:
                if key_soon in seen:
                    continue
                body = _subtitle(task)
                if note:
                    body += "\n" + note
                output.append(_entry(task, _STAGE_SOON, key_soon, body))
            elif now_minutes >= start - EARLY_LEAD_MINUTES and key_early not in seen:
                # 早八的第一条：只多一句「还有一小时」，其余仍是最小信息量。
                body = _subtitle(task) + "\n早八，还有一小时，记得吃早饭再出发"
                output.append(_entry(task, _STAGE_EARLY, key_early, body))
            continue

        # ---- 常规：到点前 lead 分钟 -----------------------------------
        if soon_due:
            if key_soon in seen:
                continue
            body = _subtitle(task)
            if note:
                body += "\n" + note
            output.append(_entry(task, _STAGE_SOON, key_soon, body))
            continue

        # ---- 下午第一节：上午下课后提醒一次「中午早点睡」 ----------------
        if (
            start >= AFTERNOON_START_MINUTES
            and morning_end is not None
            and identifier == first_afternoon
            and now_minutes >= morning_end
        ):
            key_nap = f"{today_key}:{identifier}:{_STAGE_NAP}"
            if key_nap in seen:
                continue
            room = classroom_of(task)
            body = f"{task.get('time')} 的 {task.get('title', '')}" + (f" 在 {room}" if room else "")
            output.append(
                _entry(
                    task,
                    _STAGE_NAP,
                    key_nap,
                    body,
                    title="中午早点睡，下午还有课",
                )
            )

    return output
