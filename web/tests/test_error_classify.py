"""T027：错误三分类器与日志脱敏（research D5 / 契约 api-tasks.md §6）。"""

from __future__ import annotations

import pytest

from core.errors import InputError, ServiceError
from web.app.runner.errors import classify_error, sanitize_for_log

# 与契约逐字对齐的固定文案
TEMPLATES = {
    "invalid_source": "论文链接或文件无法识别，请使用合法的 arXiv 链接或有效的 PDF 文件后重新提交",
    "scan_without_text": "该 PDF 没有可提取的文本层（可能是纯扫描件），请更换文件",
    "key_missing": "服务密钥未配置，请先到密钥设置填写后重试",
    "key_rejected": "外部服务拒绝了密钥（缺失、错误或已失效），请到密钥设置检查对应密钥后重试",
    "service_unavailable": "外部解析或模型服务暂时不可用（超时或服务故障），请稍后重试",
    "internal_error": "任务因系统内部问题终止，请联系维护者",
}


@pytest.mark.parametrize(
    "exc, expected",
    [
        # 普通输入问题（不支持的链接/坏 PDF）
        (InputError("不支持的链接类型：ftp://x"), ("input", "invalid_source")),
        (InputError("文件不是合法 PDF（缺少 %PDF 文件头）"), ("input", "invalid_source")),
        # 扫描件：无文本层
        (
            InputError("解析结果中没有任何可提取的文本内容：该 PDF 疑似纯扫描图片件、缺少文本层。"),
            ("input", "scan_without_text"),
        ),
        # core 把密钥缺失也抛 InputError，必须重判为 service
        (InputError("mineru_api_key 未设置（fake 模式可省略）"), ("service", "key_missing")),
        (
            InputError("DEEPSEEK_API_KEY 未设置（请传入 api_key 参数或设置环境变量）"),
            ("service", "key_missing"),
        ),
        # 鉴权类关键词 → key_rejected
        (
            InputError("MinerU 鉴权失败：HTTP 401，API Key 缺失、错误或已失效。"),
            ("service", "key_rejected"),
        ),
        (InputError("LLM API Key 无效或无权限: boom"), ("service", "key_rejected")),
        # 外部服务故障/超时
        (ServiceError("上传 PDF 到 MinerU 超时/连接失败"), ("service", "service_unavailable")),
        (ServiceError("LLM 服务调用失败"), ("service", "service_unavailable")),
        # 未知异常一律 internal_error，绝不回显原文
        (ValueError("secret boom xyz"), ("system", "internal_error")),
        (RuntimeError("disk full /tmp/xxx"), ("system", "internal_error")),
    ],
)
def test_classify_mapping(exc, expected):
    category, code, message = classify_error(exc)
    assert (category, code) == expected
    assert message == TEMPLATES[code]


def test_internal_error_message_never_leaks_detail():
    category, code, message = classify_error(
        ValueError("secret boom xyz Bearer abcdef1234567890ABCDEF1234567890")
    )
    assert category == "system"
    assert "secret" not in message
    assert "Bearer" not in message
    assert "abcdef" not in message


def test_sanitize_bearer_and_openai_style_key():
    raw = "header Authorization: Bearer sk-abcdefABCDEF1234567890xyz tail"
    out = sanitize_for_log(raw)
    assert "abcdefABCDEF1234567890xyz" not in out
    assert "sk-abcdefABCDEF" not in out
    assert "***" in out


def test_sanitize_long_hex_run():
    raw = f"token={ 'a' * 40 } end"
    out = sanitize_for_log(raw)
    assert "a" * 40 not in out
    assert "***" in out


def test_sanitize_keeps_normal_text():
    raw = "MinerU 服务端错误：HTTP 502"
    assert sanitize_for_log(raw) == raw
