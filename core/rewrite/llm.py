"""LLM 调用封装。"""
from __future__ import annotations

import os

from openai import OpenAI

from ..errors import InputError, ServiceError


def chat(
    system_prompt: str,
    user_prompt: str,
    *,
    api_key: str | None = None,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
    temperature: float = 0.3,
    fake: bool = False,
) -> str:
    """薄 LLM 调用。fake=True 时返回占位文本，不花钱。"""
    if fake:
        return (
            f"[FAKE OUTPUT] 这是假 LLM 输出占位。\n\n"
            f"system 长度: {len(system_prompt)}\n"
            f"user 长度: {len(user_prompt)}"
        )

    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise InputError("DEEPSEEK_API_KEY 未设置（请传入 api_key 参数或设置环境变量）")

    client = OpenAI(api_key=key, base_url=base_url)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
        )
    except Exception as e:
        # 简单分类：含 401/403/429 且是鉴权/配额类的归 InputError，其他归 ServiceError
        msg = str(e)
        if any(code in msg for code in ("401", "403", "invalid_api_key", "authentication")):
            raise InputError(f"LLM API Key 无效或无权限: {e}") from e
        raise ServiceError(f"LLM 服务调用失败: {e}") from e
    return resp.choices[0].message.content
