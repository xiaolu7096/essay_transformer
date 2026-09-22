"""LLM 输出 JSON Schema 校验测试（L2 闭环 + 契约漂移守门）。

- 入包 schema 必须与 specs 契约逐字节一致（漂移即失败）；
- 四类 schema_kind 的最小合法样本通过、典型违约被拒；
- DeepSeekClient 对不符 schema 的返回按服务错误重试，耗尽抛 LLMError。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from essay_transformer.errors import LLMError
from essay_transformer.llm import DeepSeekClient
from essay_transformer.llm_schema import SCHEMA_PATH, load_schema, validate_llm_output

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = (
    REPO_ROOT
    / "specs"
    / "001-paper-readability"
    / "contracts"
    / "llm-output.schema.json"
)


def test_packaged_schema_matches_contract_byte_for_byte() -> None:
    assert SCHEMA_PATH.is_file(), "essay_transformer/llm_output.schema.json 缺失"
    assert SCHEMA_PATH.read_bytes() == CONTRACT_SCHEMA.read_bytes(), (
        "入包 schema 与 specs/001-paper-readability/contracts/llm-output.schema.json "
        "不一致：契约调整后必须同步入包"
    )
    load_schema.cache_clear()
    assert load_schema()["$defs"]["PlannerOutput"]["properties"]["schema_kind"]["const"] == "planner"


# ---------------------------------------------------------------------------
# 四类根文档：最小合法样本
# ---------------------------------------------------------------------------


def test_valid_planner_minimal() -> None:
    plan = {
        "schema_kind": "planner",
        "target_order": [
            {"target_slug": slug, "source_section_ids": ["sec-x"], "treatment": "brief"}
            for slug in ("tldr", "intro", "conclusion", "related", "method",
                         "experiments", "wrapup")
        ],
        "block_actions": {"blk-1": {"action": "narrate"}},
        "key_formulas": [
            {"block_id": "blk-9", "latex": "X=1", "worked_example_required": False}
        ],
    }
    validate_llm_output(plan, "planner")  # 不抛即通过


def test_valid_glossary_draft_minimal() -> None:
    validate_llm_output(
        {"schema_kind": "glossary_draft", "terms": []}, "glossary_draft"
    )


def test_valid_section_rewrite_supplement_and_factual_rules() -> None:
    # supplement：必须有 markdown+supplement_kind，source_block_ids 必须为空
    supplement = {
        "schema_kind": "section_rewrite",
        "target_slug": "method",
        "blocks": [
            {
                "kind": "supplement",
                "supplement_kind": "background",
                "markdown": "补充背景",
                "source_block_ids": [],
            },
            {
                "kind": "paragraph",
                "markdown": "正文",
                "source_block_ids": ["blk-1"],
            },
        ],
        "out_of_glossary": [],
    }
    validate_llm_output(supplement, "section_rewrite")


def test_valid_worked_example_minimal() -> None:
    # LLM 形态：只含 toy 设定与四步讲解；matrix/result 由系统计算，不在契约内
    doc = {
        "schema_kind": "worked_example",
        "formula_block_id": "blk-9",
        "is_toy_data": True,
        "disclaimer": "教学示意/非论文真实数据",
        "setup": {
            "tokens": ["思", "考", "力"],
            "d_k": 1,
            "Q": [[0]],
            "K": [[0]],
            "V": [[0]],
        },
        "steps": [
            {"name": n, "expr": "e", "explain_zh": "x"}
            for n in ("一", "二", "三", "四")
        ],
    }
    validate_llm_output(doc, "worked_example")


# ---------------------------------------------------------------------------
# 违约样本
# ---------------------------------------------------------------------------


def test_planner_target_order_must_have_exactly_seven() -> None:
    base = {
        "schema_kind": "planner",
        "target_order": [],
        "block_actions": {},
        "key_formulas": [
            {"block_id": "b", "latex": "x", "worked_example_required": False}
        ],
    }
    with pytest.raises(LLMError, match="target_order"):
        validate_llm_output(base, "planner")


def test_planner_rejects_extra_field() -> None:
    doc = {
        "schema_kind": "planner",
        "target_order": [
            {"target_slug": s, "source_section_ids": ["x"], "treatment": "brief"}
            for s in ("tldr", "intro", "conclusion", "related", "method",
                      "experiments", "wrapup")
        ],
        "block_actions": {},
        "key_formulas": [
            {"block_id": "b", "latex": "x", "worked_example_required": False}
        ],
        "unexpected": 1,
    }
    with pytest.raises(LLMError, match="契约外字段"):
        validate_llm_output(doc, "planner")


def test_section_rewrite_supplement_requires_markdown_and_empty_sources() -> None:
    # 缺 markdown
    bad1 = {
        "schema_kind": "section_rewrite",
        "target_slug": "intro",
        "blocks": [
            {"kind": "supplement", "supplement_kind": "analogy",
             "source_block_ids": []}
        ],
        "out_of_glossary": [],
    }
    with pytest.raises(LLMError):
        validate_llm_output(bad1, "section_rewrite")
    # supplement 携带 source id 违约（maxItems:0）
    bad2 = {
        "schema_kind": "section_rewrite",
        "target_slug": "intro",
        "blocks": [
            {"kind": "supplement", "supplement_kind": "analogy",
             "markdown": "x", "source_block_ids": ["blk-1"]}
        ],
        "out_of_glossary": [],
    }
    with pytest.raises(LLMError, match="source_block_ids"):
        validate_llm_output(bad2, "section_rewrite")
    # 非 supplement 事实块 source_block_ids minItems:1
    bad3 = {
        "schema_kind": "section_rewrite",
        "target_slug": "intro",
        "blocks": [{"kind": "paragraph", "markdown": "x", "source_block_ids": []}],
        "out_of_glossary": [],
    }
    with pytest.raises(LLMError, match="source_block_ids"):
        validate_llm_output(bad3, "section_rewrite")


def test_worked_example_const_fields_and_shapes() -> None:
    good = {
        "schema_kind": "worked_example",
        "formula_block_id": "blk-9",
        "is_toy_data": True,
        "disclaimer": "教学示意/非论文真实数据",
        "setup": {"tokens": ["a", "b", "c"], "d_k": 3,
                  "Q": [[0, 0, 0]], "K": [[0, 0, 0]], "V": [[0, 0, 0]]},
        "steps": [
            {"name": n, "expr": "e", "explain_zh": "x"}
            for n in "一二三四"
        ],
    }
    validate_llm_output(good, "worked_example")

    bad_disclaimer = {**good, "disclaimer": "随便写写"}
    with pytest.raises(LLMError, match="disclaimer"):
        validate_llm_output(bad_disclaimer, "worked_example")

    bad_toy = {**good, "is_toy_data": False}
    with pytest.raises(LLMError, match="is_toy_data"):
        validate_llm_output(bad_toy, "worked_example")

    # d_k minimum:1；steps 必须恰好 4
    bad_steps = {**good, "steps": good["steps"][:3]}
    with pytest.raises(LLMError, match="steps"):
        validate_llm_output(bad_steps, "worked_example")
    bad_steps5 = {**good, "steps": good["steps"] + [dict(good["steps"][-1])]}
    with pytest.raises(LLMError, match="steps"):
        validate_llm_output(bad_steps5, "worked_example")

    # 四步矩阵与 result 由系统确定性计算，LLM 不得提供（additionalProperties:false）
    bad_matrix = {
        **good,
        "steps": [
            {**s, "matrix": [[0, 0, 0]]} for s in good["steps"]
        ],
    }
    with pytest.raises(LLMError, match="契约外字段"):
        validate_llm_output(bad_matrix, "worked_example")
    bad_result = {**good, "result": [[0, 0, 0]]}
    with pytest.raises(LLMError, match="契约外字段"):
        validate_llm_output(bad_result, "worked_example")


def test_unknown_schema_kind_raises() -> None:
    with pytest.raises(LLMError, match="未知 schema_kind"):
        validate_llm_output({}, "nope")


# ---------------------------------------------------------------------------
# DeepSeekClient：schema 不符按服务错误重试（L2）
# ---------------------------------------------------------------------------


class _StubMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubChoice:
    def __init__(self, content: str) -> None:
        self.message = _StubMessage(content)


class _StubCompletions:
    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.calls = 0

    def create(self, **_kwargs):
        content = self._contents[min(self.calls, len(self._contents) - 1)]
        self.calls += 1
        return type("R", (), {"choices": [_StubChoice(content)]})()


class _StubClient:
    def __init__(self, contents: list[str]) -> None:
        self.chat = type("C", (), {"completions": _StubCompletions(contents)})()


def test_deepseek_client_retries_schema_invalid_then_succeeds() -> None:
    invalid = json.dumps({"schema_kind": "planner"}, ensure_ascii=False)
    valid = {
        "schema_kind": "planner",
        "target_order": [
            {"target_slug": s, "source_section_ids": ["x"], "treatment": "brief"}
            for s in ("tldr", "intro", "conclusion", "related", "method",
                      "experiments", "wrapup")
        ],
        "block_actions": {},
        "key_formulas": [
            {"block_id": "b", "latex": "x", "worked_example_required": False}
        ],
    }
    stub = _StubClient([invalid, json.dumps(valid, ensure_ascii=False)])
    client = DeepSeekClient(
        client=stub, max_retries=2, backoff_base=0.0, sleep=lambda _s: None
    )
    out = client.complete_json("sys", "user", "planner")
    assert out["schema_kind"] == "planner"
    assert stub.chat.completions.calls == 2  # 首次 schema 不符触发 1 次重试


def test_deepseek_client_raises_after_retries_on_persistent_invalid_schema() -> None:
    invalid = json.dumps({"schema_kind": "planner"}, ensure_ascii=False)
    stub = _StubClient([invalid])
    client = DeepSeekClient(
        client=stub, max_retries=2, backoff_base=0.0, sleep=lambda _s: None
    )
    with pytest.raises(LLMError, match="JSON Schema 契约"):
        client.complete_json("sys", "user", "planner")
    assert stub.chat.completions.calls == 3  # 首次 + 2 次重试
