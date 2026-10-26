r"""compose.py —— 组装要发出去的邮件（纯函数，不碰网络）。

【为什么把「写邮件」和「发邮件」分开】
内容生成是最容易改的部分（改文案、加字段、改版式），而发送逻辑涉及网络、认证、
超时、错误码。两者分开后：想改邮件长什么样只需动这个文件，且**可以脱离网络单元
测试**——直接断言生成出来的主题与正文，不需要真的发一封信。

【为什么同时给纯文本和 HTML】
手机邮件客户端、以及很多校园邮箱的「纯文本模式」不渲染 HTML；只发 HTML 会出现
一封白底空邮件。所以用 `set_content(纯文本)` + `add_alternative(html)` 的标准结构，
让客户端自己挑。
"""

from __future__ import annotations

import html as _html
from datetime import date as _date
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

__all__ = ["build_message", "reminder_message", "digest_message", "test_message", "CATEGORY_LABELS"]

CATEGORY_LABELS = {"work": "工作 / 项目", "study": "学习 / 成长", "life": "生活 / 日常"}
PRIORITY_LABELS = {"high": "重要", "normal": "普通"}
_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _esc(text) -> str:
    return _html.escape(str(text or ""))


def build_message(config: dict, subject: str, text: str, html: str | None = None) -> EmailMessage:
    """按配置组装一封邮件（收件人、发件人、时间、Message-ID 都在这里定）。"""
    sender = config.get("sender") or config.get("user") or ""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(("南七日程", sender)) if sender else "南七日程"
    message["To"] = ", ".join(config.get("recipients") or [])
    message["Date"] = formatdate(localtime=True)
    domain = sender.split("@")[-1] if "@" in sender else "nanqi.local"
    message["Message-ID"] = make_msgid(domain=domain)
    message["X-Mailer"] = "nanqi_schedule"
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    return message


def _task_line(task: dict) -> str:
    parts = [f"{task.get('time', '')}", task.get("title", "（无标题）")]
    if task.get("category") in CATEGORY_LABELS:
        parts.append(CATEGORY_LABELS[task["category"]])
    if task.get("duration"):
        parts.append(f"{task['duration']} 分钟")
    if task.get("priority") == "high":
        parts.append("重要")
    return " · ".join(part for part in parts if part)


def _task_row_html(task: dict) -> str:
    meta = []
    if task.get("category") in CATEGORY_LABELS:
        meta.append(CATEGORY_LABELS[task["category"]])
    if task.get("duration"):
        meta.append(f"{task['duration']} 分钟")
    if task.get("priority") == "high":
        meta.append("重要")
    notes = f'<div style="color:#6b7280;font-size:12px;margin-top:2px">{_esc(task.get("notes"))}</div>' if task.get("notes") else ""
    return (
        '<tr>'
        f'<td style="padding:8px 10px;color:#44724e;font-weight:600;white-space:nowrap;vertical-align:top">{_esc(task.get("time"))}</td>'
        f'<td style="padding:8px 10px;vertical-align:top"><div style="font-weight:600;color:#1f2937">{_esc(task.get("title"))}</div>'
        f'<div style="color:#6b7280;font-size:12px">{" · ".join(meta)}</div>{notes}</td>'
        "</tr>"
    )


def _wrap_html(title: str, intro: str, body: str, footnote: str) -> str:
    """统一的邮件外壳（内联样式，邮件客户端对外部样式表支持很差）。"""
    return (
        '<div style="font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif;'
        'max-width:600px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden">'
        '<div style="background:#44724e;color:#fff;padding:18px 20px">'
        f'<div style="font-size:16px;font-weight:600">{_esc(title)}</div>'
        f'<div style="font-size:12px;opacity:.85;margin-top:4px">{_esc(intro)}</div></div>'
        f'<div style="padding:8px 10px 16px">{body}</div>'
        f'<div style="padding:12px 20px;border-top:1px solid #eef1ee;color:#9ca3af;font-size:12px">{_esc(footnote)}</div>'
        "</div>"
    )


def reminder_message(config: dict, task: dict, message_text: str = "") -> EmailMessage:
    """到期提醒邮件：一项计划的详情。"""
    when = f"{task.get('date', '')} {_WEEKDAYS[_weekday_index(task.get('date'))]} {task.get('time', '')}"
    subject = f"【南七日程】提醒：{task.get('title', '')}（{task.get('time', '')}）"
    lines = [
        f"计划：{task.get('title', '')}",
        f"时间：{when}",
        f"分类：{CATEGORY_LABELS.get(task.get('category'), task.get('category') or '')}",
        f"时长：{task.get('duration', '')} 分钟",
    ]
    if task.get("priority") == "high":
        lines.append("优先级：重要")
    if task.get("notes"):
        lines += ["", "备注：", str(task["notes"])]
    if message_text:
        lines += ["", message_text]
    text = "\n".join(lines)
    body = (
        '<table style="width:100%;border-collapse:collapse">' + _task_row_html(task) + "</table>"
        + (f'<p style="margin:14px 4px 0;color:#44724e;font-size:13px">{_esc(message_text)}</p>' if message_text else "")
    )
    html = _wrap_html("计划提醒", when, body, "这封邮件由「南七日程」按你的提醒设置发出。")
    return build_message(config, subject, text + "\n\n—— 南七日程", html)


def _weekday_index(date_text) -> int:
    try:
        year, month, day = (int(part) for part in str(date_text).split("-"))
        return _date(year, month, day).weekday()
    except (ValueError, TypeError):
        return 0


def digest_message(config: dict, tasks: list[dict], day: str, message_text: str = "") -> EmailMessage:
    """每日摘要：把某一天的待办按时间排好发出去。"""
    ordered = sorted(tasks, key=lambda task: (task.get("time", ""), task.get("title", "")))
    pending = [task for task in ordered if not task.get("done")]
    finished = [task for task in ordered if task.get("done")]
    weekday = _WEEKDAYS[_weekday_index(day)]
    if ordered:
        subject = f"【南七日程】{day} {weekday} · {len(pending)} 项待办"
    else:
        subject = f"【南七日程】{day} {weekday} · 今天没有计划"

    lines = [f"{day} {weekday}", ""]
    if pending:
        lines.append(f"待办（{len(pending)} 项）：")
        lines += [f"  {_task_line(task)}" for task in pending]
    else:
        lines.append("今天没有待办计划。")
    if finished:
        lines += ["", f"已完成（{len(finished)} 项）："]
        lines += [f"  ✓ {_task_line(task)}" for task in finished]
    if message_text:
        lines += ["", message_text]

    rows = "".join(_task_row_html(task) for task in pending)
    done_rows = "".join(
        f'<div style="padding:6px 10px;color:#9ca3af;text-decoration:line-through;font-size:13px">✓ {_esc(task.get("title"))}</div>'
        for task in finished
    )
    body = (
        (f'<table style="width:100%;border-collapse:collapse">{rows}</table>' if rows
         else '<p style="margin:14px 10px;color:#6b7280">今天没有待办计划，享受一下空白。</p>')
        + (f'<div style="margin-top:14px;border-top:1px solid #eef1ee;padding-top:8px">{done_rows}</div>' if done_rows else "")
        + (f'<p style="margin:14px 10px 0;color:#44724e;font-size:13px">{_esc(message_text)}</p>' if message_text else "")
    )
    html = _wrap_html(f"{day} {weekday}", f"{len(pending)} 项待办 · {len(finished)} 项已完成", body, "这封邮件由「南七日程」每日摘要发出。")
    return build_message(config, subject, "\n".join(lines) + "\n\n—— 南七日程", html)


def test_message(config: dict) -> EmailMessage:
    """测试邮件：说明配置成功，并回显关键参数，方便用户核对。"""
    lines = [
        "这是一封测试邮件。",
        "",
        "如果你收到了它，说明南七日程的邮件设置是正确的。",
        "",
        f"发信服务器：{config.get('host')}:{config.get('port')}（{config.get('security')}）",
        f"账号：{config.get('user')}",
        f"收件人：{', '.join(config.get('recipients') or [])}",
        "",
        "—— 南七日程",
    ]
    html = _wrap_html(
        "邮件设置已生效",
        "来自南七日程的测试邮件",
        '<p style="margin:14px 10px;color:#1f2937;line-height:1.7">这是一封测试邮件。<br>'
        "如果你收到了它，说明南七日程的邮件设置是正确的。</p>"
        f'<div style="margin:14px 10px;padding:12px;background:#f6f8f6;border-radius:8px;font-size:13px;color:#4b5563">'
        f"发信服务器：{_esc(config.get('host'))}:{_esc(config.get('port'))}（{_esc(config.get('security'))}）<br>"
        f"收件人：{_esc(', '.join(config.get('recipients') or []))}</div>",
        "这封邮件由「南七日程」的「发送测试邮件」按钮发出。",
    )
    return build_message(config, "【南七日程】测试邮件 · 设置已生效", "\n".join(lines), html)
