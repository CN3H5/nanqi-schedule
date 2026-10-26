"""test_jsjson.py —— 验证 `nanqi.jsjson.py` 与 JavaScript `JSON.stringify` 等价。

【为什么这个模块值得单独测】
`plans.json` 能不能被 Electron 版和本版互相读写，完全取决于这里的输出是否与
`JSON.stringify(state, null, 2)` **逐字符一致**；重复计划的确认令牌也用同一个
序列化器算 SHA-256。Python 标准库 `json.dumps` 有三处不一样（浮点写法、中文转义、
缩进细节），所以这里逐条钉住行为。
"""

from __future__ import annotations

import json

import pytest

from nanqi.jsjson import dumps, js_number, js_string


def test_primitives_match_json_stringify() -> None:
    """null / 布尔 / 字符串 / 数字的基本写法。"""
    assert dumps(None) == "null"
    assert dumps(True) == "true"
    assert dumps(False) == "false"
    assert dumps(0) == "0"
    assert dumps(-12) == "-12"
    assert dumps("re") == '"re"'


def test_numbers_follow_ecmascript_rules() -> None:
    """JS 不区分 1 与 1.0；NaN/Infinity 在 JSON 里变成 null。"""
    assert dumps(1.0) == "1"
    assert dumps(-0.0) == "0"
    assert dumps(1.5) == "1.5"
    assert dumps(1e21) == "1e+21"
    assert dumps(float("nan")) == "null"
    assert dumps(float("inf")) == "null"
    assert dumps(float("-inf")) == "null"
    assert js_number(60) == "60"
    assert js_number(60.0) == "60"


def test_strings_are_escaped_like_quote_json_string() -> None:
    """只转义必须转义的字符；非 ASCII 原样输出（不写成 \\uXXXX）。"""
    assert js_string("a\"b") == '"a\\"b"'
    assert js_string("a\\b") == '"a\\\\b"'
    assert js_string("a\nb\tc\rd") == '"a\\nb\\tc\\rd"'
    assert js_string("\x00\x1f") == '"\\u0000\\u001f"'
    # 中文与 emoji 保持原样，这正是不用 json.dumps(ensure_ascii=True) 的原因。
    assert dumps("南七日程") == '"南七日程"'
    assert dumps("完成 ✓") == '"完成 ✓"'
    assert dumps("🍊") == '"🍊"'


def test_containers_and_key_order() -> None:
    """键顺序 = dict 插入顺序（与 JS 对象的属性顺序一致）。"""
    value = {"b": 1, "a": 2, "c": {"d": [3, {"e": None}]}}
    assert dumps(value) == '{"b":1,"a":2,"c":{"d":[3,{"e":null}]}}'
    assert dumps({}) == "{}"
    assert dumps([]) == "[]"
    assert dumps({"only": {}}) == '{"only":{}}'
    assert dumps([[]]) == "[[]]"


def test_indented_output_is_character_identical_to_stringify_with_two_spaces() -> None:
    """`JSON.stringify(value, null, 2)` 的逐字符格式（空容器不展开）。"""
    value = {"version": 1, "tasks": [], "settings": {"sound": True, "nested": {"a": [1, 2]}}}
    expected = (
        "{\n"
        '  "version": 1,\n'
        '  "tasks": [],\n'
        '  "settings": {\n'
        '    "sound": true,\n'
        '    "nested": {\n'
        '      "a": [\n'
        "        1,\n"
        "        2\n"
        "      ]\n"
        "    }\n"
        "  }\n"
        "}"
    )
    assert dumps(value, indent=2) == expected
    assert dumps({}, indent=2) == "{}"
    assert dumps([], indent=2) == "[]"


def test_plans_file_head_matches_the_electron_writer() -> None:
    """真实数据文件的开头必须与 Electron 版写出的一模一样。

    这是冒烟测试里那条「plans.json 与 Electron 版格式一致」断言的单元级版本：
    一旦 `dumps` 改了缩进或分隔符，这里会立刻失败。
    """
    state = {
        "version": 1,
        "tasks": [
            {
                "id": "0f3d1c6e-1f5a-4a1e-9a3f-2b7c8d9e0f11",
                "title": "阶段复盘",
                "date": "2026-09-28",
                "time": "10:00",
                "duration": 60,
                "reminder": 10,
                "category": "work",
                "priority": "normal",
                "notes": "",
                "done": False,
                "createdAt": 1790000000000,
                "updatedAt": 1790000000000,
            }
        ],
        "settings": {},
        "history": [],
    }
    text = dumps(state, indent=2)
    assert text.startswith('{\n  "version": 1,\n  "tasks": [\n    {\n      "id": "')
    assert '\n      "title": "阶段复盘",\n' in text
    # 中文没有被转义成 \uXXXX（Electron 版也不会）
    assert "\\u" not in text
    # 与标准库读回来是同一份数据
    assert json.loads(text) == state


def test_round_trip_through_json_keeps_data_equal() -> None:
    """序列化 → json.loads 必须还原出相等的对象（含嵌套与 Unicode）。"""
    value = {
        "tasks": [{"title": "复习 线性代数", "notes": "第 3 章 · 习题 1–20", "done": False}],
        "settings": {"widget": {"bounds": {"x": -12, "y": 44, "width": 410, "height": 650}}},
        "history": [],
    }
    assert json.loads(dumps(value)) == value
    assert json.loads(dumps(value, indent=2)) == value


def test_indent_must_be_a_non_negative_int() -> None:
    for invalid in (-1, 1.5, "2", True):
        with pytest.raises(ValueError):
            dumps({"a": 1}, indent=invalid)


def test_unsupported_values_raise_instead_of_writing_something_wrong() -> None:
    """遇到无法按 JSON 表达的值要直接报错，而不是悄悄写出错误内容。"""
    with pytest.raises(TypeError):
        dumps({"a": object()})
    with pytest.raises(TypeError):
        dumps({1, 2})
