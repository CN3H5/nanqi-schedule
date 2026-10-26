"""store.py —— 计划数据的状态层（移植自 src/main.cjs 的主进程状态部分）。

【它负责什么】
Electron 版把所有可变状态放在主进程的一个 `state` 变量里，界面通过 IPC 读写。
这里把同样的职责收进一个类：

  * **落盘策略完全一致**：先写 `plans.json.tmp` → 把旧的 `plans.json` 复制成
    `plans.json.bak` → 再原子改名覆盖。任何一步崩掉都不会留下半截文件；
  * **快照**：`snapshot()` 把数据、更新检查状态、数据目录、开机启动状态、
    当前提醒文案一起打包给界面，界面只认这一份快照；
  * **变更**：`change(fn)` 在深拷贝上执行修改，落盘成功后才替换内存状态并广播，
    所以校验抛错时数据一点都不会变（对应 JS 的 `structuredClone` + `persist`）。

【数据文件与上一代互通】
用 `nanqi.jsjson`（等价于 `JSON.stringify(state, null, 2)`）写入，所以上一代
「橙子日程 / weeklight」的 `plans.json` 可以被原样读取，反过来也能被旧版读回。
数据目录本身换成了 `%APPDATA%\\nanqi_schedule`，首次运行会把旧目录里的计划复制过来。
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import __version__
from .core import initial_state, validate_state
from .data_path import resolve_data_path
from .jsjson import dumps
from .login_settings import create_login_settings
from .paths import (
    DATA_DIR_ENV,
    LEGACY_DATA_DIR_ENV,
    is_packaged,
    migrate_legacy_data_dir,
    user_data_dir,
)
from .reminder_copy import advance_message, get_current_message, message_count
from .task_series import validate_series_tasks
from .core.state import check_state_for_write
from .core.categories import MAX_CATEGORIES

__all__ = ["Store", "merge_import"]

# 备份文件大小上限（与 JS 版一致：20 MB）。
MAX_IMPORT_BYTES = 20 * 1024 * 1024
# 历史记录（提醒事件）最多保留条数。
MAX_HISTORY = 300
# 开机启动状态的缓存时长（秒）。读注册表是内核调用，而快照会被频繁生成。
AUTOSTART_CACHE_SECONDS = 3.0


def merge_import(state: dict, incoming: dict) -> None:
    """把备份合并进当前状态（就地修改 `state`），对应 `handle('import')` 里的合并逻辑。

    规则：相同 ID 用备份里的内容覆盖，其余保留；如果当前那条属于某个重复分组，
    而备份里的同 ID 记录**没有**分组信息（旧版备份），则保留原有的分组归属，
    否则一次导入就会把重复计划拆散。

    分类（categories）也一并合并：备份里多出来的自定义分类会加进当前列表，
    否则导入进来的任务会引用一个不存在的分类。
    """
    merged: dict[str, dict] = {task["id"]: task for task in state["tasks"]}
    for task in incoming["tasks"]:
        existing = merged.get(task["id"])
        if existing and existing.get("seriesId") and "seriesId" not in task:
            task = {**task, "seriesId": existing.get("seriesId"), "seriesIndex": existing.get("seriesIndex")}
        merged[task["id"]] = task
    validate_series_tasks(list(merged.values()))
    if len(merged) > 10000:
        raise ValueError("合并后超过 10000 项上限")
    known = {c["id"] for c in state["categories"]}
    if len(known | {c["id"] for c in incoming.get("categories", [])}) > MAX_CATEGORIES:
        raise ValueError("合并后分类超过 20 个，请先整理分类")
    for category in incoming.get("categories", []):
        if category["id"] not in known:
            state["categories"].append(category)
            known.add(category["id"])
    state["tasks"] = list(merged.values())


class Store(QObject):
    """计划数据 + 设置 + 更新状态 + 开机启动状态的唯一持有者。"""

    # 广播给所有窗口：载荷是 JSON 字符串形式的完整快照。
    stateChanged = Signal(str)
    # 需要弹给用户的错误（保存失败、层级设置失败等）。
    errorRaised = Signal(str)

    def __init__(
        self,
        *,
        data_dir: str | None = None,
        login=None,
        packaged: bool | None = None,
        now=None,
        mail=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._now = now or (lambda: int(time.time() * 1000))
        self.packaged = is_packaged() if packaged is None else packaged
        self._autostart_cache: tuple[bool, str] | None = None
        self._autostart_at = 0.0
        override = os.environ.get(DATA_DIR_ENV) or os.environ.get(LEGACY_DATA_DIR_ENV)
        default_dir = str(user_data_dir())
        self.data_dir = Path(resolve_data_path(data_dir or default_dir, override))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # 更名后第一次运行：如果新目录还是空的、而上一代「橙子日程」留下了计划，
        # 就把那份 plans.json 复制过来（复制而非移动，旧数据保持可用）。
        self.migration_note = ""
        migrated_from = migrate_legacy_data_dir(self.data_dir)
        if migrated_from:
            self.migration_note = (
                f"已将上一代「橙子日程」的计划复制到新的数据目录（原目录仍保留：{migrated_from}）"
            )
        self.data_file = self.data_dir / "plans.json"
        self.startup_error = ""
        self._load_failed = False
        # 邮件配置独立于 plans.json（见 nanqi/mail/config.py 的说明）：
        # 这里只持有它的存储层，快照里给出「公开视图」（永远不含密码）。
        self.mail = mail
        if self.mail is None:
            try:
                from .mail.config import MailConfigStore

                self.mail = MailConfigStore(self.data_dir)
            except Exception:  # noqa: BLE001 - 邮件是可选功能，绝不能挡住启动
                self.mail = None

        self.state = initial_state()
        self._load()
        self._repair_legacy_courses()
        self.login = login or create_login_settings(packaged=self.packaged)

    # ------------------------------------------------------------------ 读取

    def _repair_legacy_courses(self) -> None:
        """一次性整理旧版课表导入留下的脏数据（拼接标题 / 重复 / 错误时长）。

        【为什么放在 Store 的加载流程里】
        这些数据的错法很具体（标题是两个粘连的时刻开头，见 `course_repair.py`），
        用户自己没法修，也不该让他去手改 JSON。加载后立刻修好、写一份旁路备份、
        再用 `migration_note` 在界面上一句话告知即可。

        **幂等**：修完再启动不会再有变化，因此不会每次都写盘。
        """
        try:
            from .course_repair import repair_state

            if self._load_failed:
                return
            candidate = copy.deepcopy(self.state)
            stats = repair_state(candidate)
            if not any(stats.values()):
                return
            # 备份必须拿“修复前”的**磁盘原文件**：repair_state 是就地修改 self.state 的。
            if self.data_file.exists():
                try:
                    shutil.copyfile(
                        self.data_file,
                        self.data_dir / f"before-repair-{self._now()}.json",
                    )
                except OSError:
                    pass
            self.persist(candidate)
            parts = []
            if stats["removed"]:
                parts.append(f"清理重复课程 {stats['removed']} 项")
            if stats["repaired"]:
                parts.append(f"修复课程 {stats['repaired']} 项")
            if stats["reminders"]:
                parts.append(f"提醒改为提前 30 分钟 {stats['reminders']} 项")
            note = "已整理导入的日程：" + "、".join(parts) + "（原文件已备份）"
            self.migration_note = f"{self.migration_note} {note}".strip()
        except Exception as error:  # noqa: BLE001 - 整理失败绝不能挡住启动
            print("Course repair skipped:", error, file=sys.stderr)

    def _load(self) -> None:
        """读盘；文件损坏时保留原文件并留一份恢复副本，然后从空状态继续。"""
        if not self.data_file.exists():
            return
        try:
            with open(self.data_file, "r", encoding="utf-8") as handle:
                self.state = validate_state(json.load(handle))
        except Exception as error:  # noqa: BLE001 - 任何读取/校验失败都走恢复分支
            self._load_failed = True
            self.startup_error = "计划文件无法读取，已保留原文件。请从设置导入备份。" + str(error)
            try:
                shutil.copyfile(
                    self.data_file,
                    self.data_dir / f"plans-recovery-{self._now()}.json",
                )
            except OSError:
                pass

    # ------------------------------------------------------------------ 写入

    def persist(self, next_state: dict) -> None:
        """临时文件 → 备份旧文件 → 原子改名（与 JS 版顺序完全一致）。"""
        if self._load_failed:
            raise ValueError("原计划文件无法读取，请先从设置导入有效备份")
        check_state_for_write(next_state)
        temp = Path(str(self.data_file) + ".tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(dumps(next_state, indent=2))
        if self.data_file.exists():
            shutil.copyfile(self.data_file, str(self.data_file) + ".bak")
        os.replace(temp, self.data_file)
        self.state = next_state

    def restore_backup(self, incoming: dict) -> dict:
        """Explicit recovery is the only write allowed after a corrupt load."""
        candidate = validate_state(incoming)
        check_state_for_write(candidate)
        failed = self._load_failed
        if failed and self.data_file.exists():
            shutil.copyfile(self.data_file, self.data_dir / f"plans-recovery-{time.time_ns()}.json")
        self._load_failed = False
        try:
            self.persist(candidate)
        except Exception:
            self._load_failed = failed
            raise
        self.startup_error = ""
        self.broadcast()
        return self.snapshot()

    def change(self, mutator) -> dict:
        """在深拷贝上执行 `mutator`，落盘后再替换内存状态并广播。"""
        next_state = copy.deepcopy(self.state)
        mutator(next_state)
        self.persist(next_state)
        self.broadcast()
        return self.snapshot()

    def write_side_backup(self, prefix: str) -> Path:
        """写一份与当前状态同格式的旁路备份（导入前/导出用）。"""
        target = self.data_dir / f"{prefix}-{self._now()}.json"
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(dumps(self.state, indent=2))
        return target

    def export_to(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(dumps(self.state, indent=2))
        return path

    def load_backup(self, path: str) -> dict:
        """读取并校验一份备份文件（自动去掉 UTF-8 BOM）。"""
        size = os.path.getsize(path)
        if size > MAX_IMPORT_BYTES:
            raise ValueError("备份文件超过 20 MB")
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        return validate_state(json.loads(raw.lstrip("\ufeff")))

    def suggested_export_path(self) -> str:
        return str(self.data_dir / f"南七日程备份-{datetime.now().strftime('%Y-%m-%d')}.json")

    # ------------------------------------------------------------------ 广播

    def autostart_status(self) -> tuple[bool, str]:
        """开机启动状态（读注册表）。

        `snapshot()` 每次广播、每次 `get()` 都会调用它，而读注册表是内核调用；
        缓存几秒可以省下这条热路径上的开销。我们自己写设置时会立即失效缓存，
        所以用户在设置页看到的永远是最新值；只有「用户从任务管理器改了启动项」
        这种情况最多延迟几秒才反映出来——那本来也不需要实时。
        """
        moment = time.monotonic()
        if self._autostart_cache is not None and moment - self._autostart_at < AUTOSTART_CACHE_SECONDS:
            return self._autostart_cache
        try:
            status = (self.login["get_open_at_login"]() is True, "")
        except Exception as error:  # noqa: BLE001 - 读注册表失败要让设置页看到原因
            status = (False, "无法读取 Windows 开机启动状态：" + str(error))
        self._autostart_cache = status
        self._autostart_at = moment
        return status

    def invalidate_autostart(self) -> None:
        """作废开机启动状态缓存（写入设置后立刻调用）。"""
        self._autostart_cache = None

    def snapshot(self) -> dict:
        """界面唯一认可的数据形状（键名与 JS 版保持一致）。"""
        autostart, autostart_error = self.autostart_status()
        return {
            **self.state,
            "appVersion": __version__,
            "dataDir": str(self.data_dir),
            "startupError": self.startup_error,
            "migrationNote": self.migration_note,
            "autostart": autostart,
            "autostartError": autostart_error,
            "packaged": self.packaged,
            "mail": self.mail.public() if self.mail is not None else None,
            "reminderMessage": get_current_message(self.state["settings"]["reminderRotation"]),
            "reminderMessageCount": message_count(),
        }

    def broadcast(self) -> None:
        self._emit_state()

    def _emit_state(self) -> None:
        """把快照广播给所有页面。

        这里是每次改动都要走的热路径，而前端拿到载荷只做一次 `JSON.parse`，
        所以用 C 实现的 `json.dumps`（比 `nanqi.jsjson` 快十几倍）。两者只在
        浮点写法上有差别（`1.0` vs `1`），而快照里所有数值都是整数，前端也不在乎；
        **落盘的 `plans.json` 仍然用 `jsjson`**，那份必须与 Electron 版逐字兼容。
        """
        self.stateChanged.emit(json.dumps(self.snapshot(), ensure_ascii=False, separators=(",", ":")))

    # ------------------------------------------------------------------ 提醒文案

    def next_reminder_message(self, next_state: dict) -> None:
        """把轮换进度推进一句（就地修改传入的状态）。"""
        next_state["settings"]["reminderRotation"] = advance_message(
            next_state["settings"]["reminderRotation"]
        )["rotation"]

    def current_message(self) -> dict:
        return get_current_message(self.state["settings"]["reminderRotation"])

    def encouragement(self) -> str:
        """通知正文里附上的名句与出处。"""
        message = self.current_message()
        text = message.get("text", "")
        return text + (" — " + message["source"] if message.get("url") else "")

    # ------------------------------------------------------------------ 历史

    def push_history(self, next_state: dict, task_id: str, at_ms: int) -> None:
        next_state["history"] = [
            entry
            for entry in next_state["history"]
            if entry.get("taskId") != task_id or entry.get("dismissed")
        ]
        next_state["history"].append(
            {
                "id": _new_id(),
                "taskId": task_id,
                "at": at_ms,
                "dismissed": False,
            }
        )
        next_state["history"] = next_state["history"][-MAX_HISTORY:]


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())
