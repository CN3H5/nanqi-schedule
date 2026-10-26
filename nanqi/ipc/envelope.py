r"""envelope.py —— 调用结果的「信封」格式与序列化。

【为什么统一信封】
原版 preload 暴露了 20 多个方法，每个方法的返回值和异常各走一条路径。这里所有调用
都收敛成同一种返回值：

    {"ok": true,  "value": ...}     调用成功
    {"ok": false, "error": "..."}   调用失败，前端会 reject 成一个 Error

这样有三点好处：前端只需一处解包逻辑；返回值与异常走同一条 JSON 路径，不会被 Qt 的
类型转换吃掉；错误文案不会因为「异常类型」不同而丢信息。

【性能取舍】
这里用标准库 `json`（C 实现）而不是 `nanqi.jsjson`：前端拿到后只做一次 `JSON.parse`，
不需要 `JSON.stringify` 的逐字兼容；而这是每次调用都要走的热路径，快十几倍。
真正需要逐字兼容的只有落盘的 `plans.json`。
"""

from __future__ import annotations

import json

__all__ = ["ok", "reject", "envelope"]


def ok(value=None) -> dict:
    return {"ok": True, "value": value}


def reject(message: str) -> dict:
    return {"ok": False, "error": message}


def envelope(payload: dict) -> str:
    """把结果信封序列化成 JSON 字符串交给前端。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
