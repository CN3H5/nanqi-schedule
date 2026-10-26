"""reminder_copy.py —— 120 条名句文案与轮换进度（移植自 src/reminder-copy.cjs）。

【轮换规则】
每条提醒配一句古典名句/诗词正文与出处。一轮之内不重复；一轮看完后重新开始，
但**新一轮的第一句不会与上一句相同**（否则连着两次看到同一句体验很差）。
进度（看过哪些 id、当前是哪句）保存在 `plans.json` 的 settings 里，重启后接着轮换。

【为什么要清洗 progress】
早期版本用的是另一套 id（`original-001` 之类）。备份可能来自旧版、也可能被手工
改过，因此这里把「不认识的 id」和「重复的 id」全部剔除，只保留仍存在且合法的部分；
currentId 必须同时出现在 seen 里，保证「当前句」和「已看集合」不会自相矛盾。
"""

from __future__ import annotations

import json
import secrets
from functools import lru_cache

from .paths import assets_dir

__all__ = [
    "messages",
    "message_count",
    "normalize_rotation",
    "get_current_message",
    "advance_message",
]

_CLASSICS_FILE = "reminder-classics.json"


@lru_cache(maxsize=1)
def messages() -> tuple[dict, ...]:
    """完整文案库（只读一次，按文件顺序返回，保持与 JS 数组一致的索引）。"""
    with open(assets_dir() / _CLASSICS_FILE, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list):
        raise ValueError("提醒文案库格式不正确")
    return tuple(raw)


@lru_cache(maxsize=1)
def _by_id() -> dict[str, dict]:
    return {message["id"]: message for message in messages()}


def message_count() -> int:
    """文案总数（界面上的「120 条名句」）。"""
    return len(messages())


def normalize_rotation(raw=None) -> dict:
    """清洗轮换进度：只保留已知且不重复的 id，长度不超过文案总数（参数可省略）。"""
    catalogue = _by_id()
    total = len(messages())
    seen: list[str] = []
    source = raw.get("seen") if isinstance(raw, dict) else None
    if isinstance(source, list):
        for identifier in source:
            if isinstance(identifier, str) and identifier in catalogue and identifier not in seen:
                seen.append(identifier)
        seen = seen[-total:]
    current = raw.get("currentId") if isinstance(raw, dict) else None
    current_id = current if isinstance(current, str) and current in catalogue else None
    if current_id and current_id not in seen:
        seen.append(current_id)
    return {"seen": seen[-total:], "currentId": current_id}


def get_current_message(raw=None) -> dict:
    """当前应展示的那一句；没有进度或 id 已失效时用第一条兜底（参数可省略）。"""
    rotation = normalize_rotation(raw)
    if rotation["currentId"]:
        return _by_id()[rotation["currentId"]]
    return messages()[0]


def advance_message(raw=None, choose_index=None) -> dict:
    """挑下一句并返回新的进度（`raw` 可省略，表示还没有任何进度）。

    `choose_index(limit)` 默认是密码学安全的随机数（对应 Node 的 `crypto.randomInt`），
    测试可以注入确定性的选择函数。
    """
    picker = choose_index or (lambda limit: secrets.randbelow(limit))
    rotation = normalize_rotation(raw)
    seen = list(rotation["seen"])
    catalogue = messages()
    available = [message for message in catalogue if message["id"] not in seen]
    if not available:
        # 一轮看完：清空进度，但排除刚刚展示过的那一句。
        seen = []
        available = [message for message in catalogue if message["id"] != rotation["currentId"]]

    index = picker(len(available))
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or index < 0
        or index >= len(available)
    ):
        raise ValueError("Invalid reminder message selection")

    message = available[index]
    return {
        "message": message,
        "rotation": {"seen": seen + [message["id"]], "currentId": message["id"]},
    }
