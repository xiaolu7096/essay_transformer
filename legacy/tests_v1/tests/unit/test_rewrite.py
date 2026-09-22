"""T023：分章耦合转写的确定性校验、并发/重试与表外词聚合。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from essay_transformer.errors import LLMError
from essay_transformer.glossary import Term
from essay_transformer.planner import SLUG_ORDER
from essay_transformer.rewrite import (
    WORKED_EXAMPLE_ID,
    build_section_payload,
    clamp_workers,
    merge_out_of_glossary,
    run_rewrite,
    section_path,
    validate_section_response,
)


# ---------------------------------------------------------------------------
# 夹具数据（与 test_planner_mapping 的最小 normalized/plan 同构）
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
            {"id": "sec-a", "heading": "Appendix A", "level": 1,
             "kind": "appendix", "page_start": 12,
             "blocks": ["blk-7", "blk-8"]},
        ],
        "blocks": [
            {"id": "blk-1", "type": "heading", "page_idx": 0, "text": "1 Introduction"},
            {"id": "blk-2", "type": "paragraph", "page_idx": 0, "text": "English body."},
            {"id": "blk-3", "type": "heading", "page_idx": 1, "text": "2 Method"},
            {"id": "blk-4", "type": "equation", "page_idx": 1, "latex": "X=1"},
            {"id": "blk-5", "type": "heading", "page_idx": 9, "text": "9 Conclusion"},
            {"id": "blk-6", "type": "paragraph", "page_idx": 9, "text": "Conclude."},
            {"id": "blk-7", "type": "heading", "page_idx": 12},
            {"id": "blk-8", "type": "paragraph", "page_idx": 12},
        ],
        "appendix_section_ids": ["sec-a"],
    }


def _entry(slug, sources, treatment="brief"):
    return {"target_slug": slug, "source_section_ids": sources, "treatment": treatment}


def _plan() -> dict:
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
            "blk-7": {"action": "drop"},
            "blk-8": {"action": "fold", "fold_group": "derivation"},
        },
        "key_formulas": [
            {"block_id": "blk-4", "latex": "X=1", "worked_example_required": True,
             "example_spec": {"tokens": 3, "steps": ["a", "b", "c", "d"]}},
        ],
    }


def _terms() -> list[Term]:
    return [
        Term(en="self-attention", zh="自注意力机制", mode="bilingual",
             scope="seed", source="seed", aliases=["self attention"]),
        Term(en="softmax", zh="softmax", mode="zh_only",
             scope="seed", source="seed"),
    ]


@pytest.fixture()
def context() -> dict:
    normalized = _normalized()
    plan = _plan()
    terms = _terms()
    payloads = {
        entry["target_slug"]: build_section_payload(normalized, plan, entry, terms)
        for entry in plan["target_order"]
    }
    pages = {
        bid: block.get("page_idx")
        for block in normalized["blocks"]
        for bid in [block["id"]]
    }
    return {"normalized": normalized, "plan": plan, "terms": terms,
            "payloads": payloads, "pages": pages}


# ---------------------------------------------------------------------------
# payload 装配
# ---------------------------------------------------------------------------


def test_payload_resolves_synthetic_section_ids_and_allowed_blocks(context) -> None:
    related = context["payloads"]["related"]  # sec-9-tail → sec-9
    assert related["real_section_ids"] == ["sec-9"]
    assert related["allowed_block_ids"] == ["blk-5", "blk-6"]

    experiments = context["payloads"]["experiments"]  # sec-2-tail → sec-2
    assert experiments["real_section_ids"] == ["sec-2"]
    assert experiments["allowed_block_ids"] == ["blk-3", "blk-4"]


def test_payload_never_includes_appendix_blocks(context) -> None:
    for slug, payload in context["payloads"].items():
        assert "blk-7" not in payload["allowed_block_ids"], slug
        assert "blk-8" not in payload["allowed_block_ids"], slug


def test_glossary_terms_rendered_with_en_zh_mode_aliases(context) -> None:
    text = context["payloads"]["method"]["glossary_terms"]
    assert "en=self-attention" in text
    assert "zh=自注意力机制" in text
    assert "mode=bilingual" in text
    assert "self attention" in text
    assert "en=softmax" in text and "mode=zh_only" in text


def test_plan_summary_marks_current_segment(context) -> None:
    summary = context["payloads"]["intro"]["plan_summary"]
    assert "1. tldr" in summary and "2. intro（教程式详解" in summary
    assert "← 本段" in summary


# ---------------------------------------------------------------------------
# validate_section_response golden + 违约
# ---------------------------------------------------------------------------


def _good_response(payload: dict, page_lookup: dict) -> dict:
    bid = payload["allowed_block_ids"][0]
    return {
        "schema_kind": "section_rewrite",
        "target_slug": payload["target_slug"],
        "blocks": [
            {"kind": "paragraph", "markdown": "这是中文教程讲解。",
             "source_block_ids": [bid], "source_pages": [page_lookup[bid]]}
        ],
        "out_of_glossary": [],
    }


def _formula_response() -> dict:
    return {
        "schema_kind": "section_rewrite",
        "target_slug": "intro",
        "blocks": [
            {"kind": "formula_card",
             "markdown": "公式卡标题",
             "latex": "X=1",
             "symbol_breakdown": [{"symbol": "X", "meaning": "未知数"}],
             "intuition": "它在算一件小事。",
             "worked_example_ref": WORKED_EXAMPLE_ID,
             "source_block_ids": ["blk-4"], "source_pages": [1]}
        ],
        "out_of_glossary": [],
    }


def test_golden_responses_for_all_seven_slugs_pass(context) -> None:
    for slug in SLUG_ORDER:
        payload = context["payloads"][slug]
        problems = validate_section_response(
            _good_response(payload, context["pages"]), payload,
            worked_example_block_id="blk-4",
        )
        assert problems == [], (slug, problems)


def test_formula_card_golden_passes(context) -> None:
    payload = context["payloads"]["intro"]
    assert validate_section_response(
        _formula_response(), payload, worked_example_block_id="blk-4"
    ) == []


def test_target_slug_mismatch_rejected(context) -> None:
    payload = context["payloads"]["intro"]
    response = _good_response(payload, context["pages"])
    response["target_slug"] = "method"
    problems = validate_section_response(response, payload)
    assert any("target_slug" in p for p in problems)


def test_source_block_id_outside_segment_rejected(context) -> None:
    payload = context["payloads"]["intro"]  # 只有 blk-3/blk-4
    response = _good_response(payload, context["pages"])
    response["blocks"][0]["source_block_ids"] = ["blk-2"]  # sec-1 的块
    problems = validate_section_response(response, payload)
    assert any("blk-2" in p for p in problems)


def test_parse_gap_block_must_not_be_referenced(context) -> None:
    """FR-026（T037）：parse_gap 占位块没有可转写内容，LLM 引用即拒。"""
    normalized = deepcopy(context["normalized"])
    plan = deepcopy(context["plan"])
    # sec-2 是 intro 段的源节；插入一个损坏公式占位块
    normalized["sections"][1]["blocks"].append("blk-gap")
    normalized["blocks"].append({
        "id": "blk-gap", "type": "parse_gap", "raw_type": "equation",
        "gap_kind": "equation", "page_idx": 1,
        "placeholder": "原文公式解析缺失，未编造内容补全。",
        "reason": "latex 为空"})
    plan["block_actions"]["blk-gap"] = {"action": "narrate"}
    entry = next(e for e in plan["target_order"] if e["target_slug"] == "intro")
    payload = build_section_payload(normalized, plan, entry, context["terms"])

    response = _good_response(payload, {**context["pages"], "blk-gap": 1})
    response["blocks"][0]["source_block_ids"] = ["blk-gap"]
    response["blocks"][0]["source_pages"] = [1]
    problems = validate_section_response(response, payload)
    assert any("parse_gap" in p and "blk-gap" in p and "FR-026" in p
               for p in problems)


def test_empty_source_block_ids_rejected_for_factual_block(context) -> None:
    payload = context["payloads"]["intro"]
    response = _good_response(payload, context["pages"])
    response["blocks"][0]["source_block_ids"] = []
    problems = validate_section_response(response, payload)
    assert any("FR-024" in p for p in problems)


def test_supplement_block_requires_empty_sources(context) -> None:
    payload = context["payloads"]["intro"]
    response = _good_response(payload, context["pages"])
    response["blocks"].append(
        {"kind": "supplement", "supplement_kind": "analogy",
         "markdown": "打个比方……", "source_block_ids": ["blk-3"]}
    )
    problems = validate_section_response(response, payload)
    assert any("supplement" in p and "空数组" in p for p in problems)


def test_supplement_block_golden_passes_without_sources(context) -> None:
    payload = context["payloads"]["intro"]
    response = _good_response(payload, context["pages"])
    response["blocks"].append(
        {"kind": "supplement", "supplement_kind": "background",
         "markdown": "补充背景……", "source_block_ids": []}
    )
    assert validate_section_response(response, payload) == []


def test_tldr_block_only_allowed_in_tldr_segment(context) -> None:
    payload = context["payloads"]["intro"]
    response = {
        "schema_kind": "section_rewrite", "target_slug": "intro",
        "blocks": [{"kind": "tldr", "markdown": "太快了。",
                    "source_block_ids": ["blk-3"], "source_pages": [1]}],
        "out_of_glossary": [],
    }
    problems = validate_section_response(response, payload)
    assert any("tldr 块只允许" in p for p in problems)


def test_formula_card_requires_three_pieces(context) -> None:
    payload = context["payloads"]["intro"]
    response = _formula_response()
    block = response["blocks"][0]
    del block["intuition"]
    block["symbol_breakdown"] = []
    problems = validate_section_response(response, payload)
    assert any("intuition" in p for p in problems)
    assert any("symbol_breakdown" in p for p in problems)


def test_formula_card_latex_must_match_original(context) -> None:
    payload = context["payloads"]["intro"]
    response = _formula_response()
    response["blocks"][0]["latex"] = "Y = 2"
    problems = validate_section_response(response, payload)
    assert any("latex" in p and "FR-003" in p for p in problems)


def test_formula_card_worked_example_ref_rules(context) -> None:
    payload = context["payloads"]["intro"]

    bad_ref = _formula_response()
    bad_ref["blocks"][0]["worked_example_ref"] = "we-999"
    problems = validate_section_response(
        bad_ref, payload, worked_example_block_id="blk-4"
    )
    assert any("we-999" in p for p in problems)

    wrong_source = _formula_response()
    wrong_source["blocks"][0]["source_block_ids"] = ["blk-3"]
    problems = validate_section_response(
        wrong_source, payload, worked_example_block_id="blk-4"
    )
    assert any("关键公式块" in p and "blk-4" in p for p in problems)


def test_figure_requires_caption_and_original_img_path(context) -> None:
    payload = context["payloads"]["intro"]
    response = {
        "schema_kind": "section_rewrite", "target_slug": "intro",
        "blocks": [{"kind": "figure", "img_path": "images/x.jpg",
                    "rewritten_caption": "",
                    "source_block_ids": ["blk-3"], "source_pages": [1]}],
        "out_of_glossary": [],
    }
    problems = validate_section_response(response, payload)
    assert any("rewritten_caption" in p for p in problems)
    assert any("img_path" in p for p in problems)


def test_table_explained_requires_table_and_explanation(context) -> None:
    payload = context["payloads"]["intro"]
    response = {
        "schema_kind": "section_rewrite", "target_slug": "intro",
        "blocks": [{"kind": "table_explained",
                    "source_block_ids": ["blk-3"], "source_pages": [1]}],
        "out_of_glossary": [],
    }
    problems = validate_section_response(response, payload)
    assert any("markdown_table" in p for p in problems)
    assert any("explanation" in p for p in problems)


def test_fold_ref_requires_group_title_content(context) -> None:
    payload = context["payloads"]["intro"]
    response = {
        "schema_kind": "section_rewrite", "target_slug": "intro",
        "blocks": [{"kind": "fold_ref",
                    "source_block_ids": ["blk-3"], "source_pages": [1]}],
        "out_of_glossary": [],
    }
    problems = validate_section_response(response, payload)
    assert any("fold_group" in p for p in problems)
    assert any("title" in p for p in problems)
    assert any("content_markdown" in p for p in problems)


def _payload_with_reference_list(context: dict) -> dict:
    """在 sec-9 追加一个 reference_list 块，返回 method（sec-9-extract）payload。"""
    normalized = deepcopy(context["normalized"])
    plan = deepcopy(context["plan"])
    sec9 = next(s for s in normalized["sections"] if s["id"] == "sec-9")
    sec9["blocks"].append("blk-9r")
    normalized["blocks"].append(
        {"id": "blk-9r", "type": "reference_list", "page_idx": 9,
         "items": ["[1] Some Ref. 2016."]}
    )
    entry = next(e for e in plan["target_order"]
                 if e["target_slug"] == "method")
    return build_section_payload(normalized, plan, entry, context["terms"])


def test_references_fold_requires_reference_list_source(context) -> None:
    payload = _payload_with_reference_list(context)

    def fold(sources: list[str]) -> dict:
        return {
            "schema_kind": "section_rewrite", "target_slug": "method",
            "blocks": [{
                "kind": "fold_ref", "fold_group": "references",
                "title": "参考文献",
                "content_markdown": "[1] Some Ref. 2016.",
                "source_block_ids": sources, "source_pages": [9],
            }],
            "out_of_glossary": [],
        }

    assert validate_section_response(fold(["blk-9r"]), payload) == []
    problems = validate_section_response(fold(["blk-6"]), payload)
    assert any("reference_list" in p and "references" in p for p in problems)


def test_source_pages_out_of_range_rejected(context) -> None:
    payload = context["payloads"]["intro"]
    response = _good_response(payload, context["pages"])
    response["blocks"][0]["source_pages"] = [42]
    problems = validate_section_response(response, payload)
    assert any("source_pages" in p for p in problems)


def test_out_of_glossary_page_must_be_in_segment(context) -> None:
    payload = context["payloads"]["intro"]
    response = _good_response(payload, context["pages"])
    response["out_of_glossary"] = [
        {"term": "sublayer", "context": "two sub-layers", "page_idx": 9}
    ]
    problems = validate_section_response(response, payload)
    assert any("page_idx" in p for p in problems)


# ---------------------------------------------------------------------------
# merge_out_of_glossary
# ---------------------------------------------------------------------------


def test_merge_out_of_glossary_dedup_case_insensitive_and_locations() -> None:
    collected = [
        {"term": "Sublayer", "context": "two sub-layers", "page_idx": 0,
         "section": "tldr"},
        {"term": "sublayer", "context": "each sublayer", "page_idx": 1,
         "section": "intro", "temporary_handling": "子层（临时）"},
        {"term": "BPE", "context": "byte pair encoding", "page_idx": 3,
         "section": "method"},
    ]
    merged = merge_out_of_glossary(collected)
    assert [r["term"] for r in merged] == ["Sublayer", "BPE"]
    sub = merged[0]
    assert [loc["section"] for loc in sub["locations"]] == ["tldr", "intro"]
    assert sub["locations"][1]["temporary_handling"] == "子层（临时）"
    assert "temporary_handling" not in sub["locations"][0]


def test_merge_out_of_glossary_skips_blank_terms() -> None:
    assert merge_out_of_glossary([{"term": "   "}]) == []


def test_clamp_workers_hard_cap() -> None:
    assert clamp_workers(4) == 4
    assert clamp_workers(99) == 4
    assert clamp_workers(1) == 1
    assert clamp_workers(0) == 1
    assert clamp_workers(-3) == 1


# ---------------------------------------------------------------------------
# run_rewrite：并发 + 落盘 + 重试
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"- `(?P<slug>[a-z_]+)`：本段目标段")


def _prepare_run(tmp_path: Path, context: dict) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "normalized.json").write_text(
        json.dumps(context["normalized"], ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "plan.json").write_text(
        json.dumps({"schema_kind": "planner", **context["plan"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return run_dir


def _responses(context: dict) -> dict:
    return {
        slug: _good_response(payload, context["pages"])
        for slug, payload in context["payloads"].items()
    }


def test_run_rewrite_writes_seven_section_files_and_collects_oog(
    tmp_path, context, make_fake_llm
) -> None:
    run_dir = _prepare_run(tmp_path, context)
    recorded = _responses(context)
    # tldr/intro 各报一个表外词（同词不同写法/不同段）
    recorded["tldr"]["out_of_glossary"] = [
        {"term": "Sublayer", "context": "two sub-layers", "page_idx": 0}
    ]
    recorded["intro"]["out_of_glossary"] = [
        {"term": "sublayer", "context": "each sublayer", "page_idx": 1,
         "temporary_handling": "子层"}
    ]

    def answer(system, user, schema_kind):
        slug = _SLUG_RE.search(user).group("slug")
        return recorded[slug]

    llm = make_fake_llm({"section_rewrite": answer})
    result = run_rewrite(run_dir, llm, terms=context["terms"], jobs=4)

    assert llm.schema_kinds_called() == ["section_rewrite"] * 7
    # 七段文件齐备，且没有任何额外/译文中间产物
    files = {p.name for p in (run_dir / "sections").iterdir()}
    assert files == {f"{slug}.json" for slug in SLUG_ORDER}
    for slug in SLUG_ORDER:
        written = json.loads(section_path(run_dir, slug).read_text(encoding="utf-8"))
        assert written == recorded[slug]
        assert written["target_slug"] == slug

    oog = result["out_of_glossary"]
    assert len(oog) == 1
    assert [loc["section"] for loc in oog[0]["locations"]] == ["tldr", "intro"]


def test_run_rewrite_prompt_carries_blocks_actions_and_full_glossary(
    tmp_path, context, make_fake_llm
) -> None:
    run_dir = _prepare_run(tmp_path, context)
    recorded = _responses(context)

    seen: list[str] = []

    def answer(system, user, schema_kind):
        seen.append(user)
        slug = _SLUG_RE.search(user).group("slug")
        return recorded[slug]

    llm = make_fake_llm({"section_rewrite": answer})
    run_rewrite(run_dir, llm, terms=context["terms"])

    intro_prompt = next(u for u in seen if _SLUG_RE.search(u).group("slug") == "intro")
    assert "blk-4" in intro_prompt  # SECTION_BLOCKS / BLOCK_ACTIONS 带入
    assert '"action": "narrate"' in intro_prompt
    assert "en=self-attention" in intro_prompt  # 全量术语逐条枚举
    assert "X=1" in intro_prompt  # 原文公式块入 prompt


def test_run_rewrite_retries_one_segment_once_then_succeeds(
    tmp_path, context, make_fake_llm
) -> None:
    run_dir = _prepare_run(tmp_path, context)
    recorded = _responses(context)
    bad = json.loads(json.dumps(recorded["intro"]))
    bad["blocks"][0]["source_block_ids"] = ["blk-404"]  # schema 合法、业务非法
    attempts = {"intro": 0}

    def answer(system, user, schema_kind):
        slug = _SLUG_RE.search(user).group("slug")
        if slug == "intro":
            attempts["intro"] += 1
            return bad if attempts["intro"] == 1 else recorded["intro"]
        return recorded[slug]

    llm = make_fake_llm({"section_rewrite": answer})
    run_rewrite(run_dir, llm, terms=context["terms"])

    assert attempts["intro"] == 2
    assert llm.call_count == 8  # 6 段一次 + intro 两次
    # T040：业务校验问题回喂到 intro 第二次调用的 user 文本
    intro_users = [
        u
        for (_, u, kind) in llm.calls
        if kind == "section_rewrite" and _SLUG_RE.search(u).group("slug") == "intro"
    ]
    assert "上一次转写未通过确定性校验" in intro_users[1]
    assert "blk-404" in intro_users[1]
    written = json.loads(section_path(run_dir, "intro").read_text(encoding="utf-8"))
    assert written == recorded["intro"]


def test_run_rewrite_persistent_failure_raises_and_writes_nothing(
    tmp_path, context, make_fake_llm
) -> None:
    run_dir = _prepare_run(tmp_path, context)
    recorded = _responses(context)
    bad = json.loads(json.dumps(recorded["intro"]))
    bad["blocks"][0]["source_block_ids"] = ["blk-404"]

    def answer(system, user, schema_kind):
        slug = _SLUG_RE.search(user).group("slug")
        return bad if slug == "intro" else recorded[slug]

    llm = make_fake_llm({"section_rewrite": answer})
    with pytest.raises(LLMError, match="intro"):
        run_rewrite(run_dir, llm, terms=context["terms"])

    assert not (run_dir / "sections").exists()
    assert llm.call_count == 8  # 其他段照跑，intro 耗尽 2 次


def test_run_rewrite_schema_failure_also_retries(
    tmp_path, context, make_fake_llm
) -> None:
    run_dir = _prepare_run(tmp_path, context)
    recorded = _responses(context)
    attempts = {"intro": 0}

    def answer(system, user, schema_kind):
        slug = _SLUG_RE.search(user).group("slug")
        if slug == "intro":
            attempts["intro"] += 1
            if attempts["intro"] == 1:
                return {"schema_kind": "section_rewrite"}  # 结构非法
        return recorded[slug]

    llm = make_fake_llm({"section_rewrite": answer})
    run_rewrite(run_dir, llm, terms=context["terms"])
    assert attempts["intro"] == 2
