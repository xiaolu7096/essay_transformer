"""core 异常 → 用户可懂的三分类失败（US3，research D5）。

口径：
- 用户可见 message 只能是本模块固定模板，绝不回显异常原文（防泄露密钥/路径）；
- core 目前把 401/403 和 key 缺失都抛 InputError，这里按关键词重判为 service；
- 原始异常仅经 sanitize_for_log 后进 warning 日志。
"""

from __future__ import annotations

import re

from core.errors import ETError, InputError, ServiceError

# code → 固定中文文案（与 contracts/api-tasks.md §6 逐字对齐）
MESSAGES: dict[str, str] = {
    "invalid_source": "论文链接或文件无法识别，请使用合法的 arXiv 链接或有效的 PDF 文件后重新提交",
    "scan_without_text": "该 PDF 没有可提取的文本层（可能是纯扫描件），请更换文件",
    "key_missing": "服务密钥未配置，请先到密钥设置填写后重试",
    "key_rejected": "外部服务拒绝了密钥（缺失、错误或已失效），请到密钥设置检查对应密钥后重试",
    "service_unavailable": "外部解析或模型服务暂时不可用（超时或服务故障），请稍后重试",
    "interrupted_restart": "服务曾重启，该任务被中断，请重新提交",
    "internal_error": "任务因系统内部问题终止，请联系维护者",
}

# InputError 重判为 service 类问题的关键词
_KEY_MISSING_HINTS = ("未设置",)
_KEY_REJECTED_HINTS = ("鉴权失败", "API Key 无效", "无权限")
# 无文本层（纯扫描件）
_SCAN_HINTS = ("扫描", "缺少文本层", "没有任何可提取的文本", "没有可提取的文本")

_BEARER_RE = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_OPENAI_KEY_RE = re.compile(r"sk-[A-Za-z0-9]+")
# 连续 24 位以上十六进制 / 常规 base64 长串
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_=-]{24,}")

_MASK = "***"


def classify_error(exc: BaseException) -> tuple[str, str, str]:
    """返回 (category, code, 用户可见固定文案)。"""
    text = str(exc)

    if isinstance(exc, InputError):
        if any(h in text for h in _KEY_MISSING_HINTS):
            return "service", "key_missing", MESSAGES["key_missing"]
        if any(h in text for h in _KEY_REJECTED_HINTS):
            return "service", "key_rejected", MESSAGES["key_rejected"]
        if any(h in text for h in _SCAN_HINTS):
            return "input", "scan_without_text", MESSAGES["scan_without_text"]
        return "input", "invalid_source", MESSAGES["invalid_source"]

    if isinstance(exc, ServiceError):
        return "service", "service_unavailable", MESSAGES["service_unavailable"]

    # ETError 直接子类之外的任意异常（含 ValueError/RuntimeError/OSError…）
    if isinstance(exc, ETError):
        return "system", "internal_error", MESSAGES["internal_error"]

    return "system", "internal_error", MESSAGES["internal_error"]


def sanitize_for_log(text: str) -> str:
    """日志脱敏：Bearer 令牌、sk- 密钥、24+ 位长串一律打码。"""
    if not isinstance(text, str):
        text = repr(text)
    out = _BEARER_RE.sub(f"Bearer {_MASK}", text)
    out = _OPENAI_KEY_RE.sub(_MASK, out)
    out = _LONG_TOKEN_RE.sub(_MASK, out)
    return out
