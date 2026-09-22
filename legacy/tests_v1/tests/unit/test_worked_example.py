"""T024：手算示例 numpy 独立复算闸门、带 diff 重试与显式缺失留痕。"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from essay_transformer.errors import LLMError
from essay_transformer.worked_example import (
    REQUIRED_STEP_COUNT,
    TOY_DISCLAIMER,
    TOLERANCE,
    WORKED_EXAMPLE_FILENAME,
    WORKED_EXAMPLE_ID,
    _assemble_numerics,
    run_worked_example,
    verify_worked_example,
)

TOKENS = ["思考", "是", "力量"]
DK = 3
Q = np.array([[1, 0, 1], [0, 1, 1], [1, 1, 0]], dtype=float)
K = np.array([[1, 0, 0], [0, 1, 0], [1, 1, 1]], dtype=float)
V = np.array([[1, 2, 0], [0, 1, 1], [1, 0, 1]], dtype=float)


def _pipeline(q=Q, k=K, v=V, dk=DK):
    scores = q @ k.T
    scaled = scores / math.sqrt(dk)
    shifted = scaled - scaled.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    weights = exp / exp.sum(axis=1, keepdims=True)
    output = weights @ v
    return scores, scaled, weights, output


def _golden_response(*, block_id="blk-4") -> dict:
    scores, scaled, weights, output = _pipeline()
    names = ["计算相关性分数", "缩放", "逐行 softmax 得注意力权重", "加权求和"]
    exprs = ["Q K^T", "/ sqrt(d_k)", "softmax(...)", "weights · V"]
    matrices = [scores, scaled, weights, output]
    explains = [
        "每个 token 与每个 token 的相关性。",
        "除以根号 d_k 防止点积过大。",
        "每行归一化为权重，和为 1。",
        "用权重对 V 加权求和得到输出。",
    ]
    return {
        "schema_kind": "worked_example",
        "formula_block_id": block_id,
        "is_toy_data": True,
        "disclaimer": TOY_DISCLAIMER,
        "setup": {"tokens": list(TOKENS), "d_k": DK,
                  "Q": Q.tolist(), "K": K.tolist(), "V": V.tolist()},
        "steps": [
            {"name": n, "expr": e, "matrix": m.tolist(), "explain_zh": x}
            for n, e, m, x in zip(names, exprs, matrices, explains)
        ],
        "result": output.tolist(),
    }


def _llm_response(*, block_id="blk-4") -> dict:
    """schema 合法的 LLM 形态响应：toy 设定 + 四步讲解，不含 matrix/result。

    四步数值由系统 ``_assemble_numerics`` 确定性计算（T040 契约变更）。
    """
    data = _golden_response(block_id=block_id)
    data.pop("result")
    for step in data["steps"]:
        step.pop("matrix")
    return data


# ---------------------------------------------------------------------------
# verify_worked_example
# ---------------------------------------------------------------------------


def test_golden_example_passes_within_tolerance() -> None:
    result = verify_worked_example(_golden_response())
    assert result.passed, result.diffs
    assert result.max_abs_diff <= TOLERANCE


def test_wrong_scaled_step_is_caught_with_diff() -> None:
    data = _golden_response()
    scores = _pipeline()[0]
    data["steps"][1]["matrix"] = (scores / 2.0).tolist()  # 错误除数
    result = verify_worked_example(data)
    assert not result.passed
    assert any("第2步" in d and "1e-06" in d.replace(" ", "") or "第2步" in d
               for d in result.diffs)


def test_shape_mismatch_is_caught() -> None:
    data = _golden_response()
    data["steps"][0]["matrix"] = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]  # 2×3
    result = verify_worked_example(data)
    assert not result.passed
    assert any("形状不匹配" in d for d in result.diffs)


def test_result_must_equal_final_step() -> None:
    data = _golden_response()
    data["result"] = [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]]
    result = verify_worked_example(data)
    assert not result.passed
    assert any("result" in d for d in result.diffs)


def test_non_numeric_matrix_element_rejected() -> None:
    data = _golden_response()
    data["setup"]["Q"][0][0] = "一"
    result = verify_worked_example(data)
    assert not result.passed
    assert any("数字" in d for d in result.diffs)


def test_boolean_matrix_element_rejected() -> None:
    data = _golden_response()
    data["setup"]["V"][1][1] = True
    result = verify_worked_example(data)
    assert any("数字" in d for d in result.diffs)


def test_tokens_count_must_match_rows_and_be_3_to_5() -> None:
    data = _golden_response()
    data["setup"]["tokens"] = ["甲", "乙"]
    result = verify_worked_example(data)
    assert not result.passed
    assert any("tokens" in d for d in result.diffs)

    data = _golden_response()
    data["setup"]["tokens"].append("多")  # 4 个词但 Q 只有 3 行
    result = verify_worked_example(data)
    assert any("行数" in d for d in result.diffs)


def test_d_k_fixed_at_3() -> None:
    data = _golden_response()
    data["setup"]["d_k"] = 4
    result = verify_worked_example(data)
    assert not result.passed
    assert any("d_k" in d for d in result.diffs)


def test_toy_constants_enforced() -> None:
    data = _golden_response()
    data["is_toy_data"] = False
    data["disclaimer"] = "真实数据"
    result = verify_worked_example(data)
    assert not result.passed
    assert any("is_toy_data" in d for d in result.diffs)
    assert any("disclaimer" in d for d in result.diffs)


def test_steps_must_be_exactly_four() -> None:
    data = _golden_response()
    data["steps"].append(dict(data["steps"][-1]))  # 第 5 步
    result = verify_worked_example(data)
    assert not result.passed
    assert any("固定四步" in d for d in result.diffs)


def test_softmax_rows_must_sum_to_one_pattern() -> None:
    # 行和为 1 但并非 softmax 的错误权重，仍应被复算抓出
    data = _golden_response()
    data["steps"][2]["matrix"] = [
        [0.9, 0.05, 0.05], [0.05, 0.9, 0.05], [0.05, 0.05, 0.9]
    ]
    # 第 4 步与 result 也按错误权重重算，保证唯一问题是 softmax
    wrong_w = np.array(data["steps"][2]["matrix"], dtype=float)
    wrong_out = wrong_w @ V
    data["steps"][3]["matrix"] = wrong_out.tolist()
    data["result"] = wrong_out.tolist()
    result = verify_worked_example(data)
    assert not result.passed
    assert any("第3步" in d for d in result.diffs)


# ---------------------------------------------------------------------------
# _assemble_numerics：LLM 只给 toy 设定与讲解，四步数值由 numpy 确定性计算
# ---------------------------------------------------------------------------


def test_assemble_computes_four_matrices_and_result() -> None:
    data = _llm_response()
    assert "matrix" not in data["steps"][0]
    assert "result" not in data

    result = _assemble_numerics(data)

    assert result.passed, result.diffs
    assert result.max_abs_diff <= TOLERANCE
    scores, _, weights, output = _pipeline()
    assert np.allclose(np.array(data["steps"][0]["matrix"]), scores)
    assert np.allclose(np.array(data["steps"][2]["matrix"]), weights)
    assert np.allclose(np.array(data["steps"][3]["matrix"]), output)
    assert np.allclose(np.array(data["result"]), output)
    # setup 是模型自己的 toy 设定，原样保留
    assert data["setup"]["Q"] == Q.tolist()
    assert data["setup"]["tokens"] == list(TOKENS)


def test_assemble_preserves_model_prose() -> None:
    data = _llm_response()
    _assemble_numerics(data)
    assert data["steps"][0]["name"] == "计算相关性分数"
    assert data["steps"][0]["expr"] == "Q K^T"
    assert data["steps"][0]["explain_zh"] == "每个 token 与每个 token 的相关性。"


def test_assemble_rejects_empty_prose_without_filling_matrices() -> None:
    # 讲解是 LLM 的唯一语义产出，缺失即失败重试，系统不替它写讲解
    data = _llm_response()
    data["steps"][1]["explain_zh"] = "   "
    before = json.dumps(data, ensure_ascii=False)

    result = _assemble_numerics(data)

    assert not result.passed
    assert any("explain_zh" in d for d in result.diffs)
    assert json.dumps(data, ensure_ascii=False) == before
    assert "matrix" not in data["steps"][1]
    assert "result" not in data


def test_assemble_refuses_broken_setup_without_mutation() -> None:
    data = _llm_response()
    # Q 只有 2 行，与 tokens/K/V 行数不一致：无法安全计算
    data["setup"]["Q"] = [[1, 0, 1], [0, 1, 1]]
    before = json.dumps(data, ensure_ascii=False)

    result = _assemble_numerics(data)

    assert not result.passed
    assert any("setup.Q" in d for d in result.diffs)
    assert json.dumps(data, ensure_ascii=False) == before


def test_assemble_refuses_wrong_step_count_without_mutation() -> None:
    data = _llm_response()
    data["steps"].append(dict(data["steps"][-1]))
    before = json.dumps(data, ensure_ascii=False)

    result = _assemble_numerics(data)

    assert not result.passed
    assert any("固定四步" in d for d in result.diffs)
    assert json.dumps(data, ensure_ascii=False) == before


# ---------------------------------------------------------------------------
# run_worked_example：落盘 / 重试 / 缺失
# ---------------------------------------------------------------------------


def _prepare_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    normalized = {
        "paper_id": "p",
        "sections": [],
        "blocks": [{"id": "blk-4", "type": "equation", "page_idx": 1,
                    "latex": r"\operatorname{softmax}(QK^{T}/\sqrt{d_k})V"}],
    }
    plan = {
        "key_formulas": [
            {"block_id": "blk-4",
             "latex": r"\operatorname{softmax}(QK^{T}/\sqrt{d_k})V",
             "worked_example_required": True,
             "example_spec": {"tokens": 3,
                              "steps": ["分数", "缩放", "softmax", "加权"]}}
        ]
    }
    (run_dir / "normalized.json").write_text(
        json.dumps(normalized, ensure_ascii=False), encoding="utf-8")
    (run_dir / "plan.json").write_text(
        json.dumps({"schema_kind": "planner", **plan}, ensure_ascii=False),
        encoding="utf-8")
    return run_dir


def test_run_writes_verified_payload(tmp_path, make_fake_llm) -> None:
    run_dir = _prepare_run(tmp_path)
    llm = make_fake_llm({"worked_example": _llm_response()})
    outcome = run_worked_example(run_dir, llm)

    assert outcome["status"] == "passed"
    payload = json.loads(
        (run_dir / WORKED_EXAMPLE_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["id"] == WORKED_EXAMPLE_ID
    assert payload["is_toy_data"] is True
    assert payload["disclaimer"] == TOY_DISCLAIMER
    verification = payload["verification"]
    assert verification["status"] == "passed"
    assert verification["max_abs_diff"] <= TOLERANCE
    assert verification["checked_at"]
    assert verification["numeric_source"] == "system_numpy"
    # LLM 未给任何数值，四步矩阵与 result 由系统确定性填充
    assert isinstance(payload["steps"][0]["matrix"], list)
    assert isinstance(payload["result"], list)
    assert verify_worked_example(payload).passed
    # prompt 带入关键公式定位与 toy 设定，且明确不要 LLM 算矩阵
    system, user, kind = llm.calls[0]
    assert kind == "worked_example"
    assert "blk-4" in user
    assert r"\sqrt{d_k}" in user
    assert "不允许做任何矩阵算术" in user


def _structurally_broken_response() -> dict:
    """schema 合法但 setup 结构损坏（Q 行数与 tokens/K/V 不一致）。

    系统无法据此确定性计算四步矩阵，必须走结构 diff→重试路径。
    """
    data = _llm_response()
    data["setup"]["Q"] = [[1, 0, 1], [0, 1, 1]]
    return data


def test_run_llm_gives_no_matrices_and_system_computes_them(
    tmp_path, make_fake_llm
) -> None:
    # T040 契约：LLM 响应天然不含 matrix/result，系统计算、首调即过
    run_dir = _prepare_run(tmp_path)
    llm = make_fake_llm({"worked_example": _llm_response()})

    outcome = run_worked_example(run_dir, llm)

    assert outcome["status"] == "passed"
    assert llm.call_count == 1
    payload = json.loads(
        (run_dir / WORKED_EXAMPLE_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["verification"]["status"] == "passed"
    assert payload["verification"]["numeric_source"] == "system_numpy"
    _, _, weights, output = _pipeline()
    assert np.allclose(np.array(payload["steps"][2]["matrix"]), weights)
    assert np.allclose(np.array(payload["result"]), output)


def test_run_retries_once_with_diff_then_passes(tmp_path, make_fake_llm) -> None:
    run_dir = _prepare_run(tmp_path)
    prompts_seen: list[str] = []

    def answer(system, user, schema_kind):
        prompts_seen.append(user)
        if len(prompts_seen) == 1:
            return _structurally_broken_response()
        return _llm_response()

    llm = make_fake_llm({"worked_example": answer})
    outcome = run_worked_example(run_dir, llm)

    assert outcome["status"] == "passed"
    assert llm.call_count == 2
    assert "结构校验" in prompts_seen[1]
    assert "setup.Q" in prompts_seen[1]
    assert "matrix/result" in prompts_seen[1]


def test_run_persistent_failure_raises_and_writes_nothing(
    tmp_path, make_fake_llm
) -> None:
    run_dir = _prepare_run(tmp_path)
    llm = make_fake_llm({"worked_example": _structurally_broken_response()})

    with pytest.raises(LLMError, match="FR-026"):
        run_worked_example(run_dir, llm)
    assert not (run_dir / WORKED_EXAMPLE_FILENAME).exists()
    assert llm.call_count == 2


def test_run_persistent_failure_with_allow_missing_writes_missing_marker(
    tmp_path, make_fake_llm
) -> None:
    run_dir = _prepare_run(tmp_path)
    llm = make_fake_llm({"worked_example": _structurally_broken_response()})

    outcome = run_worked_example(run_dir, llm, allow_missing=True)
    assert outcome["status"] == "missing"
    payload = json.loads(
        (run_dir / WORKED_EXAMPLE_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["id"] == WORKED_EXAMPLE_ID
    assert payload["verification"]["status"] == "missing"
    assert payload["verification"]["diffs"]
    assert payload["missing_reason"]
    assert "FR-026" in payload["missing_reason"]


def test_run_wrong_formula_block_id_fails_verification(tmp_path, make_fake_llm) -> None:
    run_dir = _prepare_run(tmp_path)
    response = _llm_response(block_id="blk-999")
    llm = make_fake_llm({"worked_example": response})
    with pytest.raises(LLMError, match="blk-4"):
        run_worked_example(run_dir, llm)
    assert llm.call_count == 2


def test_run_schema_failure_also_retries(tmp_path, make_fake_llm) -> None:
    run_dir = _prepare_run(tmp_path)
    prompts_seen: list[str] = []

    def answer(system, user, schema_kind):
        prompts_seen.append(user)
        if len(prompts_seen) == 1:
            return {"schema_kind": "worked_example"}
        return _llm_response()

    llm = make_fake_llm({"worked_example": answer})
    outcome = run_worked_example(run_dir, llm)
    assert outcome["status"] == "passed"
    assert llm.call_count == 2
