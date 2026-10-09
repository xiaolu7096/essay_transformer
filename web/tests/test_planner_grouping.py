"""T003：planner 层级归组 + 空章判定的构造文档测试。

三类文档：
① standard：六类小节齐全的标准论文（abstract/intro/related/method/experiments/conclusion）；
② survey：无 related_work、无 experiments，多个顶级方法节（§2/§2.1/§3/§3.1 …）；
③ short：仅 abstract+intro+conclusion 的短篇。

期望行为（T005/T006 实现前整体 xfail）：
- target_order 恒为 SLUG_ORDER 7 项，每项含 empty bool；
- plan 顶层含 empty_slugs；
- 层级归组：顶级节与其全部后代节强制同 slug；
- 多章可同时 empty；标准论文七章 empty 全为 false。
另含 T004 的 _classify_kind 关键词增补期望（xfail 至 T004）。
"""

from __future__ import annotations

import pytest

from core.normalize import _classify_kind
from core.planner import SLUG_ORDER, build_plan


def _block(bid: str, btype: str = "paragraph", page: int = 0, **extra):
    b = {"id": bid, "type": btype, "page_idx": page, "text": f"text of {bid}"}
    b.update(extra)
    return b


def _section(sid, kind, level, heading, block_ids, page_start=0):
    return {
        "id": sid,
        "kind": kind,
        "level": level,
        "heading": heading,
        "page_start": page_start,
        "blocks": block_ids,
    }


def _norm(sections, blocks):
    return {
        "paper_id": "p",
        "title": "T",
        "sections": sections,
        "blocks": blocks,
        "references": [],
        "appendix_section_ids": [
            s["id"] for s in sections if s["kind"] == "appendix"
        ],
    }


def _standard_norm():
    secs = [
        _section("sec-abstract", "abstract", 2, "Abstract", ["b-abs", "h-abs"]),
        _section("sec-1", "introduction", 1, "Introduction", ["b-int", "h-int"]),
        _section("sec-2", "related_work", 1, "Related Work", ["b-rel", "h-rel"]),
        _section("sec-3", "unclassified", 1, "Method", ["b-m", "h-m"]),
        _section("sec-3-1", "unclassified", 2, "Details", ["b-m1"]),
        _section("sec-4", "experiments", 1, "Experiments", ["b-e", "h-e"]),
        _section("sec-5", "conclusion", 1, "Conclusion", ["b-con", "h-con"]),
        _section(
            "sec-a", "appendix", 2, "Appendix: Implementation Notes",
            ["h-a", "b-a"],
        ),
    ]
    bids = [bid for s in secs for bid in s["blocks"]]
    blocks = [_block(bid) for bid in dict.fromkeys(bids)]
    return _norm(secs, blocks)


def _survey_norm():
    # 顶级方法节 §2/§3/§4/§5，全部应归 method；无 related、无 experiments
    secs = [
        _section("sec-abstract", "abstract", 2, "Abstract", ["b-abs", "h-abs"]),
        _section("sec-1", "introduction", 1, "Introduction", ["b-int", "h-int"]),
        _section("sec-2", "unclassified", 1, "Task Formulation", ["h-2", "b-2"]),
        _section("sec-2-1", "unclassified", 2, "Integrating Layout", ["b-21"]),
        _section("sec-2-2", "unclassified", 2, "Pre-training", ["b-22"]),
        _section("sec-3", "unclassified", 1, "Methods Taxonomy", ["h-3", "b-3"]),
        _section("sec-3-1", "unclassified", 2, "Extractive", ["b-31"]),
        _section("sec-6", "conclusion", 1, "Conclusion", ["b-con", "h-con"]),
    ]
    bids = [bid for s in secs for bid in s["blocks"]]
    blocks = [_block(bid) for bid in dict.fromkeys(bids)]
    return _norm(secs, blocks)


def _short_norm():
    secs = [
        _section("sec-abstract", "abstract", 2, "Abstract", ["b-abs", "h-abs"]),
        _section("sec-1", "introduction", 1, "Introduction", ["b-int", "h-int"]),
        _section("sec-2", "conclusion", 1, "Conclusion", ["b-con", "h-con"]),
    ]
    bids = [bid for s in secs for bid in s["blocks"]]
    blocks = [_block(bid) for bid in dict.fromkeys(bids)]
    return _norm(secs, blocks)


def _slug(plan, sec_id):
    for t in plan["target_order"]:
        if sec_id in t["source_section_ids"]:
            return t["target_slug"]
    return None


def _by_slug(plan):
    return {t["target_slug"]: t for t in plan["target_order"]}


# ---------- 结构契约：恒 7 项 + empty/empty_slugs ----------

@pytest.mark.parametrize("factory", [_standard_norm, _survey_norm, _short_norm])
def test_target_order_shape(factory):
    plan = build_plan(factory())
    assert [t["target_slug"] for t in plan["target_order"]] == list(SLUG_ORDER)
    assert "empty_slugs" in plan
    for t in plan["target_order"]:
        assert isinstance(t["empty"], bool)


def test_standard_all_nonempty():
    # 标准论文六类小节齐全 + appendix 总评素材，七章均非空
    plan = build_plan(_standard_norm())
    assert all(t["empty"] is False for t in plan["target_order"])
    assert plan["empty_slugs"] == []


def test_survey_empty_slugs():
    plan = build_plan(_survey_norm())
    by = _by_slug(plan)
    assert by["related"]["empty"] is True
    assert by["experiments"]["empty"] is True
    assert by["wrapup"]["empty"] is True
    assert by["method"]["empty"] is False
    assert by["intro"]["empty"] is False
    # 有序
    assert plan["empty_slugs"] == [
        s for s in SLUG_ORDER if _by_slug(plan)[s]["empty"]
    ]


def test_short_empty_slugs():
    plan = build_plan(_short_norm())
    by = _by_slug(plan)
    for slug in ("related", "method", "experiments", "wrapup"):
        assert by[slug]["empty"] is True
    for slug in ("tldr", "intro", "conclusion"):
        assert by[slug]["empty"] is False


# ---------- 层级归组 ----------

def test_survey_hierarchical_grouping():
    plan = build_plan(_survey_norm())
    anchor = _slug(plan, "sec-2")
    assert anchor == "method"  # 无评测词，顶级方法组归 method
    assert _slug(plan, "sec-2-1") == anchor
    assert _slug(plan, "sec-2-2") == anchor
    assert _slug(plan, "sec-3") == "method"
    assert _slug(plan, "sec-3-1") == "method"
    # 没有任何真实评测内容 → experiments 整章留空
    assert _by_slug(plan)["experiments"]["source_section_ids"] == []


def test_standard_method_children_grouped():
    plan = build_plan(_standard_norm())
    # 标准论文里这一归组现状即正确，T005 重写后必须保持
    assert _slug(plan, "sec-3") == "method"
    assert _slug(plan, "sec-3-1") == "method"
    assert _slug(plan, "sec-4") == "experiments"


# 含评测词的层级组整组判 experiments
def test_group_with_evaluation_keyword_goes_experiments():
    secs = [
        _section("sec-abstract", "abstract", 2, "Abstract", ["b-abs"]),
        _section("sec-1", "introduction", 1, "Introduction", ["b-int"]),
        _section("sec-4", "unclassified", 1, "Evaluation and Results", ["h-4"]),
        _section("sec-4-1", "unclassified", 2, "Metrics", ["b-41"]),
    ]
    bids = [bid for s in secs for bid in s["blocks"]]
    plan = build_plan(_norm(secs, [_block(b) for b in dict.fromkeys(bids)]))
    assert _slug(plan, "sec-4") == "experiments"
    assert _slug(plan, "sec-4-1") == "experiments"
    # method 无实质内容
    assert _by_slug(plan)["method"]["empty"] is True


# ---------- T004：_classify_kind 关键词增补 ----------

def test_classify_conclusion_keywords():
    assert _classify_kind("Limitations and Future Work", text_level=2) == "conclusion"


def test_classify_existing_keywords_unchanged():
    # 现有关键词归类保持不变
    assert _classify_kind("Discussion") == "conclusion"
    assert _classify_kind("Background") == "related_work"


def test_classify_related_new_keywords():
    assert _classify_kind("Overview of Prior Systems", text_level=2) == "related_work"
    assert _classify_kind("A Survey of Prior Methods", text_level=2) == "related_work"


def test_title_level_not_misclassified():
    # "A Survey on ..." 是 text_level<=1 的文档总标题，不应判 related_work
    assert _classify_kind(
        "A Survey on Question Answering over Documents", text_level=1
    ) != "related_work"
