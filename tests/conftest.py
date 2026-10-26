"""conftest.py —— 让 pytest 在 orange-schedule 目录下直接 `import nanqi.*`。

即使 pytest 已经默认把 rootdir 放进 `sys.path`（rootdir 里没有 `__init__.py` 时），
这里仍然显式插入一次项目根目录，保证：

    & '...python.exe' -m pytest tests/test_core.py -q

在「从 orange-schedule 目录运行」和「从仓库根目录运行」两种方式下都能找到 `app` 包。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
