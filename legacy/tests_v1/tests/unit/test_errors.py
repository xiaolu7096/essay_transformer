"""统一异常体系与 error_type 确定性映射测试（CP-1 F3，T013，data-model.md §9）。

硬约束：

- error_type 只允许 data-model.md §9 六值枚举：
  input/credentials/service_timeout/parse/llm/qa，不存在 "runtime" 兜底；
- 业务异常按自身 error_type 归类；未知异常按 failed_stage 确定性映射；
- 退出码 0/1/2/3 与 contracts/cli.md 退出码表一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from essay_transformer.errors import (
    ERROR_TYPES,
    CredentialsError,
    EssayTransformerError,
    GlossaryNotConfirmedError,
    InputError,
    LLMError,
    ParseServiceError,
    QAFailedError,
    ServiceTimeoutError,
    default_error_type_for_stage,
    error_type_for,
    exit_code_for,
)
from essay_transformer.state import STAGES, JobState

#: 未知异常（无 error_type 属性）按失败阶段的确定性映射
STAGE_FALLBACK = {
    "ingested": "input",
    "parsed": "parse",
    "normalized": "parse",
    "glossary_drafted": "llm",
    "glossary_confirmed": "input",
    "planned": "llm",
    "rewritten": "llm",
    "example_verified": "llm",
    "assembled": "llm",
    "qa_passed": "qa",
    "done": "qa",
}


def test_error_types_are_exactly_the_six_value_enum() -> None:
    assert set(ERROR_TYPES) == {
        "input",
        "credentials",
        "service_timeout",
        "parse",
        "llm",
        "qa",
    }


@pytest.mark.parametrize("stage", STAGES)
def test_unknown_exception_maps_deterministically_by_stage(stage: str) -> None:
    etype = error_type_for(RuntimeError("unexpected boom"), stage)
    assert etype == STAGE_FALLBACK[stage]
    assert etype in ERROR_TYPES
    assert default_error_type_for_stage(stage) == STAGE_FALLBACK[stage]


@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_maps_to_a_known_enum_value(stage: str) -> None:
    assert default_error_type_for_stage(stage) in ERROR_TYPES


def test_business_exception_keeps_its_own_error_type_regardless_of_stage() -> None:
    # 业务异常类型优先于阶段默认值（如 ingest 阶段超时仍是 service_timeout）
    assert error_type_for(ServiceTimeoutError("slow"), "ingested") == "service_timeout"
    assert error_type_for(InputError("bad"), "rewritten") == "input"
    assert error_type_for(CredentialsError("no key"), "parsed") == "credentials"
    assert error_type_for(ParseServiceError("failed"), "normalized") == "parse"
    assert error_type_for(LLMError("5xx"), "planned") == "llm"
    assert error_type_for(QAFailedError("x"), "qa_passed") == "qa"


def test_no_runtime_value_anywhere_in_hierarchy() -> None:
    # 具体业务异常全部自带六值枚举；任何层级都不允许 "runtime"
    for exc in (
        InputError(),
        CredentialsError(),
        GlossaryNotConfirmedError(),
        ServiceTimeoutError(),
        ParseServiceError(),
        LLMError(),
        QAFailedError(),
    ):
        assert exc.error_type in ERROR_TYPES
        assert exc.error_type != "runtime"
    # 根类不预设具体六值（None），归类时按 failed_stage 确定性映射
    assert EssayTransformerError().error_type is None
    assert error_type_for(EssayTransformerError("boom"), "parsed") == "parse"


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (InputError("x"), 3),
        (CredentialsError("x"), 3),
        (GlossaryNotConfirmedError("x"), 3),
        (ServiceTimeoutError("x"), 1),
        (ParseServiceError("x"), 1),
        (LLMError("x"), 1),
        (QAFailedError("x"), 2),
        (RuntimeError("plain"), 1),
        (ValueError("plain"), 1),
    ],
)
def test_exit_code_mapping(exc: BaseException, code: int) -> None:
    assert exit_code_for(exc) == code


def test_mark_failed_uses_stage_default_for_unknown_exception(tmp_path: Path) -> None:
    state = JobState(tmp_path)
    record = state.mark_failed("rewritten", RuntimeError("kaboom"))
    assert record.error_type == "llm"
    assert "kaboom" in record.message
    assert "RuntimeError" in record.message  # 未知异常细节放 message


def test_mark_failed_prefers_business_error_type(tmp_path: Path) -> None:
    state = JobState(tmp_path)
    record = state.mark_failed("ingested", ServiceTimeoutError("download slow"))
    assert record.error_type == "service_timeout"
    assert record.failed_stage == "ingested"


def test_persisted_failed_record_never_contains_runtime(tmp_path: Path) -> None:
    state = JobState(tmp_path)
    state.mark_failed("normalized", RuntimeError("structure broken"))
    reloaded = JobState.load(tmp_path)
    assert reloaded.failed is not None
    assert reloaded.failed.error_type == "parse"
    assert reloaded.failed.error_type in ERROR_TYPES


def test_legacy_runtime_value_in_json_is_remapped_by_stage(tmp_path: Path) -> None:
    # 旧版 job_state.json 可能写出已废止的 "runtime"，读取时按阶段确定性纠正
    (tmp_path / "job_state.json").write_text(
        '{"paper_id": "p", "stage": "parsed", "updated_at": "2026-01-01T00:00:00",'
        ' "failed": {"failed_stage": "parsed", "error_type": "runtime",'
        ' "message": "old", "updated_at": "2026-01-01T00:00:00"}}',
        encoding="utf-8",
    )
    reloaded = JobState.load(tmp_path)
    assert reloaded.failed is not None
    assert reloaded.failed.error_type == "parse"
