"""DeepSeekClient 真实 API 接缝的离线测试（T040 首跑修复回归）。

覆盖三件真实首跑暴露、离线录制链路发现不了的事：

1. 每次请求显式携带 ``max_tokens``（防止端点默认上限截断长 plan JSON）；
2. 完整 JSON Schema 随首调 user 消息注入（模型自造字段名的根因）；
3. Schema/JSON 校验失败重试时，把坏输出与具体错误回喂进对话，
   而不是拿相同输入盲目重试。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from essay_transformer import config
from essay_transformer.errors import LLMError
from essay_transformer.llm import DeepSeekClient


# ---------------------------------------------------------------------------
# openai 兼容的最小假对象
# ---------------------------------------------------------------------------


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Completion:
    def __init__(self, content: str) -> None:
        self.choices = [type("_Choice", (), {"message": _Message(content)})()]


class _FakeCompletions:
    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Completion:
        self.calls.append(kwargs)
        if not self._contents:
            raise AssertionError("假客户端响应队列已耗尽")
        return _Completion(self._contents.pop(0))


class _FakeOpenAI:
    def __init__(self, contents: list[str]) -> None:
        self.completions = _FakeCompletions(contents)

    @property
    def chat(self) -> "_FakeOpenAI":
        return self


def _client(contents: list[str], **kwargs: Any) -> tuple[DeepSeekClient, _FakeOpenAI]:
    fake = _FakeOpenAI(contents)
    sleeps: list[float] = []
    client = DeepSeekClient(
        client=fake,
        api_key="sk-test",
        sleep=sleeps.append,
        **kwargs,
    )
    return client, fake


# ---------------------------------------------------------------------------
# 最小 schema 合法 planner 输出
# ---------------------------------------------------------------------------

_SLUGS = ["tldr", "intro", "conclusion", "related", "method", "experiments", "wrapup"]


def _valid_planner() -> dict[str, Any]:
    return {
        "schema_kind": "planner",
        "target_order": [
            {
                "target_slug": slug,
                "source_section_ids": [f"sec-{i + 1}"],
                "treatment": "tutorial" if slug == "method" else "brief",
            }
            for i, slug in enumerate(_SLUGS)
        ],
        "block_actions": {},
        "key_formulas": [
            {
                "block_id": "blk-0009",
                "latex": "Attention(Q,K,V)=...",
                "worked_example_required": True,
                "example_spec": {"tokens": 3, "steps": ["Q/K/V 投影", "softmax"]},
            }
        ],
    }


def _invalid_planner() -> dict[str, Any]:
    """真实首跑第 1 次响应的典型错误形态：自造字段 + object→array。"""
    valid = _valid_planner()
    valid["target_order"] = [
        {"section_id": "sec-1", "goal": "动机"} for _ in range(7)
    ]
    valid["block_actions"] = []
    return valid


def test_max_tokens_sent_explicitly_and_schema_injected_on_first_call() -> None:
    client, fake = _client([json.dumps(_valid_planner(), ensure_ascii=False)])

    result = client.complete_json("system", "user task", "planner")

    assert result["schema_kind"] == "planner"
    kwargs = fake.completions.calls[0]
    assert kwargs["max_tokens"] == config.DEEPSEEK_MAX_TOKENS
    assert kwargs["response_format"] == {"type": "json_object"}
    first_user = kwargs["messages"][1]["content"]
    assert "user task" in first_user
    assert "$defs/PlannerOutput" in first_user
    assert '"schema_kind"' in first_user  # 完整 schema 文本已注入


def test_schema_violation_retried_with_bad_output_and_errors_fed_back() -> None:
    client, fake = _client(
        [
            json.dumps(_invalid_planner(), ensure_ascii=False),
            json.dumps(_valid_planner(), ensure_ascii=False),
        ]
    )

    result = client.complete_json("sys", "task", "planner")

    assert result == _valid_planner()
    assert len(fake.completions.calls) == 2
    second_messages = fake.completions.calls[1]["messages"]
    # 上一轮坏输出以 assistant 身份回喂
    assistant_msgs = [m for m in second_messages if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    fed_back = json.loads(assistant_msgs[0]["content"])
    assert fed_back["block_actions"] == []
    # 修正指令含具体校验问题（自造字段名 + 类型错误都应出现在错误文本里）
    correction = second_messages[-1]
    assert correction["role"] == "user"
    assert "未通过校验" in correction["content"]
    assert "target_slug" in correction["content"]
    assert "block_actions" in correction["content"]


def test_truncated_json_retried_with_raw_content_fed_back() -> None:
    truncated = '{"schema_kind": "planner", "target_order": [{"target_slug": "tldr'
    client, fake = _client(
        [truncated, json.dumps(_valid_planner(), ensure_ascii=False)]
    )

    result = client.complete_json("sys", "task", "planner")

    assert result["schema_kind"] == "planner"
    second_messages = fake.completions.calls[1]["messages"]
    assert second_messages[-2] == {"role": "assistant", "content": truncated}
    assert "不符合 JSON 输出契约" in second_messages[-1]["content"]


def test_all_attempts_invalid_raises_llm_error_after_retries() -> None:
    bad = json.dumps(_invalid_planner(), ensure_ascii=False)
    client, fake = _client([bad, bad, bad])

    with pytest.raises(LLMError):
        client.complete_json("sys", "task", "planner")
    assert len(fake.completions.calls) == 3  # 首调 + 2 次重试


def test_unknown_schema_kind_rejected_without_api_call() -> None:
    client, fake = _client([])
    with pytest.raises(LLMError):
        client.complete_json("sys", "task", "not_a_kind")
    assert fake.completions.calls == []


def test_custom_max_tokens_forwarded() -> None:
    client, fake = _client(
        [json.dumps(_valid_planner(), ensure_ascii=False)],
        max_tokens=4096,
    )
    client.complete_json("sys", "task", "planner")
    assert fake.completions.calls[0]["max_tokens"] == 4096
