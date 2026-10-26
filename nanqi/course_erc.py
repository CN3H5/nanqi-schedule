r"""course_erc.py —— 二课（团学活动）日程解析。

【真实结构（来自 mySchedule 快照）】
`mySchedule` 页面里是一张 Ant Design 表格，列固定为：

    序号 | 标题 | 地点 | 开始时间 | 结束时间

所以**不需要点日期切换**：整张表就是「我报名的二课」清单，逐行取五个字段即可。
（之前推测「必须点每一天」是错的——那张全历只是页面上的另一个视图。）

【为什么要按表头定位列】
列顺序可能变（学校改版、不同视图），所以先读 `thead` 的列名，再按名字取对应索引；
认不出表头时才退回「按固定顺序 序号/标题/地点/开始/结束」的兜底。

【时间格式】
单元格里可能是 `2026-10-21 14:00`、`2026-10-21 14:00:00`，也可能只有 `14:00`。
解析时把「日期」和「时刻」分开取：日期缺失就跳过这一行（因为无法排进日历）。
"""

from __future__ import annotations

import html as _html
import re

__all__ = ["parse_ercourse", "parse_erc_api_records"]

#: 「我的日程」页面调用的接口。**这是从站点自己的前端包里读出来的，不是猜的**：
#: `chunk-55584ce1…js` 里的 `mySchedule.vue` 写着
#:     url: { list: "/personal/scCalendar/getPageUserList" }
#:     change: function(e) { this.queryParam = { now: moment(e).format("YYYY-MM-DD") }; this.loadData() }
#: 而 axios 实例的 baseURL 是 `https://young.ustc.edu.cn/login/wisdom-group-learning-bg/`。
#: 也就是说**一次请求只返回一天**——这就是「只能抓到当天」的根因。
ERC_API_BASE = "https://young.ustc.edu.cn/login/wisdom-group-learning-bg/"
ERC_API_PATH = "personal/scCalendar/getPageUserList"

_HEADER_RE = re.compile(r"<thead[^>]*>(.*?)</thead>", re.S)
_ROW_RE = re.compile(r'<tr[^>]*class="[^"]*ant-table-row[^"]*"[^>]*>(.*?)</tr>', re.S)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.S)

_DEFAULT_COLUMNS = {"序号": 0, "标题": 1, "地点": 2, "开始时间": 3, "结束时间": 4}

_DATE_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")
_TIME_RE = re.compile(r"(\d{1,2})[:：](\d{2})")


def _text(fragment: str) -> str:
    """去标签 + 还原实体 + 压缩空白。"""
    text = re.sub(r"(?s)<[^>]+>", " ", fragment or "")
    text = _html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _columns(html: str) -> dict[str, int]:
    """读表头，返回 {列名: 索引}；认不出就返回默认顺序。"""
    head = _HEADER_RE.search(html)
    if not head:
        return dict(_DEFAULT_COLUMNS)
    names = [_text(cell) for cell in _TH_RE.findall(head.group(1))]
    columns = {name: index for index, name in enumerate(names) if name}
    for key in _DEFAULT_COLUMNS:
        if key not in columns:
            # 表头里找不到就沿用默认位置（避免整页解析失败）
            return {**_DEFAULT_COLUMNS, **columns}
    return columns


def _split_time(text: str) -> tuple[str, str]:
    """从一段文本里取 (日期 YYYY-MM-DD, 时刻 HH:MM)；缺日期返回 ("", ...)。"""
    date_text = ""
    match = _DATE_RE.search(text or "")
    if match:
        year, month, day = (int(part) for part in match.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            date_text = f"{year:04d}-{month:02d}-{day:02d}"
    clock = _TIME_RE.search(text or "")
    clock_text = f"{int(clock.group(1)):02d}:{clock.group(2)}" if clock else ""
    return date_text, clock_text


def parse_ercourse(html: str) -> list[dict]:
    """解析「我的二课」表格，返回 [{'title','date','time','end_time','location'}]。

    ⚠️ 这个表格是**按所选日期过滤**的（页面左侧日历选中哪天就只显示哪天），
    所以从一份 HTML 里最多只能拿到一天的数据。要拿全量请走接口
    （见 `parse_erc_api_records` 与 `controller._grab_ercourse_via_api`）。
    """
    if not isinstance(html, str) or "ant-table" not in html:
        return []
    columns = _columns(html)
    entries: list[dict] = []
    for row in _ROW_RE.finditer(html):
        cells = [_text(cell) for cell in _CELL_RE.findall(row.group(1))]
        if not cells:
            continue

        def pick(name: str) -> str:
            index = columns.get(name, -1)
            return cells[index] if 0 <= index < len(cells) else ""

        title = pick("标题")
        if not title:
            continue
        date_text, start = _split_time(pick("开始时间"))
        if not date_text:
            continue  # 没有日期就无法排进日历
        end_date, end = _split_time(pick("结束时间"))
        entries.append(
            {
                "title": title,
                "date": date_text,
                "time": start or "18:00",
                "end_time": end,
                "end_date": end_date if end_date and end_date != date_text else "",
                "location": pick("地点"),
                "source": "二课",
            }
        )
    return entries


def parse_erc_api_records(records) -> list[dict]:
    """解析 `/personal/scCalendar/getPageUserList` 返回的记录。

    字段来自页面自己的表格列定义（`mySchedule.vue` 的 `columns`）：
    `title` / `place` / `startTime` / `endTime`（值形如 `2026-10-10 20:00:00`）。
    字段名做宽容匹配，站点改名时不至于整批失败。
    """
    items: list[dict] = []
    seen: set[tuple] = set()
    if not isinstance(records, (list, tuple)):
        return items
    for record in records:
        if not isinstance(record, dict):
            continue
        title = str(record.get("title") or record.get("name") or "").strip()
        if not title:
            continue
        start_raw = str(record.get("startTime") or record.get("start") or "")
        date_text, start = _split_time(start_raw)
        if not date_text:
            continue
        end_raw = str(record.get("endTime") or record.get("end") or "")
        end_date, end = _split_time(end_raw)
        place = str(record.get("place") or record.get("location") or "").strip() or "暂无"
        key = (title, date_text, start, end_raw)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "title": title,
                "date": date_text,
                "time": start or "18:00",
                "end_time": end,
                "end_date": end_date if end_date and end_date != date_text else "",
                "location": place,
                "source": "二课",
            }
        )
    items.sort(key=lambda item: (item["date"], item["time"]))
    return items
