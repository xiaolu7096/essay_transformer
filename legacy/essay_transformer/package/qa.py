"""13 项确定性 QA hard checks（T026，data-model.md §8）。

组装产物（dist 成稿 + sections/*.json + normalized/plan/worked_example +
confirmed glossary）落盘后，本模块做**纯确定性扫描**，不调用 LLM：

 1. glossary-format        已确认词表 meta/条目合法
 2. glossary-consistency   主线全文术语格式扫描（中文（English）配对，两种括号等价）
 3. target-order           七段固定标题齐备且有序
 4. tldr-ratio             30 秒摘要 ≤ 正文 5%
 5. mainline-no-full-tables 主线图：超参/消融全表、训练曲线图、
    具体硬件配置、训练时长数量为 0（一句话定性描述允许）
 6. experiment-conclusion-ratio 实验章节结论级陈述块 ≥80%（SC-008），
    table_explained 完整数据表块计入原始数据；figure/fold_ref/supplement
    不占陈述分母（数据图由第 5 项单独拦截）
 7. fold-data-linkage      抽 5：fold_ref 与同段结论块共享 source_block_ids
 8. source-trace           抽 20：事实块在成稿中有斜体溯源
 9. supplement-marking     supplement 以 callout 呈现且无伪溯源
10. missing-placeholder    解析缺失处必有固定占位文案
11. image-links            成稿图片链接 0 死链
12. worked-example         verification=passed 且主线存在 we-001 引用
13. toy-data-disclaimer    is_toy_data=true 且 disclaimer 逐字进入成稿

任一 failed → overall=failed（CLI 退出码 2 + .unchecked.md）。报告落
``qa_report.json``。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .assemble import DIST_DIRNAME, SECTION_HEADINGS, slugify_title
from .errors import InputError, PreconditionError, QAFailedError
from .glossary import (
    Term,
    load_confirmed_glossary,
    validate_glossary,
)
from .planner import SLUG_ORDER, load_normalized, load_plan
from .worked_example import (
    TOY_DISCLAIMER,
    WORKED_EXAMPLE_FILENAME,
    WORKED_EXAMPLE_ID,
)

__all__ = ["QA_REPORT_FILENAME", "CHECK_IDS", "run_qa", "run_qa_report"]

logger = logging.getLogger(__name__)

QA_REPORT_FILENAME = "qa_report.json"

CHECK_IDS: tuple[str, ...] = (
    "glossary-format",
    "glossary-consistency",
    "target-order",
    "tldr-ratio",
    "mainline-no-full-tables",
    "experiment-conclusion-ratio",
    "fold-data-linkage",
    "source-trace",
    "supplement-marking",
    "missing-placeholder",
    "image-links",
    "worked-example",
    "toy-data-disclaimer",
)

TLDR_RATIO_LIMIT = 0.05
# SC-008：实验章节结论级陈述块占比下限（原始数据表块 ≤20%）
EXPERIMENT_CONCLUSION_RATIO_LIMIT = 0.8
FOLD_SAMPLE_SIZE = 5
TRACE_SAMPLE_SIZE = 20

# experiment-conclusion-ratio 的块分类
_RATIO_CONCLUSION_KINDS = ("paragraph", "formula_card")
_RATIO_RAW_DATA_KINDS = ("table_explained",)
_RATIO_EXCLUDED_KINDS = ("fold_ref", "supplement", "figure")

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_BLOCK_MATH_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"\$[^$\n]+\$")
# intuition 等讲解字段里模型裸写（未包 $）的 LaTeX 数学标识符：
# 带下标/上标的 token（warmup_steps^{-1.5}、head_i、W_i^Q），
# 以及形如 Attention(Q W, K W) 的函数调用（括号内含 Q/K/V 类数学变量）。
# 这些是数学符号不是英文术语，先剥下标 token 再剥含单/双大写变量的调用。
_TEX_SUBSCRIPT_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9]*(?:[_\^]\{?[A-Za-z0-9.,\s\-+]*\}?)+"
)
_TEX_CALL_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9]*\s*\([^()]*\b[A-Z]{1,2}\b[^()]*\)"
)
_FOLD_ZONE_RE = re.compile(r"<details>.*?</details>", re.DOTALL)
_IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_TRACE_RE = re.compile(r"\*（原文 [^*]+）\*")
_PLACEHOLDER_CORE = "解析缺失，未编造内容补全"
# FR-020：具体硬件配置（型号或数量+设备）不得进主线；裸提"GPU"的定性句
# （如"GPU 算力更省"）合法，故要求数量词或 NVIDIA/英伟达前缀，避免误伤。
_HARDWARE_DETAIL_RE = re.compile(
    r"(?:\d+\s*(?:张|块|台|颗|路|核|×|x|X|\*)?\s*(?:NVIDIA\s*)?"
    r"(?:GPU|TPU|V-?100|P100|A100|H100|显卡)"
    r"|(?:GPU|TPU|V-?100|P100|A100|H100)\s*(?:×|x|X|\*)\s*\d+"
    r"|(?:NVIDIA|英伟达)\s*(?:GPU|TPU|V-?100|P100|A100|H100))",
    re.IGNORECASE,
)
# FR-020：具体训练时长（数字 + 时间/算力单位）不得进主线；
# "训练时间大幅缩短"这类无数字的定性描述不匹配。
_TRAINING_DURATION_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:GPU|TPU)[-\s]?(?:小时|hours?|hrs?)"
    r"|(?:训练耗时?|总耗时|耗时|用时|花费)[^。\n；;]{0,24}?"
    r"\d+(?:\.\d+)?\s*(?:个?小时|天|日|周|分钟|hours?|hrs?|days?|weeks?|minutes?))",
    re.IGNORECASE,
)
_CALLOUT_HEADER_RE = re.compile(r"^> [📖💡] \*\*（?补充知识|打个比方）?")

# 主线中常见的高维文本字段（tldr 比率只数讲解性文字）
_TEXT_FIELDS = ("markdown", "intuition", "explanation", "rewritten_caption", "title")


# ---------------------------------------------------------------------------
# 装载与文本工具
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{path.name} 无法读取/解析：{path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise InputError(f"{path.name} 根结构不是对象：{path}")
    return data


def _load_sections(run_dir: Path) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    root = run_dir / "sections"
    for slug in SLUG_ORDER:
        path = root / f"{slug}.json"
        if not path.is_file():
            raise InputError(f"缺少转写章节：{path}")
        sections[slug] = _read_json(path)
    return sections


def _find_markdown(run_dir: Path, normalized: dict[str, Any]) -> Path:
    dist = run_dir / DIST_DIRNAME
    candidates = [
        p for p in dist.glob("*.md")
        if not p.name.endswith(".unchecked.md")
    ]
    if len(candidates) == 1:
        return candidates[0]
    expected = slugify_title(
        normalized.get("title") or normalized.get("paper_id") or "paper"
    ) + ".md"
    canonical = dist / expected
    if canonical.is_file():
        return canonical
    raise InputError(
        f"dist 下找不到唯一成稿（期望 {expected}）；请先运行 assemble 阶段。"
    )


def _strip_for_scan(markdown: str, *, keep_folds: bool = False) -> str:
    """去掉代码/公式（术语豁免）、折叠区（参考文献等原文转写豁免）以及
    论文标题/元信息/callout 行（不承载术语规则的版式元素）。"""
    text = markdown
    text = _FENCE_RE.sub("", text)
    text = _INLINE_CODE_RE.sub("", text)
    text = _BLOCK_MATH_RE.sub("", text)
    text = _INLINE_MATH_RE.sub("", text)
    # 裸写 LaTeX：先下标/上标 token，再数学函数调用（下标剥除后暴露变量）
    text = _TEX_SUBSCRIPT_RE.sub("", text)
    text = _TEX_CALL_RE.sub("", text)
    if not keep_folds:
        text = _FOLD_ZONE_RE.sub("", text)
    kept = [
        line for line in text.splitlines()
        if not line.startswith("#") and not line.startswith(">")
    ]
    return "\n".join(kept)


def _visible_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _iter_blocks(sections: dict[str, dict[str, Any]]):
    for slug in SLUG_ORDER:
        for idx, block in enumerate(sections[slug].get("blocks", [])):
            yield slug, idx, block


# ---------------------------------------------------------------------------
# 13 项检查（每个返回 dict：id/status 必填，可附 measured/findings 等）
# ---------------------------------------------------------------------------


def _check_glossary_format(run_dir: Path) -> dict[str, Any]:
    """glossary.yml 必须存在且 status=confirmed，并通过结构校验。"""
    findings: list[str] = []
    try:
        merged = load_confirmed_glossary(run_dir)
        # 合并 seed 后的条目仍须逐条满足术语契约（meta 已由加载器确认）
        validate_glossary(merged.terms, require_seed_terms=False)
    except PreconditionError as exc:
        findings.extend(str(exc).splitlines())
    return {"id": "glossary-format",
            "status": "passed" if not findings else "failed",
            **({"findings": findings} if findings else {})}


def _check_glossary_consistency(
    run_dir: Path, mainline_text: str
) -> dict[str, Any]:
    findings: list[str] = []
    try:
        terms = load_confirmed_glossary(run_dir).terms
    except PreconditionError as exc:
        # GlossaryNotConfirmedError 与 InputError 同为 PreconditionError 子类
        return {"id": "glossary-consistency", "status": "failed",
                "findings": [str(exc)]}

    # 英文原形跨词表最长匹配（连字符边界 + span 遮蔽）：
    # self-attention 不再内部触发 attention；encoder-decoder 作为词表
    # 词条整体命中后不再逐成分触发 encoder/decoder。
    alternatives: list[tuple[str, int]] = []  # (variant, term_index)
    bilingual_idx: list[int] = []
    for idx, term in enumerate(terms):
        variants = sorted(
            {v.strip() for v in (term.en, *term.aliases) if v.strip()},
            key=len, reverse=True,
        )
        if not variants:
            continue
        if term.mode == "bilingual":
            zh = term.zh.strip()
            # zh 与英文同形（Transformer=Transformer、BLEU=BLEU 类专名）
            # 时无配对概念，整体豁免，不纳入英文扫描
            if not zh or zh.casefold() in {v.casefold() for v in variants}:
                continue
            bilingual_idx.append(idx)
        else:
            # zh_only：英文同形变体豁免
            variants = [v for v in variants
                        if term.zh.strip().casefold() != v.casefold()]
        alternatives.extend((v, idx) for v in variants)

    if alternatives:
        alternatives.sort(key=lambda item: len(item[0]), reverse=True)
        pattern = re.compile(
            r"(?<![A-Za-z-])(?:"
            + "|".join(re.escape(v) for v, _ in alternatives)
            + r")(?![A-Za-z-])",
            re.IGNORECASE,
        )
        hits = [(m.start(), m.end(), m.group(0)) for m in pattern.finditer(mainline_text)]
    else:
        hits = []

    # 合法配对括号覆盖的区间（含中文译名部分，整体豁免）与译名裸用区间
    paired_spans = _bilingual_pair_spans(mainline_text, terms, bilingual_idx)
    paired_spans += _zh_mention_spans(mainline_text, terms, bilingual_idx)

    def in_pair(start: int, end: int) -> bool:
        return any(span_start <= start and end <= span_end
                   for span_start, span_end in paired_spans)

    variant_to_term = {v.lower(): idx for v, idx in alternatives}
    for start, end, matched in hits:
        ti = variant_to_term[matched.lower()]
        term = terms[ti]
        if term.mode == "bilingual":
            if not in_pair(start, end):
                findings.append(
                    f"术语 {term.en!r} 未按「{term.zh.strip()}（{term.en}）」格式出现："
                    f"…{mainline_text[max(0, start - 12):end + 12].strip()}…"
                )
        else:
            findings.append(
                f"{term.en!r} 为 zh_only 术语，但主线出现英文原形："
                f"…{mainline_text[max(0, start - 12):end + 12].strip()}…"
            )
    return {"id": "glossary-consistency",
            "status": "passed" if not findings else "failed",
            **({"findings": findings[:20]} if findings else {})}


# markdown 强调/行内代码标记（容许插在译名与括号之间、译名各字之间）
_EMPH_PAT = r"(?:\*{1,2}|_{1,2}|`)"
_HAN = r"[一-鿿]"


def _zh_loose(zh: str) -> str:
    """中文译名宽松形态：字间与两端容许 markdown 强调/代码标记。"""
    core = (_EMPH_PAT + "*").join(re.escape(ch) for ch in zh)
    return _EMPH_PAT + "*" + core + _EMPH_PAT + "*"


def _bilingual_pair_spans(
    text: str, terms: list[Term], bilingual_idx: list[int]
) -> list[tuple[int, int]]:
    """识别全部合法「中文译名（English）」配对，返回括号英文串的全文区间。

    容许的真实成稿形态（T040 真实首跑取证）：
    - 译名与左括号之间夹 markdown 强调：``**注意力机制**（attention）``；
    - 译名带有限中文前缀：``多头自注意力机制（multi-head self-attention）``
      （后缀命中 self-attention 词条）；
    - 英文为括号内短语的后缀：multi-head 等修饰语 + 词条变体；
    - 英文后附缩写补充：``（recurrent neural network，RNN）``。
    """
    spans: list[tuple[int, int]] = []
    for ti in bilingual_idx:
        term = terms[ti]
        zh = term.zh.strip()
        variants = sorted(
            {v.strip() for v in (term.en, *term.aliases) if v.strip()},
            key=len, reverse=True,
        )
        # 前缀：0–8 个汉字（如"多头""缩放点积"），紧贴译名；zh 前容许空白
        pair_re = re.compile(
            r"(?:" + _HAN + r"{0,8}" + r"\s*" + _zh_loose(zh) + r")"
            r"\s*[（(]\s*(?P<en>[^）)]{0,80}?)\s*[）)]",
        )
        for match in pair_re.finditer(text):
            phrase = match.group("en")
            # 括号内若误填了中文译名本身（如"（ReLU 激活函数）"），
            # 不含任何变体，留待 rewrite 规范化，不判为合法配对
            if not re.search(r"[A-Za-z]", phrase):
                continue
            # 去掉缩写补充后缀（，RNN /, RNN）
            phrase = re.sub(r"[,，]\s*[A-Za-z][^,，]*$", "", phrase).strip()
            if not phrase:
                continue
            for variant in variants:
                sub = re.compile(
                    r"(?<![A-Za-z-])" + re.escape(variant) + r"(?![A-Za-z-])",
                    re.IGNORECASE,
                )
                last = None
                for sub_hit in sub.finditer(phrase):
                    last = sub_hit
                # 变体须位于短语末尾（容许 multi-head 这类前缀修饰）
                if last is not None and last.end() == len(phrase):
                    # 覆盖整个配对区间（含中文译名）：译名自身夹带的
                    # 英文原形（BLEU 分数、WMT 2014 数据集）一并豁免
                    spans.append((match.start(), match.end()))
                    break
    return spans


def _zh_mention_spans(
    text: str, terms: list[Term], bilingual_idx: list[int]
) -> list[tuple[int, int]]:
    """中文译名裸用区间：译名内夹带的英文原形（ReLU 激活函数）豁免。

    FR-007 评论：中文译名裸用合法，检查只追究英文出现却无可识别译名；
    旧实现对 zh 自身含英文的条目（BLEU 分数 / ReLU 激活函数）会在译名
    内部误报，此处按译名宽松形态整体豁免。
    """
    spans: list[tuple[int, int]] = []
    for ti in bilingual_idx:
        zh = terms[ti].zh.strip()
        for match in re.compile(_zh_loose(zh)).finditer(text):
            spans.append((match.start(), match.end()))
    return spans


def _check_target_order(markdown: str) -> dict[str, Any]:
    positions = []
    missing = []
    for heading in SECTION_HEADINGS.values():
        pos = markdown.find(heading)
        if pos < 0:
            missing.append(heading)
        else:
            positions.append(pos)
    ordered = positions == sorted(positions) and len(positions) == len(SLUG_ORDER)
    findings = []
    if missing:
        findings.append("七段固定标题缺失：" + "、".join(missing))
    if positions and not ordered:
        findings.append("七段标题顺序不符合固定动机优先顺序")
    return {"id": "target-order",
            "status": "passed" if not findings else "failed",
            **({"findings": findings} if findings else {})}


def _block_text(block: dict[str, Any]) -> str:
    parts = [str(block.get(name, "")) for name in _TEXT_FIELDS if block.get(name)]
    breakdown = block.get("symbol_breakdown")
    if isinstance(breakdown, list):
        parts.extend(
            str(item.get("meaning", "")) for item in breakdown
            if isinstance(item, dict)
        )
    return " ".join(p for p in parts if p)


def _check_tldr_ratio(sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tldr_len = 0
    total_len = 0
    for slug, _, block in _iter_blocks(sections):
        if block.get("kind") in ("fold_ref", "supplement"):
            continue
        length = _visible_len(_block_text(block))
        total_len += length
        if slug == "tldr" or block.get("kind") == "tldr":
            tldr_len += length
    ratio = (tldr_len / total_len) if total_len else 1.0
    findings = []
    if ratio > TLDR_RATIO_LIMIT:
        findings.append(
            f"30 秒摘要占比 {ratio:.1%} 超过 {TLDR_RATIO_LIMIT:.0%}"
            f"（tldr={tldr_len} / 正文={total_len}）"
        )
    return {"id": "tldr-ratio", "status": "passed" if not findings else "failed",
            "measured": round(ratio, 4),
            **({"findings": findings} if findings else {})}


def _check_mainline_tables(
    sections: dict[str, dict[str, Any]],
    normalized: dict[str, Any],
    plan: dict[str, Any],
    mainline_text: str,
) -> dict[str, Any]:
    findings: list[str] = []
    blocks = {b["id"]: b for b in normalized.get("blocks", [])}
    actions = plan.get("block_actions", {})
    for slug, idx, block in _iter_blocks(sections):
        if block.get("kind") == "fold_ref":
            continue
        for bid in block.get("source_block_ids", []):
            source = blocks.get(bid)
            decision = actions.get(bid) if isinstance(actions, dict) else None
            # 训练曲线/工程图（chart）只准进 repro_data 折叠
            if isinstance(source, dict) and source.get("mineru_type") == "chart":
                findings.append(
                    f"{slug}#blocks[{idx}]：训练曲线/工程图 {bid} 出现在主线"
                    "（只能进 repro_data 折叠区，FR-020）"
                )
            # planner 判 fold 的表不得在主线复现
            if isinstance(decision, dict) and decision.get("action") == "fold":
                findings.append(
                    f"{slug}#blocks[{idx}]：块 {bid} 被 planner 判 fold，"
                    "却出现在主线（主线完整表数量必须为 0，FR-019）"
                )
    # FR-020：具体硬件配置/训练时长只允许收入 repro_data 折叠；
    # mainline_text 已剥离折叠区、公式、代码、标题与 callout。
    for label, pattern in (("硬件配置", _HARDWARE_DETAIL_RE),
                           ("训练时长", _TRAINING_DURATION_RE)):
        for hit in list(pattern.finditer(mainline_text))[:5]:
            findings.append(
                f"主线出现具体{label}：…"
                f"{mainline_text[max(0, hit.start()-12):hit.end()+12].strip()}…"
                "（只能一句话定性带过或收入 repro_data 折叠区，FR-020）"
            )
    return {"id": "mainline-no-full-tables",
            "status": "passed" if not findings else "failed",
            "mainline_full_tables": len(findings),
            **({"findings": findings} if findings else {})}


def _check_experiment_conclusion_ratio(
    sections: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """SC-008：实验章节须以结论级表达为主——结论级陈述块占比 ≥80%。

    分母为实验段主线陈述块：paragraph/formula_card 计为结论级，
    table_explained（保留在主线的完整数据表）计为原始数据；fold_ref、
    supplement、figure 不占分母（数据图由 mainline-no-full-tables 拦截，
    图示不是陈述）。未知 kind 保守计入分母但不算结论级。
    """
    doc = sections.get("experiments")
    conclusion_blocks = 0
    mainline_blocks = 0
    raw_indexes: list[int] = []
    if isinstance(doc, dict):
        for idx, block in enumerate(doc.get("blocks", [])):
            kind = block.get("kind")
            if kind in _RATIO_EXCLUDED_KINDS or kind == "tldr":
                continue
            mainline_blocks += 1
            if kind in _RATIO_CONCLUSION_KINDS:
                conclusion_blocks += 1
            elif kind in _RATIO_RAW_DATA_KINDS:
                raw_indexes.append(idx)
    ratio = (
        conclusion_blocks / mainline_blocks if mainline_blocks else 1.0
    )
    findings: list[str] = []
    if mainline_blocks and ratio < EXPERIMENT_CONCLUSION_RATIO_LIMIT:
        findings.append(
            f"实验章节结论级陈述占比 {ratio:.0%} 低于 "
            f"{EXPERIMENT_CONCLUSION_RATIO_LIMIT:.0%}"
            f"（结论级 {conclusion_blocks} / 主线陈述块 {mainline_blocks}；"
            f"完整数据表块位置 experiments#blocks{raw_indexes}，"
            "完整超参/消融数据应只留一句话结论并收入折叠区，FR-019/SC-008）"
        )
    return {"id": "experiment-conclusion-ratio",
            "status": "passed" if not findings else "failed",
            "measured": round(ratio, 4),
            "conclusion_blocks": conclusion_blocks,
            "mainline_blocks": mainline_blocks,
            **({"findings": findings} if findings else {})}


def _check_fold_linkage(sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    folds: list[tuple[str, int, dict]] = []
    for slug, idx, block in _iter_blocks(sections):
        if block.get("kind") == "fold_ref" \
                and block.get("fold_group") in ("derivation", "repro_data"):
            folds.append((slug, idx, block))
    sample = folds[:FOLD_SAMPLE_SIZE]
    matched = 0
    findings: list[str] = []
    for slug, idx, fold in sample:
        fold_sources = set(fold.get("source_block_ids", []))
        sibling_sources: set[str] = set()
        for other in sections[slug].get("blocks", []):
            if other.get("kind") in ("fold_ref", "supplement"):
                continue
            sibling_sources.update(other.get("source_block_ids", []))
        if fold_sources & sibling_sources:
            matched += 1
        else:
            findings.append(
                f"{slug}#blocks[{idx}]：折叠块与同段结论块无共享 source_block_ids"
                "（结论↔原始数据无法对应）"
            )
    return {"id": "fold-data-linkage",
            "status": "passed" if not findings else "failed",
            "sampled": len(sample), "matched": matched,
            **({"findings": findings} if findings else {})}


def _check_source_trace(
    sections: dict[str, dict[str, Any]],
    normalized: dict[str, Any],
    markdown: str,
) -> dict[str, Any]:
    block_docs = {b["id"]: b for b in normalized.get("blocks", [])}
    valid_ids = set(block_docs)
    # 缺图块在成稿中以固定占位（自带 §/p 定位）替代斜体溯源，豁免本检查
    missing_image_ids = {
        bid for bid, b in block_docs.items()
        if isinstance(b, dict) and b.get("type") == "figure" and b.get("image_missing")
    }
    factual: list[tuple[str, int, dict]] = [
        (slug, idx, block)
        for slug, idx, block in _iter_blocks(sections)
        if block.get("kind") not in ("fold_ref", "supplement")
        and not (
            block.get("kind") == "figure"
            and set(block.get("source_block_ids", [])) <= missing_image_ids
        )
    ]
    sample = factual[:TRACE_SAMPLE_SIZE]
    traced = 0
    findings: list[str] = []
    for slug, idx, block in sample:
        sources = block.get("source_block_ids", [])
        has_valid_sources = bool(sources) and all(
            isinstance(bid, str) and bid in valid_ids for bid in sources
        )
        if has_valid_sources:
            traced += 1
        else:
            findings.append(
                f"{slug}#blocks[{idx}]：缺少可溯源的 source_block_ids"
                f"（实际 {sources!r}）"
            )
    # 成稿中的斜体溯源标记数量必须覆盖抽样块
    trace_count = len(_TRACE_RE.findall(markdown))
    if trace_count < traced:
        findings.append(
            f"成稿斜体溯源标记 {trace_count} 处，少于可溯源抽样块 {traced} 处"
        )
    return {"id": "source-trace",
            "status": "passed" if not findings else "failed",
            "sampled": len(sample), "traced": traced,
            "trace_markers": trace_count,
            **({"findings": findings} if findings else {})}


def _extract_callouts(markdown: str) -> list[str]:
    callouts: list[str] = []
    current: list[str] | None = None
    for line in markdown.splitlines():
        if line.startswith("> 📖") or line.startswith("> 💡"):
            current = [line]
            continue
        if current is not None:
            if line.startswith(">"):
                current.append(line)
            else:
                callouts.append("\n".join(current))
                current = None
    if current is not None:
        callouts.append("\n".join(current))
    return callouts


def _check_supplement(
    sections: dict[str, dict[str, Any]], markdown: str
) -> dict[str, Any]:
    findings: list[str] = []
    supplement_blocks = [
        (slug, idx, block)
        for slug, idx, block in _iter_blocks(sections)
        if block.get("kind") == "supplement"
    ]
    callouts = _extract_callouts(markdown)
    expected_headers = {"background": "补充知识（非论文原文）",
                        "analogy": "打个比方（非论文原文）"}
    for slug, idx, block in supplement_blocks:
        kind = block.get("supplement_kind")
        header = expected_headers.get(kind)
        if header is None or not any(header in c for c in callouts):
            findings.append(
                f"{slug}#blocks[{idx}]：supplement({kind}) 未以规定 callout 呈现"
            )
        if block.get("source_block_ids"):
            findings.append(
                f"{slug}#blocks[{idx}]：supplement 块不得携带 source_block_ids"
            )
    for callout in callouts:
        if "原文 §" in callout or "原文§" in callout:
            findings.append("callout 内出现伪溯源标记（补充内容原文中不存在）")
    return {"id": "supplement-marking",
            "status": "passed" if not findings else "failed",
            "callout_count": len(callouts),
            **({"findings": findings} if findings else {})}


def _check_missing_placeholder(
    normalized: dict[str, Any], plan: dict[str, Any], markdown: str
) -> dict[str, Any]:
    findings: list[str] = []
    missing_figures = [
        b for b in normalized.get("blocks", [])
        if isinstance(b, dict) and b.get("type") == "figure" and b.get("image_missing")
    ]
    for block in missing_figures:
        expected = block.get("expected_img_path", block.get("id"))
        if _PLACEHOLDER_CORE not in markdown:
            findings.append(
                f"缺失图片 {expected} 处未找到固定占位文案"
                "「原文该位置…解析缺失，未编造内容补全」"
            )

    # FR-026（T037）：进主线（narrate/condense）的 parse_gap 块由组装层
    # 确定性注入固定占位 blockquote；逐个核对成稿中的占位行数
    actions = plan.get("block_actions", {})
    mainline_gaps = [
        b for b in normalized.get("blocks", [])
        if isinstance(b, dict)
        and b.get("type") == "parse_gap"
        and isinstance(actions.get(b.get("id")), dict)
        and actions[b["id"]].get("action") in ("narrate", "condense")
    ]
    placeholder_lines = [
        line for line in markdown.splitlines()
        if line.lstrip().startswith(">") and _PLACEHOLDER_CORE in line
    ]
    if len(placeholder_lines) < len(mainline_gaps):
        findings.append(
            f"主线 parse_gap 解析占位块 {len(mainline_gaps)} 个，成稿固定缺失"
            f"说明仅 {len(placeholder_lines)} 处（要求一一对应，FR-026）"
        )
    for block in mainline_gaps:
        if str(block.get("placeholder", "")).strip() not in markdown:
            findings.append(
                f"parse_gap {block.get('id')} 的固定占位文案"
                f"「{block.get('placeholder')}」未进入成稿（FR-026）"
            )
    return {"id": "missing-placeholder",
            "status": "passed" if not findings else "failed",
            "missing_assets": len(missing_figures),
            "parse_gaps": len(mainline_gaps),
            "placeholder_lines": len(placeholder_lines),
            **({"findings": findings} if findings else {})}


def _check_image_links(run_dir: Path, markdown: str) -> dict[str, Any]:
    dist = run_dir / DIST_DIRNAME
    dead: list[str] = []
    for target in _IMAGE_LINK_RE.findall(markdown):
        target = target.strip().split()[0]
        if not (dist / target).is_file():
            dead.append(target)
    return {"id": "image-links", "status": "passed" if not dead else "failed",
            "dead_links": len(dead),
            **({"findings": [f"死链：{t}" for t in dead]} if dead else {})}


def _load_worked_example(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / WORKED_EXAMPLE_FILENAME
    return _read_json(path) if path.is_file() else None


def _check_worked_example(
    sections: dict[str, dict[str, Any]], example: dict[str, Any] | None
) -> dict[str, Any]:
    findings: list[str] = []
    status = None
    if not isinstance(example, dict):
        findings.append(f"缺少 {WORKED_EXAMPLE_FILENAME}")
    else:
        verification = example.get("verification")
        status = verification.get("status") if isinstance(verification, dict) else None
        if status != "passed":
            findings.append(
                f"verification.status={status!r}（必须 passed 才允许组装，FR-017）"
            )
        if example.get("id") != WORKED_EXAMPLE_ID:
            findings.append(f"worked_example.id 必须为 {WORKED_EXAMPLE_ID}")
    has_ref = any(
        block.get("worked_example_ref") == WORKED_EXAMPLE_ID
        for _, _, block in _iter_blocks(sections)
    )
    if not has_ref:
        findings.append("主线 formula_card 未引用手算示例 we-001（SC-007）")
    return {"id": "worked-example",
            "status": "passed" if not findings else "failed",
            "verification_status": status,
            **({"findings": findings} if findings else {})}


def _check_toy_disclaimer(
    example: dict[str, Any] | None, markdown: str
) -> dict[str, Any]:
    findings: list[str] = []
    if not isinstance(example, dict):
        findings.append("缺少 worked_example.json")
    else:
        if example.get("is_toy_data") is not True:
            findings.append("is_toy_data 必须为 true")
        if example.get("disclaimer") != TOY_DISCLAIMER:
            findings.append(
                f"disclaimer 必须逐字为 {TOY_DISCLAIMER!r}"
                f"（实际 {example.get('disclaimer')!r}）"
            )
    if TOY_DISCLAIMER not in markdown:
        findings.append("成稿未包含教学示意/非论文真实数据声明")
    return {"id": "toy-data-disclaimer",
            "status": "passed" if not findings else "failed",
            **({"findings": findings} if findings else {})}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_qa_report(run_dir: Path | str) -> dict[str, Any]:
    """扫描 run_dir 全部产物，构造 qa_report dict（不落盘）。"""
    run_dir = Path(run_dir)
    normalized = load_normalized(run_dir)
    plan = load_plan(run_dir)
    sections = _load_sections(run_dir)
    example = _load_worked_example(run_dir)
    markdown_path = _find_markdown(run_dir, normalized)
    markdown = markdown_path.read_text(encoding="utf-8")
    mainline_text = _strip_for_scan(markdown, keep_folds=False)

    checks = [
        _check_glossary_format(run_dir),
        _check_glossary_consistency(run_dir, mainline_text),
        _check_target_order(markdown),
        _check_tldr_ratio(sections),
        _check_mainline_tables(sections, normalized, plan, mainline_text),
        _check_experiment_conclusion_ratio(sections),
        _check_fold_linkage(sections),
        _check_source_trace(sections, normalized, markdown),
        _check_supplement(sections, markdown),
        _check_missing_placeholder(normalized, plan, markdown),
        _check_image_links(run_dir, markdown),
        _check_worked_example(sections, example),
        _check_toy_disclaimer(example, markdown),
    ]
    overall = "passed" if all(c["status"] == "passed" for c in checks) else "failed"
    return {"overall": overall, "markdown_path": str(markdown_path), "checks": checks}


def run_qa_report(run_dir: Path | str) -> dict[str, Any]:
    """扫描并写 qa_report.json；overall=failed 时抛 :class:`QAError`（退出码 2）。

    报告无论 passed/failed 都落盘（失败时 CLI 再改 .unchecked.md）。
    """
    run_dir = Path(run_dir)
    report = build_qa_report(run_dir)
    out_path = run_dir / QA_REPORT_FILENAME
    try:
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        raise InputError(f"qa_report.json 写入失败：{out_path}（{exc}）") from exc

    failed = [c["id"] for c in report["checks"] if c["status"] == "failed"]
    if failed:
        detail_lines = []
        for check in report["checks"]:
            if check["status"] != "failed":
                continue
            for finding in check.get("findings", [])[:3]:
                detail_lines.append(f"- [{check['id']}] {finding}")
        raise QAFailedError(
            f"QA hard checks 未通过（{len(failed)}/{len(CHECK_IDS)} 项："
            f"{', '.join(failed)}）：\n"
            + "\n".join(detail_lines)
        )
    logger.info("QA 全部通过：%s", out_path)
    return report


# CLI 入口别名（命名与 tasks.md 对齐）
run_qa = run_qa_report
