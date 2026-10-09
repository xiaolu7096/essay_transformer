"""T024：来源标注 span 化（去斜体）+ prompt 文本契约 + survey e2e 零斜体命中。"""
from __future__ import annotations

import shutil
from pathlib import Path

from core.assemble import render_source_notes
from core.pipeline import transform_paper
from web.app.config import WEB_DIR

SURVEY_DIR = WEB_DIR / "fixtures" / "survey_run"
SECTION_MD = Path(__file__).resolve().parents[2] / "core" / "prompts" / "section.md"


def test_old_italic_note_becomes_span():
    out = render_source_notes("这是结论。*（原文 §2.2，第 3 页）*")
    assert '<span class="src-note">原文 §2.2，第 3 页</span>' in out
    assert "*（原文" not in out


def test_bare_note_at_line_end_becomes_span():
    out = render_source_notes("方法分三步。原文 §4，第 7 页")
    assert '<span class="src-note">原文 §4，第 7 页</span>' in out
    # 已包裹的不二次处理
    twice = render_source_notes(
        '<span class="src-note">原文 §5，第 2 页</span>'
    )
    assert twice.count('class="src-note"') == 1


def test_duplicate_notes_in_same_subsection_merged():
    # SC-008：同一小节 6 条完全相同的标注 → 1 条
    md = (
        "### 综述的几条主线\n\n"
        "主线一。<span class=\"src-note\">原文 §1，第 1 页</span>\n\n"
        "主线二。<span class=\"src-note\">原文 §1，第 1 页</span>\n"
    )
    out = render_source_notes(md)
    assert out.count('class="src-note"') == 1
    assert '<span class="src-note">原文 §1，第 1 页</span>' in out


def test_parent_section_covers_subsections_and_pages_ranged():
    # §5 覆盖 §5.1/§5.2/§5.3；页码 7、8 压成区间
    md = (
        "### 把视觉特征注入大模型\n\n"
        "总述。<span class=\"src-note\">原文 §5，第 7 页</span>\n\n"
        "做法一。<span class=\"src-note\">原文 §5.1，第 7 页</span>\n\n"
        "做法二。<span class=\"src-note\">原文 §5.2，第 7 页</span>\n\n"
        "做法三。<span class=\"src-note\">原文 §5.3，第 8 页</span>\n"
    )
    out = render_source_notes(md)
    assert out.count('class="src-note"') == 1
    note = '<span class="src-note">原文 §5，第 7–8 页</span>'
    assert note in out


def test_same_section_distinct_pages_ranged():
    # 同父节跨页：§2.2 第 3 页 + §2.2 第 4 页
    md = (
        "### 融合视觉信息\n\n"
        "a<span class=\"src-note\">原文 §2.2，第 3 页</span>\n\n"
        "b<span class=\"src-note\">原文 §2.2，第 4 页</span>\n"
    )
    out = render_source_notes(md)
    assert out.count('class="src-note"') == 1
    assert "原文 §2.2，第 3–4 页" in out


def test_each_subsection_independently_keeps_one_note():
    # 不同 ### 小节各保留 1 条，互不合并
    md = (
        "### 小节一\n\na<span class=\"src-note\">原文 §1，第 1 页</span>\n\n"
        "### 小节二\n\nb<span class=\"src-note\">原文 §2，第 2 页</span>\n"
    )
    out = render_source_notes(md)
    assert out.count('class="src-note"') == 2


def test_dedupe_is_idempotent():
    once = render_source_notes(
        "### x\n\na<span class=\"src-note\">原文 §5，第 7 页</span>\n\n"
        "b<span class=\"src-note\">原文 §5.1，第 8 页</span>\n"
    )
    assert render_source_notes(once) == once


def test_unrelated_emphasis_untouched():
    assert render_source_notes("这是 *强调* 文本") == "这是 *强调* 文本"
    assert render_source_notes("这是 **粗体** 文本") == "这是 **粗体** 文本"
    # 行中（非行尾）裸句式不匹配
    mid = "行中引用 原文 §4，第 7 页 的内容继续讲"
    assert render_source_notes(mid) == mid


def test_section_prompt_contract():
    text = SECTION_MD.read_text(encoding="utf-8")
    assert "至多集中标注一次" in text
    assert "斜体包裹" in text
    assert "每段事实性叙述末尾" not in text


def _run_survey(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SURVEY_DIR / "source.pdf", run_dir / "source.pdf")
    shutil.copytree(SURVEY_DIR / "parsed", run_dir / "parsed")
    return transform_paper(
        run_dir / "source.pdf", output_dir=run_dir, fake=True
    )


def test_survey_output_has_no_italic_source_notes(tmp_path):
    result = _run_survey(tmp_path)
    assert result.full_markdown.count("*（原文") == 0
