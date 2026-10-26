r"""categories.py —— 计划分类（可自定义）的校验与颜色派生。

【为什么分类不再是写死的三个】
v1.0 的分类（work/study/life）是硬编码常量；现在用户可以自行添加、改名、
换色、删除分类，所以分类变成了**数据**（存在 plans.json 的 `categories` 里），
不再是代码里的常量。任务上的 `category` 字段存的仍然是分类 id。

【颜色怎么存】
一个分类需要三个颜色（主色 / 浅底 / 深字）才能画出日历里的事件块。
用户只挑一个主色，其余两个在这里按固定比例混出来并**一起存进数据**——
这样前端（主窗口与桌面便签两份 JS）不需要各自再写一遍混色逻辑。

【删除与导入的一致性】
删除分类时由 IPC 层把该分类下的任务改派到剩下的第一个分类；导入备份时
把备份里多出来的分类一并并入。因此落盘文件里「任务引用的分类」始终存在，
校验（validate_state）可以维持「宁报错不修复」的原则。
"""

from __future__ import annotations

import re

from .numbers import js_len

__all__ = [
    "CATEGORY_ID_RE",
    "DEFAULT_CATEGORIES",
    "MAX_CATEGORIES",
    "clean_category",
    "category_ids",
    "normalize_categories",
    "next_category_color",
]

# id 规则与任务 id 保持一致的风格：小写字母/数字/连字符，便于在手写备份里辨认。
CATEGORY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

MAX_CATEGORIES = 20


def ensure_category(categories: list, category_id: str, name: str) -> None:
    """All import paths share the same category limit as the editor."""
    if any(c["id"] == category_id for c in categories):
        return
    if len(categories) >= MAX_CATEGORIES:
        raise ValueError("分类最多 20 个，请先整理分类")
    categories.append(clean_category({"id": category_id, "name": name,
                                      "color": next_category_color(categories)}))

# 默认三个分类与 v1.0 前端硬编码的配色逐值一致，老数据升级后外观不变。
DEFAULT_CATEGORIES = [
    {"id": "work", "name": "工作 / 项目", "color": "#6b9b87", "bg": "#e9f3ed", "ink": "#355e4b"},
    {"id": "study", "name": "学习 / 成长", "color": "#a195b9", "bg": "#f1edf7", "ink": "#6b5784"},
    {"id": "life", "name": "生活 / 日常", "color": "#c6a574", "bg": "#fbf1df", "ink": "#84602f"},
]

# 新建分类时依次取用的主色（避开三个默认色，让新分类一眼能区分）。
_PALETTE = ["#5b8fc9", "#c97b8f", "#7ba05b", "#b07cc6", "#c9853f", "#4fa8a0"]


def _hex_channels(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _mix(color: str, other: tuple[int, int, int], ratio: float) -> str:
    """把 `color` 按比例混向 `other`（ratio=0 → 原色，1 → other）。"""
    r, g, b = _hex_channels(color)
    mixed = [round(c + (o - c) * ratio) for c, o in zip((r, g, b), other)]
    return "#" + "".join(f"{max(0, min(255, c)):02x}" for c in mixed)


def derive_shades(color: str) -> tuple[str, str]:
    """由主色派生浅底与深字（与默认三分类的观感一致：浅底≈88% 白，深字≈45% 黑）。"""
    return _mix(color, (255, 255, 255), 0.88), _mix(color, (0, 0, 0), 0.45)


def next_category_color(existing: list[dict]) -> str:
    """给新分类挑一个没被占用的调色板颜色（都用过了就循环取）。"""
    used = {str(c.get("color", "")).lower() for c in existing if isinstance(c, dict)}
    for color in _PALETTE:
        if color not in used:
            return color
    return _PALETTE[len(existing) % len(_PALETTE)]


def category_ids(categories: list[dict]) -> list[str]:
    return [c["id"] for c in categories]


def clean_category(source, existing_ids: list[str] | None = None) -> dict:
    """校验并规范化一个分类（新增时 `existing_ids` 用于查重）。

    与 `clean_task` 同一风格：任何一项不合法都直接抛中文错误。
    缺 `color` 时自动从调色板取色并派生 bg/ink。
    """
    if not isinstance(source, dict):
        raise ValueError("分类格式不正确")
    category_id = source.get("id")
    if not isinstance(category_id, str) or not CATEGORY_ID_RE.fullmatch(category_id):
        raise ValueError("分类标识不正确")
    if existing_ids is not None and category_id in existing_ids:
        raise ValueError("已存在同名标识的分类")
    name = str(source.get("name") or "").strip()
    if not name or js_len(name) > 20:
        raise ValueError("请填写 1–20 字的分类名称")
    color = source.get("color")
    if not (isinstance(color, str) and _COLOR_RE.fullmatch(color)):
        color = next_category_color([])
    color = color.lower()
    bg, ink = derive_shades(color)
    return {"id": category_id, "name": name, "color": color, "bg": bg, "ink": ink}


def normalize_categories(raw) -> list[dict]:
    """把备份里的 `categories` 归一化；缺失或不合法时回退到默认三分类。

    与 validate_state 的「宁报错不修复」不同，这里对**单个坏条目**采取跳过：
    分类只是显示用的元数据，丢一个自定义分类不该让整份备份打不开——
    但「任务引用了不存在的分类」仍然会在 clean_task 那里报错。
    """
    if not isinstance(raw, list):
        return [dict(c) for c in DEFAULT_CATEGORIES]
    seen: set[str] = set()
    result: list[dict] = []
    for item in raw[:MAX_CATEGORIES]:
        if not isinstance(item, dict):
            continue
        category_id = item.get("id")
        if not isinstance(category_id, str) or not CATEGORY_ID_RE.fullmatch(category_id):
            continue
        if category_id in seen:
            continue
        name = str(item.get("name") or "").strip()
        if not name or js_len(name) > 20:
            continue
        color = item.get("color")
        if not (isinstance(color, str) and _COLOR_RE.fullmatch(color)):
            color = next_category_color(result)
        color = color.lower()
        bg = item.get("bg")
        ink = item.get("ink")
        if not (isinstance(bg, str) and _COLOR_RE.fullmatch(bg)) or not (
            isinstance(ink, str) and _COLOR_RE.fullmatch(ink)
        ):
            bg, ink = derive_shades(color)
        seen.add(category_id)
        result.append({"id": category_id, "name": name, "color": color, "bg": bg, "ink": ink})
    if not result:
        return [dict(c) for c in DEFAULT_CATEGORIES]
    return result
