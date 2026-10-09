"""T002：survey 论文（任务 2d26f6b9）离线回归。

固化当前问题论文的 4 个事实（红测试，后续任务翻转）：
(a) related/wrapup 无实质正文块，按 FR-001 应判 empty；
(b) §2.1（sec-2-1）被分到与 §2（sec-2）不同的 slug（顺序瓜分 bug）；
(c) fake 端到端成品含 blk- 字样；空相关章仍被调 LLM；
(d) 折叠区表格 HTML 在 </t 处截断，缺少 </table>。
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from core.normalize import normalize_content_list
from core.planner import build_plan
from core.pipeline import transform_paper
from web.app.config import WEB_DIR

FIXTURE_DIR = WEB_DIR / "fixtures" / "survey_run"


@pytest.fixture(scope="module")
def survey_norm() -> dict:
    raw = json.loads(
        (FIXTURE_DIR / "parsed" / "content_list.json").read_text(encoding="utf-8")
    )
    return normalize_content_list(raw, paper_id="survey")


def _slug_of(plan: dict, sec_id: str) -> str:
    for t in plan["target_order"]:
        if sec_id in t["source_section_ids"]:
            return t["target_slug"]
    raise AssertionError(f"{sec_id} 未出现在任何 target_order 中")


def _substantive_blocks(norm: dict, plan: dict, slug: str) -> list[str]:
    """FR-001 实质块：非 heading、非 parse_gap、非 fold/drop 的正文块。"""
    target = next(t for t in plan["target_order"] if t["target_slug"] == slug)
    secs = {s["id"]: s for s in norm["sections"]}
    blocks = {b["id"]: b for b in norm["blocks"]}
    bids: list[str] = []
    for sid in target["source_section_ids"]:
        bids.extend(secs[sid]["blocks"])
    out = []
    for bid in bids:
        b = blocks[bid]
        if b["type"] in ("heading", "parse_gap"):
            continue
        if plan["block_actions"][bid]["action"] in ("fold", "drop"):
            continue
        out.append(bid)
    return out


# (a) survey 的 related/wrapup 实质正文块数为 0
def test_related_wrapup_have_no_substantive_blocks(survey_norm):
    plan = build_plan(survey_norm)
    assert _substantive_blocks(survey_norm, plan, "related") == []
    assert _substantive_blocks(survey_norm, plan, "wrapup") == []


# (a+) FR-001：plan 为空章给出 empty 标记
def test_plan_marks_empty_slugs(survey_norm):
    plan = build_plan(survey_norm)
    assert "empty_slugs" in plan
    related = next(t for t in plan["target_order"] if t["target_slug"] == "related")
    wrapup = next(t for t in plan["target_order"] if t["target_slug"] == "wrapup")
    assert related["empty"] is True
    assert wrapup["empty"] is True
    assert "related" in plan["empty_slugs"]
    assert "wrapup" in plan["empty_slugs"]


# (b) T005 层级归组：sec-2-1 与 sec-2 同 slug
def test_section_2_1_grouped_with_2(survey_norm):
    plan = build_plan(survey_norm)
    assert _slug_of(plan, "sec-2-1") == _slug_of(plan, "sec-2") == "method"


def _run_fake(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    shutil.copyfile(FIXTURE_DIR / "source.pdf", run_dir / "source.pdf")
    shutil.copytree(FIXTURE_DIR / "parsed", run_dir / "parsed")
    return transform_paper(
        run_dir / "source.pdf", output_dir=run_dir, fake=True
    )


# (c) T013/T016：成品（正文 + 折叠区）不再泄漏 blk- 内部 id
def test_output_has_no_blk_leak(tmp_path):
    result = _run_fake(tmp_path)
    assert not re.search(r"blk-\d", result.full_markdown)


# (c+) T008/T009：空章零 LLM 调用、正文逐字等于固定文案；非空章照常调用
def test_empty_slug_skips_llm(survey_norm, tmp_path, monkeypatch):
    import core.pipeline as pipeline_mod
    from core.planner import EMPTY_FALLBACK

    plan = build_plan(survey_norm)
    empty = plan["empty_slugs"]
    assert empty, "survey 夹具应至少有一个空章"

    calls: list[str] = []
    orig = pipeline_mod.chat

    def counting_chat(prompt_system, prompt_user, **kwargs):  # noqa: ANN001
        calls.append(prompt_user)
        return orig(prompt_system, prompt_user, **kwargs)

    monkeypatch.setattr(pipeline_mod, "chat", counting_chat)
    result = _run_fake(tmp_path)

    # (a) 恰好 7 - len(empty) 次调用，且空章 prompt 从不构造
    assert len(calls) == 7 - len(empty)

    # (c) 每个空章正文逐字等于固定文案
    for slug in empty:
        sec = next(s for s in result.sections if s["slug"] == slug)
        assert sec["content"] == EMPTY_FALLBACK[slug]
        assert "\n" not in sec["content"]  # 每句固定文案只有一句话


# (b) T010：成品恰好 7 个章标题，编号严格 0..6，顺序固定
def test_output_has_exactly_seven_headings(tmp_path):
    from core.planner import SLUG_ORDER

    result = _run_fake(tmp_path)
    headings = re.findall(r"^## (\d) · ", result.full_markdown, flags=re.MULTILINE)
    assert headings == ["0", "1", "2", "3", "4", "5", "6"]
    assert [s["slug"] for s in result.sections] == list(SLUG_ORDER)


# (d) T010：全文无工程词泄漏（blk 泄漏由 T013/T018 单独翻转，此处不查）
def test_output_has_no_engineering_tokens(tmp_path):
    result = _run_fake(tmp_path)
    for token in ("content_list", "fold_group", "target_slug"):
        assert token not in result.full_markdown
    # 固定文案不得出现成段工程说明
    from core.planner import EMPTY_FALLBACK

    for line in EMPTY_FALLBACK.values():
        for banned in ("重新解析", "未编造", "原文块为空"):
            assert banned not in line


# (d) T016：折叠区表格完整输出，无截断尾串
def test_fold_tables_not_truncated(tmp_path):
    result = _run_fake(tmp_path)
    md = result.full_markdown
    assert not re.search(r"</t{1,2}(?!able|head|body|r|d|h)", md)
    # survey 有 4 张表，折叠区应含完整闭合
    assert md.count("</table>") >= 3
