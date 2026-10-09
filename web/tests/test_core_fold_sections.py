"""T019：折叠区表格完整性、表标题、参考文献编号/链接/OCR 清理。

直接对 build_fold_sections / render_reference_items 用构造数据断言；
survey fixture 的整表完整性在 test_core_survey_regression 中固化。
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from core.assemble.fold_sections import build_fold_sections
from core.assemble.references import render_reference_items
from core.normalize import normalize_content_list
from core.planner import build_plan
from core.pipeline import transform_paper
from web.app.config import WEB_DIR

SURVEY_DIR = WEB_DIR / "fixtures" / "survey_run"


def _norm_plan(blocks: list[dict], group: str = "repro_data"):
    norm = {"blocks": blocks}
    plan = {
        "block_actions": {
            b["id"]: {"action": "fold", "fold_group": group} for b in blocks
        }
    }
    return norm, plan


def _big_table_html(n_cells: int) -> str:
    cells = "".join(f"<td>cell {i} data payload</td>" for i in range(n_cells))
    return f"<table><tbody><tr>{cells}</tr></tbody></table>"


# T016/T019(a)：超过旧 3000 截断阈值的表格必须完整输出
def test_long_table_not_truncated():
    html = _big_table_html(200)  # 远超 3000 字符
    assert len(html) > 4000
    blocks = [{"id": "blk-0001", "type": "table", "page_idx": 2,
               "html": html, "caption": "Table 9: Big"}]
    norm, plan = _norm_plan(blocks)
    md = build_fold_sections(norm, plan)

    assert html in md                      # 完整 HTML 原样在
    assert md.count("</table>") == 1
    # 无 </t / </tt 之类被截断的尾串
    assert not re.search(r"</t{1,2}(?!able|head|body|r|d|h)", md)


# T019(b)：有 caption 用 caption；无 caption 用"表（第 M 页）"；不泄漏 blk id
def test_table_titles():
    with_cap = {"id": "blk-0001", "type": "table", "page_idx": 4,
                "html": "<table></table>", "caption": "Table 2: Results"}
    no_cap = {"id": "blk-0002", "type": "table", "page_idx": 7,
              "html": "<table></table>", "caption": ""}
    norm, plan = _norm_plan([with_cap, no_cap])
    md = build_fold_sections(norm, plan)

    assert not re.search(r"blk-\d", md)
    assert "**Table 2: Results**" in md
    assert "**表（第 8 页）**" in md   # page_idx 7 → 第 8 页


# T019(c)：参考文献 100% 编号，arXiv/DOI/URL 转链接，普通文本不加链接
def test_reference_numbering_and_links():
    items = [
        "A. Author. 2020. Title one. arXiv:2004.08483.",
        "B. Author. 2021. Title two. doi 10.1145/1234567.8901234.",
        "C. Author. 2022. Title three, see https://example.com/paper for data.",
        "D. Author. 2023. Plain venue citation, ACL 2020.",
        # 已自带编号的不重复编号
        "[9] E. Author. 2024. Pre-numbered entry.",
    ]
    out = render_reference_items(items)

    assert re.match(r"^\[1\]", out[0])
    assert re.match(r"^\[2\]", out[1])
    assert re.match(r"^\[3\]", out[2])
    assert re.match(r"^\[4\]", out[3])
    assert out[4].startswith("[9]") and not out[4].startswith("[9] [5]")

    assert "[arXiv 摘要 2004.08483](https://arxiv.org/abs/2004.08483)" in out[0]
    assert "[10.1145/1234567.8901234](https://doi.org/10.1145/1234567.8901234)" in out[1]
    assert "[https://example.com/paper](https://example.com/paper)" in out[2]
    # 普通文本不被加链接
    assert "](http" not in out[3]
    # 不产生嵌套链接
    assert not re.search(r"\]\(\[[^\]]*\]\(", "\n".join(out))


# T019(d)：白名单外的损坏条目不被改写（仅折叠空白 + 补编号），残片被清理
def test_damaged_entries_whitelist_only():
    items = [
        "??   broken  OCR    tokens  without identifier",
        "visually rich document d di Preprint i 5",
    ]
    out = render_reference_items(items)

    # 无 arXiv/DOI/URL 时不加链接
    assert all("](http" not in line for line in out)
    # 损坏但无残片特征的条目内容原样（空白折叠后）
    assert out[0] == "[1] ?? broken OCR tokens without identifier"
    # Preprint i N 残片被移除
    assert "Preprint" not in out[1]
    assert out[1].startswith("[2]")


# ---------------------------------------------------------------------------
# T020：图片完整性（survey fake 端到端）
# ---------------------------------------------------------------------------


def _run_survey(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    shutil.copyfile(SURVEY_DIR / "source.pdf", run_dir / "source.pdf")
    shutil.copytree(SURVEY_DIR / "parsed", run_dir / "parsed")
    return transform_paper(run_dir / "source.pdf", output_dir=run_dir, fake=True)


def test_no_dead_links_and_figures_copied(tmp_path):
    result = _run_survey(tmp_path)
    # 成品无图片死链
    assert result.dead_links == []
    # 所有 narrate/fold 的 figure 都已拷到输出 images/
    raw = json.loads((SURVEY_DIR / "parsed" / "content_list.json").read_text("utf-8"))
    norm = normalize_content_list(raw, paper_id="survey")
    plan = build_plan(norm)
    for b in norm["blocks"]:
        if b["type"] != "figure":
            continue
        if plan["block_actions"][b["id"]]["action"] == "drop":
            continue
        if b.get("img_path"):
            assert Path(b["img_path"]).name in result.image_files


def test_architecture_figure_kept_in_mainline(tmp_path):
    """survey 唯一图为'数据集示意/概览'类，按 caption 判据保留 narrate（不被折叠）。"""
    raw = json.loads((SURVEY_DIR / "parsed" / "content_list.json").read_text("utf-8"))
    norm = normalize_content_list(raw, paper_id="survey")
    plan = build_plan(norm)
    figures = [b for b in norm["blocks"] if b["type"] == "figure"]
    assert figures, "survey 夹具应至少含 1 张 figure"
    # 无一张因'实验章 figure'规则被无脑折叠：只有数值图表词才 fold
    for b in figures:
        cap = (b.get("caption") or "").lower()
        act = plan["block_actions"][b["id"]]["action"]
        arch_word = any(
            w in cap
            for w in ("architecture", "framework", "overview",
                      "pipeline", "structure", "model")
        )
        if arch_word:
            assert act == "narrate", (b["id"], cap)
