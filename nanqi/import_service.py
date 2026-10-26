"""Import task creation and deduplication, without window dependencies."""
from datetime import datetime
from .core.categories import ensure_category
DEFAULT_COURSE_LAST_WEEK = 18

class ImportService:
    def __init__(self, store):
        self.store = store
        self._last_import_skipped = 0

    def _ensure_category(self, next_state: dict, category_id: str, name: str) -> None:
        """按需建分类（课程 / 二课 / 考试），颜色用调色板轮转。"""
        ensure_category(next_state["categories"], category_id, name)


    def _create_ercourse_tasks(self, items: list[dict]) -> int:
        """把解析出的二课条目变成日程（一次性，不重复展开）。

        【它属于「普通待办」，不是课表】
        用户明确要求：「二课…这个就可以当做一个计划来完成了，这个就不是课表类型」。
        所以二课单独成一个分类（`er-course`，名「二课」，侧栏可一键筛出），
        但**不走**上课提醒那套节奏——它是一条普通日程，提前 `reminder` 分钟提醒一次即可。

        【已经过去的直接丢掉】用户要求：过去的项目「不用提醒用户该处理了，直接当做垃圾
        信息丢掉即可，甚至都可以不用标出一个已完成」。所以这里直接跳过，不建任务。
        """
        from .core import clean_task

        skipped = 0

        def build(next_state: dict) -> int:
            nonlocal skipped
            self._ensure_category(next_state, "er-course", "二课")
            category_ids = [c["id"] for c in next_state["categories"]]
            existing = {
                task_key(task) for task in next_state["tasks"]
            }
            created = 0
            for item in items:
                start = str(item.get("time") or "18:00")
                end = str(item.get("end_time") or "")
                if _is_past(item.get("date"), start):
                    skipped += 1
                    continue
                notes_lines = []
                end_date = str(item.get("end_date") or "")
                if end_date and end_date != item.get("date"):
                    # 跨天的活动（例如 10-10 20:00 → 10-13 23:45）：时长按真实跨度算，
                    # 但 clean_task 最多接受 1440 分钟，所以夹一下并在备注里写清结束时刻。
                    try:
                        start_dt = datetime.strptime(f"{item['date']} {start}", "%Y-%m-%d %H:%M")
                        end_dt = datetime.strptime(
                            f"{end_date} {end or start}", "%Y-%m-%d %H:%M"
                        )
                        duration = int((end_dt - start_dt).total_seconds() // 60)
                    except ValueError:
                        duration = 90
                    duration = max(15, min(1440, duration))
                    notes_lines.append(f"结束：{end_date} {end}".strip())
                else:
                    duration = _minutes_between(start, end) if end else 90
                if item.get("location"):
                    notes_lines.append("地点：" + item["location"])
                notes_lines.append("来源：二课（团学活动）导入")
                draft = {
                    "title": item["title"],
                    "date": item["date"],
                    "time": start,
                    "duration": duration,
                    "reminder": 30,
                    "category": "er-course",
                    "priority": "normal",
                    "notes": "\n".join(notes_lines),
                    "kind": "schedule",
                    "done": False,
                }
                task = clean_task(draft, categories=category_ids)
                key = task_key(task)
                if key in existing:
                    continue
                existing.add(key)
                if len(next_state["tasks"]) + 1 > 10000:
                    raise ValueError("计划总数不能超过 10000 项")
                next_state["tasks"].append(task)
                created += 1
            return created

        holder: dict[str, int] = {}

        def mutate(next_state: dict) -> None:
            holder["created"] = build(next_state)

        self.store.change(mutate)
        self._last_import_skipped = skipped
        return holder.get("created", 0)


    def _create_exam_tasks(self, items: list[dict]) -> int:
        """把解析出的考试变成日程：一场考试 = 一项计划（`exam` 分类）。

        考试与课表同属「第一课堂」：都用课程式提醒（`course_reminder`），
        主标题是科目，副标题是时间与地点。
        """
        from .core import clean_task

        skipped = 0

        def build(next_state: dict) -> int:
            nonlocal skipped
            self._ensure_category(next_state, "exam", "考试")
            category_ids = [c["id"] for c in next_state["categories"]]
            existing = {
                task_key(task) for task in next_state["tasks"]
            }
            created = 0
            for item in items:
                start = str(item.get("time") or "")
                end = str(item.get("end_time") or "")
                if _is_past(item.get("date"), start):
                    skipped += 1
                    continue
                # 考试通常两小时；页面没给结束时刻时按 120 分钟排（clean_task 允许 15–1440）。
                duration = _minutes_between(start, end) if (start and end) else 120
                notes_lines = []
                if item.get("location"):
                    notes_lines.append("地点：" + item["location"])
                if item.get("seat"):
                    notes_lines.append("座位：" + str(item["seat"]))
                if item.get("form"):
                    notes_lines.append("形式：" + str(item["form"]))
                notes_lines.append("来源：教务系统考试导入")
                draft = {
                    "title": item["name"],
                    "date": item["date"],
                    "time": start or "09:00",
                    "duration": duration,
                    "reminder": 30,
                    "category": "exam",
                    "priority": "high",
                    "notes": "\n".join(notes_lines),
                    "kind": "schedule",
                    "done": False,
                }
                task = clean_task(draft, categories=category_ids)
                key = task_key(task)
                if key in existing:
                    continue
                existing.add(key)
                if len(next_state["tasks"]) + 1 > 10000:
                    raise ValueError("计划总数不能超过 10000 项")
                next_state["tasks"].append(task)
                created += 1
            return created

        holder: dict[str, int] = {}

        def mutate(next_state: dict) -> None:
            holder["created"] = build(next_state)

        self.store.change(mutate)
        self._last_import_skipped = skipped
        return holder.get("created", 0)


    def _create_course_tasks(self, courses: list[dict], semester_start) -> int:
        """把解析出的课程变成每周重复的日程（同一门课的多个上课时段各自成组）。"""
        from datetime import timedelta

        from .core import clean_task, make_series

        skipped = 0

        def build(next_state: dict) -> int:
            nonlocal skipped
            categories = next_state["categories"]
            ensure_category(categories, "course", "课程")
            category_ids = [c["id"] for c in categories]
            created = 0
            for course in courses:
                duration = _minutes_between(course["start_time"], course["end_time"])
                # 【关键修复】课表页面常常不给周次（或给的文本解析不出来）。
                # 原来的兜底是 last_week = first_week = 1，结果每门课只生成「第 1 周」
                # 那一天的一次计划——而第 1 周往往早已过去，用户在日历里自然什么都看不到。
                # 现在缺省按「整学期」展开：每个上课时段每周一次，一直到第 18 周。
                first_week = max(1, int(course["first_week"] or 1))
                last_week = int(course["last_week"] or 0) or DEFAULT_COURSE_LAST_WEEK
                if last_week < first_week:
                    last_week = first_week
                first_date = semester_start + timedelta(days=(course["weekday"] - 1) + (first_week - 1) * 7)
                last_date = semester_start + timedelta(days=(course["weekday"] - 1) + (last_week - 1) * 7)
                notes_lines = []
                if course["location"]:
                    notes_lines.append("地点：" + course["location"])
                if course["teacher"]:
                    notes_lines.append("教师：" + course["teacher"])
                if course["weeks_text"]:
                    notes_lines.append("周次：" + course["weeks_text"])
                notes_lines.append("来源：教务系统课表导入")
                draft = {
                    "title": course["name"],
                    "date": first_date.strftime("%Y-%m-%d"),
                    "time": course["start_time"],
                    "duration": duration,
                    "reminder": 30,
                    "category": "course",
                    "priority": "normal",
                    "notes": "\n".join(notes_lines),
                    "kind": "schedule",
                    "done": False,
                }
                task = clean_task(draft, categories=category_ids)
                # 【按数据来，不假定连续周】教务系统给出的是「周次集合」，
                # 例如 2-5,7-18 表示第 6 周停课。逐周照抄才不会把停课周也算成上课。
                weeks = course.get("weeks") or []
                if weeks:
                    series = []
                    for week in weeks:
                        day = semester_start + timedelta(
                            days=(course["weekday"] - 1) + (week - 1) * 7
                        )
                        item_draft = dict(draft)
                        item_draft["date"] = day.strftime("%Y-%m-%d")
                        series.append(clean_task(item_draft, categories=category_ids))
                else:
                    repeat = (
                        {"frequency": "weekly", "until": last_date.strftime("%Y-%m-%d")}
                        if last_date > first_date
                        else 1
                    )
                    series = make_series(task, repeat)
                # 去重：同一门课同一日期时间已存在时跳过（允许用户重复点按钮）
                existing_keys = {
                    task_key(t) for t in next_state["tasks"]
                }
                series = [
                    item
                    for item in series
                    if task_key(item) not in existing_keys
                ]
                # 【过去的课不要】用户要求：一键导入时发现的「已经过去」的项目直接当垃圾丢掉，
                # 既不提醒也不要标成已完成。所以只保留今天及以后的课。
                today_key = datetime.now().strftime("%Y-%m-%d")
                kept = [item for item in series if item["date"] >= today_key]
                skipped += len(series) - len(kept)
                series = kept
                if len(next_state["tasks"]) + len(series) > 10000:
                    raise ValueError("计划总数不能超过 10000 项")
                next_state["tasks"].extend(series)
                created += len(series)
            return created

        holder: dict[str, int] = {}

        def mutate(next_state: dict) -> None:
            holder["created"] = build(next_state)

        self.store.change(mutate)
        self._last_import_skipped = skipped
        return holder.get("created", 0)


def _is_past(date_text, time_text="") -> bool:
    """这一条是不是「已经过去了」。

    导入时用它把过去的项目直接丢掉（用户明确要求：不提醒、也不标成已完成）。
    日期认不出来时**不当成过去**（宁可留下，也不要误删用户的东西）。
    """
    if not isinstance(date_text, str) or len(date_text) < 10:
        return False
    clock = time_text if isinstance(time_text, str) and len(time_text) >= 5 else "00:00"
    try:
        when = datetime.strptime(f"{date_text[:10]} {clock[:5]}", "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    return when < datetime.now()


def _minutes_between(start: str, end: str) -> int:
    """两个 'HH:MM' 之间的分钟数（限定在 15–1440，与 clean_task 的时长规则一致）。"""
    try:
        sh, sm = int(start[:2]), int(start[3:5])
        eh, em = int(end[:2]), int(end[3:5])
        minutes = (eh * 60 + em) - (sh * 60 + sm)
    except (ValueError, IndexError):
        minutes = 60
    return max(15, min(1440, minutes))


def task_key(task):
    return task["title"], task["date"], task["time"]
