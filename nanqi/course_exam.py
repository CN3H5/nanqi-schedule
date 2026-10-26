r"""course_exam.py —— 教务系统「考试安排」页面的解析。

【它要产出什么】
一条考试 = 一个**确定日期与时刻**的任务（不像课表按周次展开）：

    {'name': '力学A', 'date': '2026-11-15', 'time': '14:00', 'end_time': '16:00',
     'location': '5107', 'seat': '32', 'form': '闭卷', 'source': '考试'}

【为什么按表头取列】
考试页不同学期/不同入口的列顺序会变（有的还带「座位号」「考试形式」），
按「第 3 列是地点」这种位置假设写死，改版就全错。所以先读表头定位列，
认不出表头时才退回「按内容猜」的兜底（日期单元格 → 时间；含「楼/室/教/场」→ 地点）。

【时间文本的几种真实写法】
    2026-11-15 14:00-16:00
    2026/11/15 14:00~16:00
    2026年11月15日 下午2:00-4:00
    2026-11-15（只有日期，没有时刻）
前三种都能取到日期与起止时刻；**只有日期**时保留日期、时刻留空，由调用方决定怎么处理
（导入时会把它当成「当天没给时刻的考试」，默认按 09:00 排，并在结果里说明）。
"""

from __future__ import annotations

import html as _html
import re

__all__ = ["looks_like_exam", "parse_exam_html"]

_TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.S | re.I)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)

_DATE_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")
_CLOCK_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")
_MERIDIEM_RE = re.compile(r"(上午|下午|中午|晚上|凌晨)")

# 列名关键词（按优先级）。表头里出现任一关键词即认定该列。
_COLUMN_KEYWORDS = {
    "name": ("考试科目", "课程名称", "课程名", "科目", "课程", "名称"),
    "time": ("考试时间", "时间", "日期"),
    "location": ("考试地点", "考场", "地点", "教室"),
    "seat": ("座位号", "座位", "座号"),
    "form": ("考核方式", "考试形式", "形式", "方式"),
}

#: 判定「这是考试页」的结构证据：表头里出现这些词，且不是课表（有星期/节次）。
_EXAM_HEADER_SIGNS = ("考试时间", "考试地点", "考场", "座位号", "考核方式", "考试形式", "考试安排")
#: URL 里的弱证据：必须是一个**路径段**，不能是别的单词的一部分。
#: （踩过的坑：用 `"exam" in url` 时，`https://example.com/` 里的 `example` 会命中，
#:   于是任何示例站点都被判成考试页。）
_EXAM_URL_RE = re.compile(r"/exams?(?:[/?#]|$)|考试", re.I)
#: 负向证据：课表页有这些，考试页没有。
_SCHEDULE_SIGNS = ("星期一", "星期二", "节次", "课表")


def _text(fragment: str) -> str:
    """去标签 + 还原实体 + 压缩空白（含 &nbsp;）。"""
    text = re.sub(r"(?s)<[^>]+>", " ", fragment or "")
    text = _html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _header_texts(table_html: str) -> list[str]:
    """表格第一行的表头文字（优先 `<th>`，没有就拿第一行的 `<td>`）。"""
    rows = _ROW_RE.findall(table_html)
    if not rows:
        return []
    first = rows[0]
    cells = _TH_RE.findall(first)
    if not cells:
        cells = _TD_RE.findall(first)
    return [_text(cell) for cell in cells]


def _columns_of(header: list[str]) -> dict[str, int]:
    """按关键词把表头映射成字段 → 列索引。"""
    columns: dict[str, int] = {}
    for index, name in enumerate(header):
        for field, keywords in _COLUMN_KEYWORDS.items():
            if field in columns:
                continue
            if any(keyword in name for keyword in keywords):
                # 「考试时间」同时含「时间」和「考试」：先匹配更长的关键词，避免抢列
                columns[field] = index
                break
    return columns


def _clock_of(text: str, meridiem: str = "") -> str:
    match = _CLOCK_RE.search(text or "")
    if not match:
        return ""
    hour, minute = int(match.group(1)), int(match.group(2))
    if meridiem in ("下午", "晚上") and hour < 12:
        hour += 12
    elif meridiem == "中午" and hour < 11:
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


def _split_when(text: str) -> tuple[str, str, str]:
    """把一段「时间」文本拆成 (日期 YYYY-MM-DD, 开始 HH:MM, 结束 HH:MM)。"""
    raw = text or ""
    date_text = ""
    match = _DATE_RE.search(raw)
    if match:
        year, month, day = (int(part) for part in match.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            date_text = f"{year:04d}-{month:02d}-{day:02d}"

    # 起止时刻：先看有没有 14:00-16:00 这种区间，再退回「取第一个时刻」。
    clocks = _CLOCK_RE.findall(raw)
    meridiem = ""
    found = _MERIDIEM_RE.search(raw)
    if found:
        meridiem = found.group(1)
    start = _clock_of(raw, meridiem)
    end = ""
    if len(clocks) >= 2:
        # 结束时刻沿用同一个「上午/下午」段（「下午2:00-4:00」里 4:00 也是下午）
        end = _clock_of(f"{clocks[1][0]}:{clocks[1][1]}", meridiem)
        if end and start and end <= start:
            end = ""
    return date_text, start, end


def looks_like_exam(page_html: str, url: str = "") -> bool:
    """这份 HTML 看起来是不是「考试安排」页面。

    判据是**结构证据**（表头里有「考试时间/考场/座位号」），而不是「页面里出现『考试』
    两个字」——教务的导航栏到处都是「考试」，拿它当证据会把任何页面都判成考试页。
    """
    text = page_html or ""
    if any(sign in text for sign in _SCHEDULE_SIGNS) and not any(
        sign in text for sign in _EXAM_HEADER_SIGNS
    ):
        return False
    header_hits = sum(1 for sign in _EXAM_HEADER_SIGNS if sign in text)
    return header_hits >= 2 or bool(_EXAM_URL_RE.search(url or ""))


def _guess_fields(cells: list[str]) -> dict[str, str]:
    """没有表头时的兜底：按内容猜（日期 → 时间，楼/室/场 → 地点）。

    纯数字的格子是**有歧义**的：科大教室号是四位（`5107`、`2210`），座位号通常一到三位。
    所以四位数字当教室、一到三位当座位；认错也只是备注里的一行，不影响日程。
    """
    when = ""
    location = ""
    seat = ""
    name = ""
    for cell in cells:
        if not when and _DATE_RE.search(cell):
            when = cell
            continue
        if not location and re.search(r"(楼|室|教|场|馆|中心)", cell) and len(cell) <= 30:
            location = cell
            continue
        if not location and re.fullmatch(r"\d{4}", cell or ""):
            location = cell
            continue
        if not seat and re.fullmatch(r"\d{1,3}", cell or ""):
            seat = cell
            continue
        if not name and len(cell) >= 2 and not re.fullmatch(r"[\d\s.\-/]+", cell):
            name = cell
    return {"name": name, "time": when, "location": location, "seat": seat}


def parse_exam_html(page_html: str) -> list[dict]:
    """解析考试安排页，返回考试列表；认不出时返回空列表（不抛错）。"""
    if not isinstance(page_html, str) or "<table" not in page_html.lower():
        return []
    entries: list[dict] = []
    seen: set[tuple] = set()
    for table_html in _TABLE_RE.findall(page_html):
        header = _header_texts(table_html)
        columns = _columns_of(header)
        rows = _ROW_RE.findall(table_html)
        body_rows = rows[1:] if header else rows
        for row_html in body_rows:
            cells = [_text(cell) for cell in _TD_RE.findall(row_html)]
            if not cells:
                continue
            if columns:
                def pick(field: str) -> str:
                    index = columns.get(field, -1)
                    return cells[index] if 0 <= index < len(cells) else ""

                name = pick("name")
                when_text = pick("time")
                location = pick("location")
                seat = pick("seat")
                form = pick("form")
            else:
                guessed = _guess_fields(cells)
                name = guessed["name"]
                when_text = guessed["time"]
                location = guessed["location"]
                seat = guessed["seat"]
                form = ""
            date_text, start, end = _split_when(when_text)
            if not name or not date_text:
                continue  # 没有科目或没有日期都无法排进日历
            key = (name, date_text, start)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "name": name,
                    "date": date_text,
                    "time": start,
                    "end_time": end,
                    "location": location,
                    "seat": seat,
                    "form": form,
                    "source": "考试",
                }
            )
    return entries
