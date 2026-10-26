r"""numbers.py —— JS 数值与字符串语义（领域逻辑的地基）。

【为什么要单独一层】
`core.cjs` 里有大量「JS 特有的语义」被不显眼地使用着，而这些语义在 Python 里
**默认并不成立**。把它们集中到这里，好处是：任何一处需要「按 JS 的方式判断数字」
时都调同一个函数，不会有人在别处写成 `isinstance(x, (int, float))` 而漏掉 bool。

对不齐会真的产生 bug（单元测试抓到过一次：`Infinity > 0` 在 Python 为真、
在 JS 里 `Number.isFinite(Infinity)` 为假，导致伪造的重复分组被接受）：

  * `Number.isFinite`   —— 只认数字本身，不认 `'60'` 这种字符串；
  * `Number.isInteger`  —— 是数字且没有小数部分（`60.0` 算，`60.5` 不算）；
  * `Number.isSafeInteger` —— 再加上 ±2^53-1 的范围；
  * `str.length`        —— 计的是 **UTF-16 码元**，不是 Python 的码点；
  * 布尔值是数字的陷阱  —— Python 的 `isinstance(True, int)` 为真，JS 里 `true` 不是数字。
"""

from __future__ import annotations

import time

from ..jsjson import js_number

__all__ = [
    "js_len",
    "truncate_js",
    "is_finite_number",
    "is_integer_number",
    "is_safe_integer",
    "now_ms",
]

SAFE_INTEGER_MAX = 9007199254740991  # 2^53 - 1


def js_len(value: str) -> int:
    """字符串的 UTF-16 码元长度，等价于 JS 的 `str.length`。

    星形符号（U+1F4A1 之类）在 JS 里算 2 个长度，Python 算 1 个；
    标题 1–160、备注 5000 的上限都按 JS 的算法核对，否则同一个标题
    在两边会一边通过一边报错。
    """
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def truncate_js(value: str, limit: int) -> str:
    """按 UTF-16 码元截断，但不在代理对中间切开（避免产生孤立代理）。"""
    if js_len(value) <= limit:
        return value
    used = 0
    keep: list[str] = []
    for char in value:
        width = 2 if ord(char) > 0xFFFF else 1
        if used + width > limit:
            break
        used += width
        keep.append(char)
    return "".join(keep)


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def is_finite_number(value) -> bool:
    """等价于 JS 的 `Number.isFinite`：必须是数字本身，不接受数字字符串。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and _finite(value)


def is_integer_number(value) -> bool:
    """等价于 JS 的 `Number.isInteger`：是数字本身且没有小数部分。"""
    if not is_finite_number(value):
        return False
    return not isinstance(value, float) or value.is_integer()


def is_safe_integer(value) -> bool:
    """等价于 JS 的 `Number.isSafeInteger`。"""
    if not is_integer_number(value):
        return False
    return abs(int(value)) <= SAFE_INTEGER_MAX


def now_ms() -> int:
    """`Date.now()`：本地时钟的 Unix 毫秒数。"""
    return int(time.time() * 1000)


# `js_number` 由调用方在需要「与 JSON.stringify 相同的数字输出」时使用；
# 这里保留引用，表明它属于本模块的对外契约。
_ = js_number
