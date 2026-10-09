"""T011-T015：parse_gap 呈现降级（疑似图位 / gap 合并 / prompt 去内部 id）。

对 survey fixture：
- normalize 对大 bbox 空 text 置 suspected="figure"（保守命中）；
- planner 每个 slug 的 gap 合并为至多一条 gap_range，带页码范围；
- prompt_builder 块头用 [块N]、无 blk-/content_list；gap 每章只插一条提示；
- 空章不构造 prompt（Phase 3 保证），只见固定文案。
"""

from __future__ import annotations

import json

import pytest

from core.normalize import normalize_content_list
from core.planner import build_plan
from core.rewrite.prompt_builder import build_section_prompt
from core.pipeline import transform_paper
from web.app.config import WEB_DIR

FIXTURE_DIR = WEB_DIR / "fixtures" / "survey_run"


@pytest.fixture(scope="module")
def survey_plan() -> tuple[dict, dict]:
    raw = json.loads(
        (FIXTURE_DIR / "parsed" / "content_list.json").read_text(encoding="utf-8")
    )
    norm = normalize_content_list(raw, paper_id="survey")
    return norm, build_plan(norm)


# T011：大 bbox 空文本条目标 suspected=figure（保守：只命中近栏宽的大图）
def test_large_bbox_gap_marked_suspected_figure():
    raw = [
        {"type": "text", "text_level": 1, "text": "Title", "page_idx": 0},
        {"type": "text", "text_level": 2, "text": "1 Introduction", "page_idx": 0},
        # 378×241 近整栏大图
        {"type": "text", "text": "", "page_idx": 0, "bbox": [112, 85, 490, 326]},
        # 100×40 小残片，不得误判
        {"type": "text", "text": "", "page_idx": 0, "bbox": [112, 85, 212, 125]},
        # 无 bbox 不判
        {"type": "text", "text": "", "page_idx": 1},
    ]
    norm = normalize_content_list(raw, paper_id="gapfig")
    gaps = [b for b in norm["blocks"] if b["type"] == "parse_gap"]
    assert len(gaps) == 3
    assert gaps[0].get("suspected") == "figure"
    assert "suspected" not in gaps[1]
    assert "suspected" not in gaps[2]


# T012：survey 的每个非空 slug 至多一条 gap_range
def test_gap_ranges_merged_per_slug(survey_plan):
    _norm, plan = survey_plan
    ranges = plan["gap_ranges"]
    assert set(ranges.keys()) == {t["target_slug"] for t in plan["target_order"]}
    for slug, rs in ranges.items():
        assert len(rs) <= 1, slug

    method_range = ranges["method"][0]
    # method 章 gaps 跨 page 1..7（0-based，末块 blk-0083 在第 7 页）
    assert method_range["page_start"] == 1
    assert method_range["page_end"] == 7
    assert method_range["suspected"] == "figure"  # 26/34/47 命疑似图位
    assert len(method_range["block_ids"]) >= 2

    # intro 只有一条普通 gap
    assert ranges["intro"][0]["suspected"] is None


# T012：被合并的 gap 在 block_actions 上记 merged_into，动作仍是 narrate
def test_merged_gaps_marked(survey_plan):
    _norm, plan = survey_plan
    method_range = plan["gap_ranges"]["method"][0]
    for bid in method_range["block_ids"]:
        act = plan["block_actions"][bid]
        assert act["action"] == "narrate"
        assert act["merged_into"].startswith("method#gap-")


# T013(a)：prompt 无内部 id、无 content_list；块头为 [块N]
def test_prompt_has_no_internal_ids(survey_plan):
    norm, plan = survey_plan
    import re

    for slug in ("intro", "method", "conclusion"):
        _system, user = build_section_prompt(norm, plan, slug)
        assert not re.search(r"blk-\d", user)
        assert "content_list" not in user
        assert "[块1]" in user


# T013(b)：同一章 prompt 中缺失提示 ≤1 条
def test_prompt_gap_note_at_most_one(survey_plan):
    norm, plan = survey_plan
    _system, method_user = build_section_prompt(norm, plan, "method")
    assert method_user.count("[解析缺失") == 1
    # 疑似图片措辞进提示
    assert "疑似" in method_user
    assert "解析未提取" in method_user


# T013：折叠块摘要也不带内部 id
def test_fold_summary_no_ids(survey_plan):
    norm, plan = survey_plan
    _system, user = build_section_prompt(norm, plan, "method")
    # 摘要里不得出现 blk-xxxx
    import re

    assert not re.search(r"blk-\d", user)


# T015(d)：空章不构造 prompt —— pipeline 空章 chat 零调用已在
# test_core_survey_regression / test_core_empty_fallback 固化；这里只确认
# 空章即便被外部调用 build_section_prompt 也不会罗列 gap（related 无 gap）。
def test_empty_slug_has_no_gap_ranges(survey_plan):
    _norm, plan = survey_plan
    for slug in plan["empty_slugs"]:
        assert plan["gap_ranges"][slug] == []
