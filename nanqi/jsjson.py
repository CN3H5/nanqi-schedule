"""jsjson.py —— 与 JavaScript `JSON.stringify` 行为一致的序列化。

【为什么要自己写一个】
南七日程原来的数据文件由 Electron 用 `JSON.stringify(state, null, 2)` 写出，
重复计划的确认令牌用 `JSON.stringify({...})` 做 SHA-256。Python 自带的
`json.dumps` 有三处不一样：

  1. 浮点数：JS 把 `1.0` 写成 `1`，Python 写成 `1.0`；
  2. 默认 `ensure_ascii=True` 会把中文转成 `\\uXXXX`；
  3. 分隔符与空容器的写法在缩进模式下需要逐字对齐。

本项目要让 Python 版和 Electron 版**共用同一个 `plans.json`**（互相可读、
可回退、可迁移），所以这里按 ECMAScript 的规则实现一个最小序列化器：
数字、字符串转义、`true/false/null`、以及 `stringify(value, null, 2)` 的
缩进格式全部对齐。字典保持插入顺序，因此键顺序与 JS 对象一致。
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["dumps", "js_number", "js_string"]

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def js_string(value: str) -> str:
    """按 ECMAScript 的 QuoteJSONString 规则转义，非 ASCII 字符保持原样。"""
    parts: list[str] = ['"']
    for char in value:
        mapped = _ESCAPES.get(char)
        if mapped is not None:
            parts.append(mapped)
            continue
        code = ord(char)
        if code < 0x20:
            parts.append("\\u%04x" % code)
        else:
            # JSON.stringify 输出原始字符（不做 \\uXXXX 转义），包括 U+2028/U+2029。
            parts.append(char)
    parts.append('"')
    return "".join(parts)


def js_number(value: Any) -> str:
    """按 ECMAScript 的 Number::toString 规则输出数字。

    JS 只区分「整数」和「浮点」两种 IEEE-754 表示，`1.0` 打印为 `1`。
    NaN 与 Infinity 在 JSON.stringify 中会变成 `null`。
    """
    if isinstance(value, bool):  # bool 是 int 的子类，必须先拦下
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        return "null"
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    text = repr(float(value))
    if "e" in text or "E" in text:
        # JS 用 e+21 这样的形式；Python 的 repr 已经足够接近，去掉多余的 0
        mantissa, _, exponent = text.partition("e")
        sign = "+" if not exponent.startswith("-") else "-"
        return f"{mantissa}e{sign}{int(exponent)}"
    return text


def _serialize(value: Any, indent: str | None, depth: int) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return js_string(value)
    if isinstance(value, (int, float)):
        return js_number(value)
    if isinstance(value, dict):
        if not value:
            return "{}"
        if indent is None:
            inner = ",".join(
                f"{js_string(str(key))}:{_serialize(item, None, depth + 1)}"
                for key, item in value.items()
            )
            return "{" + inner + "}"
        pad = indent * (depth + 1)
        inner = ",\n".join(
            f"{pad}{js_string(str(key))}: {_serialize(item, indent, depth + 1)}"
            for key, item in value.items()
        )
        return "{\n" + inner + "\n" + indent * depth + "}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        if indent is None:
            inner = ",".join(_serialize(item, None, depth + 1) for item in value)
            return "[" + inner + "]"
        pad = indent * (depth + 1)
        inner = ",\n".join(
            f"{pad}{_serialize(item, indent, depth + 1)}" for item in value
        )
        return "[\n" + inner + "\n" + indent * depth + "]"
    raise TypeError(f"无法按 JSON.stringify 语义序列化：{type(value)!r}")


def dumps(value: Any, indent: int | None = None) -> str:
    """等价于 `JSON.stringify(value)`（indent 为 None）或 `JSON.stringify(value, null, indent)`。"""
    if indent is None:
        return _serialize(value, None, 0)
    if not isinstance(indent, int) or isinstance(indent, bool) or indent < 0:
        raise ValueError("indent 必须是非负整数")
    return _serialize(value, " " * indent, 0)
