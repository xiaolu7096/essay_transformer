"""T019：plan.json 确定性业务校验（data-model.md §4）。

JSON Schema 层的结构校验由 test_llm_schema.py 覆盖；本文件聚焦 planner 的
论文结构规则：

- target_order 恰为固定七段且顺序固定；
- 每个非 appendix source section 恰好出现一次；appendix 不进主线；
  FR-014 尾段抽取允许合成 id ``<real-sec>-tail`` / ``<real-sec>-extract``；
- 每个非噪声 block 有合法 action；fold 必填 fold_group；appendix 块只能
  fold/drop；
- key_formulas ≥1、worked_example_required=true 恰 1 条且 example_spec 合法、
  latex 与原文一致。
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from essay_transformer.errors import LLMError
from essay_transformer.planner import (
    ACTIONS,
    SLUG_ORDER,
    PLAN_FILENAME,
    _repair_plan,
    run_planner,
    validate_plan,
)


# ---------------------------------------------------------------------------
# 手工构造的最小 normalized 文档
# ---------------------------------------------------------------------------


def _normalized() -> dict:
    return {
        "paper_id": "p",
        "title": "Demo Paper",
        "sections": [
            {"id": "sec-1", "heading": "1 Introduction", "level": 1,
             "kind": "introduction", "page_start": 0,
             "blocks": ["blk-1", "blk-2"]},
            {"id": "sec-2", "heading": "2 Method", "level": 1,
             "kind": "body", "page_start": 1,
             "blocks": ["blk-3", "blk-4"]},
            {"id": "sec-9", "heading": "9 Conclusion", "level": 1,
             "kind": "conclusion", "page_start": 9,
             "blocks": ["blk-5", "blk-6"]},
            {"id": "sec-a", "heading": "Appendix A Proofs", "level": 1,
             "kind": "appendix", "page_start": 12,
             "blocks": ["blk-7", "blk-8"]},
        ],
        "blocks": [
            {"id": "blk-1", "type": "heading", "page_idx": 0},
            {"id": "blk-2", "type": "paragraph", "page_idx": 0},
            {"id": "blk-3", "type": "heading", "page_idx": 1},
            {"id": "blk-4", "type": "equation", "page_idx": 1, "latex": "X=1"},
            {"id": "blk-5", "type": "heading", "page_idx": 9},
            {"id": "blk-6", "type": "paragraph", "page_idx": 9},
            {"id": "blk-7", "type": "heading", "page_idx": 12},
            {"id": "blk-8", "type": "paragraph", "page_idx": 12},
            {"id": "blk-n", "type": "noise", "page_idx": 12,
             "noise_kind": "page_number"},
        ],
        "appendix_section_ids": ["sec-a"],
    }


def _entry(slug: str, sources: list[str], treatment: str = "brief") -> dict:
    return {"target_slug": slug, "source_section_ids": sources, "treatment": treatment}


def _good_plan() -> dict:
    # 三个真实非附录节各出现一次；四个段用 FR-014 合成抽取 id 补足七段
    return {
        "target_order": [
            _entry("tldr", ["sec-1"]),
            _entry("intro", ["sec-2"], "tutorial"),
            _entry("conclusion", ["sec-9"], "front_load_extract"),
            _entry("related", ["sec-9-tail"], "compress"),
            _entry("method", ["sec-9-extract"], "tutorial"),
            _entry("experiments", ["sec-2-tail"], "conclusion_only"),
            _entry("wrapup", ["sec-1-extract"], "summary"),
        ],
        "block_actions": {
            "blk-1": {"action": "narrate"},
            "blk-2": {"action": "narrate"},
            "blk-3": {"action": "narrate"},
            "blk-4": {"action": "narrate"},
            "blk-5": {"action": "narrate"},
            "blk-6": {"action": "condense", "one_liner": "一句话"},
            # appendix 块只能 fold/drop，不进主线
            "blk-7": {"action": "drop"},
            "blk-8": {"action": "fold", "fold_group": "derivation"},
            # 噪声块给不给 action 都可以，这里给 drop
            "blk-n": {"action": "drop"},
        },
        "key_formulas": [
            {"block_id": "blk-4", "latex": "X=1", "label": "公式 (1)",
             "worked_example_required": True,
             "example_spec": {"tokens": 3,
                              "steps": ["分数", "缩放", "softmax", "加权"]}},
            {"block_id": "blk-4", "latex": "X=1",
             "worked_example_required": False},
        ],
    }


# ---------------------------------------------------------------------------
# golden
# ---------------------------------------------------------------------------


def test_slug_order_constant_is_fixed_seven() -> None:
    assert SLUG_ORDER == (
        "tldr", "intro", "conclusion", "related", "method", "experiments", "wrapup"
    )
    assert set(ACTIONS) == {
        "narrate", "condense", "fold", "drop", "keep_figure", "keep_table_explained"
    }


def test_good_plan_passes() -> None:
    assert validate_plan(_good_plan(), _normalized()) == []


# ---------------------------------------------------------------------------
# target_order
# ---------------------------------------------------------------------------


def test_target_order_must_be_seven() -> None:
    plan = _good_plan()
    plan["target_order"].pop()
    problems = validate_plan(plan, _normalized())
    assert any("七段" in p for p in problems)


def test_target_order_must_follow_fixed_sequence() -> None:
    plan = _good_plan()
    plan["target_order"][0]["target_slug"] = "intro"
    plan["target_order"][1]["target_slug"] = "tldr"
    problems = validate_plan(plan, _normalized())
    assert any("顺序" in p for p in problems)


def test_duplicate_slug_rejected() -> None:
    plan = _good_plan()
    plan["target_order"][-1]["target_slug"] = "method"
    problems = validate_plan(plan, _normalized())
    assert any("七段" in p or "顺序" in p or "重复" in p for p in problems)


# ---------------------------------------------------------------------------
# source sections
# ---------------------------------------------------------------------------


def test_each_real_section_must_appear_once_missing() -> None:
    plan = _good_plan()
    # sec-2 从 intro 移除，换成另一个合成 id：真实节缺失
    plan["target_order"][1]["source_section_ids"] = ["sec-1-tail"]
    problems = validate_plan(plan, _normalized())
    assert any("sec-2" in p and ("恰好一次" in p or "未出现" in p) for p in problems)


def test_real_section_appearing_twice_rejected() -> None:
    plan = _good_plan()
    plan["target_order"][3]["source_section_ids"] = ["sec-1"]  # sec-1 二次出现
    problems = validate_plan(plan, _normalized())
    assert any("sec-1" in p and ("多次" in p or "一次" in p) for p in problems)


def test_appendix_section_must_not_enter_mainline() -> None:
    plan = _good_plan()
    plan["target_order"][3]["source_section_ids"] = ["sec-a"]
    problems = validate_plan(plan, _normalized())
    assert any("appendix" in p and "sec-a" in p for p in problems)


def test_unknown_source_section_rejected() -> None:
    plan = _good_plan()
    plan["target_order"][3]["source_section_ids"] = ["sec-404"]
    problems = validate_plan(plan, _normalized())
    assert any("sec-404" in p for p in problems)


def test_synthetic_extract_id_requires_real_base() -> None:
    plan = _good_plan()
    plan["target_order"][3]["source_section_ids"] = ["sec-404-tail"]
    problems = validate_plan(plan, _normalized())
    assert any("sec-404-tail" in p for p in problems)


# ---------------------------------------------------------------------------
# block_actions
# ---------------------------------------------------------------------------


def test_every_non_noise_block_needs_action() -> None:
    plan = _good_plan()
    del plan["block_actions"]["blk-2"]
    problems = validate_plan(plan, _normalized())
    assert any("blk-2" in p and "action" in p for p in problems)


def test_noise_block_does_not_require_action() -> None:
    plan = _good_plan()
    del plan["block_actions"]["blk-n"]
    assert validate_plan(plan, _normalized()) == []


def test_unknown_block_id_in_actions_rejected() -> None:
    plan = _good_plan()
    plan["block_actions"]["blk-999"] = {"action": "narrate"}
    problems = validate_plan(plan, _normalized())
    assert any("blk-999" in p for p in problems)


def test_fold_requires_fold_group() -> None:
    plan = _good_plan()
    plan["block_actions"]["blk-8"] = {"action": "fold"}
    problems = validate_plan(plan, _normalized())
    assert any("blk-8" in p and "fold_group" in p for p in problems)


def test_fold_group_on_non_fold_action_rejected() -> None:
    plan = _good_plan()
    plan["block_actions"]["blk-2"] = {"action": "narrate", "fold_group": "references"}
    problems = validate_plan(plan, _normalized())
    assert any("blk-2" in p for p in problems)


def test_illegal_action_enum_rejected() -> None:
    plan = _good_plan()
    plan["block_actions"]["blk-2"] = {"action": "translate"}
    problems = validate_plan(plan, _normalized())
    assert any("blk-2" in p and "translate" in p for p in problems)


def test_appendix_blocks_can_only_fold_or_drop() -> None:
    plan = _good_plan()
    plan["block_actions"]["blk-8"] = {"action": "narrate"}
    problems = validate_plan(plan, _normalized())
    assert any("blk-8" in p and "appendix" in p for p in problems)


# ---------------------------------------------------------------------------
# T031：折叠归类的确定性硬规则（reference_list / chart）
# ---------------------------------------------------------------------------


def _doc_with_reference_list_and_chart() -> tuple[dict, dict]:
    """在最小文档上追加一个参考文献节：reference_list + chart 各一块。"""
    normalized = deepcopy(_normalized())
    plan = deepcopy(_good_plan())
    normalized["sections"].append(
        {"id": "sec-ref", "heading": "References", "level": 1,
         "kind": "references", "page_start": 13,
         "blocks": ["blk-r1", "blk-r2"]}
    )
    normalized["blocks"].extend([
        {"id": "blk-r1", "type": "reference_list", "page_idx": 13,
         "items": ["[1] Some Ref. 2016."]},
        {"id": "blk-r2", "type": "figure", "page_idx": 13,
         "img_path": "images/chart.jpg", "mineru_type": "chart"},
    ])
    # 新真实节在 wrapup 直接出现一次（与合成 id sec-1-extract 同列允许）
    plan["target_order"][6]["source_section_ids"].append("sec-ref")
    plan["block_actions"]["blk-r1"] = {
        "action": "fold", "fold_group": "references"}
    plan["block_actions"]["blk-r2"] = {
        "action": "fold", "fold_group": "repro_data",
        "rationale": "训练曲线图属复现原始数据，进 repro_data 折叠（FR-020/027）"}
    return normalized, plan


def test_reference_list_and_chart_golden_classification_passes() -> None:
    normalized, plan = _doc_with_reference_list_and_chart()
    assert validate_plan(plan, normalized) == []


def test_reference_list_must_fold_to_references() -> None:
    normalized, plan = _doc_with_reference_list_and_chart()
    for decision in (
        {"action": "narrate"},
        {"action": "drop"},
        {"action": "fold", "fold_group": "derivation"},
        {"action": "fold", "fold_group": "repro_data"},
    ):
        plan["block_actions"]["blk-r1"] = decision
        problems = validate_plan(plan, normalized)
        assert any(
            "blk-r1" in p and "reference_list" in p for p in problems
        ), decision


def test_chart_must_fold_to_repro_data() -> None:
    normalized, plan = _doc_with_reference_list_and_chart()
    for decision in (
        {"action": "keep_figure"},
        {"action": "narrate"},
        {"action": "drop"},
        {"action": "fold", "fold_group": "derivation"},
        {"action": "fold", "fold_group": "references"},
    ):
        plan["block_actions"]["blk-r2"] = decision
        problems = validate_plan(plan, normalized)
        assert any(
            "blk-r2" in p and "chart" in p for p in problems
        ), decision


# ---------------------------------------------------------------------------
# T046：appendix 公式块 fold 必须进 derivation
# ---------------------------------------------------------------------------


def _doc_with_appendix_equation() -> tuple[dict, dict]:
    normalized = deepcopy(_normalized())
    plan = deepcopy(_good_plan())
    normalized["sections"][3]["blocks"].append("blk-9")
    normalized["blocks"].insert(
        8, {"id": "blk-9", "type": "equation", "page_idx": 12,
            "latex": "A=B"}
    )
    return normalized, plan


def test_appendix_equation_fold_must_be_derivation() -> None:
    normalized, plan = _doc_with_appendix_equation()
    plan["block_actions"]["blk-9"] = {
        "action": "fold", "fold_group": "repro_data"}
    problems = validate_plan(plan, normalized)
    assert any("blk-9" in p and "derivation" in p for p in problems)

    plan["block_actions"]["blk-9"] = {
        "action": "fold", "fold_group": "references"}
    problems = validate_plan(plan, normalized)
    assert any("blk-9" in p and "derivation" in p for p in problems)


def test_appendix_equation_fold_to_derivation_passes() -> None:
    normalized, plan = _doc_with_appendix_equation()
    plan["block_actions"]["blk-9"] = {
        "action": "fold", "fold_group": "derivation"}
    assert validate_plan(plan, normalized) == []


def test_non_appendix_equation_fold_group_not_forced() -> None:
    """T046 规则只约束 appendix 节内公式：正文公式 fold 到任意合法组不拦截。"""
    normalized = deepcopy(_normalized())
    plan = deepcopy(_good_plan())
    # blk-4 是 sec-2（非 appendix）的 equation；golden 是 narrate，
    # 改成 fold/repro_data 不应触发 T046（key_formulas 规则先删掉它再换公式）
    plan["key_formulas"] = [
        {"block_id": "blk-4", "latex": "X=1", "label": "x",
         "worked_example_required": True,
         "example_spec": {"tokens": 3, "steps": ["a", "b", "c", "d"]}}
    ]
    plan["block_actions"]["blk-4"] = {
        "action": "fold", "fold_group": "derivation"}
    # 换一个非 key 公式承载：把 blk-9 加入正文 sec-2 并作为关键公式
    normalized["sections"][1]["blocks"].append("blk-9")
    normalized["blocks"].insert(
        8, {"id": "blk-9", "type": "equation", "page_idx": 2, "latex": "Y=2"}
    )
    plan["block_actions"]["blk-9"] = {"action": "narrate"}
    plan["key_formulas"] = [
        {"block_id": "blk-9", "latex": "Y=2", "label": "y",
         "worked_example_required": True,
         "example_spec": {"tokens": 3, "steps": ["a", "b", "c", "d"]}}
    ]
    problems = validate_plan(plan, normalized)
    assert not any("derivation" in p and "blk-4" in p for p in problems)


# ---------------------------------------------------------------------------
# T047：图片决策 rationale 必填非空（FR-027）
# ---------------------------------------------------------------------------


def _doc_with_image_figure() -> tuple[dict, dict]:
    normalized = deepcopy(_normalized())
    plan = deepcopy(_good_plan())
    normalized["sections"][0]["blocks"].append("blk-f")
    normalized["blocks"].append(
        {"id": "blk-f", "type": "figure", "page_idx": 0,
         "img_path": "images/f.jpg", "mineru_type": "image",
         "caption": "Figure 1: architecture"}
    )
    return normalized, plan


def test_figure_decision_requires_nonempty_rationale() -> None:
    normalized, plan = _doc_with_image_figure()
    for decision in (
        {"action": "keep_figure"},
        {"action": "keep_figure", "rationale": "   "},
        {"action": "drop", "rationale": ""},
    ):
        plan["block_actions"]["blk-f"] = decision
        problems = validate_plan(plan, normalized)
        assert any(
            "blk-f" in p and "rationale" in p for p in problems
        ), decision


def test_figure_decision_with_rationale_passes() -> None:
    normalized, plan = _doc_with_image_figure()
    plan["block_actions"]["blk-f"] = {
        "action": "keep_figure",
        "rationale": "caption 表明是模型结构图，保留并重写图注辅助导读"}
    assert validate_plan(plan, normalized) == []


def test_non_figure_decision_does_not_require_rationale() -> None:
    # golden 全部非图片决策均无 rationale，必须通过
    assert validate_plan(_good_plan(), _normalized()) == []


# ---------------------------------------------------------------------------
# T037/FR-026：parse_gap 占位块只能 narrate/condense 进主线
# ---------------------------------------------------------------------------


def _doc_with_parse_gap() -> tuple[dict, dict]:
    normalized = deepcopy(_normalized())
    plan = deepcopy(_good_plan())
    normalized["sections"][0]["blocks"].append("blk-g")
    normalized["blocks"].append(
        {"id": "blk-g", "type": "parse_gap", "page_idx": 0,
         "raw_type": "equation", "gap_kind": "equation",
         "placeholder": "原文公式解析缺失，未编造内容补全。",
         "reason": "latex 为空"}
    )
    return normalized, plan


def test_parse_gap_must_stay_on_mainline() -> None:
    normalized, plan = _doc_with_parse_gap()
    for decision in (
        {"action": "fold", "fold_group": "derivation"},
        {"action": "fold", "fold_group": "repro_data"},
        {"action": "drop"},
        {"action": "keep_figure"},
        {"action": "keep_table_explained"},
    ):
        plan["block_actions"]["blk-g"] = decision
        problems = validate_plan(plan, normalized)
        assert any(
            "blk-g" in p and "parse_gap" in p for p in problems
        ), decision


def test_parse_gap_narrate_or_condense_passes() -> None:
    normalized, plan = _doc_with_parse_gap()
    plan["block_actions"]["blk-g"] = {"action": "narrate"}
    assert validate_plan(plan, normalized) == []
    plan["block_actions"]["blk-g"] = {"action": "condense"}
    assert validate_plan(plan, normalized) == []


# ---------------------------------------------------------------------------
# key_formulas
# ---------------------------------------------------------------------------


def test_key_formulas_at_least_one() -> None:
    plan = _good_plan()
    plan["key_formulas"] = []
    problems = validate_plan(plan, _normalized())
    assert any("key_formulas" in p for p in problems)


def test_exactly_one_worked_example_required() -> None:
    plan = _good_plan()
    plan["key_formulas"][1]["worked_example_required"] = True
    problems = validate_plan(plan, _normalized())
    assert any("worked_example_required" in p and "恰好 1" in p for p in problems)


def test_worked_example_formula_block_must_exist_and_be_equation() -> None:
    plan = _good_plan()
    plan["key_formulas"][0]["block_id"] = "blk-2"  # paragraph
    problems = validate_plan(plan, _normalized())
    assert any("blk-2" in p and "equation" in p for p in problems)

    plan = _good_plan()
    plan["key_formulas"][0]["block_id"] = "blk-404"
    problems = validate_plan(plan, _normalized())
    assert any("blk-404" in p for p in problems)


def test_key_formula_latex_must_match_source() -> None:
    plan = _good_plan()
    plan["key_formulas"][0]["latex"] = "Y=2"
    problems = validate_plan(plan, _normalized())
    assert any("latex" in p and "blk-4" in p for p in problems)


def test_required_example_spec_tokens_in_3_5() -> None:
    plan = _good_plan()
    plan["key_formulas"][0]["example_spec"]["tokens"] = 2
    problems = validate_plan(plan, _normalized())
    assert any("example_spec" in p or "tokens" in p for p in problems)


def test_required_example_spec_must_be_present() -> None:
    plan = _good_plan()
    del plan["key_formulas"][0]["example_spec"]
    problems = validate_plan(plan, _normalized())
    assert any("example_spec" in p for p in problems)


def test_key_formula_block_must_not_be_dropped_or_folded() -> None:
    plan = _good_plan()
    plan["block_actions"]["blk-4"] = {"action": "fold", "fold_group": "derivation"}
    problems = validate_plan(plan, _normalized())
    assert any("blk-4" in p for p in problems)


def test_validator_does_not_mutate_input() -> None:
    plan = _good_plan()
    snapshot = deepcopy(plan)
    validate_plan(plan, _normalized())
    assert plan == snapshot


# ---------------------------------------------------------------------------
# run_planner：调用 + 重试 + 落盘
# ---------------------------------------------------------------------------


def _prepare_run(tmp_path) -> object:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "normalized.json").write_text(
        json.dumps(_normalized(), ensure_ascii=False), encoding="utf-8"
    )
    return run_dir


def _llm_plan() -> dict:
    """schema + 业务双合法的 planner LLM 响应。"""
    return {"schema_kind": "planner", **_good_plan()}


def test_run_planner_retries_once_on_business_violation_then_writes_plan(
    tmp_path, make_fake_llm
) -> None:
    run_dir = _prepare_run(tmp_path)
    bad = _llm_plan()
    del bad["block_actions"]["blk-2"]  # schema 合法但业务校验不过
    good = _llm_plan()
    llm = make_fake_llm({"planner": [bad, good]})

    plan = run_planner(run_dir, llm)
    assert plan == good
    assert llm.schema_kinds_called() == ["planner", "planner"]
    # T040：业务校验问题必须回喂到第二次调用的 user 文本
    second_user = llm.calls[1][1]
    assert "上一次规划未通过确定性校验" in second_user
    assert "blk-2" in second_user
    written = json.loads((run_dir / PLAN_FILENAME).read_text(encoding="utf-8"))
    assert written == good


def test_run_planner_retries_once_on_schema_violation(tmp_path, make_fake_llm) -> None:
    run_dir = _prepare_run(tmp_path)
    bad_schema = {"schema_kind": "planner"}
    good = _llm_plan()
    llm = make_fake_llm({"planner": [bad_schema, good]})

    plan = run_planner(run_dir, llm)
    assert plan["schema_kind"] == "planner"
    assert llm.call_count == 2


def test_run_planner_raises_after_three_invalid_attempts_without_plan_file(
    tmp_path, make_fake_llm
) -> None:
    run_dir = _prepare_run(tmp_path)
    bad = _llm_plan()
    del bad["block_actions"]["blk-2"]
    llm = make_fake_llm({"planner": [bad, bad, bad]})

    with pytest.raises(LLMError, match="3 次尝试"):
        run_planner(run_dir, llm)
    assert not (run_dir / PLAN_FILENAME).exists()
    assert llm.call_count == 3


def test_run_planner_missing_normalized_raises_input_error(
    tmp_path, make_fake_llm
) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    llm = make_fake_llm({"planner": _llm_plan()})
    with pytest.raises(Exception, match="normalized.json"):
        run_planner(empty_dir, llm)
    assert llm.call_count == 0


def test_run_planner_canonicalizes_wrong_segment_order_without_retry(
    tmp_path, make_fake_llm
) -> None:
    """T040：七项齐全但顺序错乱（模型把 conclusion 排到 intro 前）由代码
    确定性归位，不消耗重试、不要求模型重排。"""
    run_dir = _prepare_run(tmp_path)
    response = _llm_plan()
    # 交换 intro 与 conclusion
    order = response["target_order"]
    order[1], order[2] = order[2], order[1]
    llm = make_fake_llm({"planner": response})

    plan = run_planner(run_dir, llm)
    assert llm.call_count == 1  # 一次通过，无重试
    slugs = [t["target_slug"] for t in plan["target_order"]]
    assert slugs == list(SLUG_ORDER)
    written = json.loads((run_dir / PLAN_FILENAME).read_text(encoding="utf-8"))
    assert [t["target_slug"] for t in written["target_order"]] == list(SLUG_ORDER)
    # 取材映射保持模型原样（只是位置归位）
    intro_entry = written["target_order"][1]
    assert intro_entry["target_slug"] == "intro"
    assert intro_entry["source_section_ids"] == ["sec-2"]


def test_run_planner_missing_segment_still_fails_validation(
    tmp_path, make_fake_llm
) -> None:
    """七项不齐全（缺段/重复段）时不归位，按真实业务错误重试耗尽。"""
    run_dir = _prepare_run(tmp_path)
    bad = _llm_plan()
    bad["target_order"][2] = _entry("intro", ["sec-9"], "front_load_extract")
    llm = make_fake_llm({"planner": [bad, bad, bad]})

    with pytest.raises(LLMError, match="3 次尝试"):
        run_planner(run_dir, llm)
    assert not (run_dir / PLAN_FILENAME).exists()


# ---------------------------------------------------------------------------
# _repair_plan：T040 真实首跑——机械性违规确定性修复（不消耗重试）
# ---------------------------------------------------------------------------


def _repair_fixture() -> tuple[dict, dict]:
    normalized = {
        "paper_id": "p",
        "title": "Demo",
        "sections": [
            {"id": "sec-1", "heading": "1 Intro", "level": 1, "kind": "introduction",
             "page_start": 0, "blocks": ["blk-1", "blk-f1", "blk-f2"]},
            {"id": "sec-2", "heading": "2 Method", "level": 1, "kind": "body",
             "page_start": 1, "blocks": ["blk-eq", "blk-f3"]},
        ],
        "blocks": [
            {"id": "blk-1", "type": "paragraph", "page_idx": 0},
            {"id": "blk-f1", "type": "figure", "mineru_type": "chart",
             "page_idx": 2, "caption": "Figure 3: Attention visualizations"},
            {"id": "blk-f2", "type": "figure", "mineru_type": "image",
             "page_idx": 2, "caption": None},
            {"id": "blk-eq", "type": "equation", "page_idx": 3, "latex": "A=B"},
            {"id": "blk-f3", "type": "figure", "mineru_type": "image",
             "page_idx": 3, "caption": "Figure 1: Architecture"},
        ],
        "appendix_section_ids": [],
    }

    def entry(slug, sources, treatment="brief"):
        return {"target_slug": slug, "source_section_ids": sources,
                "treatment": treatment}

    plan = {
        "schema_kind": "planner",
        "target_order": [
            entry("tldr", ["sec-1"]),
            entry("intro", ["sec-1-extract"], "expand"),
            entry("conclusion", ["sec-1-tail"], "conclusion_only"),
            entry("related", ["sec-2-tail"], "compress"),
            entry("method", ["sec-2"], "tutorial"),
            entry("experiments", ["sec-2-extract"], "conclusion_only"),
            entry("wrapup", ["sec-1-tail"], "summary"),
        ],
        "block_actions": {
            "blk-1": {"action": "narrate"},
            # chart 折叠合法但缺 rationale（真实首跑 blk-0174 同款）
            "blk-f1": {"action": "fold", "fold_group": "repro_data"},
            # image 保留但缺 rationale 且无 caption
            "blk-f2": {"action": "keep_figure", "rewrite_caption": True},
            # 关键公式却被 fold（真实首跑 blk-0093 同款）
            "blk-eq": {"action": "fold", "fold_group": "derivation"},
            # 已有 rationale 的图片保持不动
            "blk-f3": {"action": "keep_figure", "rationale": "结构图，导读用"},
        },
        "key_formulas": [
            {"block_id": "blk-eq", "latex": "A=B",
             "worked_example_required": True,
             "example_spec": {"tokens": 3, "steps": ["a", "b", "c"]}},
        ],
    }
    return normalized, plan


def test_repair_plan_fills_figure_rationale_from_real_evidence() -> None:
    normalized, plan = _repair_fixture()
    # 修复前确实违规
    problems_before = validate_plan(plan, normalized)
    assert any("blk-f1" in p and "rationale" in p for p in problems_before)
    assert any("blk-f2" in p and "rationale" in p for p in problems_before)

    notes = _repair_plan(plan, normalized)

    r1 = plan["block_actions"]["blk-f1"]["rationale"]
    r2 = plan["block_actions"]["blk-f2"]["rationale"]
    assert r1.startswith("（自动补全）")
    assert "chart" in r1 and "Attention visualizations" in r1
    assert r2.startswith("（自动补全）") and "无图注" in r2
    # 已有理由不被覆盖
    assert plan["block_actions"]["blk-f3"]["rationale"] == "结构图，导读用"
    assert len(notes) == 3  # 2 个 rationale + 1 个关键公式
    assert validate_plan(plan, normalized) == []


def test_repair_plan_coerces_key_formula_fold_to_narrate() -> None:
    normalized, plan = _repair_fixture()
    _repair_plan(plan, normalized)
    decision = plan["block_actions"]["blk-eq"]
    assert decision["action"] == "narrate"
    assert "fold_group" not in decision
    assert validate_plan(plan, normalized) == []


def test_run_planner_mechanical_violations_pass_on_first_attempt(
    tmp_path, make_fake_llm
) -> None:
    """真实首跑场景：模型一次输出带 4 处机械违规，确定性修复后直接通过，
    不重试。"""
    normalized, plan = _repair_fixture()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "normalized.json").write_text(
        json.dumps(normalized, ensure_ascii=False), encoding="utf-8"
    )
    llm = make_fake_llm({"planner": plan})

    written = run_planner(run_dir, llm)
    assert llm.call_count == 1
    assert written["block_actions"]["blk-eq"]["action"] == "narrate"
    assert written["block_actions"]["blk-f1"]["rationale"].startswith("（自动补全）")
    on_disk = json.loads((run_dir / PLAN_FILENAME).read_text(encoding="utf-8"))
    assert on_disk["block_actions"]["blk-eq"]["action"] == "narrate"
