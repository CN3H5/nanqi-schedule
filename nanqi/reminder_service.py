"""Pure scheduling and persisted delivery bookkeeping, separate from windows."""
from datetime import datetime

from .core import due_tasks
from .course_reminder import notifications, is_course, classroom_of
from .weather import weather_note


def schedule_key(task):
    return "|".join(str(task.get(k, "")) for k in ("date", "time", "reminder", "duration"))


class ReminderService:
    def __init__(self, store):
        self.store = store

    def collect(self, now=None, weather=None):
        moment = now or datetime.now()
        milliseconds = int(moment.timestamp() * 1000)
        tasks = self.store.state["tasks"]
        delivered = set()
        for task in tasks:
            saved = task.get("deliveryState", {})
            if saved.get("schedule") == schedule_key(task):
                delivered.update(f"{task['date']}:{task['id']}:{s}" for s in saved.get("stages", []))
        pending = [t for t in tasks if not t.get("snoozeUntil")]
        bad = bool(isinstance(weather, dict) and (weather.get("judge") or {}).get("bad"))
        entries = notifications(pending, moment, delivered=delivered,
                                bad_weather=bad, weather_note=weather_note(weather))
        # Snooze is independent of task category and takes precedence over stages.
        for task in due_tasks(tasks, milliseconds):
            if is_course(task) and not task.get("snoozeUntil"):
                continue
            consume = []
            if is_course(task) and task.get("snoozeUntil"):
                consume = [e["stage"] for e in notifications(tasks, moment, delivered=delivered,
                           bad_weather=bad) if e["task"]["id"] == task["id"]]
            entries.append({"task": task, "stage": "snooze" if task.get("snoozeUntil") else "due", "consumeStages": consume,
                            "title": task["title"], "body": f"{task['date']} {task['time']} · 计划提醒"})
        # Adjacent course room changes are also stages and survive restarts.
        courses = sorted((t for t in pending if t.get("category") == "course" and
                          t["date"] == moment.strftime("%Y-%m-%d") and not t.get("done") and t["reminder"] >= 0), key=lambda t: t["time"])
        minutes = moment.hour * 60 + moment.minute
        for current, following in zip(courses, courses[1:]):
            clock = lambda t: int(t["time"][:2]) * 60 + int(t["time"][3:])
            end, start = clock(current) + current["duration"], clock(following)
            key = f"{following['date']}:{following['id']}:change"
            room, previous_room = classroom_of(following), classroom_of(current)
            if room and previous_room and room != previous_room and 0 <= start - end <= 5 and end <= minutes < start + 5 and key not in delivered:
                entries.append({"task": following, "stage": "change", "title": "换教室",
                                "body": f"下节课 {following['title']}（{following['time']} 开始） · {classroom_of(following)}"})
        if not entries:
            return []

        def commit(state):
            self.store.next_reminder_message(state)
            by_id = {t["id"]: t for t in state["tasks"]}
            for entry in entries:
                task = by_id[entry["task"]["id"]]
                task["notifiedAt"] = milliseconds
                task["snoozeUntil"] = None
                consume = entry.get("consumeStages", [])
                if is_course(task) and (consume or entry["stage"] not in ("due", "snooze")):
                    old = task.get("deliveryState", {})
                    stages = old.get("stages", []) if old.get("schedule") == schedule_key(task) else []
                    add = consume if entry["stage"] == "snooze" else [entry["stage"]]
                    task["deliveryState"] = {"schedule": schedule_key(task), "stages": sorted(set(stages + add))}
                self.store.push_history(state, task["id"], milliseconds)
        self.store.change(commit)
        return entries
