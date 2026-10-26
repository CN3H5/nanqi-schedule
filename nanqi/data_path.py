"""data_path.py —— 数据目录解析（移植自 src/data-path.cjs）。

Electron 通过 `app.getPath('userData')` 拿到「当前用户的应用数据目录」，
并允许 `NANQI_DATA_DIR` 覆盖。这里保持同样的优先级与规范化方式：
非空字符串覆盖优先，否则用默认目录，最后统一取绝对路径（相对路径也要能落盘）。
"""

from __future__ import annotations

import os

__all__ = ["resolve_data_path"]


def resolve_data_path(default_user_data: str, override=None) -> str:
    """返回绝对化的数据目录。

    * `override` 是去掉首尾空白后仍非空的字符串时优先使用；
    * 空字符串、纯空白、None 以及非字符串一律回退到默认目录；
    * 返回值一定是绝对路径，可直接用于建目录与写文件。
    """
    chosen = override if isinstance(override, str) and override.strip() else default_user_data
    return os.path.abspath(chosen)
