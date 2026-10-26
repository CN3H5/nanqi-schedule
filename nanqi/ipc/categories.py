r"""categories.py —— 自定义分类的增删改（`save-category` / `delete-category`）。

【删除语义】
删除一个分类时，它名下的任务不能跟着丢：一律改派到**剩下的第一个分类**，
并清空便签里指向它的筛选。至少保留一个分类（没有分类，新建计划就无从归类）。

【新增分类的 id】
由后端生成（`c` + 8 位随机十六进制），不取自名称——中文名称没法直接做 id，
而 id 会写进每一项任务的 `category` 字段，必须稳定且唯一。
"""

from __future__ import annotations

import uuid

from ..core import clean_category, next_category_color
from ..core.categories import MAX_CATEGORIES
from .base import HandlerMixin
from .envelope import ok

__all__ = ["CategoryHandlers"]


class CategoryHandlers(HandlerMixin):
    """分类管理。"""

    def _handle_save_category(self, payload) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("分类格式不正确")
        categories = self.store.state["categories"]
        category_id = payload.get("id")

        if category_id:
            # 改名 / 换色：必须已存在
            existing = next((c for c in categories if c["id"] == category_id), None)
            if existing is None:
                raise ValueError("该分类已被删除")
            color = payload.get("color") or existing["color"]
            cleaned = clean_category({**existing, "name": payload.get("name", existing["name"]), "color": color})

            def mutate(next_state: dict) -> None:
                next_state["categories"] = [
                    cleaned if c["id"] == category_id else c for c in next_state["categories"]
                ]

            return ok(self.store.change(mutate))

        if len(categories) >= MAX_CATEGORIES:
            raise ValueError(f"分类最多 {MAX_CATEGORIES} 个")
        if not payload.get("color"):
            payload = {**payload, "color": next_category_color(categories)}
        cleaned = clean_category({**payload, "id": "c" + uuid.uuid4().hex[:8]}, [])

        def mutate(next_state: dict) -> None:
            next_state["categories"].append(cleaned)

        return ok(self.store.change(mutate))

    def _handle_delete_category(self, category_id) -> dict:
        categories = self.store.state["categories"]
        if not any(c["id"] == category_id for c in categories):
            raise ValueError("该分类不存在")
        if len(categories) <= 1:
            raise ValueError("至少保留一个分类")
        fallback = next(c["id"] for c in categories if c["id"] != category_id)

        def mutate(next_state: dict) -> None:
            next_state["categories"] = [
                c for c in next_state["categories"] if c["id"] != category_id
            ]
            for task in next_state["tasks"]:
                if task.get("category") == category_id:
                    task["category"] = fallback
            if next_state["settings"]["widget"].get("category") == category_id:
                next_state["settings"]["widget"]["category"] = "all"

        return ok(self.store.change(mutate))
