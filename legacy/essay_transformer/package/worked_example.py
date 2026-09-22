"""关键公式手算示例：LLM 出 toy 设定与讲解，numpy 确定性计算并复算（T024/T040，FR-017/026）。

职责划分（T040 真实首跑后定稿）：DeepSeek 按 planner 的 ``example_spec``
只产出**教学语义**——3–5 个 toy 词、简单数值的 Q/K/V 输入矩阵，以及固定
四步的 ``name/expr/explain_zh`` 讲解；**LLM 不做任何矩阵算术**（真实模型
无法可靠完成精确乘法/softmax，重试不收敛）。四步结果矩阵由本模块用 numpy
从 LLM 给的 setup **确定性计算**填充：

    scores = Q · K^T
    scaled = scores / sqrt(d_k)
    weights = row_softmax(scaled)      # 逐行，减最大值数值稳定
    output = weights · V

填充后的完整 payload 再经 :func:`verify_worked_example` 独立复算闸门
（逐步比对、容差 ``1e-6``）——数值由系统计算，构造上必然自洽。LLM 侧
唯一可能失败的是 **setup 结构**（tokens/Q/K/V 行列不自洽、d_k≠3、四步
讲解缺失等），此时：

1. 带着具体结构 diff 让模型**重试**（默认每篇 2 次尝试）；
2. 再失败：默认抛 :class:`~essay_transformer.errors.LLMError`（退出码 1，
   禁止放行）；仅当 ``allow_missing=True``（CLI
   ``--allow-missing-example`` 显式留痕）时写 status=missing 的
   worked_example.json，assemble 在该处渲染固定缺失占位（FR-026）。

成功产物固定含 ``id="we-001"``、``is_toy_data=true``、
``disclaimer="教学示意/非论文真实数据"`` 与 verification
（status/max_abs_diff/checked_at/numeric_source="system_numpy"）。
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from . import config
from .errors import LLMError
from .llm_schema import validate_llm_output
from .planner import load_normalized, load_plan

__all__ = [
    "WORKED_EXAMPLE_FILENAME",
    "WORKED_EXAMPLE_ID",
    "TOY_DISCLAIMER",
    "TOLERANCE",
    "REQUIRED_STEP_COUNT",
    "VerificationResult",
    "verify_worked_example",
    "run_worked_example",
]

logger = logging.getLogger(__name__)

WORKED_EXAMPLE_FILENAME = "worked_example.json"
WORKED_EXAMPLE_ID = "we-001"
TOY_DISCLAIMER = "教学示意/非论文真实数据"
TOLERANCE = 1e-6
REQUIRED_STEP_COUNT = 4


@dataclass
class VerificationResult:
    """numpy 复算结论。"""

    passed: bool
    max_abs_diff: float
    diffs: list[str] = field(default_factory=list)
    recomputed: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "max_abs_diff": self.max_abs_diff,
            "diffs": list(self.diffs),
        }


# ---------------------------------------------------------------------------
# 纯函数复算
# ---------------------------------------------------------------------------


def _as_matrix(value: Any, where: str, diffs: list[str]) -> np.ndarray | None:
    if not isinstance(value, list) or not value:
        diffs.append(f"{where}：必须是非空二维数组（嵌套数字数组）")
        return None
    rows: list[list[float]] = []
    width: int | None = None
    for i, row in enumerate(value):
        if not isinstance(row, list) or not row:
            diffs.append(f"{where}：第 {i} 行必须是非空数组")
            return None
        if width is None:
            width = len(row)
        elif len(row) != width:
            diffs.append(f"{where}：第 {i} 行长度与首行不一致（矩阵必须矩形）")
            return None
        for x in row:
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                diffs.append(f"{where}：元素必须是数字（不接受布尔/字符串）")
                return None
        rows.append([float(x) for x in row])
    return np.array(rows, dtype=float)


def _row_softmax(matrix: np.ndarray) -> np.ndarray:
    shifted = matrix - matrix.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def _compare(
    actual: np.ndarray | None,
    expected: np.ndarray | None,
    where: str,
    diffs: list[str],
) -> float:
    """比对一步矩阵；返回该步最大绝对差（无法比对时返回 inf）。"""
    if actual is None or expected is None:
        return math.inf
    if actual.shape != expected.shape:
        diffs.append(
            f"{where}：形状不匹配，期望 {expected.shape}，实际 {actual.shape}"
        )
        return math.inf
    diff = float(np.max(np.abs(actual - expected)))
    if diff > TOLERANCE:
        diffs.append(
            f"{where}：数值与 numpy 复算不一致，最大绝对差 {diff:.3g}"
            f"（容差 {TOLERANCE:g}）；期望首行 {expected[0].round(6).tolist()}，"
            f"实际首行 {actual[0].round(6).tolist()}"
        )
    return diff


def verify_worked_example(data: dict[str, Any]) -> VerificationResult:
    """按 setup 独立复算四步并逐项比对；聚合全部 diff。"""
    diffs: list[str] = []

    if data.get("is_toy_data") is not True:
        diffs.append("is_toy_data 必须为 true（教学示意，非论文真实数据）")
    if data.get("disclaimer") != TOY_DISCLAIMER:
        diffs.append(
            f"disclaimer 必须逐字为 {TOY_DISCLAIMER!r}（实际 {data.get('disclaimer')!r}）"
        )

    setup = data.get("setup")
    steps = data.get("steps")
    result_raw = data.get("result")
    if not isinstance(setup, dict):
        diffs.append("setup：必须是对象")
        return VerificationResult(False, math.inf, diffs)
    if not isinstance(steps, list):
        diffs.append("steps：必须是数组")
        return VerificationResult(False, math.inf, diffs)

    tokens = setup.get("tokens")
    d_k = setup.get("d_k")
    if not isinstance(tokens, list) or not 3 <= len(tokens) <= 5 \
            or not all(isinstance(t, str) and t.strip() for t in tokens):
        diffs.append("setup.tokens：必须是 3–5 个非空字符串（toy 词）")
    if not isinstance(d_k, int) or isinstance(d_k, bool) or d_k != 3:
        diffs.append(f"setup.d_k：固定为 3（实际 {d_k!r}）")

    q = _as_matrix(setup.get("Q"), "setup.Q", diffs)
    k = _as_matrix(setup.get("K"), "setup.K", diffs)
    v = _as_matrix(setup.get("V"), "setup.V", diffs)

    n = len(tokens) if isinstance(tokens, list) else None
    for name, matrix in (("Q", q), ("K", k), ("V", v)):
        if matrix is not None and n is not None and matrix.shape[0] != n:
            diffs.append(
                f"setup.{name}：行数 {matrix.shape[0]} 与 tokens 数 {n} 不一致"
            )
    if q is not None and k is not None and q.shape != k.shape:
        diffs.append(f"setup.Q/K 形状必须一致（Q {q.shape} vs K {k.shape}）")
    if q is not None and v is not None and v.shape[0] != q.shape[0]:
        diffs.append("setup.V：行数必须与 Q 一致（每个 token 一行）")

    if len(steps) != REQUIRED_STEP_COUNT:
        diffs.append(
            f"steps：固定四步且顺序为 QK^T → /√d_k → 行 softmax → ·V"
            f"（实际 {len(steps) if isinstance(steps, list) else '非数组'} 步）"
        )

    # 结构不全时无法复算，直接返回
    matrices_present = q is not None and k is not None and v is not None
    if diffs and not matrices_present:
        return VerificationResult(False, math.inf, diffs)

    recomputed: dict[str, Any] = {}
    max_diff = 0.0
    if matrices_present:
        scores = q @ k.T
        scaled = scores / math.sqrt(float(d_k))
        weights = _row_softmax(scaled)
        output = weights @ v
        recomputed = {
            "scores": scores, "scaled": scaled, "weights": weights, "output": output
        }

        step_names = ("第1步 QK^T", "第2步 /√d_k", "第3步 行softmax", "第4步 weights·V")
        expected_steps = (scores, scaled, weights, output)
        for idx, (label, expected) in enumerate(zip(step_names, expected_steps)):
            if idx >= len(steps) or not isinstance(steps[idx], dict):
                diffs.append(f"{label}：缺失或不是对象")
                max_diff = math.inf
                continue
            actual = _as_matrix(
                steps[idx].get("matrix"), f"steps[{idx}].matrix（{label}）", diffs
            )
            step_diff = _compare(actual, expected, f"steps[{idx}]（{label}）", diffs)
            max_diff = max(max_diff, step_diff)
            for field_name in ("name", "expr", "explain_zh"):
                if not isinstance(steps[idx].get(field_name), str) \
                        or not steps[idx][field_name].strip():
                    diffs.append(f"steps[{idx}]（{label}）：{field_name} 必填非空字符串")

        result_matrix = _as_matrix(result_raw, "result", diffs)
        result_diff = _compare(result_matrix, output, "result（第4步最终输出）", diffs)
        max_diff = max(max_diff, result_diff)
        if len(steps) >= REQUIRED_STEP_COUNT and isinstance(steps[3], dict):
            step4 = _as_matrix(steps[3].get("matrix"), "steps[3].matrix", [])
            if step4 is not None and result_matrix is not None:
                tail_diff = _compare(
                    result_matrix, step4, "result 与 steps[3]", []
                )
                max_diff = max(max_diff, tail_diff)

    passed = not diffs and max_diff <= TOLERANCE
    return VerificationResult(
        passed=passed,
        max_abs_diff=(0.0 if passed and math.isinf(max_diff) else max_diff),
        diffs=diffs,
        recomputed=recomputed,
    )


# ---------------------------------------------------------------------------
# 数值确定性组装（T040 真实首跑，2026-09-20）
# ---------------------------------------------------------------------------
#
# LLM 只产出 toy 设定（tokens/Q/K/V）与四步讲解（name/expr/explain_zh），
# steps[*].matrix 与 result 不由 LLM 给出：真实模型无法可靠完成精确矩阵
# 算术（真实首跑两次尝试 diff 0.024→1.0 不收敛），而四步推导是纯确定性
# 计算。setup 结构合法时由 numpy 计算填充，再走 verify_worked_example
# 独立复算闸门；结构不合法（行列不自洽/d_k≠3/讲解缺失）则返回结构 diff，
# 交 run 层带着 diff 重试。

_MATRIX_DECIMALS = 9


def _assemble_numerics(data: dict[str, Any]) -> VerificationResult:
    """校验 LLM 形态算例并确定性填充四步矩阵与 result；就地修改。

    成功时写入 ``steps[i].matrix`` 与 ``result``（9 位小数四舍五入），再经
    :func:`verify_worked_example` 独立复算并返回其结论；结构不合法时不修改
    ``data``，返回带结构 diff 的失败结果（调用方据此重试）。
    """
    diffs: list[str] = []
    setup = data.get("setup")
    steps = data.get("steps")
    if not isinstance(setup, dict):
        return VerificationResult(False, math.inf, ["setup：必须是对象"])
    if not isinstance(steps, list):
        return VerificationResult(False, math.inf, ["steps：必须是数组"])

    tokens = setup.get("tokens")
    d_k = setup.get("d_k")
    if not isinstance(tokens, list) or not 3 <= len(tokens) <= 5 \
            or not all(isinstance(t, str) and t.strip() for t in tokens):
        diffs.append("setup.tokens：必须是 3–5 个非空字符串（toy 词）")
    if not isinstance(d_k, int) or isinstance(d_k, bool) or d_k != 3:
        diffs.append(f"setup.d_k：固定为 3（实际 {d_k!r}）")

    q = _as_matrix(setup.get("Q"), "setup.Q", diffs)
    k = _as_matrix(setup.get("K"), "setup.K", diffs)
    v = _as_matrix(setup.get("V"), "setup.V", diffs)

    n = len(tokens) if isinstance(tokens, list) else None
    for name, matrix in (("Q", q), ("K", k), ("V", v)):
        if matrix is not None and n is not None and matrix.shape[0] != n:
            diffs.append(
                f"setup.{name}：行数 {matrix.shape[0]} 与 tokens 数 {n} 不一致"
            )
    if q is not None and k is not None and q.shape != k.shape:
        diffs.append(f"setup.Q/K 形状必须一致（Q {q.shape} vs K {k.shape}）")
    if q is not None and v is not None and v.shape[0] != q.shape[0]:
        diffs.append("setup.V：行数必须与 Q 一致（每个 token 一行）")

    if len(steps) != REQUIRED_STEP_COUNT:
        diffs.append(
            "steps：固定四步且顺序为 QK^T → /√d_k → 行 softmax → ·V"
            f"（实际 {len(steps) if isinstance(steps, list) else '非数组'} 步）"
        )
    else:
        labels = ("第1步 QK^T", "第2步 /√d_k", "第3步 行softmax", "第4步 weights·V")
        for idx, (step, label) in enumerate(zip(steps, labels)):
            if not isinstance(step, dict):
                diffs.append(f"{label}：缺失或不是对象")
                continue
            for field_name in ("name", "expr", "explain_zh"):
                value = step.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    diffs.append(
                        f"steps[{idx}]（{label}）：{field_name} 必填非空字符串"
                    )

    if diffs:
        return VerificationResult(False, math.inf, diffs)

    scores = q @ k.T
    scaled = scores / math.sqrt(float(d_k))
    weights = _row_softmax(scaled)
    output = weights @ v
    for idx, matrix in enumerate((scores, scaled, weights, output)):
        steps[idx]["matrix"] = np.round(matrix, _MATRIX_DECIMALS).tolist()
    data["result"] = np.round(output, _MATRIX_DECIMALS).tolist()
    logger.info(
        "worked_example 四步矩阵与 result 由 numpy 从 setup.Q/K/V 确定性计算填充"
    )
    return verify_worked_example(data)


# ---------------------------------------------------------------------------
# LLM 生成 + 带 diff 重试 + 落盘
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _find_key_formula(plan: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    blocks = {b["id"]: b for b in normalized.get("blocks", [])}
    for kf in plan.get("key_formulas", []):
        if isinstance(kf, dict) and kf.get("worked_example_required") is True:
            block = blocks.get(kf.get("block_id"))
            if not isinstance(block, dict) or block.get("type") != "equation":
                raise LLMError(
                    f"planner 指定手算公式块 {kf.get('block_id')!r} 不存在或非 equation"
                )
            return kf
    raise LLMError("plan.json 中没有 worked_example_required=true 的关键公式")


def _render_prompt(
    template: str, key_formula: dict[str, Any], diff_text: str | None
) -> str:
    spec = key_formula.get("example_spec") or {}
    spec_text = json.dumps(spec, ensure_ascii=False)
    prompt = (
        template.replace("{{FORMULA_BLOCK_ID}}", str(key_formula["block_id"]))
        .replace("{{FORMULA_LATEX}}", str(key_formula.get("latex", "")))
        .replace("{{EXAMPLE_SPEC}}", spec_text)
    )
    if diff_text:
        prompt += (
            "\n\n## 上一次给出的 toy 设定未通过结构校验，系统无法据此确定性"
            "计算四步矩阵\n请逐项修正以下问题后，重新输出**完整**的 "
            "worked_example JSON（只含 setup 与四步讲解，不要输出任何 "
            "matrix/result 字段，数值由系统计算）：\n"
            f"{diff_text}"
        )
    return prompt


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def run_worked_example(
    run_dir: Path | str,
    llm: Any,
    *,
    prompts_dir: Path | str | None = None,
    max_attempts: int = 2,
    allow_missing: bool = False,
) -> dict[str, Any]:
    """生成→复算→（带 diff）重试→落盘 worked_example.json。

    返回 ``{"status": "passed"|"missing", "path": Path, "data": payload}``。
    默认重试耗尽即抛 :class:`LLMError`（退出码 1）；``allow_missing=True``
    时改写 status=missing 的留痕文件并返回 missing（assemble 渲染缺失占位）。
    """
    run_dir = Path(run_dir)
    prompts_root = Path(prompts_dir) if prompts_dir is not None else config.PROMPTS_DIR
    normalized = load_normalized(run_dir)
    plan = load_plan(run_dir)
    key_formula = _find_key_formula(plan, normalized)
    formula_block_id = key_formula["block_id"]

    system_prompt = (prompts_root / "system.md").read_text(encoding="utf-8")
    template = (prompts_root / "worked_example.md").read_text(encoding="utf-8")

    last: VerificationResult | None = None
    last_data: dict[str, Any] | None = None
    diff_text: str | None = None
    for attempt in range(1, max_attempts + 1):
        user_prompt = _render_prompt(template, key_formula, diff_text)
        try:
            data = llm.complete_json(system_prompt, user_prompt, "worked_example")
            validate_llm_output(data, "worked_example")
        except LLMError as exc:
            last = VerificationResult(False, math.inf, [f"schema 校验失败：{exc}"])
            last_data = None
            diff_text = "\n".join(f"- {d}" for d in last.diffs)
            logger.warning("worked_example schema 失败（第 %d/%d 次）",
                           attempt, max_attempts)
            continue

        if data.get("formula_block_id") != formula_block_id:
            result = VerificationResult(
                False, math.inf,
                [f"formula_block_id 必须为 {formula_block_id!r}"
                 f"（实际 {data.get('formula_block_id')!r}）"],
            )
        else:
            # LLM 只给 toy 设定与讲解；四步矩阵/result 由 numpy 确定性
            # 计算填充，再走独立复算闸门（T040 真实首跑）
            result = _assemble_numerics(data)
        last = result
        last_data = data
        diff_text = "\n".join(f"- {d}" for d in result.diffs)

        if result.passed:
            payload = dict(data)
            payload["id"] = WORKED_EXAMPLE_ID
            payload["verification"] = {
                "status": "passed",
                "max_abs_diff": result.max_abs_diff,
                "checked_at": _now_iso(),
                "numeric_source": "system_numpy",
            }
            out_path = run_dir / WORKED_EXAMPLE_FILENAME
            _atomic_write_json(out_path, payload)
            logger.info(
                "worked_example.json 复算通过（四步数值系统确定性计算）："
                "max_abs_diff=%.3g → %s",
                result.max_abs_diff, out_path,
            )
            return {"status": "passed", "path": out_path, "data": payload}

        logger.warning(
            "worked_example 结构校验/组装失败（第 %d/%d 次）：%d 处 diff",
            attempt, max_attempts, len(result.diffs),
        )

    # 重试耗尽
    assert last is not None
    if not allow_missing:
        raise LLMError(
            "手算示例 toy 设定经 2 次尝试仍未通过结构校验，系统无法确定性"
            "计算四步矩阵，已禁止放行（FR-026；如确认接受该处缺失，可使用 "
            "--allow-missing-example 留痕）：\n"
            + "\n".join(f"- {d}" for d in last.diffs)
        )

    missing_payload: dict[str, Any] = {
        "id": WORKED_EXAMPLE_ID,
        "formula_block_id": formula_block_id,
        "is_toy_data": True,
        "disclaimer": TOY_DISCLAIMER,
        "verification": {
            "status": "missing",
            "max_abs_diff": last.max_abs_diff,
            "checked_at": _now_iso(),
            "diffs": list(last.diffs),
        },
        "missing_reason": (
            "手算示例 toy 设定 2 次尝试均未通过结构校验、无法确定性计算"
            "四步矩阵，按 FR-026 显式标记缺失，未编造/放行错误算例"
            "（--allow-missing-example 留痕）。"
        ),
    }
    if last_data is not None:
        # 保留最后一次的 setup 供排查，但不进入成稿
        missing_payload["last_attempt_setup"] = last_data.get("setup")
    out_path = run_dir / WORKED_EXAMPLE_FILENAME
    _atomic_write_json(out_path, missing_payload)
    logger.warning("worked_example 标记为 missing（已显式留痕）：%s", out_path)
    return {"status": "missing", "path": out_path, "data": missing_payload}
