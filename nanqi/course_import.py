r"""course_import.py —— 从教务系统课表页面的静态 HTML 里提取课程表。

【输入从哪来】
「一键添加课表」流程里，内置浏览器把当前页面（用户已登录并打开课表页）
的 `toHtml()` 结果交到这里。解析全程离线进行，不访问网络。

【支持两种版面】
  1. **网格式课表**：表头含「星期一…星期日」，每行开头是节次（如「1-2」或
     「第3-4节」），课程格常见 rowspan 跨行（一节大课占两行）。格子内部用
     <br> 分行：课程名 / 教师 / 周次 / 地点（顺序不定，靠特征识别）。
  2. **列表式课表**：一行一门课，表头含「课程名称、星期、节次、地点、
     教师、周次」中的若干列。

两种版面都不命中的页面返回空列表，由调用方提示「没认出课表」。

【输出】
每门课一个 dict：
    name / weekday(1=周一…7=周日) / start_section / end_section /
    start_time / end_time（按节次时刻表换算）/
    first_week / last_week（周次范围，未知为 None）/
    location / teacher / weeks_text（周次原文，供备注使用）
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

__all__ = ["SECTION_TIMES", "parse_course_html", "describe_page"]

# 中科大节次时刻表（每节 45 分钟；只用于把「第 N–M 节」换算成开始/结束时间）。
SECTION_TIMES = {
    1: ("07:50", "08:35"),
    2: ("08:40", "09:25"),
    3: ("09:45", "10:30"),
    4: ("10:35", "11:20"),
    5: ("11:25", "12:10"),
    6: ("14:00", "14:45"),
    7: ("14:50", "15:35"),
    8: ("15:55", "16:40"),
    9: ("16:45", "17:30"),
    10: ("17:35", "18:20"),
    11: ("19:00", "19:45"),
    12: ("19:50", "20:35"),
    13: ("20:40", "21:25"),
}

_WEEKDAYS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7,
}
_WEEKDAY_HEADER_RE = re.compile(r"^(?:星期|周)?([一二三四五六日天])$")
_WEEKS_RE = re.compile(r"(\d{1,2})\s*(?:[-–—~～]\s*(\d{1,2}))?\s*周")
_CLOCK_RE = re.compile(r"([01]?\d|2[0-3])[:：]([0-5]\d)")


class _TableExtractor(HTMLParser):
    """把 HTML 里的所有 <table> 抽成「行 × 格」结构（保留 colspan/rowspan，<br> 换行）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[dict]]] = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            # 嵌套表格直接放弃内层：课表主体永远是最外层那张表
            if self._table is None:
                self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            attr = dict(attrs)
            self._cell = {
                "lines": [""],
                "colspan": _positive_int(attr.get("colspan"), 1),
                "rowspan": _positive_int(attr.get("rowspan"), 1),
            }
        elif tag == "br" and self._cell is not None:
            self._cell["lines"].append("")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["lines"][-1] += data


def _positive_int(value, default: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _cell_text(cell: dict) -> str:
    return "\n".join(line.strip() for line in cell["lines"]).strip()


def _expand_grid(table: list[list[dict]]) -> list[list[dict | None]]:
    """把带 colspan/rowspan 的表展开成对齐的矩阵；每个位置指向占住它的那个单元格。"""
    width = 0
    for row in table:
        width = max(width, sum(cell["colspan"] for cell in row))
    matrix: list[list[dict | None]] = [[None] * width for _ in table]
    for r, row in enumerate(table):
        c = 0
        for cell in row:
            while c < width and matrix[r][c] is not None:
                c += 1
            for dr in range(cell["rowspan"]):
                for dc in range(cell["colspan"]):
                    if r + dr < len(matrix) and c + dc < width:
                        matrix[r + dr][c + dc] = cell
            c += cell["colspan"]
    return matrix


def _weekday_of_header(text: str) -> int | None:
    match = _WEEKDAY_HEADER_RE.fullmatch(text.strip())
    return _WEEKDAYS[match.group(1)] if match else None


def _sections_of_label(text: str) -> tuple[int, int] | None:
    """从行首标签里认出节次范围（「1-2」「第3-4节」「3」「07:50-09:35」都行）。

    数字必须出现在开头（锚定），否则课程名里的编号（如「数学分析(B1)」）
    会被误认成节次。时钟格式的标签按节次时刻表反查节次。
    """
    stripped = text.strip()
    if ":" in stripped[:12] or "：" in stripped[:12]:
        clocks = [f"{int(h):02d}:{m}" for h, m in _CLOCK_RE.findall(stripped)]
        by_start = {v[0]: k for k, v in SECTION_TIMES.items()}
        by_end = {v[1]: k for k, v in SECTION_TIMES.items()}
        first = by_start.get(clocks[0]) if clocks else None
        last = None
        if len(clocks) > 1:
            last = by_end.get(clocks[1]) or by_start.get(clocks[1])
        if first is not None:
            return first, (last if last is not None and last >= first else first)
        return None
    match = re.match(r"第?\s*(\d{1,2})\s*(?:[-–—~～]\s*(\d{1,2}))?\s*节?$", stripped)
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    if not (1 <= start <= 13 and 1 <= end <= 13 and start <= end):
        return None
    return start, end


def _split_course_cell(text: str) -> dict:
    """把课程格的多行文本拆成 课程名/教师/周次/地点（顺序不定，按特征认）。"""
    lines = [line for line in (l.strip() for l in text.split("\n")) if line]
    name = lines[0] if lines else ""
    teacher = ""
    location = ""
    weeks_text = ""
    for line in lines[1:]:
        if not weeks_text and _WEEKS_RE.search(line):
            weeks_text = line
        elif not location and re.search(r"室|楼|馆|堂|中心|校区|^[A-Za-z]*\d{3,}", line):
            location = line
        elif not teacher:
            teacher = line
    return {"name": name, "teacher": teacher, "location": location, "weeks_text": weeks_text}


def _weeks_range(weeks_text: str) -> tuple[int | None, int | None]:
    """「1-16周」「1-8,10-16周」「3周」→ (首周, 末周)；多段时取整体首尾。"""
    if not weeks_text or "周" not in weeks_text:
        return None, None
    found = re.findall(r"(\d{1,2})\s*(?:[-–—~～]\s*(\d{1,2}))?", weeks_text)
    if not found:
        return None, None
    firsts = [int(a) for a, _b in found]
    lasts = [int(b) if b else int(a) for a, b in found]
    first, last = min(firsts), max(lasts)
    if not (1 <= first <= last <= 60):
        return None, None
    return first, last


def _make_entry(name, weekday, sections, weeks_text, location, teacher) -> dict | None:
    name = re.sub(r"\s+", " ", unescape(str(name))).strip()
    if not name or weekday is None or sections is None:
        return None
    start_section, end_section = sections
    start_time = SECTION_TIMES.get(start_section, ("08:00", ""))[0]
    end_time = SECTION_TIMES.get(end_section, ("", "09:40"))[1]
    first_week, last_week = _weeks_range(weeks_text or "")
    return {
        "name": name,
        "weekday": weekday,
        "start_section": start_section,
        "end_section": end_section,
        "start_time": start_time,
        "end_time": end_time,
        "first_week": first_week,
        "last_week": last_week,
        "location": location or "",
        "teacher": teacher or "",
        "weeks_text": weeks_text or "",
    }


def _parse_grid_table(table: list[list[dict]]) -> list[dict]:
    """网格式课表：表头认星期列，行首认节次，格子认课程。"""
    matrix = _expand_grid(table)
    if not matrix:
        return []
    header = matrix[0]
    weekday_columns: dict[int, int] = {}  # 列号 → 星期几
    for col, cell in enumerate(header):
        if cell is None:
            continue
        for line in cell["lines"]:
            weekday = _weekday_of_header(line)
            if weekday is not None:
                weekday_columns[col] = weekday
                break
    if len(weekday_columns) < 4:
        return []

    entries: list[dict] = []
    seen_cells: set[int] = set()
    first_weekday_col = min(weekday_columns)
    for r, row in enumerate(matrix[1:], start=1):
        # 节次标签只看星期列左侧的格子（右侧混着的可能是跨行下来的课程格）
        sections = None
        for c in range(min(first_weekday_col, len(row))):
            cell = row[c]
            if cell is None:
                continue
            sections = _sections_of_label(_cell_text(cell))
            if sections:
                break
        if sections is None:
            continue
        for col, weekday in weekday_columns.items():
            cell = row[col] if col < len(row) else None
            if cell is None or id(cell) in seen_cells:
                continue
            seen_cells.add(id(cell))
            text = _cell_text(cell)
            if len(text) < 2:
                continue
            parts = _split_course_cell(text)
            # rowspan 跨行时，节次范围 = 当前行节次 … 最末一行的节次
            span_sections = sections
            if cell["rowspan"] > 1:
                tail_index = min(r + cell["rowspan"] - 1, len(matrix) - 1)
                tail_sections = None
                for c in range(min(first_weekday_col, len(matrix[tail_index]))):
                    tail_cell = matrix[tail_index][c]
                    if tail_cell is not None:
                        tail_sections = _sections_of_label(_cell_text(tail_cell))
                        if tail_sections:
                            break
                if tail_sections:
                    span_sections = (sections[0], tail_sections[1])
            entry = _make_entry(
                parts["name"], weekday, span_sections,
                parts["weeks_text"], parts["location"], parts["teacher"],
            )
            if entry:
                entries.append(entry)
    return entries


def _parse_list_table(table: list[list[dict]]) -> list[dict]:
    """列表式课表：表头认「课程/星期/节次/地点/教师/周次」列，每行一门课。"""
    matrix = _expand_grid(table)
    if not matrix:
        return []
    header_texts = [_cell_text(cell) if cell else "" for cell in matrix[0]]

    def find_column(*keywords: str) -> int | None:
        for col, text in enumerate(header_texts):
            if any(word in text for word in keywords):
                return col
        return None

    name_col = find_column("课程名称", "课程名", "课程")
    weekday_col = find_column("星期", "周几", "上课日")
    section_col = find_column("节次", "时间")
    if name_col is None or weekday_col is None or section_col is None:
        return []
    location_col = find_column("地点", "教室", "场所")
    teacher_col = find_column("教师", "老师", "任课")
    weeks_col = find_column("周次", "周数")

    entries: list[dict] = []
    for row in matrix[1:]:
        def text_of(col):
            if col is None or col >= len(row) or row[col] is None:
                return ""
            return _cell_text(row[col])

        weekday_match = re.search(r"[一二三四五六日天]", text_of(weekday_col))
        weekday = _WEEKDAYS.get(weekday_match.group(0)) if weekday_match else None
        sections = _sections_of_label(text_of(section_col))
        entry = _make_entry(
            text_of(name_col), weekday, sections,
            text_of(weeks_col), text_of(location_col), text_of(teacher_col),
        )
        if entry:
            entries.append(entry)
    return entries


def parse_course_html(html: str) -> list[dict]:
    """从课表页 HTML 提取课程列表；认不出课表时返回空列表（不抛错）。"""
    # 教务系统的课表是结构化标签（见 course_jw.py），优先用专用解析器；
    # 认不出时才退回下面那套「猜表格」的通用逻辑。
    try:
        from .course_jw import parse_jw_schedule

        structured = parse_jw_schedule(html)
        if structured:
            return structured
    except Exception:  # noqa: BLE001 - 专用解析失败不应让导入中断
        pass
    if not isinstance(html, str) or "<table" not in html.lower():
        return []
    extractor = _TableExtractor()
    try:
        extractor.feed(html)
    except Exception:  # noqa: BLE001 - 畸形 HTML 不该让导入流程崩掉
        pass
    entries: list[dict] = []
    for table in extractor.tables:
        entries.extend(_parse_grid_table(table))
    if not entries:
        for table in extractor.tables:
            entries.extend(_parse_list_table(table))
    # 同一门课在同一星期同一节次可能出现多次（多格同课），按 (名称, 星期, 起止节) 去重
    seen: set[tuple] = set()
    unique: list[dict] = []
    for entry in entries:
        key = (entry["name"], entry["weekday"], entry["start_section"], entry["end_section"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


# --------------------------------------------------------------------------- 页面诊断

#: 用户点了「一键添加」但当前页不是可导入页面时，我们要能说清「他到底在哪个页面」。
PAGE_SCHEDULE = "schedule"
PAGE_ERCOURSE = "ercourse"
PAGE_EXAM = "exam"
PAGE_LOGIN = "login"
PAGE_EMPTY = "empty"
PAGE_UNKNOWN = "unknown"

#: 能直接导入的页面类型（浏览器里那个「一键添加」按钮据此自动分派）。
IMPORTABLE = (PAGE_SCHEDULE, PAGE_ERCOURSE, PAGE_EXAM)

# 【曾经的坑】二课站点的特征词里放过 `todos-table`，而**课表页面里也出现这个词**
# （教务系统自己的 CSS 类名），于是课表被误判成二课页面，用户看到「这是二课页面」
# 的错误提示。结论：**页面类型一律用解析器本身来判定**，关键词只用于「URL 猜站点」。
_ERCOURSE_URL_SIGNS = ("young.ustc.edu.cn", "sc-wisdom-group-learning", "myschedule")
_LOGIN_SIGNS = ("统一身份认证", "name=\"password\"", "id=\"password\"", "login.ustc.edu.cn", "oauth")
# 「表格里有星期表头」——通用的课表结构证据（专用解析器认不出时的兜底）。
_TABLE_WEEKDAY_RE = re.compile(r">\s*(?:星期|周)?[一二三四五六日天]\s*<")


def describe_page(html: str, url: str = "", title: str = "", prefer: str = "") -> dict:
    """判断这份 HTML 是哪种页面，并给出一句**能照着做**的提示。

    【判定顺序：先证据，后猜测】
      1. `parse_jw_schedule` 真的取出了课程 → 课表（最强的结构证据）；
      2. 考试页的结构证据（表头里有「考试时间/考场/座位号」）→ 考试；
      3. `parse_ercourse` 真的取出了二课 → 二课；
      4. 以上都不成立但 URL 指向团学平台 → 二课（空表也算，提示「没找到报名记录」）；
      5. 表格里有星期表头 → 课表（交给通用解析器再试一次）；
      6. 其余 → 说清看到了什么，以及下一步做什么。

    【返回值里的 import】
    `kind` 是「这是什么页面」，`import` 是「该按哪种格式导入」（不可导入时为空串）。
    浏览器右下角的「一键添加」按钮就是拿 `import` 自动分派到课表 / 二课 / 考试，
    这样用户不需要先选类型再点——选错页面也不会白点一次。
    """
    from .course_erc import parse_ercourse
    from .course_exam import looks_like_exam, parse_exam_html

    text = html or ""
    lowered = text.lower()
    hint_url = (url or "").lower()
    tables = lowered.count("<table")
    frames = lowered.count("<iframe") + lowered.count("<frame ")
    try:
        from .course_jw import parse_jw_schedule

        schedule_courses = parse_jw_schedule(text)
    except Exception:  # noqa: BLE001 - 诊断本身绝不能抛错
        schedule_courses = []
    try:
        ercourse_items = parse_ercourse(text)
    except Exception:  # noqa: BLE001
        ercourse_items = []
    try:
        # 【为什么考试也要「解析器说了算」】考试页里同时挂着「我的课表」框架，
    # 抓下来能解析出 18 个上课时段；只看「有没有课表」就会把考试页当成课表页导入，
    # 结果一条考试都没进去（用户实测就是这样）。所以考试用**自己的解析器**判定，
    # 并且排在课表之前。
        exam_items = parse_exam_html(text)
    except Exception:  # noqa: BLE001
        exam_items = []
    exam_like = bool(exam_items) or looks_like_exam(text, url)
    login = any(sign.lower() in lowered or sign.lower() in hint_url for sign in _LOGIN_SIGNS)
    on_ercourse_site = any(sign in hint_url for sign in _ERCOURSE_URL_SIGNS)
    weekday_table = bool(_TABLE_WEEKDAY_RE.search(text)) or "节次" in text

    if not text.strip():
        return {
            "kind": PAGE_EMPTY, "import": "", "tables": 0, "frames": 0,
            "hint": "页面还是空的：请等页面加载完（或先登录）再点一次。",
        }
    if login and tables == 0:
        return {
            "kind": PAGE_LOGIN, "import": "", "tables": 0, "frames": frames,
            "hint": "这看起来是登录页。请先在内置浏览器里登录教务系统，进到目标页面再点一次。",
        }

    # ---- 先看用户这次想加什么（他点的是「考试」，就优先按考试解析）----
    wanted = str(prefer or "").strip().lower()
    if wanted == PAGE_EXAM and exam_items:
        return {
            "kind": PAGE_EXAM, "import": PAGE_EXAM, "tables": tables, "frames": frames,
            "hint": f"这是**考试安排**页面（识别到 {len(exam_items)} 场考试），点「一键添加」即可导入。",
        }
    if wanted == PAGE_SCHEDULE and schedule_courses:
        return {
            "kind": PAGE_SCHEDULE, "import": PAGE_SCHEDULE, "tables": tables, "frames": frames,
            "hint": f"这是**课表**页面（识别到 {len(schedule_courses)} 个上课时段），点「一键添加」即可导入。",
        }
    if wanted == PAGE_ERCOURSE and (ercourse_items or on_ercourse_site):
        return {
            "kind": PAGE_ERCOURSE, "import": PAGE_ERCOURSE, "tables": tables, "frames": frames,
            "hint": f"这是**二课**页面（当前日期有 {len(ercourse_items)} 条记录），点「一键添加」即可导入。",
        }

    # ---- 再看页面自己的结构证据：考试 → 课表 → 二课 ----
    if exam_like and exam_items:
        return {
            "kind": PAGE_EXAM, "import": PAGE_EXAM, "tables": tables, "frames": frames,
            "hint": f"这是**考试安排**页面（识别到 {len(exam_items)} 场考试），点「一键添加」即可导入。",
        }
    if schedule_courses:
        return {
            "kind": PAGE_SCHEDULE, "import": PAGE_SCHEDULE, "tables": tables, "frames": frames,
            "hint": f"这是**课表**页面（识别到 {len(schedule_courses)} 个上课时段），点「一键添加」即可导入。",
        }
    if exam_like:
        return {
            "kind": PAGE_EXAM, "import": PAGE_EXAM, "tables": tables, "frames": frames,
            "hint": "这是**考试安排**页面，点「一键添加」即可导入考试日程。",
        }
    if ercourse_items:
        return {
            "kind": PAGE_ERCOURSE, "import": PAGE_ERCOURSE, "tables": tables, "frames": frames,
            "hint": f"这是**二课**页面（当前日期识别到 {len(ercourse_items)} 条），点「一键添加」即可导入全部。",
        }
    if on_ercourse_site:
        return {
            "kind": PAGE_ERCOURSE, "import": PAGE_ERCOURSE, "tables": tables, "frames": frames,
            "hint": (
                "这是团学平台的页面，但没找到「我报名的二课」表格。\n"
                "请切到「我的日程 / mySchedule」页面，确认列表里已有活动后再点一次。"
            ),
        }
    if weekday_table:
        return {
            "kind": PAGE_SCHEDULE, "import": PAGE_SCHEDULE, "tables": tables, "frames": frames,
            "hint": (
                "这像是课表页面（有星期表头），但没能取出课程。\n"
                "请确认周一到周日的格子都已显示出来再点一次；快照会保存下来便于排查。"
            ),
        }
    if tables == 0:
        return {
            "kind": PAGE_UNKNOWN, "import": "", "tables": 0, "frames": frames,
            "hint": (
                "当前页面里没有任何表格，目标内容可能还在加载，或者它没有用表格排版。\n"
                "请确认页面上已经显示出内容后再点一次。"
            ),
        }
    detail = f"页面里有 {tables} 个表格、{frames} 个内嵌框架"
    if frames:
        detail += "（内容可能在框架里；已一并抓取，若仍失败请把快照发给我）"
    return {
        "kind": PAGE_UNKNOWN, "import": "", "tables": tables, "frames": frames,
        "hint": f"看到了表格但认不出结构（既不是课表也不是考试或二课）。{detail}。",
    }
