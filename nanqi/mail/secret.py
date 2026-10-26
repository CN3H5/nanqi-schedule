r"""secret.py —— 邮件授权码的本地加密（Windows DPAPI）。

【为什么不能明文存】
SMTP 的「授权码」等价于邮箱密码：拿到就能登录发信、读信。所以它
**绝不能**写进 `plans.json`——那个文件是会被导出、导入、复制到别的电脑的，
一旦带上凭据就等于泄漏。

【为什么用 DPAPI 而不是自己加密】
自己实现加密必然要把密钥存在某处，等于把锁和钥匙放在同一个抽屉里。
Windows 的 DPAPI（`CryptProtectData` / `CryptUnprotectData`）把密钥交给操作系统
保管，并绑定到**当前 Windows 用户账户**：密文文件拷到别的机器、或换成别的用户，
都解不开。这就是我们要的效果，而且不需要任何第三方依赖。

【失败时怎么办】
如果 DPAPI 不可用（非 Windows、或系统调用失败），`available()` 返回 False，
上层会**拒绝保存授权码**并给出明确提示，而不是退化成明文保存——
宁可每次手输，也不要悄悄把密码写成明文。
"""

from __future__ import annotations

import ctypes
import os
import sys

__all__ = ["available", "protect", "unprotect", "SecretError"]

# 不让系统弹任何界面（否则无控制台的 exe 会卡在一个看不见的对话框上）。
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_ENTROPY = b"nanqi_schedule.mail.v1"


class SecretError(RuntimeError):
    """加密/解密失败（包含 DPAPI 不可用的情况）。"""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    """当前环境是否提供 DPAPI（只做平台判断，不实际调用）。"""
    return os.name == "nt"


def _blob(data: bytes):
    """把 bytes 包成 DATA_BLOB；同时返回底层缓冲区以免被 GC 回收。"""
    buffer = ctypes.create_string_buffer(data, max(len(data), 1))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def protect(text: str) -> bytes:
    """把明文加密成只能被当前 Windows 用户解开的密文。"""
    if not available():
        raise SecretError("当前系统不支持安全保存授权码（仅 Windows 可用）")
    payload = text.encode("utf-8")
    data_in, _keep_in = _blob(payload)
    entropy, _keep_ent = _blob(_ENTROPY)
    data_out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(data_in),
        "nanqi_schedule",
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(data_out),
    )
    if not ok:
        raise SecretError(f"加密授权码失败（Windows 错误码 {ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(data_out.pbData)


def unprotect(blob: bytes) -> str:
    """解密 `protect()` 的结果；换用户或换机器会失败。"""
    if not available():
        raise SecretError("当前系统不支持安全保存授权码（仅 Windows 可用）")
    if not blob:
        raise SecretError("没有已保存的授权码")
    data_in, _keep_in = _blob(blob)
    entropy, _keep_ent = _blob(_ENTROPY)
    data_out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(data_in),
        None,
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(data_out),
    )
    if not ok:
        raise SecretError(
            "无法解开已保存的授权码（换过 Windows 账户或复制自其它电脑）。请重新输入。"
        )
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData).decode("utf-8", "replace")
    finally:
        ctypes.windll.kernel32.LocalFree(data_out.pbData)
