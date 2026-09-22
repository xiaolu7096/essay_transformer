"""T025 组装：七段结构、公式卡/手算、图片拷贝与零死链、折叠、callout、
溯源、缺失占位、out_of_glossary 聚合、文件命名与 IO 错误类型（T021）。"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from essay_transformer import assemble as assemble_mod
from essay_transformer.assemble import (
    MISSING_PLACEHOLDER,
    SECTION_HEADINGS,
    assemble_run,
    rename_for_qa,
    render_trace,
    slugify_title,
)
from essay_transformer.errors import InputError, LocalIOError
from essay_transformer.planner import SLUG_ORDER
from essay_transformer.worked_example import TOY_DISCLAIMER

FIXTURE_IMAGES = (
    Path(__file__).resolve().parents[1]
    / "integration" / "fixtures" / "mini_parsed" / "images"
)
FIG_JPG = "f05ba6a0a2e484dbd8bf2a4a4fa2597a69a3a076f5b03cf4e818b176b4049244.jpg"
CHART_JPG = "8b8122ff9e5a304dbbfde70f24d29dc112b471b5f78d64243dcea37188fb0eb7.jpg"
LATEX_TAG = r"\operatorname{Attention} (Q, K, V) = \cdots\tag{1}"


# ---------------------------------------------------------------------------
# run 构建
# ---------------------------------------------------------------------------


def _normalized() -> dict:
    return {
        "paper_id": "arxiv-1706.03762",
        "title": "Attention Is All You Need",
        "source": {"kind": "arxiv", "ref": "1706.03762",
                   "pdf_path": "runs/arxiv-1706.03762/source.pdf"},
        "authors": ["Ash Vaswani"],
        "sections": [
            {"id": "sec-0", "heading": "Abstract", "level": 2, "kind": "abstract",
             "page_start": 0, "blocks": ["blk-ah", "blk-b"]},
            {"id": "sec-1", "heading": "Introduction", "level": 2,
             "kind": "introduction", "page_start": 1, "blocks": ["blk-ih", "blk-i"]},
            {"id": "sec-2", "heading": "Background", "level": 2,
             "kind": "related_work", "page_start": 2, "blocks": ["blk-rh", "blk-rel"]},
            {"id": "sec-3-2", "heading": "Attention", "level": 2, "kind": "body",
             "page_start": 3, "blocks": ["blk-eq", "blk-fig"]},
            {"id": "sec-6", "heading": "Results", "level": 2,
             "kind": "experiments", "page_start": 5,
             "blocks": ["blk-tbl", "blk-figmiss", "blk-chart"]},
            {"id": "sec-7", "heading": "Conclusion", "level": 2,
             "kind": "conclusion", "page_start": 9,
             "blocks": ["blk-ch", "blk-c", "blk-refh", "blk-refs"]},
        ],
        "blocks": [
            {"id": "blk-ah", "type": "heading", "page_idx": 0},
            {"id": "blk-b", "type": "paragraph", "page_idx": 0},
            {"id": "blk-ih", "type": "heading", "page_idx": 1},
            {"id": "blk-i", "type": "paragraph", "page_idx": 1},
            {"id": "blk-rh", "type": "heading", "page_idx": 2},
            {"id": "blk-rel", "type": "paragraph", "page_idx": 2},
            {"id": "blk-eq", "type": "equation", "page_idx": 3,
             "latex": LATEX_TAG, "number": "1"},
            {"id": "blk-fig", "type": "figure", "page_idx": 2,
             "img_path": f"images/{FIG_JPG}", "mineru_type": "image"},
            {"id": "blk-tbl", "type": "table", "page_idx": 5},
            {"id": "blk-figmiss", "type": "figure", "page_idx": 5,
             "img_path": None,
             "expected_img_path": "images/__missing_fixture_image__.jpg",
             "image_missing": True,
             "placeholder": "原文图片解析缺失，未编造内容补全。"},
            {"id": "blk-chart", "type": "figure", "page_idx": 12,
             "img_path": f"images/{CHART_JPG}", "mineru_type": "chart"},
            {"id": "blk-ch", "type": "heading", "page_idx": 9},
            {"id": "blk-c", "type": "paragraph", "page_idx": 9},
            {"id": "blk-refh", "type": "heading", "page_idx": 9},
            {"id": "blk-refs", "type": "reference_list", "page_idx": 9,
             "items": ["[1] Ba et al. Layer normalization. 2016.",
                       "[2] Bahdanau et al. NMT. 2014."]},
        ],
        "references": ["[1] Ba et al. Layer normalization. 2016.",
                       "[2] Bahdanau et al. NMT. 2014."],
        "appendix_section_ids": [],
    }


def _plan() -> dict:
    return {
        "target_order": [{"target_slug": s, "source_section_ids": [],
                          "treatment": "brief"} for s in SLUG_ORDER],
        "block_actions": {},
        "key_formulas": [
            {"block_id": "blk-eq", "latex": LATEX_TAG, "label": "公式 (1)",
             "worked_example_required": True,
             "example_spec": {"tokens": 3, "steps": ["s", "x", "m", "w"]}}
        ],
    }


def _worked_example(status: str = "passed") -> dict:
    payload = {
        "id": "we-001",
        "formula_block_id": "blk-eq",
        "is_toy_data": True,
        "disclaimer": TOY_DISCLAIMER,
        "setup": {"tokens": ["思考", "是", "力量"], "d_k": 3,
                  "Q": [[1, 0, 1], [0, 1, 1], [1, 1, 0]],
                  "K": [[1, 0, 0], [0, 1, 0], [1, 1, 1]],
                  "V": [[1, 2, 0], [0, 1, 1], [1, 0, 1]]},
        "steps": [
            {"name": "计算相关性分数", "expr": "Q K^T",
             "matrix": [[1.0, 0.0, 2.0], [0.0, 1.0, 2.0], [1.0, 1.0, 2.0]],
             "explain_zh": "点积。"},
            {"name": "缩放", "expr": "/ sqrt(d_k)",
             "matrix": [[0.577, 0.0, 1.155], [0.0, 0.577, 1.155],
                        [0.577, 0.577, 1.155]],
             "explain_zh": "除以根号 3。"},
            {"name": "逐行 softmax", "expr": "softmax(...)",
             "matrix": [[0.29, 0.16, 0.55], [0.18, 0.31, 0.51],
                        [0.26, 0.26, 0.48]],
             "explain_zh": "归一化。"},
            {"name": "加权求和", "expr": "weights · V",
             "matrix": [[0.84, 0.74, 0.71], [0.69, 0.67, 0.82],
                        [0.74, 0.78, 0.74]],
             "explain_zh": "加权。"},
        ],
        "result": [[0.84, 0.74, 0.71], [0.69, 0.67, 0.82], [0.74, 0.78, 0.74]],
        "verification": {"status": status, "max_abs_diff": 1e-9,
                         "checked_at": "2026-09-19T00:00:00"},
    }
    if status == "missing":
        payload["verification"] = {
            "status": "missing", "max_abs_diff": float("inf"),
            "checked_at": "2026-09-19T00:00:00",
            "diffs": ["steps[1]（第2步 /√d_k）：数值不一致"],
        }
        payload["missing_reason"] = "FR-026"
    return payload


def _section(slug: str, blocks: list[dict], oog=None) -> dict:
    return {"schema_kind": "section_rewrite", "target_slug": slug,
            "blocks": blocks, "out_of_glossary": oog or []}


def _sections() -> dict[str, dict]:
    para = lambda bid, text: {"kind": "paragraph", "markdown": text,
                              "source_block_ids": [bid]}
    return {
        "tldr": _section("tldr", [
            {"kind": "tldr", "markdown": "三十秒读完：注意力就是全部。",
             "source_block_ids": ["blk-b"]},
        ], oog=[{"term": "Sublayer", "context": "two sub-layers", "page_idx": 0}]),
        "intro": _section("intro", [para(
            "blk-i",
            "动机：过去的序列建模主要依赖循环神经网络，必须按顺序一步步计算，"
            "难以并行，长距离依赖还会随距离衰减；作者想只用注意力机制彻底"
            "去掉循环与卷积，让整条通路可以同时计算。")]),
        "conclusion": _section("conclusion", [para(
            "blk-c",
            "结论：只用注意力的 Transformer 在翻译任务上又快又好，训练时间"
            "大幅缩短，并且能迁移到句法分析等其他任务，说明这一范式具有通用性。")]),
        "related": _section("related", [para(
            "blk-rel",
            "前作方面，序列建模长期由循环神经网络与编码器解码器架构主导，"
            "注意力机制过去只作为连接编码器与解码器的辅助部件，本文把它"
            "提升为模型的唯一核心构件。")]),
        "method": _section("method", [
            {"kind": "formula_card",
             "markdown": "注意力公式",
             "latex": LATEX_TAG,
             "symbol_breakdown": [
                 {"symbol": "Q", "meaning": "查询矩阵"},
                 {"symbol": "K", "meaning": "键矩阵"}],
             "intuition": "用 Q 和 K 的相关性给 V 加权。",
             "worked_example_ref": "we-001",
             "source_block_ids": ["blk-eq"]},
            {"kind": "figure",
             "img_path": f"images/{FIG_JPG}",
             "rewritten_caption": "图 1：Transformer 整体架构",
             "source_block_ids": ["blk-fig"]},
            {"kind": "table_explained",
             "markdown_table": "| 层类型 | 复杂度 |\n|---|---|\n| 自注意力 | O(n²d) |",
             "explanation": "自注意力路径最短。",
             "source_block_ids": ["blk-tbl"]},
            {"kind": "supplement", "supplement_kind": "background",
             "markdown": "在进入之前，先说说检索：\n\n- 查询就是需求",
             "source_block_ids": []},
            {"kind": "fold_ref", "fold_group": "derivation",
             "title": "多头注意力推导",
             "content_markdown": "完整推导过程……\n\n$$\nW^O\n$$",
             "source_block_ids": ["blk-eq"]},
        ], oog=[{"term": "sublayer", "context": "each sublayer", "page_idx": 3,
                "temporary_handling": "子层"}]),
        "experiments": _section("experiments", [
            para("blk-tbl",
                 "实验结论：在两个机器翻译任务上，模型质量超过当时已有最佳"
                 "结果，训练算力却只占很小一部分；完整超参与曲线数据都收在"
                 "折叠区，主线只讲结论级表达。"),
            {"kind": "figure",
             "img_path": "images/__missing_fixture_image__.jpg",
             "rewritten_caption": "图 99",
             "source_block_ids": ["blk-figmiss"]},
            {"kind": "fold_ref", "fold_group": "repro_data",
             "title": "完整实验数据",
             "content_markdown": (
                 "训练曲线：\n\n"
                 f"![训练曲线](images/{CHART_JPG})\n\n"
                 "| 配置 | BLEU |\n|---|---|\n| base | 27.3 |"
             ),
             "source_block_ids": ["blk-chart", "blk-tbl"]},
        ]),
        "wrapup": _section("wrapup", [
            para("blk-c",
                 "总评：注意力范式成立——去掉循环与卷积后，模型更易并行、"
                 "路径更短、效果更好，也为后续大规模预训练模型奠定了结构"
                 "基础，值得读者重点掌握。"),
            {"kind": "fold_ref", "fold_group": "references",
             "title": "参考文献",
             # 故意给中文转述：assemble 必须以 normalized.references 原文
             # 为权威来源渲染（FR-022/023），该转述不得进入成稿
             "content_markdown": "[1] LLM-转述占位，不应出现在成稿。\n\n"
                                 "[2] LLM-转述占位，不应出现在成稿。",
             "source_block_ids": ["blk-refs"]},
        ]),
    }


def _build_run(tmp_path: Path, *, we_status: str = "passed") -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "parsed" / "images").mkdir(parents=True)
    shutil.copyfile(FIXTURE_IMAGES / FIG_JPG,
                    run_dir / "parsed" / "images" / FIG_JPG)
    shutil.copyfile(FIXTURE_IMAGES / CHART_JPG,
                    run_dir / "parsed" / "images" / CHART_JPG)
    (run_dir / "normalized.json").write_text(
        json.dumps(_normalized(), ensure_ascii=False), encoding="utf-8")
    (run_dir / "plan.json").write_text(
        json.dumps({"schema_kind": "planner", **_plan()}, ensure_ascii=False),
        encoding="utf-8")
    (run_dir / "worked_example.json").write_text(
        json.dumps(_worked_example(we_status), ensure_ascii=False),
        encoding="utf-8")
    for slug, doc in _sections().items():
        path = run_dir / "sections" / f"{slug}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return run_dir


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


def test_slugify_title_kebab_and_60_char_cap() -> None:
    assert slugify_title("Attention Is All You Need") == "attention-is-all-you-need"
    long_title = "A " * 100
    assert len(slugify_title(long_title)) <= 60


def test_render_trace_multiple_sources_dedup() -> None:
    owner = {"b1": "§3.2", "b2": "§3.2", "b3": "§4"}
    pages = {"b1": 3, "b2": 3, "b3": 0}
    trace = render_trace(["b1", "b2", "b3", "bx"], owner, pages)
    assert trace == "*（原文 §3.2，p.4；§4，p.1）*"


# ---------------------------------------------------------------------------
# 组装 golden
# ---------------------------------------------------------------------------


def test_assemble_golden_structure(tmp_path) -> None:
    run_dir = _build_run(tmp_path)
    result = assemble_run(run_dir)
    md = result["markdown_path"]

    assert md.name == "attention-is-all-you-need.md"
    text = md.read_text(encoding="utf-8")

    # 顶层
    assert text.startswith("# Attention Is All You Need\n")
    assert "https://arxiv.org/abs/1706.03762" in text
    assert "Ash Vaswani" in text and "共 13 页" in text  # max page_idx=12 → 13

    # 七段标题固定顺序
    positions = [text.index(heading) for heading in SECTION_HEADINGS.values()]
    assert positions == sorted(positions)
    assert len(positions) == 7


def test_assemble_trace_and_formula_card_and_example(tmp_path) -> None:
    run_dir = _build_run(tmp_path)
    text = (assemble_run(run_dir)["markdown_path"]).read_text(encoding="utf-8")

    # 公式卡：标题来自 plan label 的兜底在 markdown 缺失时；这里 markdown=注意力公式
    assert "### 注意力公式" in text
    assert "$$" in text and r"\tag{1}" in text  # 公式原文/编号保留
    assert "**每个符号是什么意思**" in text
    assert "- Q：查询矩阵" in text
    assert "**直觉讲解**" in text and "用 Q 和 K" in text

    # 手算示例
    assert "**手算小例子（3 个词走一遍）**" in text
    assert f"> ⚠️ {TOY_DISCLAIMER}。输入设定：思考 / 是 / 力量，d_k = 3。" in text
    assert "**计算相关性分数**" in text or "计算相关性分数" in text
    assert "weights · V" in text
    assert "**最终输出**" in text

    # 溯源（公式 §3.2 p.4；正文 p.10）
    assert "*（原文 §3.2，p.4）*" in text
    assert "*（原文 §7，p.10）*" in text


def test_assemble_images_copied_and_zero_dead_links(tmp_path) -> None:
    run_dir = _build_run(tmp_path)
    result = assemble_run(run_dir)
    text = result["markdown_path"].read_text(encoding="utf-8")

    # 两张真实图片都拷贝（主线图 + 折叠区训练曲线）
    images_dir = run_dir / "dist" / "images"
    assert (images_dir / FIG_JPG).is_file()
    assert (images_dir / CHART_JPG).is_file()
    assert set(result["images"]) == {FIG_JPG, CHART_JPG}

    # Markdown 中的每个图片相对链接都真实存在 → 0 死链
    for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
        assert (run_dir / "dist" / target).is_file(), target

    assert "![图 1：Transformer 整体架构](images/" + FIG_JPG + ")" in text
    assert "**图解**：图 1：Transformer 整体架构" in text


def test_assemble_missing_image_renders_placeholder_not_dead_link(tmp_path) -> None:
    run_dir = _build_run(tmp_path)
    text = (assemble_run(run_dir)["markdown_path"]).read_text(encoding="utf-8")

    assert "__missing_fixture_image__" not in text  # 不产生死链
    assert MISSING_PLACEHOLDER.format(where="").strip("。") in text or \
        "解析缺失，未编造内容补全" in text


def test_assemble_three_fold_zones(tmp_path) -> None:
    run_dir = _build_run(tmp_path)
    text = (assemble_run(run_dir)["markdown_path"]).read_text(encoding="utf-8")

    assert "<details>" in text and "<summary>" in text
    assert "<summary>推导与证明：多头注意力推导（点击展开）</summary>" in text
    assert "<summary>复现参考：完整实验数据（点击展开）</summary>" in text
    assert "<summary>参考文献（共 2 条，点击展开）</summary>" in text

    # 折叠内容前后各空一行
    assert "</summary>\n\n完整推导过程" in text
    assert "Bahdanau et al. NMT. 2014.\n\n</details>" in text

    # 折叠区在文末独立分隔线 --- 之后，且在七段之后（排除表格里的 |---|）
    sep = text.index("\n---\n\n<details>")
    assert sep > text.index(SECTION_HEADINGS["wrapup"])


def test_assemble_folds_closed_by_default(tmp_path) -> None:
    """T030：所有折叠默认闭合——<details> 不带 open 属性。"""
    run_dir = _build_run(tmp_path)
    text = (assemble_run(run_dir)["markdown_path"]).read_text(encoding="utf-8")

    tags = re.findall(r"<details\b[^>]*>", text)
    assert len(tags) == 3  # derivation / repro_data / references 各一个
    assert all(tag == "<details>" for tag in tags)
    assert "<details open" not in text and " open>" not in text


def test_assemble_references_verbatim_from_normalized(tmp_path) -> None:
    """T030：参考文献条数与 normalized.references 一致，原文逐字、无缺失，
    LLM 的 content_markdown 转述不参与渲染（FR-022/023）。"""
    run_dir = _build_run(tmp_path)
    text = (assemble_run(run_dir)["markdown_path"]).read_text(encoding="utf-8")

    references = _normalized()["references"]
    zone_start = text.index("<summary>参考文献")
    zone = text[zone_start:]
    for entry in references:
        assert entry in zone
    # 条数声明与实际条目数一致
    assert f"参考文献（共 {len(references)} 条，点击展开）" in text
    # 折叠区内每条原文恰好出现一次（无缺失、无重复）
    for entry in references:
        assert zone.count(entry) == 1
    # LLM 转述内容被忽略
    assert "LLM-转述占位" not in text


def test_assemble_missing_references_fold_raises(tmp_path) -> None:
    """T030：normalized 有参考文献却没有 references 折叠 → 确定性报错，
    不得静默输出缺失参考文献的成稿。"""
    run_dir = _build_run(tmp_path)
    wrapup_path = run_dir / "sections" / "wrapup.json"
    wrapup = json.loads(wrapup_path.read_text(encoding="utf-8"))
    wrapup["blocks"] = [
        b for b in wrapup["blocks"]
        if not (b.get("kind") == "fold_ref"
                and b.get("fold_group") == "references")
    ]
    wrapup_path.write_text(json.dumps(wrapup, ensure_ascii=False),
                           encoding="utf-8")

    with pytest.raises(InputError, match="参考文献"):
        assemble_run(run_dir)


def test_assemble_without_references_needs_no_fold(tmp_path) -> None:
    """T030：normalized 无参考文献时，不要求 references 折叠且不渲染该组。"""
    run_dir = _build_run(tmp_path)
    norm_path = run_dir / "normalized.json"
    normalized = json.loads(norm_path.read_text(encoding="utf-8"))
    normalized["references"] = []
    norm_path.write_text(json.dumps(normalized, ensure_ascii=False),
                         encoding="utf-8")
    wrapup_path = run_dir / "sections" / "wrapup.json"
    wrapup = json.loads(wrapup_path.read_text(encoding="utf-8"))
    wrapup["blocks"] = [
        b for b in wrapup["blocks"]
        if not (b.get("kind") == "fold_ref"
                and b.get("fold_group") == "references")
    ]
    wrapup_path.write_text(json.dumps(wrapup, ensure_ascii=False),
                           encoding="utf-8")

    result = assemble_run(run_dir)
    text = result["markdown_path"].read_text(encoding="utf-8")
    assert "参考文献（共" not in text
    # 另外两组折叠不受影响
    assert "<summary>推导与证明：多头注意力推导（点击展开）</summary>" in text
    assert "<summary>复现参考：完整实验数据（点击展开）</summary>" in text


def test_assemble_supplement_callout(tmp_path) -> None:
    run_dir = _build_run(tmp_path)
    text = (assemble_run(run_dir)["markdown_path"]).read_text(encoding="utf-8")

    assert "> 📖 **补充知识（非论文原文）**" in text
    callout_pos = text.index("> 📖")
    # callout 块：从标题起连续的 blockquote 行
    callout_lines = []
    for line in text[callout_pos:].splitlines():
        if not line.startswith(">"):
            break
        callout_lines.append(line)
    assert len(callout_lines) >= 3
    assert all(line.startswith(">") for line in callout_lines)
    # callout 本身不带溯源（块外段落的溯源不算）
    assert not any("原文 §" in line for line in callout_lines)


def test_assemble_table_explained(tmp_path) -> None:
    run_dir = _build_run(tmp_path)
    text = (assemble_run(run_dir)["markdown_path"]).read_text(encoding="utf-8")
    assert "| 层类型 | 复杂度 |" in text
    assert "自注意力路径最短。" in text


def test_assemble_out_of_glossary_aggregated(tmp_path) -> None:
    run_dir = _build_run(tmp_path)
    result = assemble_run(run_dir)
    records = json.loads(result["out_of_glossary_path"].read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["term"] == "Sublayer"  # 保留首次写法
    assert [loc["section"] for loc in records[0]["locations"]] == ["tldr", "method"]


def test_assemble_missing_worked_example_renders_placeholder(tmp_path) -> None:
    run_dir = _build_run(tmp_path, we_status="missing")
    result = assemble_run(run_dir)
    assert result["worked_example_status"] == "missing"
    text = result["markdown_path"].read_text(encoding="utf-8")
    assert "手算示例" in text
    assert "未编造/放行错误算例" in text
    assert "**最终输出**" not in text  # 不渲染算例矩阵


def test_rename_for_qa(tmp_path) -> None:
    md = tmp_path / "attention-is-all-you-need.md"
    md.write_text("x", encoding="utf-8")
    unchecked = rename_for_qa(md, passed=False)
    assert unchecked.name == "attention-is-all-you-need.unchecked.md"
    assert not md.exists() and unchecked.exists()
    restored = rename_for_qa(unchecked, passed=True)
    assert restored == md and md.exists()


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------


def test_assemble_missing_section_raises_input_error(tmp_path) -> None:
    run_dir = _build_run(tmp_path)
    (run_dir / "sections" / "method.json").unlink()
    with pytest.raises(InputError, match="method.json"):
        assemble_run(run_dir)


def test_assemble_missing_worked_example_raises_input_error(tmp_path) -> None:
    run_dir = _build_run(tmp_path)
    (run_dir / "worked_example.json").unlink()
    with pytest.raises(InputError, match="worked_example.json"):
        assemble_run(run_dir)


def test_assemble_image_copy_failure_raises_local_io_error(tmp_path, monkeypatch) -> None:
    run_dir = _build_run(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(assemble_mod.shutil, "copyfile", boom)
    with pytest.raises(LocalIOError, match="图片拷贝失败"):
        assemble_run(run_dir)


# ---------------------------------------------------------------------------
# T037/FR-026：parse_gap 占位由组装层确定性注入，不经过 LLM
# ---------------------------------------------------------------------------


def _run_with_parse_gap(tmp_path: Path, *, action: str = "narrate") -> Path:
    run_dir = _build_run(tmp_path)
    normalized = json.loads((run_dir / "normalized.json").read_text("utf-8"))
    # sec-1 = Introduction，page_start=1；插入一个损坏公式占位块
    normalized["sections"][1]["blocks"].append("blk-gap")
    normalized["blocks"].append({
        "id": "blk-gap", "type": "parse_gap", "raw_type": "equation",
        "gap_kind": "equation", "page_idx": 1,
        "placeholder": "原文公式解析缺失，未编造内容补全。",
        "reason": "latex 去 $$ 后为空"})
    (run_dir / "normalized.json").write_text(
        json.dumps(normalized, ensure_ascii=False), encoding="utf-8")
    plan = json.loads((run_dir / "plan.json").read_text("utf-8"))
    plan["target_order"][1]["source_section_ids"] = ["sec-1"]
    plan["block_actions"]["blk-gap"] = {"action": action}
    (run_dir / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return run_dir


def test_assemble_injects_parse_gap_notice_into_mainline(tmp_path) -> None:
    run_dir = _run_with_parse_gap(tmp_path)
    text = (assemble_run(run_dir)["markdown_path"]).read_text("utf-8")
    notice = "> ⚠️ 原文公式解析缺失，未编造内容补全。"
    assert notice in text
    # 位置溯源：节编号（§1 Introduction）+ 页码（page_idx=1 → 第 2 页）
    line = next(ln for ln in text.splitlines() if notice in ln)
    assert "§1" in line and "p.2" in line


def test_assemble_skips_parse_gap_when_folded_or_dropped(tmp_path) -> None:
    run_dir = _run_with_parse_gap(tmp_path, action="drop")
    text = (assemble_run(run_dir)["markdown_path"]).read_text("utf-8")
    assert "原文公式解析缺失，未编造内容补全" not in text


def test_render_parse_gap_notices_resolves_synthetic_section_ids(tmp_path) -> None:
    """FR-014 合成 id（sec-1-extract）也要能命中间属节 sec-1 的 gap。"""
    run_dir = _run_with_parse_gap(tmp_path, action="condense")
    normalized = json.loads((run_dir / "normalized.json").read_text("utf-8"))
    plan = json.loads((run_dir / "plan.json").read_text("utf-8"))
    plan["target_order"][1]["source_section_ids"] = ["sec-1-extract"]
    owner = {"blk-gap": "Introduction"}
    pages = {"blk-gap": 1}
    lines = assemble_mod._render_parse_gap_notices(
        "intro", plan=plan, normalized=normalized, owner=owner, pages=pages)
    assert len(lines) == 1
    assert lines[0].startswith("> ⚠️ 原文公式解析缺失")
