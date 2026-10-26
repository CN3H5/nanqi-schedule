"""课表 HTML 解析（course_import.parse_course_html）的测试。

两种版面各一份贴近真实的夹具：
  * 网格式：表头「节次/星期一…星期日」，课程格带 rowspan 与 <br> 分行；
  * 列表式：表头「课程名称/星期/节次/地点/教师/周次」，每行一门课。
"""

from nanqi.course_import import parse_course_html

GRID_HTML = """
<html><body>
<table id="kbgrid">
  <tr><th>节次</th><th>星期一</th><th>星期二</th><th>星期三</th><th>星期四</th><th>星期五</th><th>星期六</th><th>星期日</th></tr>
  <tr><td>1-2</td>
      <td>数学分析(B1)<br>张教授<br>1-16周<br>第二教学楼 2201</td>
      <td></td>
      <td>数学分析(B1)<br>张教授<br>1-16周<br>第二教学楼 2201</td>
      <td></td><td></td><td></td><td></td></tr>
  <tr><td>3-4</td>
      <td></td>
      <td rowspan="2">大学物理实验<br>李老师<br>3-12周<br>物理实验楼 305</td>
      <td></td><td></td><td></td><td></td><td></td></tr>
  <tr><td>5-6</td>
      <td>程序设计<br>王老师<br>1-8,10-16周<br>信息楼 110</td>
      <td></td><td></td><td></td><td></td><td></td></tr>
</table>
</body></html>
"""

GRID_HTML_CLOCK_LABELS = """
<html><body>
<table>
  <tr><th>时间</th><th>周一</th><th>周二</th><th>周三</th><th>周四</th><th>周五</th></tr>
  <tr><td>07:50-09:25</td><td>英语读写<br>陈教师<br>1-16周<br>东区 5101</td>
      <td></td><td></td><td></td><td></td></tr>
</table>
</body></html>
"""

LIST_HTML = """
<html><body>
<table class="kblist">
  <tr><th>课程名称</th><th>星期</th><th>节次</th><th>地点</th><th>教师</th><th>周次</th></tr>
  <tr><td>数据结构</td><td>星期三</td><td>6-7</td><td>西区 3C 201</td><td>刘老师</td><td>1-16周</td></tr>
  <tr><td>数据结构</td><td>星期五</td><td>6-7</td><td>西区 3C 201</td><td>刘老师</td><td>1-16周</td></tr>
</table>
</body></html>
"""


def test_grid_layout_extracts_courses_with_times_and_weeks():
    courses = parse_course_html(GRID_HTML)
    by_name = {}
    for course in courses:
        by_name.setdefault(course["name"], []).append(course)

    analysis = by_name["数学分析(B1)"]
    assert len(analysis) == 2
    assert {c["weekday"] for c in analysis} == {1, 3}
    assert analysis[0]["start_time"] == "07:50"
    assert analysis[0]["end_time"] == "09:25"
    assert (analysis[0]["first_week"], analysis[0]["last_week"]) == (1, 16)
    assert analysis[0]["location"] == "第二教学楼 2201"
    assert analysis[0]["teacher"] == "张教授"

    physics = by_name["大学物理实验"]
    assert len(physics) == 1
    # rowspan=2 从「3-4」行跨到「5-6」行：节次范围应合并为 3-6
    assert (physics[0]["start_section"], physics[0]["end_section"]) == (3, 6)
    assert physics[0]["start_time"] == "09:45"
    assert physics[0]["end_time"] == "14:45"

    programming = by_name["程序设计"]
    # 多段周次「1-8,10-16周」取整体首尾
    assert (programming[0]["first_week"], programming[0]["last_week"]) == (1, 16)


def test_grid_layout_with_clock_row_labels():
    courses = parse_course_html(GRID_HTML_CLOCK_LABELS)
    assert len(courses) == 1
    assert courses[0]["name"] == "英语读写"
    assert courses[0]["weekday"] == 1
    assert (courses[0]["start_section"], courses[0]["end_section"]) == (1, 2)


def test_list_layout_extracts_courses():
    courses = parse_course_html(LIST_HTML)
    assert len(courses) == 2
    assert all(c["name"] == "数据结构" for c in courses)
    assert {c["weekday"] for c in courses} == {3, 5}
    assert courses[0]["start_time"] == "14:00"
    assert courses[0]["location"] == "西区 3C 201"
    assert courses[0]["teacher"] == "刘老师"


def test_unrelated_html_returns_empty_list():
    assert parse_course_html("<html><body><p>没有课表</p></body></html>") == []
    assert parse_course_html("") == []
    assert parse_course_html("<table><tr><td>只有一格</td></tr></table>") == []


def test_course_name_with_digits_is_not_mistaken_for_section_label():
    html = """
    <table>
      <tr><th>节次</th><th>星期一</th><th>星期二</th><th>星期三</th><th>星期四</th><th>星期五</th></tr>
      <tr><td>1-2</td><td>数学分析(B1)<br>1-16周</td><td></td><td></td><td></td><td></td></tr>
    </table>
    """
    courses = parse_course_html(html)
    assert len(courses) == 1
    assert courses[0]["name"] == "数学分析(B1)"
    assert courses[0]["start_section"] == 1
