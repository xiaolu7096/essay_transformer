"""normalize 单元测试（T012，data-model.md §2，research R2）。

夹具来自 tests/integration/fixtures/mini_parsed（从真实 Attention 论文
MinerU output 截取），覆盖：章节树、公式 (1) latex、table html、
Figure 1=image / Figure 3=chart 原样保留 mineru_type、ref_text 聚合、
图片缺失降级占位。
"""

from __future__ import annotations

import json
import shutil

import pytest

from essay_transformer.normalize import (
    MISSING_IMAGE_PLACEHOLDER,
    normalize_content_list,
    normalize_run,
)
from essay_transformer.errors import ParseServiceError

SOURCE = {"kind": "arxiv", "ref": "1706.03762", "pdf_path": "runs/arxiv-1706.03762/source.pdf"}


@pytest.fixture()
def doc(mini_content_list, mini_parsed_dir):
    return normalize_content_list(
        mini_content_list,
        paper_id="arxiv-1706.03762",
        source=SOURCE,
        parsed_dir=mini_parsed_dir,
    )


def by_id(doc) -> dict[str, dict]:
    return {b["id"]: b for b in doc["blocks"]}


# ---------------------------------------------------------------------------
# 文档头与章节树
# ---------------------------------------------------------------------------


def test_document_title_from_level1_heading(doc):
    assert doc["paper_id"] == "arxiv-1706.03762"
    assert doc["source"] == SOURCE
    assert doc["title"] == "Attention Is All You Need"
    assert doc["authors"] == []


def test_section_tree_ids_levels_kinds_and_order(doc):
    sections = doc["sections"]
    ids = [s["id"] for s in sections]
    assert ids == [
        "sec-abstract",
        "sec-1",
        "sec-3",
        "sec-3-2",
        "sec-7",
        "sec-references",
    ]
    by_sec = {s["id"]: s for s in sections}
    assert by_sec["sec-abstract"]["heading"] == "Abstract"
    assert by_sec["sec-abstract"]["level"] == 2
    assert by_sec["sec-abstract"]["kind"] == "abstract"

    assert by_sec["sec-1"]["heading"] == "Introduction"
    assert by_sec["sec-1"]["level"] == 1
    assert by_sec["sec-1"]["kind"] == "introduction"

    assert by_sec["sec-3"]["heading"] == "Model Architecture"
    assert by_sec["sec-3"]["level"] == 1
    assert by_sec["sec-3"]["kind"] == "unclassified"

    # data-model §2 示例：sec-3-2 / Attention / level 2
    assert by_sec["sec-3-2"]["heading"] == "Attention"
    assert by_sec["sec-3-2"]["level"] == 2
    assert by_sec["sec-3-2"]["kind"] == "unclassified"

    assert by_sec["sec-7"]["heading"] == "Conclusion"
    assert by_sec["sec-7"]["kind"] == "conclusion"

    assert by_sec["sec-references"]["heading"] == "References"
    assert by_sec["sec-references"]["kind"] == "unclassified"
    assert doc["appendix_section_ids"] == []


def test_page_start_comes_from_heading_page(doc):
    by_sec = {s["id"]: s for s in doc["sections"]}
    assert by_sec["sec-3-2"]["page_start"] == 2
    assert by_sec["sec-references"]["page_start"] == 9


def test_every_section_block_reference_resolves_and_noise_is_excluded(doc):
    blocks = by_id(doc)
    owned: dict[str, str] = {}
    for section in doc["sections"]:
        for bid in section["blocks"]:
            assert bid in blocks, f"{section['id']} 引用不存在块 {bid}"
            assert bid not in owned, f"块 {bid} 同时属于 {owned[bid]} 与 {section['id']}"
            owned[bid] = section["id"]
    # 页码噪声不属于任何节
    noise = [b for b in doc["blocks"] if b["type"] == "noise"]
    assert noise and noise[0]["noise_kind"] == "page_number"
    assert all(b["id"] not in owned for b in noise)
    # L1 标题块不属于任何节，但保留在扁平 blocks 中
    title_block = next(b for b in doc["blocks"] if b["type"] == "heading" and b["level"] == 1)
    assert title_block["id"] not in owned
    assert title_block["text"] == "Attention Is All You Need"


# ---------------------------------------------------------------------------
# 类型化块
# ---------------------------------------------------------------------------


def test_equation_tag1_stripped_and_numbered(doc):
    block = by_id(doc)["blk-0009"]
    assert block["type"] == "equation"
    assert block["number"] == "1"
    latex = block["latex"]
    assert "$$" not in latex
    assert r"\tag" not in latex
    assert r"\operatorname{Attention} (Q, K, V)" in latex
    assert block["raw_type"] == "equation"
    assert block["page_idx"] == 3


def test_equation_without_tag_has_no_number(doc):
    block = by_id(doc)["blk-0010"]
    assert block["type"] == "equation"
    assert "number" not in block
    assert "$$" not in block["latex"]
    assert "MultiHead" in block["latex"]


def test_table_html_caption_and_footnote(doc):
    block = by_id(doc)["blk-0011"]
    assert block["type"] == "table"
    assert block["html"].lstrip().startswith("<table><tr><td>Layer Type</td>")
    assert "Table 1" in block["caption"]
    assert "per-layer complexity" in block["caption"]
    assert block["footnote"] == ""


def test_figure1_image_type_preserved(doc):
    block = by_id(doc)["blk-0007"]
    assert block["type"] == "figure"
    assert block["mineru_type"] == "image"  # R2：MinerU 分类原样保留为提示
    assert block["img_path"] == (
        "images/f05ba6a0a2e484dbd8bf2a4a4fa2597a69a3a076f5b03cf4e818b176b4049244.jpg"
    )
    assert "Figure 1" in block["caption"]


def test_figure3_chart_type_preserved_even_after_references(doc):
    # R2 实测误判样本：Figure 3 被 MinerU 标为 chart，必须原样保留
    block = by_id(doc)["blk-0018"]
    assert block["type"] == "figure"
    assert block["mineru_type"] == "chart"
    assert block["img_path"] == (
        "images/8b8122ff9e5a304dbbfde70f24d29dc112b471b5f78d64243dcea37188fb0eb7.jpg"
    )
    assert "Figure 3" in block["caption"]


def test_reference_list_aggregates_consecutive_ref_text(doc):
    ref_blocks = [b for b in doc["blocks"] if b["type"] == "reference_list"]
    assert len(ref_blocks) == 1
    block = ref_blocks[0]
    assert block["id"] == "blk-0016"  # 取连续 ref_text 的起始下标
    assert len(block["items"]) == 2
    assert block["items"][0].startswith("[1] Jimmy Lei Ba")
    assert block["items"][1].startswith("[2] Dzmitry Bahdanau")
    # 顶层 references 与聚合内容一致
    assert doc["references"] == block["items"]
    # reference_list 归属 References 节
    ref_section = next(s for s in doc["sections"] if s["id"] == "sec-references")
    assert block["id"] in ref_section["blocks"]


def test_missing_image_becomes_degraded_placeholder(doc):
    block = by_id(doc)["blk-0013"]
    assert block["type"] == "figure"
    assert block["image_missing"] is True
    assert block["img_path"] is None
    assert block["expected_img_path"] == "images/__missing_fixture_image__.jpg"
    assert block["placeholder"] == MISSING_IMAGE_PLACEHOLDER


def test_all_blocks_have_id_and_page_idx_fields(doc):
    for block in doc["blocks"]:
        assert block["id"].startswith("blk-")
        assert "page_idx" in block
        assert "raw_type" in block
        assert block["text_level"] is None or isinstance(block["text_level"], int)


# ---------------------------------------------------------------------------
# 落盘与错误路径
# ---------------------------------------------------------------------------


def test_normalize_run_writes_normalized_json(tmp_path, mini_parsed_dir):
    run_dir = tmp_path / "runs" / "arxiv-1706.03762"
    (run_dir / "parsed").parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(mini_parsed_dir, run_dir / "parsed")
    result = normalize_run(run_dir, source=SOURCE)
    written = json.loads((run_dir / "normalized.json").read_text(encoding="utf-8"))
    assert written["paper_id"] == "arxiv-1706.03762"
    assert written is not result  # 落盘内容独立
    assert written["title"] == result["title"]


def test_equation_containing_image_reference_degrades_to_parse_gap():
    """FR-026/T037：单条公式损坏不再拖垮整篇，降级 parse_gap 占位块。"""
    content_list = [
        {"type": "text", "text_level": 1, "text": "T", "page_idx": 0},
        {
            "type": "text",
            "text_level": 2,
            "text": "1 Intro",
            "page_idx": 0,
        },
        {"type": "equation", "text": "$$ x = ![](a.jpg) $$", "page_idx": 1},
    ]
    doc = normalize_content_list(
        content_list, paper_id="p", source={"kind": "local", "ref": "a.pdf"},
        parsed_dir=None,
    )
    gap = by_id(doc)["blk-0002"]
    assert gap["type"] == "parse_gap"
    assert gap["gap_kind"] == "equation"
    assert gap["raw_type"] == "equation"
    assert gap["page_idx"] == 1
    assert gap["placeholder"] == "原文公式解析缺失，未编造内容补全。"
    assert "图片引用" in gap["reason"]
    # 占位块归属它原本所在的节，成稿才能在对应位置渲染说明
    assert "blk-0002" in doc["sections"][0]["blocks"]


@pytest.mark.parametrize(
    "raw,gap_kind,label",
    [
        ({"type": "equation", "text": "$$$$", "page_idx": 0}, "equation", "公式"),
        ({"type": "table", "page_idx": 0}, "table", "表格"),
        ({"type": "text", "text": "   ", "page_idx": 0}, "text", "段落"),
    ],
)
def test_damaged_items_degrade_to_parse_gap(raw, gap_kind, label):
    content_list = [
        {"type": "text", "text_level": 1, "text": "T", "page_idx": 0},
        {"type": "text", "text_level": 2, "text": "1 Intro", "page_idx": 0},
        raw,
    ]
    doc = normalize_content_list(
        content_list, paper_id="p", source={"kind": "local", "ref": "a.pdf"},
        parsed_dir=None,
    )
    gap = next(b for b in doc["blocks"] if b["type"] == "parse_gap")
    assert gap["gap_kind"] == gap_kind
    assert gap["placeholder"] == f"原文{label}解析缺失，未编造内容补全。"
    assert gap["reason"]  # 失败原因留痕可审计


def test_damaged_ref_text_degrades_to_parse_gap_without_losing_prior_refs():
    content_list = [
        {"type": "text", "text_level": 1, "text": "T", "page_idx": 0},
        {"type": "text", "text_level": 2, "text": "References", "page_idx": 0},
        {"type": "ref_text", "text": "[1] good entry", "page_idx": 0},
        {"type": "ref_text", "text": "", "page_idx": 0},
    ]
    doc = normalize_content_list(
        content_list, paper_id="p", source={"kind": "local", "ref": "a.pdf"},
    )
    # 已成功聚合的参考文献不丢；坏条目降级为占位块
    assert doc["references"] == ["[1] good entry"]
    assert any(b["type"] == "parse_gap" for b in doc["blocks"])


def test_image_only_scanned_pdf_raises_input_error():
    """FR-004 edge：纯图片/无文本层 PDF → InputError（退出码 3），提示无 OCR。"""
    from essay_transformer.errors import InputError

    content_list = [
        {"type": "image", "img_path": "images/a.jpg", "page_idx": 0},
        {"type": "image", "img_path": "images/b.jpg", "page_idx": 1},
    ]
    with pytest.raises(InputError, match="文本层|OCR"):
        normalize_content_list(
            content_list, paper_id="p", source={"kind": "local", "ref": "s.pdf"},
            parsed_dir=None,
        )


def test_empty_content_list_raises_input_error():
    from essay_transformer.errors import InputError

    with pytest.raises(InputError, match="文本层"):
        normalize_content_list(
            [], paper_id="p", source={"kind": "local", "ref": "e.pdf"}
        )


def test_non_dict_item_still_hard_fails():
    """整篇级结构损坏（非对象条目）仍硬失败，不做静默降级。"""
    with pytest.raises(ParseServiceError, match="不是对象"):
        normalize_content_list(
            ["junk"], paper_id="p", source={"kind": "local", "ref": "a.pdf"}
        )


def test_section_referencing_missing_block_id_rejected():
    from essay_transformer.normalize import _validate_integrity

    content_list = [
        {"type": "text", "text_level": 1, "text": "T", "page_idx": 0},
        {"type": "text", "text_level": 2, "text": "1 Intro", "page_idx": 0},
        {"type": "text", "text": "body", "page_idx": 0},
    ]
    doc = normalize_content_list(
        content_list, paper_id="p", source={"kind": "local", "ref": "a.pdf"}
    )
    doc["sections"][0]["blocks"].append("blk-9999")
    with pytest.raises(ParseServiceError, match="不存在的块"):
        _validate_integrity(doc)
