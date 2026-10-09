"""第三方密钥的唯一加解密口径与展示掩码（宪法原则四）。

- Fernet（AES-128-CBC + HMAC）对称加密；主密钥来自 settings.fernet_key
  （仅 WEB_MASTER_KEY，fake 模式缺省时为进程内临时密钥）。
- 本模块不直接读环境、不持有全局密钥：密钥字节由调用方显式传入，
  便于测试与将来替换。
- 任何函数不得 print/logger 明文密钥。
"""

from __future__ import annotations

from cryptography.fernet import Fernet

_MASK_PREFIX = "••••"


def encrypt_secret(plaintext: str, key_bytes: bytes) -> str:
    """加密密钥原文，返回 Fernet token 字符串。"""
    return Fernet(key_bytes).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str, key_bytes: bytes) -> str:
    """解密 Fernet token；密钥错误/内容被篡改时抛 cryptography InvalidToken。"""
    return Fernet(key_bytes).decrypt(token.encode("ascii")).decode("utf-8")


def mask_secret(plaintext: str) -> str:
    """生成展示掩码：固定前缀 + 明文末 4 位；不足 4 位整体掩码。"""
    if len(plaintext) < 4:
        return _MASK_PREFIX
    return _MASK_PREFIX + plaintext[-4:]
