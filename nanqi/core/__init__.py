r"""core —— 领域逻辑（计划校验、重复展开、提醒判定、整份数据校验）。

【为什么拆成包】
原来是一个 `core.py`（近 400 行）装了四件互不相干的事。拆分后按「改动的理由」
分组，任何一次需求只会落在其中一个文件里：

    clock.py       日期/时间与「本地毫秒 vs UTC 毫秒」的换算
    numbers.py     JS 数值与字符串语义（Number.isFinite、UTF-16 长度……）
    tasks.py       单项计划的校验与规范化（cleanTask）
    recurrence.py  重复计划展开（makeSeries）+ 10000 项上限
    reminder.py    到点提醒判定（dueTasks）
    state.py       整份备份的校验（validateState）与初始状态

【对外契约不变】
本文件把原来 `core.py` 导出的名字**原样重新导出**，所以
`from .core import clean_task, make_series` / `from nanqi.core import due_tasks`
这类写法一个字都不用改——包内怎么拆是实现细节，调用方不需要知道。
单元测试也照原样跑，正是「拆分不改变行为」的证据。
"""

from __future__ import annotations

from .categories import (
    DEFAULT_CATEGORIES,
    clean_category,
    next_category_color,
    normalize_categories,
)
from .clock import DATE_RE, TIME_RE, at, date_key, local_ms, parse_calendar_date, utc_ms
from .numbers import (
    is_finite_number,
    is_integer_number,
    is_safe_integer,
    js_len,
    now_ms,
    truncate_js,
)
from .recurrence import MAX_SERIES, make_series
from .reminder import due_tasks
from .state import MAX_HISTORY, MAX_TASKS, initial_state, validate_state
from .tasks import ALLOWED_REMINDERS, CATEGORIES, clean_task

__all__ = [
    # 上游 core.cjs 的公开表面
    "CATEGORIES",
    "date_key",
    "at",
    "clean_task",
    "due_tasks",
    "make_series",
    "validate_state",
    "initial_state",
    "js_len",
    # 其它模块会用到的辅助件
    "local_ms",
    "now_ms",
    "utc_ms",
    "parse_calendar_date",
    "truncate_js",
    "is_finite_number",
    "is_integer_number",
    "is_safe_integer",
    "ALLOWED_REMINDERS",
    "MAX_SERIES",
    "MAX_TASKS",
    "MAX_HISTORY",
    "DATE_RE",
    "TIME_RE",
    # 自定义分类
    "DEFAULT_CATEGORIES",
    "normalize_categories",
    "clean_category",
    "next_category_color",
]
