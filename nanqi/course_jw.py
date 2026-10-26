r"""course_jw.py —— 教务系统课表的**专用**解析器（按真实 DOM 结构，不靠猜）。

【为什么另起一个模块】
`course_import.py` 里的通用表格解析是「猜结构」：找星期表头、找节次列、再把单元格
文本按空格切开。但教务系统的课表是**结构化标签**，文本被拼成一串没有分隔符的字符：

    14:0015:35PE00001.15 [1] 基础体育 李少松 2-5,7-18 6,7 西区操场（男生）

按空格切必然错位（老师名可能带空格、课程名可能带数字）。所以这里改为**按标签取字段**，
每个字段都有确定的来源：

    <td><div class="cell" data-range="2">
          <span class="start">14:00</span><span class="end">15:35</span>
          <div class="c">
            <h4 class="number">PE00001.15 [1]</h4>
            <h3 class="title">基础体育</h3>
            <p class="teacher" title="李少松"><span class="name">李少松</span></p>
            <p class="time"><span class="week">2-5,7-18</span><span class="timespan">6,7</span></p>
            <p class="classroom"><span class="name" title="西区操场（男生）">西区操场（男生）</span></p>
          </div></div></td>

行 = 节次，`<th class="span" data-start="14:00" data-end="14:45">6</th>` 给出该节的
精确起止时刻；列 = 星期（`<thead>` 里的「星期一…」）。

【周次必须按集合保留】
课程不是「每周都上」：`2-5,7-18` 意味着第 6 周停课。所以解析结果带一个
`weeks` 列表（例如 `[2,3,4,5,7,...,18]`），生成日程时**逐周照抄**，
而不是假定从第 1 周连续到第 18 周。这一条是用户明确要求的。
"""

from __future__ import annotations

import html as _html
import re

__all__ = ["parse_jw_schedule", "expand_weeks", "describe_classroom"]

#: 一节课的起止时刻由行首的 th 提供；这里只做兜底（页面缺 data-start 时用）。
_FALLBACK_SECTIONS = {
    1: ("08:00", "08:45"), 2: ("08:50", "09:35"), 3: ("09:45", "10:30"), 4: ("10:35", "11:20"),
    5: ("11:25", "12:10"), 6: ("14:00", "14:45"), 7: ("14:50", "15:35"), 8: ("15:45", "16:30"),
    9: ("16:35", "17:20"), 10: ("17:25", "18:10"), 11: ("19:00", "19:45"), 12: ("19:50", "20:35"),
    13: ("20:40", "21:25"),
}

_ROW_RE = re.compile(r'<tr class="([^"]*)">(.*?)</tr>', re.S)
_SECTION_TH_RE = re.compile(
    r'<th[^>]*class="[^"]*span[^"]*"[^>]*data-start="([^"]*)"[^>]*data-end="([^"]*)"[^>]*>(\d+)</th>'
)
_CELL_RE = re.compile(r'<div class="cell[^"]*"[^>]*>(.*?)</div>\s*</div>', re.S)
_START_RE = re.compile(r'<span class="start">([^<]*)</span>')
_END_RE = re.compile(r'<span class="end">([^<]*)</span>')
_NUMBER_RE = re.compile(r'<h4 class="number">([^<]*)</h4>')
_TITLE_RE = re.compile(r'<h3 class="title">(.*?)</h3>', re.S)
_TEACHER_RE = re.compile(r'<p class="teacher"[^>]*>(.*?)</p>', re.S)
_WEEK_RE = re.compile(r'<span class="week[^"]*">([^<]*)</span>')
_TIMESPAN_RE = re.compile(r'<span class="timespan">([^<]*)</span>')
_ROOM_RE = re.compile(r'<p class="classroom"[^>]*>(.*?)</p>', re.S)
_NAME_INNER_RE = re.compile(r'<span class="name"[^>]*>(.*?)</span>', re.S)
_TIME_RE = re.compile(r"^\s*([01]?\d|2[0-3])[:：]([0-5]\d)\s*$")


def _clean(fragment: str) -> str:
    """去标签、还原实体、压掉空白（含 &nbsp; 与非断行空格）。"""
    if not fragment:
        return ""
    text = re.sub(r"(?s)<[^>]+>", " ", fragment)
    text = _html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _clock(value: str) -> str | None:
    match = _TIME_RE.match(value or "")
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def expand_weeks(text: str) -> tuple[list[int], str]:
    """把 `2-5,7-18` / `3,6-14` / `1-16单` 展开成周次列表，并给出原始文本。

    支持：逗号分隔的多段、`a-b` 区间、可选的「单/双」后缀（单双周课）。
    返回 (周次列表, 规整后的原文)；认不出时返回 ([], 原文)。
    """
    source = (text or "").strip()
    if not source:
        return [], ""
    weeks: set[int] = set()
    for chunk in re.split(r"[,，;；/\s]+", source):
        piece = chunk.strip()
        if not piece:
            continue
        parity = ""
        match = re.match(r"^(\d+)\s*[-–—~～]\s*(\d+)\s*(单|双)?$", piece)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
            parity = match.group(3) or ""
            if low > high:
                low, high = high, low
            for week in range(low, high + 1):
                if parity == "单" and week % 2 == 0:
                    continue
                if parity == "双" and week % 2 == 1:
                    continue
                weeks.add(week)
            continue
        single = re.match(r"^(\d+)\s*(单|双)?$", piece)
        if single:
            week = int(single.group(1))
            parity = single.group(2) or ""
            if parity == "单" and week % 2 == 0:
                continue
            if parity == "双" and week % 2 == 1:
                continue
            weeks.add(week)
    return sorted(weeks), source


def describe_classroom(room: str) -> str:
    """教室号原样返回。

    曾经试过按「首位是教学楼号」补一句「五教 1 楼 107 室」，但用户指出
    **5107 本身就是教室编号**，不是 107。臆测反而制造了错误信息，所以这里
    只做清理（去 &nbsp;、压空白），不再改写数字的含义。
    """
    return (room or "").strip()


def _weekdays_from_head(html: str) -> dict[int, int]:
    """从表头取「星期X」出现的列序（返回 {星期数字: 列序号}）；取不到就给默认。"""
    order: dict[int, int] = {}
    head = re.search(r"<thead>(.*?)</thead>", html, re.S)
    scope = head.group(1) if head else html
    names = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
    for match in re.finditer(r"<t[hd][^>]*>\s*(?:星期|周)([一二三四五六日天])\s*</t[hd]>", scope):
        name = match.group(1)
        if name in names and names[name] not in order:
            order[names[name]] = len(order) + 1
    return order


def parse_jw_schedule(html: str) -> list[dict]:
    """解析教务系统课表页；结构不匹配时返回空列表（由通用解析器兜底）。"""
    if not isinstance(html, str) or 'class="cell' not in html:
        return []
    weekday_columns = _weekdays_from_head(html)
    entries: list[dict] = []
    seen: set[tuple] = set()

    for row_match in _ROW_RE.finditer(html):
        row_html = row_match.group(2)
        section_match = _SECTION_TH_RE.search(row_html)
        if not section_match:
            continue
        row_start, row_end, row_number = section_match.groups()
        section_index = int(row_number)
        fallback = _FALLBACK_SECTIONS.get(section_index, ("08:00", "08:45"))

        # 这一行里第 k 个 <td> 就是第 k 个星期（td 按列顺序排列）
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
        for column, cell_html in enumerate(cells, start=1):
            if 'class="cell' not in cell_html:
                continue
            weekday = None
            for number, col in weekday_columns.items():
                if col == column:
                    weekday = number
                    break
            if weekday is None:
                # 表头认不出时，退化为「第几个 td 就是星期几」（每周 7 列的常见布局）
                weekday = column if 1 <= column <= 7 else None
            if weekday is None:
                continue

            for cell in _CELL_RE.finditer(cell_html):
                body = cell.group(1)
                title = _clean(_TITLE_RE.search(body).group(1)) if _TITLE_RE.search(body) else ""
                if not title:
                    continue
                start = _clock(_START_RE.search(body).group(1)) if _START_RE.search(body) else None
                end = _clock(_END_RE.search(body).group(1)) if _END_RE.search(body) else None
                number = _clean(_NUMBER_RE.search(body).group(1)) if _NUMBER_RE.search(body) else ""
                teacher_html = _TEACHER_RE.search(body)
                teacher = ""
                if teacher_html:
                    inner = _NAME_INNER_RE.search(teacher_html.group(1))
                    teacher = _clean(inner.group(1) if inner else teacher_html.group(1))
                weeks_text = ""
                weeks: list[int] = []
                week_match = _WEEK_RE.search(body)
                if week_match:
                    weeks, weeks_text = expand_weeks(_clean(week_match.group(1)))
                span_match = _TIMESPAN_RE.search(body)
                timespan = _clean(span_match.group(1)) if span_match else ""
                sections = [int(part) for part in re.findall(r"\d+", timespan)] or [section_index]
                room_html = _ROOM_RE.search(body)
                room = ""
                if room_html:
                    inner = _NAME_INNER_RE.search(room_html.group(1))
                    room = _clean(inner.group(1) if inner else room_html.group(1))

                key = (title, weekday, sections[0], tuple(weeks))
                if key in seen:
                    continue
                seen.add(key)
                entries.append(
                    {
                        "name": title,
                        "weekday": weekday,
                        "start_section": sections[0],
                        "end_section": sections[-1],
                        "start_time": start or _clock(row_start) or fallback[0],
                        "end_time": end or _clock(row_end) or fallback[1],
                        "first_week": weeks[0] if weeks else None,
                        "last_week": weeks[-1] if weeks else None,
                        "weeks": weeks,
                        "weeks_text": weeks_text,
                        "location": describe_classroom(room),
                        "teacher": teacher,
                        "code": number,
                    }
                )
    return entries
