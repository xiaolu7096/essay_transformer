"""T020/T026：13 项 QA hard checks 的 golden 全绿与逐项造错判 failed。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from essay_transformer.assemble import assemble_run
from essay_transformer.errors import QAFailedError
from essay_transformer.qa import (
    CHECK_IDS,
    QA_REPORT_FILENAME,
    build_qa_report,
    run_qa_report,
)
from essay_transformer.worked_example import TOY_DISCLAIMER

# 复用 assemble 的完整 run 构建器（含真实图片/七段 sections/公式卡/折叠）
from test_assemble import _build_run, FIG_JPG, CHART_JPG  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _write_confirmed_glossary(run_dir: Path) -> None:
    glossary = {
        "meta": {"paper_id": "arxiv-1706.03762", "status": "confirmed",
                 "confirmed_at": "2026-09-19T00:00:00", "schema_version": 1},
        # 条目可空：合并后四种子词由 seed.yml 提供
        "terms": [],
    }
    (run_dir / "glossary.yml").write_text(
        json.dumps(glossary, ensure_ascii=False), encoding="utf-8"
    )


def _edit_section(run_dir: Path, slug: str, mutate) -> None:
    path = run_dir / "sections" / f"{slug}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _setup(tmp_path, *, mutate_section=None, pre_qa_edit=None,
           glossary_status="confirmed", we_status="passed") -> Path:
    run_dir = _build_run(tmp_path, we_status=we_status)
    _write_confirmed_glossary(run_dir)
    if glossary_status != "confirmed":
        path = run_dir / "glossary.yml"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["meta"]["status"] = glossary_status
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    if mutate_section is not None:
        mutate_section(run_dir)
    result = assemble_run(run_dir)
    if pre_qa_edit is not None:
        pre_qa_edit(result["markdown_path"], run_dir)
    return run_dir


def _report_or_failed(run_dir: Path) -> dict:
    """run_qa_report 通过返回报告；失败时读盘报告（失败也必须落盘）。"""
    try:
        return run_qa_report(run_dir)
    except QAFailedError:
        return json.loads(
            (run_dir / QA_REPORT_FILENAME).read_text(encoding="utf-8")
        )


def _check(report: dict, check_id: str) -> dict:
    matches = [c for c in report["checks"] if c["id"] == check_id]
    assert len(matches) == 1, check_id
    return matches[0]


# ---------------------------------------------------------------------------
# golden
# ---------------------------------------------------------------------------


def test_qa_golden_all_thirteen_passed(tmp_path) -> None:
    run_dir = _setup(tmp_path)
    report = run_qa_report(run_dir)
    assert report["overall"] == "passed"
    assert [c["id"] for c in report["checks"]] == list(CHECK_IDS)
    assert len(CHECK_IDS) == 13
    assert all(c["status"] == "passed" for c in report["checks"])
    assert (run_dir / QA_REPORT_FILENAME).is_file()
    tldr = _check(report, "tldr-ratio")
    assert tldr["measured"] <= 0.05
    fold = _check(report, "fold-data-linkage")
    assert fold["sampled"] == 2 and fold["matched"] == 2
    # golden 实验段：1 个结论段落 + figure + fold_ref（后两者不占分母）
    ratio = _check(report, "experiment-conclusion-ratio")
    assert ratio["measured"] == 1.0
    assert ratio["conclusion_blocks"] == 1
    assert ratio["mainline_blocks"] == 1


# ---------------------------------------------------------------------------
# 逐项造错
# ---------------------------------------------------------------------------


def test_qa_glossary_format_failure(tmp_path) -> None:
    run_dir = _setup(tmp_path, glossary_status="draft")
    report = _report_or_failed(run_dir)
    assert report["overall"] == "failed"
    assert _check(report, "glossary-format")["status"] == "failed"


def test_qa_glossary_consistency_bare_english(tmp_path) -> None:
    def mutate(run_dir):
        def add_bare(doc):
            doc["blocks"][0]["intuition"] = (
                doc["blocks"][0].get("intuition", "") + "这里 self attention 是核心。"
            )
        _edit_section(run_dir, "method", add_bare)

    run_dir = _setup(tmp_path, mutate_section=mutate)
    report = _report_or_failed(run_dir)
    check = _check(report, "glossary-consistency")
    assert check["status"] == "failed"
    assert any("self attention" in f or "self-attention" in f
               for f in check["findings"])


def test_qa_target_order_failure(tmp_path) -> None:
    def edit(md_path, run_dir):
        text = md_path.read_text(encoding="utf-8")
        md_path.write_text(text.replace("## 总评", "## 错误的总评标题"),
                           encoding="utf-8")

    run_dir = _setup(tmp_path, pre_qa_edit=edit)
    report = _report_or_failed(run_dir)
    assert _check(report, "target-order")["status"] == "failed"


def test_qa_tldr_ratio_failure(tmp_path) -> None:
    def mutate(run_dir):
        def bloat(doc):
            doc["blocks"][0]["markdown"] = "摘" * 3000
        _edit_section(run_dir, "tldr", bloat)

    run_dir = _setup(tmp_path, mutate_section=mutate)
    report = _report_or_failed(run_dir)
    check = _check(report, "tldr-ratio")
    assert check["status"] == "failed"
    assert check["measured"] > 0.05


def test_qa_mainline_chart_rejected(tmp_path) -> None:
    def mutate(run_dir):
        def add_chart(doc):
            doc["blocks"].append({
                "kind": "figure",
                "img_path": f"images/{CHART_JPG}",
                "rewritten_caption": "训练曲线",
                "source_block_ids": ["blk-chart"],
            })
        _edit_section(run_dir, "experiments", add_chart)

    run_dir = _setup(tmp_path, mutate_section=mutate)
    report = _report_or_failed(run_dir)
    check = _check(report, "mainline-no-full-tables")
    assert check["status"] == "failed"
    assert any("blk-chart" in f for f in check["findings"])


def test_qa_mainline_hardware_config_rejected(tmp_path) -> None:
    def mutate(run_dir):
        def add_hardware(doc):
            doc["blocks"].append({
                "kind": "paragraph",
                "markdown": "复现这套结果需要 8 块 P100 GPU，设备门槛不低。",
                "source_block_ids": ["blk-tbl"],
            })
        _edit_section(run_dir, "experiments", add_hardware)

    run_dir = _setup(tmp_path, mutate_section=mutate)
    report = _report_or_failed(run_dir)
    check = _check(report, "mainline-no-full-tables")
    assert check["status"] == "failed"
    assert any("硬件配置" in f and "P100" in f for f in check["findings"])


def test_qa_mainline_training_duration_rejected(tmp_path) -> None:
    def mutate(run_dir):
        def add_duration(doc):
            doc["blocks"].append({
                "kind": "paragraph",
                "markdown": "完整模型训练耗时约 12 小时，成本不低。",
                "source_block_ids": ["blk-tbl"],
            })
        _edit_section(run_dir, "experiments", add_duration)

    run_dir = _setup(tmp_path, mutate_section=mutate)
    report = _report_or_failed(run_dir)
    check = _check(report, "mainline-no-full-tables")
    assert check["status"] == "failed"
    assert any("训练时长" in f and "12" in f for f in check["findings"])


def test_qa_mainline_qualitative_mention_allowed(tmp_path) -> None:
    """FR-020 允许一句话定性带过：无型号/数字的定性句不得误判。"""
    def mutate(run_dir):
        def add_qualitative(doc):
            doc["blocks"].append({
                "kind": "paragraph",
                "markdown": "新架构训练时间大幅缩短，所需 GPU 算力也更少。",
                "source_block_ids": ["blk-tbl"],
            })
        _edit_section(run_dir, "experiments", add_qualitative)

    run_dir = _setup(tmp_path, mutate_section=mutate)
    report = run_qa_report(run_dir)
    assert _check(report, "mainline-no-full-tables")["status"] == "passed"


def test_qa_experiment_conclusion_ratio_failure(tmp_path) -> None:
    """SC-008：实验段主线塞入两张完整数据表（1 段落 + 2 table_explained
    = 33% 结论级 < 80%），experiment-conclusion-ratio 必须 failed。"""
    def mutate(run_dir):
        def add_raw_tables(doc):
            doc["blocks"].extend([
                {"kind": "table_explained", "title": "完整超参数表",
                 "markdown": "| 超参 | 取值 |\n|---|---|\n| 学习率 | 0.001 |",
                 "explanation": "逐行罗列原始配置。",
                 "source_block_ids": ["blk-tbl"]},
                {"kind": "table_explained", "title": "消融全表",
                 "markdown": "| 配置 | BLEU |\n|---|---|\n| 单头 | 26.0 |",
                 "explanation": "逐行罗列消融原始数值。",
                 "source_block_ids": ["blk-tbl"]},
            ])
        _edit_section(run_dir, "experiments", add_raw_tables)

    run_dir = _setup(tmp_path, mutate_section=mutate)
    report = _report_or_failed(run_dir)
    check = _check(report, "experiment-conclusion-ratio")
    assert check["status"] == "failed"
    assert check["measured"] == round(1 / 3, 4)
    assert check["conclusion_blocks"] == 1
    assert check["mainline_blocks"] == 3
    assert any("SC-008" in f for f in check["findings"])


def test_qa_fold_data_linkage_failure(tmp_path) -> None:
    def mutate(run_dir):
        def break_link(doc):
            for block in doc["blocks"]:
                if block.get("kind") == "fold_ref" \
                        and block.get("fold_group") == "repro_data":
                    block["source_block_ids"] = ["blk-refh"]
        _edit_section(run_dir, "experiments", break_link)

    run_dir = _setup(tmp_path, mutate_section=mutate)
    report = _report_or_failed(run_dir)
    check = _check(report, "fold-data-linkage")
    assert check["status"] == "failed"
    assert check["matched"] < check["sampled"]


def test_qa_source_trace_failure(tmp_path) -> None:
    def mutate(run_dir):
        def ghost_source(doc):
            doc["blocks"][0]["source_block_ids"] = ["blk-404"]
        _edit_section(run_dir, "intro", ghost_source)

    run_dir = _setup(tmp_path, mutate_section=mutate)
    report = _report_or_failed(run_dir)
    check = _check(report, "source-trace")
    assert check["status"] == "failed"
    assert any("blk-404" in f for f in check["findings"])


def test_qa_supplement_marking_failure_on_fake_trace(tmp_path) -> None:
    def edit(md_path, run_dir):
        text = md_path.read_text(encoding="utf-8")
        text = text.replace(
            "> - 查询就是需求",
            "> - 查询就是需求\n> 详见原文 §3.2",
        )
        md_path.write_text(text, encoding="utf-8")

    run_dir = _setup(tmp_path, pre_qa_edit=edit)
    report = _report_or_failed(run_dir)
    assert _check(report, "supplement-marking")["status"] == "failed"


def test_qa_missing_placeholder_failure(tmp_path) -> None:
    def edit(md_path, run_dir):
        text = md_path.read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if "解析缺失，未编造内容补全" not in ln]
        md_path.write_text("\n".join(lines), encoding="utf-8")

    run_dir = _setup(tmp_path, pre_qa_edit=edit)
    report = _report_or_failed(run_dir)
    check = _check(report, "missing-placeholder")
    assert check["status"] == "failed"
    assert check["missing_assets"] >= 1


def test_qa_parse_gap_placeholder_golden(tmp_path) -> None:
    """FR-026：主线 parse_gap 的固定缺失说明在成稿中一一对应 → 通过。"""
    from test_assemble import _run_with_parse_gap

    run_dir = _run_with_parse_gap(tmp_path, action="narrate")
    _write_confirmed_glossary(run_dir)
    assemble_run(run_dir)
    report = run_qa_report(run_dir)
    check = _check(report, "missing-placeholder")
    assert check["status"] == "passed"
    assert check["parse_gaps"] == 1
    # 缺图占位 1 行 + parse_gap 占位 1 行
    assert check["placeholder_lines"] >= 2


def test_qa_parse_gap_placeholder_missing_in_markdown(tmp_path) -> None:
    from test_assemble import _run_with_parse_gap

    def edit(md_path, run_dir):
        text = md_path.read_text(encoding="utf-8")
        lines = [
            ln for ln in text.splitlines()
            if "原文公式解析缺失，未编造内容补全" not in ln
        ]
        md_path.write_text("\n".join(lines), encoding="utf-8")

    run_dir = _run_with_parse_gap(tmp_path, action="narrate")
    _write_confirmed_glossary(run_dir)
    assemble_run(run_dir)
    edit(run_dir / "dist" / "attention-is-all-you-need.md", run_dir)
    report = _report_or_failed(run_dir)
    check = _check(report, "missing-placeholder")
    assert check["status"] == "failed"
    assert check["parse_gaps"] == 1
    assert any("parse_gap" in f for f in check["findings"])


def test_qa_image_dead_link_failure(tmp_path) -> None:
    def edit(md_path, run_dir):
        (run_dir / "dist" / "images" / FIG_JPG).unlink()

    run_dir = _setup(tmp_path, pre_qa_edit=edit)
    report = _report_or_failed(run_dir)
    check = _check(report, "image-links")
    assert check["status"] == "failed"
    assert check["dead_links"] >= 1


def test_qa_worked_example_not_passed(tmp_path) -> None:
    run_dir = _setup(tmp_path, we_status="missing")
    report = _report_or_failed(run_dir)
    check = _check(report, "worked-example")
    assert check["status"] == "failed"
    assert check["verification_status"] == "missing"


def test_qa_toy_data_disclaimer_missing_in_markdown(tmp_path) -> None:
    def edit(md_path, run_dir):
        text = md_path.read_text(encoding="utf-8")
        md_path.write_text(text.replace(TOY_DISCLAIMER, "已删除声明"), encoding="utf-8")

    run_dir = _setup(tmp_path, pre_qa_edit=edit)
    report = _report_or_failed(run_dir)
    assert _check(report, "toy-data-disclaimer")["status"] == "failed"


def test_qa_failed_raises_exit_code_two(tmp_path) -> None:
    run_dir = _setup(tmp_path, we_status="missing")
    with pytest.raises(QAFailedError) as exc:
        run_qa_report(run_dir)
    assert exc.value.exit_code == 2
    assert exc.value.error_type == "qa"
    # 即使失败，报告文件也必须落盘供调试
    assert (run_dir / QA_REPORT_FILENAME).is_file()
