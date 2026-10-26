r"""mail —— 邮件功能（SMTP 发送 + 每日摘要；收信见 README 的路线图）。

    config.py    邮件配置的读写与校验（mail.json）
    secret.py    授权码的本地加密（Windows DPAPI）
    compose.py   组装邮件内容（纯函数，可离线测试）
    sender.py    走 SMTP 真正发出去，并把错误翻译成中文
    service.py   后台线程调度：到期提醒、每日摘要、测试邮件

【设计上的一条硬线】
计划数据（`plans.json`）、邮件配置（`mail.json`）、邮件凭据（`mail.secret`）
三份文件分开存。原因是 `plans.json` 会被导出/导入/复制到别的电脑，而另外两份
不该跟着走——尤其是凭据。界面上永远拿不到密码，只能看到「有没有存过」。
"""

from __future__ import annotations

from .config import DEFAULT_CONFIG, PRESETS, MailConfigStore, normalize
from .sender import MailError
from .service import MailService
from .secret import SecretError

__all__ = [
    "MailService",
    "MailConfigStore",
    "MailError",
    "SecretError",
    "DEFAULT_CONFIG",
    "PRESETS",
    "normalize",
]
