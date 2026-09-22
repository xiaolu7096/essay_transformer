"""US2 离线全链路（T028）：normalized → planner → 七段转写 → 手算示例 →
assemble → 13 项 QA，全程 Fake LLM，录制基于 mini_parsed 的真实 block/节 id。

断言（tasks.md T028 + CP-3）：

- 无独立译文中间文件，七段只以 sections/<slug>.json 结构化落盘；
- 成稿七段顺序固定、图片为相对链接且 0 死链、缺图有固定占位；
- 手算示例 verification=passed，QA 13 项全绿；
- US3：三类折叠（derivation/repro_data/references）默认闭合、
  消融一句话结论与折叠全表共享来源、参考文献原文完整无缺失；
- runs 目录产物齐备；断点续跑不重复调用 LLM；
- FR-008 闸门：convert 前无 confirmed 词表一律退出码 3；
- QA failed：退出码 2，成稿仅以 .unchecked.md 留存。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from essay_transformer.assemble import DIST_DIRNAME
from essay_transformer.planner import SLUG_ORDER
from essay_transformer.qa import QA_REPORT_FILENAME
from essay_transformer.state import JobState
from essay_transformer.worked_example import TOY_DISCLAIMER, WORKED_EXAMPLE_FILENAME
from essay_transformer.cli import main

PID = "attention-demo"

FIG1 = "f05ba6a0a2e484dbd8bf2a4a4fa2597a69a3a076f5b03cf4e818b176b4049244.jpg"
MISSING_IMG = "__missing_fixture_image__.jpg"
CHART = "8b8122ff9e5a304dbbfde70f24d29dc112b471b5f78d64243dcea37188fb0eb7.jpg"

LATEX_ATTENTION = (
    r"\operatorname{Attention} (Q, K, V) = "
    r"\operatorname{softmax} (\frac {Q K ^ {T}}{\sqrt {d _ {k}}}) V"
)

# LLM 形态的 3 词 toy 算例：只给 setup 与四步讲解，四步矩阵/result 由
# worked_example 阶段用 numpy 确定性计算（T040 契约）
TOKENS = ["思考", "是", "力量"]
DK = 3
Q = [[1, 0, 1], [0, 1, 1], [1, 1, 0]]
K = [[1, 0, 0], [0, 1, 0], [1, 1, 1]]
V = [[1, 2, 0], [0, 1, 1], [1, 0, 1]]


# ---------------------------------------------------------------------------
# 共享录制工厂
# ---------------------------------------------------------------------------


def worked_example_response(block_id: str = "blk-0009") -> dict:
    names = ["计算相关性分数", "缩放", "逐行 softmax 得注意力权重", "加权求和"]
    exprs = ["Q K^T", "/ sqrt(d_k)", "softmax(...)", "weights · V"]
    explains = [
        "每个词与每个词的相关性。",
        "除以根号维度，防止点积过大。",
        "每行归一化为权重，和为一。",
        "用权重对取值加权求和。",
    ]
    return {
        "schema_kind": "worked_example",
        "formula_block_id": block_id,
        "is_toy_data": True,
        "disclaimer": TOY_DISCLAIMER,
        "setup": {"tokens": list(TOKENS), "d_k": DK,
                  "Q": [list(row) for row in Q],
                  "K": [list(row) for row in K],
                  "V": [list(row) for row in V]},
        "steps": [
            {"name": n, "expr": e, "explain_zh": x}
            for n, e, x in zip(names, exprs, explains)
        ],
    }


def planner_response() -> dict:
    """针对 mini_parsed 真实 6 节/19 块的录制计划（experiments 用合成尾段）。"""
    def entry(slug, sources, treatment):
        return {"target_slug": slug, "source_section_ids": sources,
                "treatment": treatment}

    return {
        "schema_kind": "planner",
        "target_order": [
            entry("tldr", ["sec-abstract"], "brief"),
            entry("intro", ["sec-1"], "expand"),
            entry("conclusion", ["sec-7"], "front_load_extract"),
            entry("related", ["sec-3"], "compress"),
            entry("method", ["sec-3-2"], "tutorial"),
            # experiments 经 sec-references-extract 合成 id 取得 chart
            # 原始数据块（FR-014），用于消融结论↔折叠全表联动；
            # sec-references 真实节仍只在 wrapup 直接出现一次
            entry("experiments", ["sec-7-tail", "sec-references-extract"],
                  "conclusion_only"),
            entry("wrapup", ["sec-references"], "summary"),
        ],
        "block_actions": {
            "blk-0000": {"action": "drop"},
            "blk-0001": {"action": "narrate"},
            "blk-0002": {"action": "narrate"},
            "blk-0003": {"action": "narrate"},
            "blk-0004": {"action": "narrate"},
            "blk-0005": {"action": "narrate"},
            # blk-0006 是 noise，无需 action
            "blk-0007": {"action": "keep_figure",
                         "rationale": "整体架构示意图，帮助建立数据流直觉，保留并重写图注"},
            "blk-0008": {"action": "narrate"},
            "blk-0009": {"action": "narrate"},
            "blk-0010": {"action": "fold", "fold_group": "derivation"},
            "blk-0011": {"action": "keep_table_explained"},
            "blk-0012": {"action": "narrate"},
            "blk-0013": {"action": "keep_figure",
                         "rationale": "原文示意图但图片文件解析缺失，保留位置以固定占位呈现，不编造图中内容"},
            "blk-0014": {"action": "narrate"},
            "blk-0015": {"action": "narrate"},
            "blk-0016": {"action": "fold", "fold_group": "references"},
            "blk-0018": {"action": "fold", "fold_group": "repro_data",
                         "rationale": "消融/训练曲线类工程图，属复现原始数据，进 repro_data 折叠"},
        },
        "key_formulas": [
            {"block_id": "blk-0009", "latex": LATEX_ATTENTION,
             "label": "注意力公式",
             "worked_example_required": True,
             "example_spec": {"tokens": 3,
                              "steps": ["相关性", "缩放", "归一化", "加权"]}},
        ],
    }


def _para(kind, bid, page, text, **extra):
    return {"kind": kind, "markdown": text,
            "source_block_ids": [bid], "source_pages": [page], **extra}


def section_responses() -> dict[str, dict]:
    """七段录制响应：全中文主线（英文只出现在公式/折叠区），保证 QA 全绿。"""
    return {
        "tldr": {
            "schema_kind": "section_rewrite", "target_slug": "tldr",
            "blocks": [_para(
                "tldr", "blk-0002", 0,
                "三十秒读完：抛弃循环、只用注意力，机器翻译又快又好，"
                "还为后续大模型铺好了路。")],
            "out_of_glossary": [],
        },
        "intro": {
            "schema_kind": "section_rewrite", "target_slug": "intro",
            "blocks": [
                _para("paragraph", "blk-0003", 1,
                      "这篇论文要解决的第一个问题是：当时主流的序列建模几乎全部"
                      "建立在循环神经网络之上，模型必须沿着词序一个接一个地"
                      "计算，训练很难并行，长句子里相距很远的信息还容易在层层"
                      "传递中被稀释。"),
                _para("paragraph", "blk-0004", 1,
                      "作者的动机很直接：如果让句子里任意两个位置都能直接"
                      "“对望”，就不必再依赖顺序传递；这篇文章第一次把这种"
                      "对望机制做成模型的唯一主角，并在机器翻译上验证它"
                      "确实更快、更好。"),
            ],
            "out_of_glossary": [],
        },
        "conclusion": {
            "schema_kind": "section_rewrite", "target_slug": "conclusion",
            "blocks": [
                _para("paragraph", "blk-0014", 9,
                      "先看结论：只靠注意力的新架构在两个翻译任务上都超过了"
                      "当时的最佳结果，训练成本反而明显更低；作者还指出它"
                      "可以推广到文本之外的其他模态，这条路线值得继续投入。"),
                {"kind": "figure",
                 "img_path": f"images/{MISSING_IMG}",
                 "rewritten_caption": "原文第 6 页示意图（解析缺失，见占位说明）",
                 "source_block_ids": ["blk-0013"], "source_pages": [5]},
            ],
            "out_of_glossary": [],
        },
        "related": {
            "schema_kind": "section_rewrite", "target_slug": "related",
            "blocks": [
                _para("paragraph", "blk-0005", 1,
                      "理解方法之前先看它站在什么基础上：过去的编码器与解码器"
                      "都靠循环结构堆叠，注意力最初只是解码器里一个用来"
                      "“瞄一眼编码器”的辅助零件；本文把这个零件升级成了"
                      "整套架构的承重墙。"),
                {"kind": "figure",
                 "img_path": f"images/{FIG1}",
                 "rewritten_caption": "图一：整体架构的数据流一览，编码器在左、解码器在右。",
                 "source_block_ids": ["blk-0007"], "source_pages": [2]},
            ],
            "out_of_glossary": [],
        },
        "method": {
            "schema_kind": "section_rewrite", "target_slug": "method",
            "blocks": [
                _para("paragraph", "blk-0008", 2,
                      "核心方法从一个问题开始：当前位置应该多关注句子里的"
                      "哪些词。作者用三组向量回答它——查询、键和值，先算"
                      "相关性，再按相关性取值。"),
                {"kind": "formula_card",
                 "markdown": "注意力公式",
                 "latex": LATEX_ATTENTION,
                 "symbol_breakdown": [
                     {"symbol": "Q", "meaning": "查询矩阵：当前位置想找什么"},
                     {"symbol": "K", "meaning": "键矩阵：每个位置能提供什么"},
                     {"symbol": "V", "meaning": "值矩阵：每个位置真正携带的信息"}],
                 "intuition": "把每个词对每个词的相关性做一次归一化，"
                              "相关性高的词在输出里占比就大；这就是标题里"
                              "“注意力”三个字的全部数学含义。",
                 "worked_example_ref": "we-001",
                 "source_block_ids": ["blk-0009"], "source_pages": [3]},
                {"kind": "table_explained",
                 "markdown_table": (
                     "| 层类型 | 最长依赖路径 |\n|---|---|\n"
                     "| 注意力层 | 常数级，任意两词直接对望 |\n"
                     "| 循环层 | 线性级，信息要沿词序接力 |"),
                 "explanation": "读这张表只记一个结论：路径越短，"
                                "梯度和信息在长句子上就越不容易丢失。",
                 "source_block_ids": ["blk-0011"], "source_pages": [5]},
                {"kind": "supplement", "supplement_kind": "background",
                 "markdown": "先补一个检索的直觉：\n\n- 查询就是你心里的问题；\n"
                             "- 键是每条候选内容的标签；\n- 两者越匹配，"
                             "对应内容被取走的份额越大。",
                 "source_block_ids": []},
                {"kind": "fold_ref", "fold_group": "derivation",
                 "title": "多头注意力的完整推导",
                 "content_markdown": (
                     "把多组查询、键、值并行做八次注意力，再拼接后过一次"
                     "线性投影，就得到多头版本；每组头可以各自关注不同的"
                     "语法与语义关系。"),
                 "source_block_ids": ["blk-0009", "blk-0010"],
                 "source_pages": [3, 4]},
            ],
            "out_of_glossary": [],
        },
        "experiments": {
            "schema_kind": "section_rewrite", "target_slug": "experiments",
            "blocks": [
                _para("paragraph", "blk-0012", 9,
                      "实验只讲结论级表达：在英译德和英译英两个任务上，"
                      "新架构的质量都超过了当时的最佳系统，而训练只用了"
                      "零头级别的算力开销，并行度的优势直接体现在墙钟时间上。"),
                _para("paragraph", "blk-0014", 9,
                      "消融还说明多头并非越多越好、键的维度也需要控制；"
                      "完整超参、逐条配置与训练曲线都收在文末折叠区，"
                      "读者需要复现时再展开即可。"),
                # T033：消融一句话结论（上句，blk-0014）↔ 折叠全表/曲线
                # 共享 source_block_ids；完整原始数据只留在折叠区（FR-019/020）
                {"kind": "fold_ref", "fold_group": "repro_data",
                 "title": "消融全表与训练曲线",
                 "content_markdown": (
                     "消融实验完整结果（原样保留，复现时逐行核对）：\n\n"
                     "| 配置 | 关键变化 | EN-DE BLEU |\n"
                     "|---|---|---|\n"
                     "| base | 8 头、单头维度 64 | 27.3 |\n"
                     "| 单头 | 仅 1 个头 | 26.0 |\n"
                     "| 32 头 | 32 个头、单头维度 16 | 26.8 |\n"
                     "| 更大键维度 | 键维度翻倍 | 26.9 |\n\n"
                     f"![训练曲线与逐层可视化（原文 chart 原图）](images/{CHART})"
                 ),
                 "source_block_ids": ["blk-0014", "blk-0018"],
                 "source_pages": [9, 12]},
            ],
            "out_of_glossary": [],
        },
        "wrapup": {
            "schema_kind": "section_rewrite", "target_slug": "wrapup",
            "blocks": [
                _para("paragraph", "blk-0015", 9,
                      "总评：这篇文章用一个极简的对望机制替换掉统治序列建模"
                      "多年的循环结构，换来并行能力、短路径与可扩展性，"
                      "也为后来的大规模预训练模型定下了基础骨架，值得精读。"),
                # T033：references 折叠只承接 reference_list 原文块；
                # content 故意给中文转述，assemble 必须以 normalized 原文覆盖
                {"kind": "fold_ref", "fold_group": "references",
                 "title": "参考文献",
                 "content_markdown": (
                     "[1] LLM-中文转述占位，组装层应以解析原文覆盖。\n\n"
                     "[2] LLM-中文转述占位，组装层应以解析原文覆盖。"),
                 "source_block_ids": ["blk-0016"],
                 "source_pages": [9]},
            ],
            "out_of_glossary": [],
        },
    }


# 从 rewrite_section user prompt 提取当前段（与 prompt 首行标记配套）
_SLUG_RE = __import__("re").compile(r"- `(?P<slug>[a-z_]+)`：本段目标段")


def make_recorded_llm(make_fake_llm, *, worked_queue=None):
    recorded = section_responses()

    def section_answer(system, user, schema_kind):
        slug = _SLUG_RE.search(user).group("slug")
        return recorded[slug]

    responses = {
        "planner": planner_response(),
        "section_rewrite": section_answer,
        "worked_example": worked_queue or worked_example_response(),
    }
    return make_fake_llm(responses)


def write_confirmed_glossary(run_dir: Path) -> None:
    glossary = {
        "meta": {"paper_id": PID, "status": "confirmed",
                 "confirmed_at": "2026-09-19T00:00:00", "schema_version": 1},
        # 条目留空：强制种子词在 convert 闸门处由 seed.yml 合并提供
        "terms": [],
    }
    (run_dir / "glossary.yml").write_text(
        yaml.safe_dump(glossary, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINERU_API_KEY", "test-mineru-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")


@pytest.fixture()
def runs_dir(tmp_path: Path) -> Path:
    path = tmp_path / "runs"
    path.mkdir()
    return path


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.5\nfake pdf body for offline test\n")
    return path


def _parse(runs_dir: Path, sample_pdf: Path, fake_mineru) -> Path:
    rc = main(
        ["parse", str(sample_pdf), "--paper-id", PID],
        mineru_client=fake_mineru,
        runs_dir=runs_dir,
    )
    assert rc == 0
    return runs_dir / PID


# ---------------------------------------------------------------------------
# 全链路
# ---------------------------------------------------------------------------


def test_normalized_to_dist_full_offline_pipeline(
    runs_dir: Path,
    sample_pdf: Path,
    make_fake_llm,
    fake_mineru,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _parse(runs_dir, sample_pdf, fake_mineru)

    # --- FR-008 闸门：confirmed 词表不存在时 convert 必须退出码 3 ---
    llm = make_recorded_llm(make_fake_llm)
    rc = main(["convert", PID], llm_client=llm, runs_dir=runs_dir)
    assert rc == 3
    assert llm.call_count == 0  # 闸门失败不得触发任何 LLM 调用
    capsys.readouterr()

    # --- 人工闸门产物就位 ---
    write_confirmed_glossary(run_dir)

    # --- 全链路 convert ---
    rc = main(["convert", PID, "--jobs", "4"], llm_client=llm, runs_dir=runs_dir)
    out = capsys.readouterr().out
    assert rc == 0, out
    kinds = llm.schema_kinds_called()
    assert kinds == ["planner"] + ["section_rewrite"] * 7 + ["worked_example"]

    # 状态机走完全程
    state = JobState.load(run_dir, paper_id=PID)
    assert state.stage == "done"
    assert state.failed is None

    # --- runs 目录产物齐备 ---
    assert (run_dir / "plan.json").is_file()
    section_files = {p.name for p in (run_dir / "sections").iterdir()}
    assert section_files == {f"{slug}.json" for slug in SLUG_ORDER}
    worked = json.loads(
        (run_dir / WORKED_EXAMPLE_FILENAME).read_text(encoding="utf-8")
    )
    assert worked["verification"]["status"] == "passed"
    assert (run_dir / "out_of_glossary.json").is_file()

    # 不存在任何独立译文中间文件（译文只允许结构化 sections JSON）
    leftovers = [
        str(p.relative_to(run_dir))
        for p in run_dir.rglob("*")
        if p.is_file() and (
            "translation" in p.name.lower()
            or p.name.endswith(".zh.md")
            or p.name.endswith(".translated.md")
        )
    ]
    assert leftovers == []

    # QA 报告 13 项全绿
    report = json.loads((run_dir / QA_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert report["overall"] == "passed"
    assert len(report["checks"]) == 13
    assert [c["status"] for c in report["checks"]] == ["passed"] * 13
    ratio_check = next(c for c in report["checks"]
                       if c["id"] == "experiment-conclusion-ratio")
    # 实验段 2 个结论段落 + 1 个 fold_ref（不占分母）→ 100%
    assert ratio_check["measured"] == 1.0

    # --- 成稿断言 ---
    dist = run_dir / DIST_DIRNAME
    md_files = sorted(p.name for p in dist.glob("*.md"))
    assert md_files == ["attention-is-all-you-need.md"]  # 无 .unchecked.md
    markdown = (dist / md_files[0]).read_text(encoding="utf-8")

    headings = [
        "## 30 秒摘要",
        "## 这篇论文要解决什么问题（引言精讲）",
        "## 结论：先看它最后说了什么",
        "## 背景与相关工作",
        "## 核心方法",
        "## 实验说明了什么",
        "## 总评",
    ]
    positions = [markdown.index(h) for h in headings]
    assert positions == sorted(positions)

    # 手算示例与教学声明进入成稿；图片相对链接 0 死链；缺图有占位
    assert TOY_DISCLAIMER in markdown
    assert "手算小例子（3 个词走一遍）" in markdown
    assert "解析缺失，未编造内容补全" in markdown
    assert f"](images/{FIG1})" in markdown
    assert not (dist / "images" / MISSING_IMG).exists()
    assert (dist / "images" / FIG1).is_file()
    # 公式逐字符保留
    assert LATEX_ATTENTION in markdown

    # --- US3（T033）：三类折叠默认闭合、内容完整、结论↔原始数据联动 ---
    details_tags = re.findall(r"<details\b[^>]*>", markdown)
    assert details_tags and all(t == "<details>" for t in details_tags)
    assert "<details open" not in markdown
    assert "<summary>推导与证明：多头注意力的完整推导（点击展开）</summary>" in markdown
    assert "<summary>复现参考：消融全表与训练曲线（点击展开）</summary>" in markdown
    assert "<summary>参考文献（共 2 条，点击展开）</summary>" in markdown

    # 消融一句话结论对应的折叠全表逐行保留，chart 原图已拷贝、0 死链
    assert "| 单头 | 仅 1 个头 | 26.0 |" in markdown
    assert "| 更大键维度 | 键维度翻倍 | 26.9 |" in markdown
    assert f"](images/{CHART})" in markdown
    assert (dist / "images" / CHART).is_file()

    # 参考文献以解析原文渲染：两条完整英文条目逐字在折叠区、无缺失，
    # LLM 中文转述不进入成稿（FR-022/023）
    ref_zone = markdown[markdown.index("<summary>参考文献"):]
    assert "Layer normalization. arXiv preprint arXiv:1607.06450, 2016." in ref_zone
    assert "CoRR, abs/1409.0473, 2014." in ref_zone
    assert "LLM-中文转述占位" not in markdown

    # fold-data-linkage：derivation@method 与 repro_data@experiments
    # 均与同段结论块共享 source_block_ids（sampled=2, matched=2）
    linkage = next(c for c in report["checks"]
                   if c["id"] == "fold-data-linkage")
    assert linkage["sampled"] == 2 and linkage["matched"] == 2

    # stdout：阶段进度 / 绝对路径 / QA 摘要 / 耗时
    assert "成稿绝对路径：" in out
    assert "QA 全部通过（13/13）" in out
    assert "耗时：" in out

    # --- 断点续跑：planner/rewrite/example 全部跳过，LLM 零新增调用 ---
    before = llm.call_count
    rc = main(["convert", PID], llm_client=llm, runs_dir=runs_dir)
    assert rc == 0
    assert llm.call_count == before

    # --- --from-stage assembled：只重做本地组装与 QA，仍不触 LLM ---
    rc = main(["convert", PID, "--from-stage", "assembled"],
              llm_client=llm, runs_dir=runs_dir)
    assert rc == 0
    assert llm.call_count == before
    assert JobState.load(run_dir, paper_id=PID).stage == "done"


def test_convert_qa_failure_keeps_only_unchecked_and_exit_2(
    runs_dir: Path,
    sample_pdf: Path,
    make_fake_llm,
    fake_mineru,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _parse(runs_dir, sample_pdf, fake_mineru)
    write_confirmed_glossary(run_dir)

    # 手算示例 setup 结构损坏（Q 行数与 tokens/K/V 不一致）：schema 合法、
    # 确定性数值重算拒绝修复、两次尝试均失败 → 留痕 missing
    bad = worked_example_response()
    bad["setup"]["Q"] = bad["setup"]["Q"][:2]
    llm = make_recorded_llm(make_fake_llm, worked_queue=[bad, bad])

    rc = main(
        ["convert", PID, "--allow-missing-example"],
        llm_client=llm, runs_dir=runs_dir,
    )
    assert rc == 2

    report = json.loads((run_dir / QA_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert report["overall"] == "failed"
    failed_ids = {c["id"] for c in report["checks"] if c["status"] == "failed"}
    assert "worked-example" in failed_ids

    dist = run_dir / DIST_DIRNAME
    clean = [p for p in dist.glob("*.md") if not p.name.endswith(".unchecked.md")]
    assert not clean  # 规范名成稿不得存在
    unchecked = list(dist.glob("*.unchecked.md"))
    assert len(unchecked) == 1

    state = JobState.load(run_dir, paper_id=PID)
    assert state.stage == "assembled"  # qa_passed 未推进
    assert state.failed is not None and state.failed.failed_stage == "qa_passed"

    err = capsys.readouterr().err
    assert "QA 未通过" in err and ".unchecked.md" in err
