"""LLM 客户端抽象与 DeepSeek 实现（T005，research R3/R4/R16）。

- :class:`LLMClient` 是业务模块（planner/rewrite/glossary/worked_example）
  唯一允许依赖的接口；测试注入 Fake，核心包内只有 CLI 适配层构造真实实现。
- :class:`DeepSeekClient` 走官方 openai SDK + ``base_url`` 指向 DeepSeek，
  固定模型 ``deepseek-flash``、``extra_body={"thinking": {"type": "disabled"}}``
  关闭思考模式、JSON 输出模式（research R3）；自带超时、有限重试与 429
  指数退避。

离线约束：本模块不允许在导入期发起任何网络请求；重试退避的 sleep 可注入，
任何真实调用只允许出现在 Phase 7 人工任务。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Protocol, runtime_checkable

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from . import config
from .errors import LLMError, ServiceTimeoutError
from .llm_schema import schema_directive_for_kind, validate_llm_output

__all__ = ["LLMClient", "DeepSeekClient", "SCHEMA_KINDS"]

logger = logging.getLogger(__name__)

#: llm-output.schema.json 的四类根文档标识
SCHEMA_KINDS = ("planner", "glossary_draft", "section_rewrite", "worked_example")


@runtime_checkable
class LLMClient(Protocol):
    """业务模块依赖的 LLM 协议：入参 system/user 文本与 schema_kind，返回 dict。

    实现方必须开启 JSON 输出模式，返回值为已解析的 JSON 对象（dict）；
    失败抛 :class:`~essay_transformer.errors.EssayTransformerError` 子类。
    """

    def complete_json(self, system: str, user: str, schema_kind: str) -> dict[str, Any]:
        ...


class DeepSeekClient:
    """DeepSeek（OpenAI 兼容端点）真实实现。

    构造参数全部可注入是为离线测试保留接缝；业务代码不应直接构造本类，
    只有 ``cli.py`` 适配层可以。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = config.DEEPSEEK_MODEL,
        base_url: str = config.DEEPSEEK_BASE_URL,
        timeout: float = config.DEEPSEEK_TIMEOUT_SECONDS,
        max_retries: int = config.DEEPSEEK_MAX_RETRIES,
        backoff_base: float = config.DEEPSEEK_RETRY_BACKOFF_SECONDS,
        max_tokens: int = config.DEEPSEEK_MAX_TOKENS,
        sleep: Callable[[float], None] = time.sleep,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.max_tokens = max_tokens
        self._sleep = sleep
        if client is not None:
            # 测试接缝：直接注入 openai 兼容对象，不触达凭据/网络
            self._client = client
        else:
            # 仅真实运行路径：构造时即读取凭据，缺失抛 CredentialsError（退出码 3）
            key = api_key or config.require_deepseek_api_key()
            # SDK 自带 max_retries 关闭：重试策略（含退避秒数）由本类显式实现
            self._client = OpenAI(
                api_key=key,
                base_url=base_url,
                timeout=timeout,
                max_retries=0,
            )

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def complete_json(self, system: str, user: str, schema_kind: str) -> dict[str, Any]:
        """发起一次 JSON 模式对话，失败按有限重试/退避策略处理。

        - 超时/连接错误重试耗尽 → :class:`ServiceTimeoutError`（退出码 1）
        - 429/5xx/非法 JSON 重试耗尽或遇到不可重试 4xx →
          :class:`LLMError`（退出码 1）
        """
        if schema_kind not in SCHEMA_KINDS:
            raise LLMError(
                f"未知 schema_kind={schema_kind!r}，允许值：{', '.join(SCHEMA_KINDS)}"
            )

        messages = [
            {"role": "system", "content": system},
            # 契约 schema 随每次首调注入：json_object 模式不约束字段结构，
            # 真实模型（T040 首跑发现）会自造 section_id/goal、object→array
            {"role": "user", "content": user + "\n\n" + schema_directive_for_kind(schema_kind)},
        ]
        last_exc: Exception | None = None
        last_bad_content: str | None = None

        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                delay = self.backoff_base * (2 ** (attempt - 1))
                logger.warning(
                    "DeepSeek 第 %d 次重试（%.1fs 后），上次错误：%s",
                    attempt,
                    delay,
                    last_exc,
                )
                self._sleep(delay)
            last_bad_content = None
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    max_tokens=self.max_tokens,
                    extra_body=config.DEEPSEEK_EXTRA_BODY,
                )
                content = response.choices[0].message.content
                if not content:
                    raise LLMError("DeepSeek 返回空内容，无法解析 JSON")
                last_bad_content = content
                data = json.loads(content)
                if not isinstance(data, dict):
                    raise LLMError(f"DeepSeek 返回的 JSON 根不是对象：{type(data).__name__}")
                # L2：完整 JSON Schema 校验（四类 schema_kind），不符按服务
                # 错误在本方法内有限重试，耗尽抛 LLMError（退出码 1）
                validate_llm_output(data, schema_kind)
                return data
            except APITimeoutError as exc:
                last_exc = exc
            except APIConnectionError as exc:
                last_exc = exc
            except InternalServerError as exc:  # 5xx
                last_exc = LLMError(f"DeepSeek 服务端错误（5xx）：{exc}")
            except RateLimitError as exc:  # 429
                last_exc = LLMError(f"DeepSeek 限流（429）：{exc}")
            except APIStatusError as exc:  # 其余 4xx：不可重试
                raise LLMError(f"DeepSeek 请求被拒绝（HTTP {exc.status_code}）：{exc}") from exc
            except (json.JSONDecodeError, KeyError, IndexError) as exc:
                # 契约：根文档不可解析/结构不符即按服务错误处理（有限重试）
                last_exc = LLMError(f"DeepSeek 返回不符合 JSON 输出契约：{exc}")
            except LLMError as exc:
                # 空内容/JSON 根非对象/JSON Schema 校验不符：同为可重试服务错误。
                # 注意：4xx 分支在其自身 handler 内 raise 的 LLMError 不会被
                # 兄弟 except 捕获，仍保持"不可重试"语义。
                last_exc = exc
            # 错误回喂（T040 首跑修复）：把模型上一轮的坏输出与具体校验问题
            # 追加进对话，下一轮让它定点修正，而不是拿相同输入盲目重试。
            if attempt < self.max_retries:
                if last_bad_content is not None:
                    messages.append({"role": "assistant", "content": last_bad_content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "你上一次的输出未通过校验，错误如下：\n"
                            f"{last_exc}\n\n"
                            "请对照输出契约修正后，重新给出**完整的** JSON 对象"
                            "（不要省略、不要解释、不要输出代码块围栏之外的文字）。"
                        ),
                    }
                )

        if isinstance(last_exc, (APITimeoutError, APIConnectionError)):
            raise ServiceTimeoutError(
                f"DeepSeek 请求在 {self.max_retries + 1} 次尝试后仍未成功"
                f"（超时或无法连接）：{last_exc}"
            ) from last_exc
        raise LLMError(
            f"DeepSeek 请求在 {self.max_retries + 1} 次尝试后仍失败：{last_exc}"
        ) from last_exc
