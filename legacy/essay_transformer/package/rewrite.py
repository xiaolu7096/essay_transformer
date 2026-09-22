"""分章耦合转写（T023，data-model.md §5/§7，FR-007/017/024，research R5）。

planner 的 plan.json 是唯一决策源；本模块按 ``target_order`` 为固定七段
**各发起一次** ``section_rewrite`` LLM 调用，在同一次生成中完成跨语言转换
与通俗化讲解——直接产出中文教程块，严禁独立译文中间产物
（``sections/<slug>.json`` 只保存中文结果 JSON）。

- 线程池有限并发：``max_workers = min(jobs, 4)``（research R6，宪法硬上限）；
- 每段输入：英文原块 + 该批块的 block_actions + 全量确认术语逐条枚举 +
  全局规划摘要（占位符与 prompts/rewrite_section.md 对齐）；
- 两道校验：JSON Schema（:mod:`essay_transformer.llm_schema`）+
  :func:`validate_section_response` 确定性业务规则（溯源 id 必须真实、
  tldr 块边界、公式卡四件套、图/表/折叠必备字段、references 折叠必须
  引用 reference_list 原文块、公式 latex 与原文一致、
  手算引用只许指向 planner 指定的 we-001）；
- schema/业务不过在该段内重试 1 次（共 2 次尝试），再失败抛
  :class:`~essay_transformer.errors.LLMError`，不落任何 section 文件；
- 表外术语随段收集，:func:`merge_out_of_glossary` 按小写 term 去重、
  locations 聚合（assemble 阶段落 out_of_glossary.json）。
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import config
from .errors import LLMError
from .glossary import Term, load_confirmed_glossary
from .llm_schema import validate_llm_output
from .planner import (
    SYNTHETIC_SECTION_RE,
    load_normalized,
    load_plan,
)

__all__ = [
    "SECTIONS_DIRNAME",
    "WORKED_EXAMPLE_ID",
    "BLOCK_KINDS",
    "clamp_workers",
    "validate_section_response",
    "merge_out_of_glossary",
    "run_rewrite",
    "section_path",
]

logger = logging.getLogger(__name__)

SECTIONS_DIRNAME = "sections"
WORKED_EXAMPLE_ID = "we-001"

BLOCK_KINDS: tuple[str, ...] = (
    "paragraph",
    "formula_card",
    "figure",
    "table_explained",
    "fold_ref",
    "tldr",
    "supplement",
)

# 与 prompts/rewrite_section.md 中可被测试稳定抓取的标记保持一致
_SLUG_MARKER_RE = re.compile(r"- `(?P<slug>[a-z_]+)`：本段目标段")

_TREATMENT_ZH = {
    "brief": "30 秒简述",
    "expand": "展开动机与前置背景",
    "front_load_extract": "结论前置抽取",
    "compress": "只讲理解必需的部分",
    "tutorial": "教程式详解",
    "conclusion_only": "只讲结论级表达",
    "summary": "总评收束",
}


# ---------------------------------------------------------------------------
# 纯函数：素材装配 / 校验 / 聚合
# ---------------------------------------------------------------------------


def clamp_workers(jobs: int) -> int:
    """并发硬上限 ≤4，下限 1（FR/research R6）。"""
    if not isinstance(jobs, int) or isinstance(jobs, bool):
        return 1
    return max(1, min(jobs, 4))


def section_path(run_dir: Path | str, slug: str) -> Path:
    return Path(run_dir) / SECTIONS_DIRNAME / f"{slug}.json"


def resolve_real_section_ids(
    source_section_ids: list[str], sections_by_id: dict[str, dict]
) -> list[str]:
    """把 FR-014 合成 id（<id>-tail/-extract）还原为真实节 id，去重保序。"""
    real: list[str] = []
    for sid in source_section_ids:
        match = SYNTHETIC_SECTION_RE.match(sid)
        base = match.group("base") if match else sid
        if base in sections_by_id and base not in real:
            real.append(base)
    return real


def _allowed_block_ids(
    real_section_ids: list[str],
    sections_in_order: list[dict],
) -> list[str]:
    """按 normalized 中节/块的原始文档顺序展开本批块 id（去重）。"""
    wanted = set(real_section_ids)
    ordered: list[str] = []
    for section in sections_in_order:
        if section["id"] not in wanted:
            continue
        for bid in section.get("blocks", []):
            if bid not in ordered:
                ordered.append(bid)
    return ordered


def render_glossary_terms(terms: list[Term]) -> str:
    """全量术语逐条枚举（FR-007；与 prompt §2 的硬性规则配套）。"""
    lines = []
    for term in terms:
        alias_part = (
            f"；aliases（等价匹配，大小写不敏感）：{', '.join(term.aliases)}"
            if term.aliases
            else ""
        )
        note_part = f"；备注：{term.note}" if term.note.strip() else ""
        lines.append(
            f"- en={term.en}；zh={term.zh}；mode={term.mode}"
            f"{alias_part}{note_part}"
        )
    return "\n".join(lines)


def render_plan_summary(target_order: list[dict], current_slug: str) -> str:
    lines = ["固定七段顺序（动机优先）："]
    for idx, entry in enumerate(target_order, start=1):
        slug = entry["target_slug"]
        treatment = _TREATMENT_ZH.get(entry.get("treatment"), entry.get("treatment", ""))
        sources = ", ".join(entry.get("source_section_ids", []))
        marker = "  ← 本段" if slug == current_slug else ""
        lines.append(f"{idx}. {slug}（{treatment}；素材来源：{sources}）{marker}")
    lines.append(f"本段角色：当前生成第 {current_slug!r} 段，只输出该段。")
    return "\n".join(lines)


def build_section_payload(
    normalized: dict[str, Any],
    plan: dict[str, Any],
    entry: dict[str, Any],
    terms: list[Term],
) -> dict[str, Any]:
    """组装单段 prompt 所需的全部结构化素材（供渲染与测试断言）。"""
    slug = entry["target_slug"]
    sections_in_order = normalized.get("sections", [])
    sections_by_id = {s["id"]: s for s in sections_in_order}
    blocks_by_id = {b["id"]: b for b in normalized.get("blocks", [])}

    real_ids = resolve_real_section_ids(
        entry.get("source_section_ids", []), sections_by_id
    )
    allowed_ids = _allowed_block_ids(real_ids, sections_in_order)

    section_blocks: list[dict[str, Any]] = []
    for sid in real_ids:
        section = sections_by_id[sid]
        section_blocks.append(
            {
                "id": sid,
                "heading": section.get("heading", ""),
                "level": section.get("level"),
                "kind": section.get("kind"),
                "page_start": section.get("page_start"),
                "blocks": [blocks_by_id[bid] for bid in section.get("blocks", [])
                           if bid in blocks_by_id],
            }
        )

    block_actions = {
        bid: plan.get("block_actions", {}).get(bid)
        for bid in allowed_ids
        if bid in plan.get("block_actions", {})
    }

    return {
        "target_slug": slug,
        "real_section_ids": real_ids,
        "allowed_block_ids": allowed_ids,
        "section_blocks": section_blocks,
        "block_actions": block_actions,
        "plan_summary": render_plan_summary(plan["target_order"], slug),
        "glossary_terms": render_glossary_terms(terms),
    }


def render_user_prompt(template: str, payload: dict[str, Any]) -> str:
    return (
        template.replace("{{TARGET_SLUG}}", payload["target_slug"])
        .replace(
            "{{SECTION_BLOCKS}}",
            json.dumps(payload["section_blocks"], ensure_ascii=False, indent=2),
        )
        .replace(
            "{{BLOCK_ACTIONS}}",
            json.dumps(payload["block_actions"], ensure_ascii=False, indent=2),
        )
        .replace("{{PLAN_SUMMARY}}", payload["plan_summary"])
        .replace("{{GLOSSARY_TERMS}}", payload["glossary_terms"])
    )


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_section_response(
    response: dict[str, Any],
    payload: dict[str, Any],
    *,
    worked_example_block_id: str | None = None,
) -> list[str]:
    """单段转写响应的确定性业务校验（空列表=通过）。

    结构合法性由 JSON Schema 兜底；这里只查论文结构规则：
    target_slug 一致、溯源块真实存在、tldr 边界、各块形态必备字段、
    公式 latex 与原文一致、手算引用唯一指向 planner 指定公式。
    """
    problems: list[str] = []
    slug = payload["target_slug"]
    allowed = set(payload["allowed_block_ids"])
    section_blocks = payload["section_blocks"]

    allowed_pages = {
        b.get("page_idx")
        for section in section_blocks
        for b in section.get("blocks", [])
        if isinstance(b.get("page_idx"), int)
    }
    equations = {
        b["id"]: str(b.get("latex", "")).strip()
        for section in section_blocks
        for b in section.get("blocks", [])
        if b.get("type") == "equation"
    }
    figures = {
        # 缺图块 img_path=None：转写沿用 expected_img_path，
        # assemble 阶段按文件是否存在渲染为固定缺失占位
        b["id"]: b.get("img_path") or b.get("expected_img_path")
        for section in section_blocks
        for b in section.get("blocks", [])
        if b.get("type") == "figure"
    }
    reference_list_ids = {
        b["id"]
        for section in section_blocks
        for b in section.get("blocks", [])
        if b.get("type") == "reference_list"
    }
    # FR-026（T037）：parse_gap 占位块没有可转写内容，LLM 不得引用/转述；
    # 成稿缺失说明由 assemble 按 planner 决策确定性注入
    parse_gap_ids = {
        b["id"]
        for section in section_blocks
        for b in section.get("blocks", [])
        if b.get("type") == "parse_gap"
    }

    if response.get("target_slug") != slug:
        problems.append(
            f"target_slug：响应为 {response.get('target_slug')!r}，请求段为 {slug!r}"
        )

    blocks = response.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        # 空 blocks 已被 schema 拦截；这里防御即可
        problems.append("blocks：必须为非空数组")
        return problems

    for idx, block in enumerate(blocks):
        if not isinstance(block, dict):
            problems.append(f"blocks[{idx}]：必须是对象")
            continue
        where = f"blocks[{idx}]"
        kind = block.get("kind")
        if kind not in BLOCK_KINDS:
            problems.append(f"{where}：非法 kind={kind!r}")
            continue

        sources = block.get("source_block_ids", [])
        if not isinstance(sources, list):
            problems.append(f"{where}：source_block_ids 必须是数组")
            sources = []

        if kind == "supplement":
            if sources:
                problems.append(
                    f"{where}：supplement 块 source_block_ids 必须为空数组"
                    "（补充内容无原文溯源，宪法原则 III）"
                )
        else:
            if not sources:
                problems.append(f"{where}：{kind} 块 source_block_ids 必填非空（FR-024）")
            unknown = [bid for bid in sources if bid not in allowed]
            if unknown:
                problems.append(
                    f"{where}：source_block_ids 含不属于本段素材的块 {unknown}"
                    "（事实只能来自输入块，宪法原则 III）"
                )
            gaps = [bid for bid in sources if bid in parse_gap_ids]
            if gaps:
                problems.append(
                    f"{where}：source_block_ids 不得引用 parse_gap 解析占位块 "
                    f"{gaps}（该位置由组装层渲染固定缺失说明，不得转写或编造，"
                    "FR-026）"
                )

        pages = block.get("source_pages")
        if pages is not None:
            if not isinstance(pages, list) or any(
                not isinstance(p, int) or isinstance(p, bool) for p in pages
            ):
                problems.append(f"{where}：source_pages 必须是整数数组")
            else:
                bad_pages = [p for p in pages if p not in allowed_pages]
                if bad_pages:
                    problems.append(
                        f"{where}：source_pages 超出原文页范围：{bad_pages}"
                    )

        if kind == "tldr" and slug != "tldr":
            problems.append(f"{where}：tldr 块只允许出现在 tldr 段（当前 {slug}）")

        # 各块形态的必备字段
        if kind == "paragraph":
            if not _is_nonempty_str(block.get("markdown")):
                problems.append(f"{where}：paragraph 块必须给非空 markdown")
        elif kind == "tldr":
            if not _is_nonempty_str(block.get("markdown")):
                problems.append(f"{where}：tldr 块必须给非空 markdown")
        elif kind == "formula_card":
            latex = block.get("latex")
            breakdown = block.get("symbol_breakdown")
            intuition = block.get("intuition")
            if not _is_nonempty_str(latex):
                problems.append(f"{where}：formula_card 必须给非空 latex（FR-017）")
            elif not any(
                str(latex).strip() == src_latex for src_latex in equations.values()
            ):
                problems.append(
                    f"{where}：latex 与本段任一原文公式块不一致"
                    "（公式必须逐字符保持原文，FR-003）"
                )
            if not isinstance(breakdown, list) or not breakdown or not all(
                isinstance(item, dict)
                and _is_nonempty_str(item.get("symbol"))
                and _is_nonempty_str(item.get("meaning"))
                for item in breakdown
            ):
                problems.append(
                    f"{where}：formula_card 必须给非空 symbol_breakdown"
                    "（逐项 symbol/meaning，FR-017）"
                )
            if not _is_nonempty_str(intuition):
                problems.append(f"{where}：formula_card 必须给非空 intuition（FR-017）")
            ref = block.get("worked_example_ref")
            if ref is not None and ref != "":
                if ref != WORKED_EXAMPLE_ID:
                    problems.append(
                        f"{where}：worked_example_ref 只允许为 {WORKED_EXAMPLE_ID}"
                        f"（实际 {ref!r}）"
                    )
                elif worked_example_block_id is not None and not any(
                    bid == worked_example_block_id for bid in sources
                ):
                    problems.append(
                        f"{where}：引用手算示例但 source_block_ids 未指向 planner"
                        f" 指定的关键公式块 {worked_example_block_id}"
                    )
        elif kind == "figure":
            img_path = block.get("img_path")
            if not _is_nonempty_str(img_path):
                problems.append(f"{where}：figure 块必须给非空 img_path")
            else:
                matched = [
                    bid for bid, path in figures.items()
                    if bid in sources and path == img_path
                ]
                if not matched:
                    problems.append(
                        f"{where}：img_path 必须沿用本段某个原图块的相对路径"
                        f"（实际 {img_path!r}）"
                    )
            if not _is_nonempty_str(block.get("rewritten_caption")):
                problems.append(f"{where}：figure 块必须给非空 rewritten_caption")
        elif kind == "table_explained":
            if not _is_nonempty_str(block.get("markdown_table")):
                problems.append(f"{where}：table_explained 必须给非空 markdown_table")
            if not _is_nonempty_str(block.get("explanation")):
                problems.append(f"{where}：table_explained 必须给非空 explanation")
        elif kind == "fold_ref":
            group = block.get("fold_group")
            if group not in ("derivation", "repro_data", "references"):
                problems.append(
                    f"{where}：fold_ref 必须给 fold_group"
                    "（derivation/repro_data/references）"
                )
            elif group == "references" and not any(
                bid in reference_list_ids for bid in sources
            ):
                problems.append(
                    f"{where}：references 折叠的 source_block_ids 必须包含本段"
                    " reference_list 原文块（参考文献以解析原文为准、完整无缺失，"
                    "FR-022/023）"
                )
            if not _is_nonempty_str(block.get("title")):
                problems.append(f"{where}：fold_ref 必须给非空 title")
            if not _is_nonempty_str(block.get("content_markdown")):
                problems.append(
                    f"{where}：fold_ref 必须给非空 content_markdown（折叠区完整内容）"
                )
        elif kind == "supplement":
            if not _is_nonempty_str(block.get("markdown")):
                problems.append(f"{where}：supplement 块必须给非空 markdown")
            if block.get("supplement_kind") not in ("background", "analogy"):
                problems.append(
                    f"{where}：supplement 块必须给 supplement_kind"
                    "（background/analogy）"
                )

    oog = response.get("out_of_glossary")
    if not isinstance(oog, list):
        problems.append("out_of_glossary：必须是数组（无表外词时给空数组）")
    else:
        for j, item in enumerate(oog):
            if not isinstance(item, dict):
                problems.append(f"out_of_glossary[{j}]：必须是对象")
                continue
            if not _is_nonempty_str(item.get("term")):
                problems.append(f"out_of_glossary[{j}]：term 必填非空")
            if not isinstance(item.get("context"), str):
                problems.append(f"out_of_glossary[{j}]：context 必须是字符串")
            page_idx = item.get("page_idx")
            if not isinstance(page_idx, int) or isinstance(page_idx, bool) \
                    or page_idx not in allowed_pages:
                problems.append(
                    f"out_of_glossary[{j}]：page_idx 必须是本段原文页内的整数"
                )

    return problems


def merge_out_of_glossary(
    collected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """跨段聚合表外词（data-model §7）：term 小写去重，locations 按出现顺序合并。

    输入项形如 ``{term, context, page_idx, temporary_handling, section}``；
    输出保持首次出现顺序，term 保留首次原写法。
    """
    merged: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for item in collected:
        term = str(item.get("term", ""))
        key = term.strip().casefold()
        if not key:
            continue
        location = {
            "section": item.get("section", ""),
            "page_idx": item.get("page_idx"),
            "context": item.get("context", ""),
        }
        handling = item.get("temporary_handling")
        if isinstance(handling, str) and handling.strip():
            location["temporary_handling"] = handling
        if key not in index:
            record = {"term": term.strip(), "locations": [location]}
            index[key] = record
            merged.append(record)
        else:
            index[key]["locations"].append(location)
    return merged


# ---------------------------------------------------------------------------
# 确定性后处理（T040）：LLM 违反 plan/契约但可机械推导的修复
#
# 原则（沿用 worked_example 第五轮教训）：精确算术/机械字段绝不交 LLM；
# 对 plan fold 边界、FR-019/FR-020、术语格式这类确定性契约，LLM 偶发违规
# 由这里在落盘前统一修复并 logger 留痕，prompt 同步收紧降低违规率。
# ---------------------------------------------------------------------------

# 参与文本修复的块字段；fold_ref 的 content_markdown 是原文转写（位于折叠
# 区，QA 扫描整体剥除），绝不在改写范围内
_PP_TEXT_FIELDS = (
    "markdown", "explanation", "intuition",
    "rewritten_caption", "markdown_table", "title",
)

# 与 qa._HARDWARE_DETAIL_RE / _TRAINING_DURATION_RE 对齐（qa 反向依赖
# assemble→rewrite，不能 import qa，这里保持副本同步）
_PP_HARDWARE_RE = re.compile(
    r"(?:\d+\s*(?:张|块|台|颗|路|核|×|x|X|\*)?\s*(?:NVIDIA\s*)?"
    r"(?:GPU|TPU|V-?100|P100|A100|H100|显卡)"
    r"|(?:GPU|TPU|V-?100|P100|A100|H100)\s*(?:×|x|X|\*)\s*\d+"
    r"|(?:NVIDIA|英伟达)\s*(?:GPU|TPU|V-?100|P100|A100|H100))",
    re.IGNORECASE,
)
_PP_DURATION_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:GPU|TPU)[-\s]?(?:小时|hours?|hrs?)"
    r"|(?:训练耗时?|总耗时|耗时|用时|花费)[^。\n；;]{0,24}?"
    r"\d+(?:\.\d+)?\s*(?:个?小时|天|日|周|分钟|hours?|hrs?|days?|weeks?|minutes?))",
    re.IGNORECASE,
)
# QA 正则只抓阿拉伯数字；后处理从严，另抓中文数字硬件/时长（八块卡/十二小时）
_PP_CN_HW_RE = re.compile(
    r"(?:[一二两三四五六七八九十百]+\s*(?:块|张)?\s*(?:GPU|显卡|卡)"
    r"|[一二两三四五六七八九十百]+\s*个?\s*(?:小时|天|日|周|分钟))"
)
_PP_CLAUSE_DELIM_RE = re.compile(r"(——|[，。；;、：！？!?])")
_PP_CONJ_PREFIX_RE = re.compile(r"^(?:而|但|且|则|可是|不过)+")

# 行内数学/代码：术语规则豁免区间（与 qa._strip_for_scan 口径一致）
_PP_PROTECTED_RE = re.compile(r"\$[^$\n]+\$|`[^`\n]+`")
_PP_EMPH = r"(?:\*{1,2}|_{1,2}|`)"
_PP_HAN = r"[一-鿿]"


def _pp_zh_core(zh: str) -> str:
    """译名核心形态：各字之间容许 markdown 强调标记（不含两端）。"""
    return (_PP_EMPH + "*").join(re.escape(ch) for ch in zh)


def _pp_zh_loose(zh: str) -> str:
    return _PP_EMPH + "*" + _pp_zh_core(zh) + _PP_EMPH + "*"


def _strip_hardware_clauses(text: str) -> tuple[str, int]:
    """剥离含具体硬件配置/训练时长的最小分句（FR-020）。

    按中文标点切句；命中硬件/时长正则的分句整体删除。列表项分句保留
    ``- 标签：`` 前缀；删除后清理悬空标点与连词。返回（新文本, 删除分句数）。
    """
    if not (_PP_HARDWARE_RE.search(text) or _PP_DURATION_RE.search(text)
            or _PP_CN_HW_RE.search(text)):
        return text, 0

    fixed = 0
    out_lines: list[str] = []
    for line in text.split("\n"):
        if not (_PP_HARDWARE_RE.search(line) or _PP_DURATION_RE.search(line)
                or _PP_CN_HW_RE.search(line)):
            out_lines.append(line)
            continue
        # 交错切分：[正文, 分隔符, 正文, 分隔符, ...]
        parts = _PP_CLAUSE_DELIM_RE.split(line)
        dropped_prev = False
        kept: list[str] = []
        for i in range(0, len(parts), 2):
            content = parts[i]
            delim = parts[i + 1] if i + 1 < len(parts) else ""
            hit = bool(
                _PP_HARDWARE_RE.search(content)
                or _PP_DURATION_RE.search(content)
                or _PP_CN_HW_RE.search(content)
            )
            if hit:
                fixed += 1
                # 列表项（或带冒号标签句）：保留 "- 英→法任务：" 这类前缀
                colon = re.search(r"^(\s*(?:[-*]\s+)?[^：:]*[：:])", content)
                if colon and not kept:
                    kept.append(colon.group(1))
                dropped_prev = True
                continue
            if dropped_prev and kept:
                content = _PP_CONJ_PREFIX_RE.sub("", content)
            kept.append(content + delim)
            dropped_prev = False
        newline = "".join(kept)
        # 悬空标点清理
        newline = re.sub(r"：\s*(?=[，。；！？、]|$)", "。", newline)
        newline = re.sub(r"[，、；]+(?=[。！？])", "", newline)
        newline = re.sub(r"([，。；！？、])\1+", r"\1", newline)
        newline = re.sub(r"^[，、；]\s*", "", newline)
        out_lines.append(newline)
    return "\n".join(out_lines), fixed


def _pp_protected_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _PP_PROTECTED_RE.finditer(text)]


def _pp_in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in spans)


def _pp_variant_tables(
    terms: list[Term],
) -> tuple[list[tuple[str, int]], dict[int, list[str]]]:
    """返回（按长度降序的全局变体表, 每术语变体列表），剔除 zh/en 同形。

    表中第二项为词表下标（Term 未冻结，不可作 dict 键）。
    """
    table: list[tuple[str, int]] = []
    per_term: dict[int, list[str]] = {}
    for idx, term in enumerate(terms):
        zh = term.zh.strip()
        variants = sorted(
            {v.strip() for v in (term.en, *term.aliases) if v.strip()
             and v.strip().casefold() != zh.casefold()},
            key=len, reverse=True,
        )
        if not variants:
            continue
        per_term[idx] = variants
        table.extend((v, idx) for v in variants)
    table.sort(key=lambda item: len(item[0]), reverse=True)
    return table, per_term


def _pp_end_variant(
    phrase: str, table: list[tuple[str, int]]
) -> tuple[str, int] | None:
    """短语末尾以连字符边界命中的最长变体（容许前缀修饰语）。"""
    for variant, ti in table:
        pat = re.compile(
            r"(?<![A-Za-z-])" + re.escape(variant) + r"$",
            re.IGNORECASE,
        )
        if pat.search(phrase):
            return variant, ti
    return None


def _pp_any_variant(
    phrase: str, variants: list[str]
) -> re.Match[str] | None:
    for variant in sorted(variants, key=len, reverse=True):
        pat = re.compile(
            r"(?<![A-Za-z-])" + re.escape(variant) + r"(?![A-Za-z-])",
            re.IGNORECASE,
        )
        match = pat.search(phrase)
        if match:
            return match
    return None


def _pp_back_run(text: str, pos: int) -> tuple[int, int, str, str] | None:
    """pos 之前紧邻的译名串（汉字/字母/数字/空白/强调标记）。

    返回（区间起, pos, 去痕核心（去空白/强调）, 原始串）。译名可能含 ASCII
    （BLEU 分数、WMT 2014 数据集、ReLU 激活函数），故回溯字符类不能只限
    汉字；核心必须至少含一个汉字才算中文译名语境。
    """
    tail = text[:pos]
    match = re.search(
        r"([一-鿿A-Za-z0-9][一-鿿A-Za-z0-9 .*_`]*)$", tail
    )
    if not match:
        return None
    raw = match.group(1)
    core = re.sub(r"[\s*_`]", "", raw)
    if not re.search(_PP_HAN, core):
        return None
    return match.start(), pos, core, raw


def _pp_align_zh(raw_run: str, zh: str) -> str | None:
    """zh 是 raw_run 核心的后缀时，保留 zh 之前的原始前缀（不吞正文）。"""
    hit = re.search(_pp_zh_loose(zh.strip()) + r"\s*$", raw_run)
    if not hit:
        return None
    return raw_run[:hit.start()] + zh.strip()


def _pp_legal_pair_start(text: str, pos: int, zh: str) -> int | None:
    """括号位于 pos 时，按 QA 口径回查合法译名配对的起点。

    与 qa._bilingual_pair_spans 对齐：括号英文短语末尾命中变体，且括号前
    紧邻「0–8 个汉字前缀 + 词表译名」（容许强调标记与空白）；ASCII 正文
    （41.8 BLEU）不算前缀，否则会遮蔽真正的裸用。
    """
    match = re.search(
        r"(?:" + _PP_HAN + r"{0,8}\s*" + _pp_zh_loose(zh.strip()) + r")\s*$",
        text[:pos],
    )
    return match.start() if match else None


def _normalize_terms(
    text: str, terms: list[Term]
) -> tuple[str, int]:
    """术语格式确定性规范化（FR-007）。返回（新文本, 修复点数）。

    处理真实成稿取证的全部可机械修复形态：
    - zh_only 词括注英文（开发集（development set））→ 删括注；
    - 配对中文是词表译名子串（注意力（attention））→ 归一为词表全称；
    - 配对译名不匹配（加性注意力（additive attention））→ 删英文括注；
    - 括号内误填中文译名（ReLU 激活函数（ReLU 激活函数））→ 换 en；
    - 数字后裸用（41.8 BLEU）→ 补「个 中文译名（English）」；
    - 其余裸用英文 → 补「中文译名（English）」。
    """
    table, per_term = _pp_variant_tables(terms)
    if not table:
        return text, 0
    bilingual = [ti for ti in per_term if terms[ti].mode == "bilingual"]
    fixed = 0

    # ---- Pass A：扫描所有括号配对并规范化 ----
    protected = _pp_protected_spans(text)
    edits: list[tuple[int, int, str]] = []
    for match in re.finditer(r"[（(](?P<inner>[^（）()\n]{0,80})[）)]", text):
        if _pp_in_spans(match.start(), protected):
            continue
        inner = match.group("inner")
        if not re.search(r"[A-Za-z]", inner):
            continue
        phrase = re.sub(r"[,，]\s*[A-Za-z][^,，]*$", "", inner).strip()
        end_hit = _pp_end_variant(phrase, table)
        if end_hit is not None:
            _variant, ti = end_hit
            term = terms[ti]
            if term.mode != "bilingual":
                # zh_only：译名后不得括注英文，整组删除
                start = match.start()
                while start > 0 and text[start - 1] in " \t":
                    start -= 1
                edits.append((start, match.end(), ""))
                fixed += 1
                continue
            back = _pp_back_run(text, match.start())
            zh = term.zh.strip()
            zh_core = re.sub(r"[\s*_`]", "", zh)
            if back is not None:
                bs, _be, core, raw = back
                if core == zh_core:
                    continue  # 合法配对
                if zh_core in core:
                    # 译名前带正文前缀（含集成模型 / 41.8 BLEU）：保留前缀
                    aligned = _pp_align_zh(raw, zh)
                    edits.append((bs, match.start(), aligned or zh))
                    fixed += 1
                    continue
                if core in zh_core:
                    # 译名是词表全称的简写（注意力→注意力机制）：归一全称
                    edits.append((bs, match.start(), zh))
                    fixed += 1
                    continue
            # 译名不匹配：保留中文，删英文括注
            start = match.start()
            while start > 0 and text[start - 1] in " \t":
                start -= 1
            edits.append((start, match.end(), ""))
            fixed += 1
            continue
        # 括号内误填中文译名（含变体 + 译名汉字）：替换为标准 en
        if re.search(_PP_HAN, inner):
            for ti in bilingual:
                term = terms[ti]
                variants = per_term[ti]
                if not _pp_any_variant(inner, variants):
                    continue
                if re.search(_pp_zh_core(term.zh.strip()), inner):
                    edits.append((
                        match.start(), match.end(), f"（{term.en}）"
                    ))
                    fixed += 1
                    break

    for start, end, repl in sorted(edits, reverse=True):
        text = text[:start] + repl + text[end:]

    # ---- Pass B：剩余裸用英文变体补配对 ----
    protected = _pp_protected_spans(text)
    legal_spans: list[tuple[int, int]] = []
    # 现存合法配对区间（译名串整体豁免）
    for match in re.finditer(r"[（(](?P<inner>[^（）()\n]{0,80})[）)]", text):
        if _pp_in_spans(match.start(), protected):
            continue
        phrase = re.sub(
            r"[,，]\s*[A-Za-z][^,，]*$", "", match.group("inner")
        ).strip()
        hit = _pp_end_variant(phrase, table)
        if hit is None:
            continue
        ti = hit[1]
        term = terms[ti]
        if term.mode != "bilingual":
            continue
        pair_start = _pp_legal_pair_start(text, match.start(), term.zh)
        if pair_start is not None:
            legal_spans.append((pair_start, match.end()))
    # 中文译名裸用区间（译名自身夹带英文，如 BLEU 分数 / WMT 2014 数据集）
    for ti in bilingual:
        term = terms[ti]
        for m in re.finditer(_pp_zh_loose(term.zh.strip()), text):
            legal_spans.append((m.start(), m.end()))

    alt_pattern = re.compile(
        r"(?<![A-Za-z-])(?:"
        + "|".join(re.escape(v) for v, _ in table)
        + r")(?![A-Za-z-])",
        re.IGNORECASE,
    )
    variant_to_term = {v.lower(): ti for v, ti in table}
    edits = []
    for hit in alt_pattern.finditer(text):
        if _pp_in_spans(hit.start(), protected) or _pp_in_spans(
            hit.start(), legal_spans
        ):
            continue
        term = terms[variant_to_term[hit.group(0).lower()]]
        if term.mode != "bilingual":
            # zh_only 裸英文：直接删除
            start = hit.start()
            while start > 0 and text[start - 1] in " \t":
                start -= 1
            edits.append((start, hit.end(), ""))
            fixed += 1
            continue
        repl = f"{term.zh.strip()}（{term.en}）"
        before = text[:hit.start()].rstrip()
        if before and before[-1].isdigit():
            repl = "个 " + repl  # 41.8 BLEU → 41.8 个 BLEU 分数（BLEU）
        edits.append((hit.start(), hit.end(), repl))
        fixed += 1
    for start, end, repl in sorted(edits, reverse=True):
        text = text[:start] + repl + text[end:]

    text = re.sub(r"[ \t]*([，。；！？、）])", r"\1", text)
    text = re.sub(r"（[ \t]*", "（", text)
    # 中文/全角括号与汉字之间的残留空格（配对补全产生），数字与汉字间保留
    text = re.sub(r"(?<=[一-鿿）」』】])[ \t]+(?=[一-鿿])", "", text)
    return text, fixed


def postprocess_section(
    response: dict[str, Any],
    *,
    slug: str,
    fold_block_ids: set[str],
    chart_block_ids: set[str],
    terms: list[Term],
    worked_example_block_id: str | None,
) -> list[str]:
    """落盘前确定性后处理；返回人类可读的修复留痕列表。

    1. 关键公式卡强制注入 ``worked_example_ref=we-001``（SC-007）；
    2. 主线块剔除 plan 判 fold / chart 的溯源块（FR-019/FR-020），
       溯源被全部剔除的块整块删除；
    3. 与同段结论块无共享溯源的 fold_ref：能锚定到提及折叠区的结论块则
       补锚定源，否则删除（fold-data-linkage）；
    4. 主线文本剥离具体硬件/时长分句（FR-020）；
    5. 术语格式规范化（FR-007）。
    """
    notes: list[str] = []
    blocks = response.get("blocks", [])

    # 1. worked_example_ref 注入
    if worked_example_block_id is not None:
        for block in blocks:
            if block.get("kind") == "formula_card" \
                    and worked_example_block_id in block.get(
                        "source_block_ids", []
                    ) \
                    and block.get("worked_example_ref") != WORKED_EXAMPLE_ID:
                block["worked_example_ref"] = WORKED_EXAMPLE_ID
                notes.append(
                    f"formula_card（源 {worked_example_block_id}）"
                    f"补 worked_example_ref={WORKED_EXAMPLE_ID}"
                )

    # 2. fold / chart 溯源剔除；溯源清空的块整块删除
    surviving: list[dict[str, Any]] = []
    forbidden = fold_block_ids | chart_block_ids
    for block in blocks:
        kind = block.get("kind")
        if kind in ("fold_ref", "supplement"):
            surviving.append(block)
            continue
        sources = block.get("source_block_ids", [])
        kept_sources = [bid for bid in sources if bid not in forbidden]
        removed = [bid for bid in sources if bid in forbidden]
        if not kept_sources:
            if removed:
                notes.append(
                    f"删除 {kind} 块（溯源 {removed} 全部被 plan 判 fold/chart）"
                )
            continue
        if removed:
            block["source_block_ids"] = kept_sources
            notes.append(
                f"{kind} 块剔除 fold/chart 溯源 {removed}（FR-019/FR-020）"
            )
        surviving.append(block)

    # 3. 孤立 fold_ref 锚定或删除
    anchored: list[dict[str, Any]] = []
    for block in surviving:
        if block.get("kind") != "fold_ref" \
                or block.get("fold_group") not in ("derivation", "repro_data"):
            anchored.append(block)
            continue
        fold_sources = set(block.get("source_block_ids", []))
        siblings = [
            b for b in surviving
            if b.get("kind") not in ("fold_ref", "supplement")
        ]
        sibling_sources = {
            bid for b in siblings
            for bid in b.get("source_block_ids", [])
        }
        if fold_sources & sibling_sources:
            anchored.append(block)
            continue
        # 寻找显式提及折叠区/表/图编号的结论块作锚
        anchor_sources: list[str] = []
        for sib in siblings:
            text = str(sib.get("markdown", ""))
            if re.search(r"折叠|见\s*Table|Table\s*\d|表\s*\d|图\s*\d", text):
                anchor_sources.extend(
                    bid for bid in sib.get("source_block_ids", [])
                    if bid not in forbidden and bid not in fold_sources
                )
        if anchor_sources:
            block["source_block_ids"] = list(block.get(
                "source_block_ids", []
            )) + anchor_sources
            notes.append(
                f"fold_ref[{block.get('fold_group')}] 补锚定溯源 {anchor_sources}"
                "（fold-data-linkage）"
            )
            anchored.append(block)
        else:
            notes.append(
                f"删除孤立 fold_ref[{block.get('fold_group')}]"
                f"（源 {block.get('source_block_ids')} 与同段结论块无共享，"
                "且无结论块提及折叠区）"
            )
    surviving = anchored

    # 4/5. 文本层修复（fold_ref 原文转写不动）
    for block in surviving:
        if block.get("kind") == "fold_ref":
            continue
        for field in _PP_TEXT_FIELDS:
            value = block.get(field)
            if not isinstance(value, str) or not value:
                continue
            new_value, n_hw = _strip_hardware_clauses(value)
            new_value, n_term = _normalize_terms(new_value, terms)
            if new_value != value:
                block[field] = new_value
                if n_hw:
                    notes.append(
                        f"{block.get('kind')}.{field} 剥离硬件/时长分句 {n_hw} 处"
                    )
                if n_term:
                    notes.append(
                        f"{block.get('kind')}.{field} 术语格式规范化 {n_term} 处"
                    )
        breakdown = block.get("symbol_breakdown")
        if isinstance(breakdown, list):
            for item in breakdown:
                if not isinstance(item, dict):
                    continue
                meaning = item.get("meaning")
                if not isinstance(meaning, str):
                    continue
                new_meaning, n_term = _normalize_terms(meaning, terms)
                if n_term:
                    item["meaning"] = new_meaning
                    notes.append(
                        f"{block.get('kind')}.symbol_breakdown 术语规范化 "
                        f"{n_term} 处"
                    )

    response["blocks"] = surviving
    for note in notes:
        logger.info("确定性后处理[%s]：%s", slug, note)
    return notes


# ---------------------------------------------------------------------------
# 并发转写
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _rewrite_one(
    *,
    llm: Any,
    system_prompt: str,
    template: str,
    payload: dict[str, Any],
    worked_example_block_id: str | None,
    max_attempts: int,
) -> dict[str, Any]:
    """单段：LLM 调用 + 双校验 + 段内重试。成功返回响应 dict。"""
    slug = payload["target_slug"]
    user_prompt = render_user_prompt(template, payload)
    last_error: Exception | None = None
    feedback = ""  # T040：业务/schema 校验问题回喂下一次调用
    for attempt in range(1, max_attempts + 1):
        try:
            response = llm.complete_json(
                system_prompt, user_prompt + feedback, "section_rewrite"
            )
            validate_llm_output(response, "section_rewrite")
        except LLMError as exc:
            last_error = exc
            logger.warning(
                "section_rewrite[%s] schema 校验失败（第 %d/%d 次）",
                slug, attempt, max_attempts,
            )
            feedback = (
                "\n\n## 上一次输出未通过 JSON Schema 校验"
                "（请逐条修正后重新输出完整 JSON）\n"
                f"{exc}"
            )
            continue
        problems = validate_section_response(
            response, payload, worked_example_block_id=worked_example_block_id
        )
        if not problems:
            return response
        last_error = LLMError(
            f"section_rewrite[{slug}] 第 {attempt} 次输出未通过确定性校验：\n- "
            + "\n- ".join(problems)
        )
        logger.warning(
            "section_rewrite[%s] 业务校验失败（第 %d/%d 次）：%d 处",
            slug, attempt, max_attempts, len(problems),
        )
        feedback = (
            "\n\n## 上一次转写未通过确定性校验"
            "（请逐条修正后重新输出完整 JSON）\n- "
            + "\n- ".join(problems)
        )
    assert last_error is not None
    raise LLMError(
        f"section_rewrite[{slug}] 在 {max_attempts} 次尝试后仍未产出合法结果："
        f"{last_error}"
    )


def run_rewrite(
    run_dir: Path | str,
    llm: Any,
    *,
    terms: list[Term] | None = None,
    seed_path: Path | str | None = None,
    prompts_dir: Path | str | None = None,
    jobs: int = config.DEFAULT_JOBS,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """并发转写七段；全部成功后统一落盘 sections/<slug>.json。

    返回 ``{"sections": {slug: response}, "out_of_glossary": 聚合记录}``。
    任一段失败即抛 :class:`LLMError`，且不落任何 section 文件（避免半截产物
    被断点续跑误判为已完成）。
    """
    run_dir = Path(run_dir)
    prompts_root = Path(prompts_dir) if prompts_dir is not None else config.PROMPTS_DIR
    normalized = load_normalized(run_dir)
    plan = load_plan(run_dir)
    if terms is None:
        merged = load_confirmed_glossary(run_dir, seed_path=seed_path)
        terms = merged.terms

    target_order = plan.get("target_order")
    if not isinstance(target_order, list):
        raise LLMError("plan.json 缺少 target_order（请重新运行 planner）")

    we_block_id: str | None = None
    for kf in plan.get("key_formulas", []):
        if isinstance(kf, dict) and kf.get("worked_example_required") is True:
            we_block_id = kf.get("block_id")
            break

    system_prompt = (prompts_root / "system.md").read_text(encoding="utf-8")
    template = (prompts_root / "rewrite_section.md").read_text(encoding="utf-8")

    payloads: list[dict[str, Any]] = [
        build_section_payload(normalized, plan, entry, terms)
        for entry in target_order
    ]

    workers = clamp_workers(jobs)
    logger.info("rewrite 并发转写：%d 段，max_workers=%d", len(payloads), workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            payload["target_slug"]: pool.submit(
                _rewrite_one,
                llm=llm,
                system_prompt=system_prompt,
                template=template,
                payload=payload,
                worked_example_block_id=we_block_id,
                max_attempts=max_attempts,
            )
            for payload in payloads
        }
        # 按七段固定顺序收集结果，future 异常在此重新抛出
        responses: dict[str, dict[str, Any]] = {}
        for payload in payloads:
            slug = payload["target_slug"]
            responses[slug] = futures[slug].result()

    # 落盘前确定性后处理：机械可推导的 plan/契约违规统一修复（T040）
    fold_block_ids = {
        bid for bid, decision in plan.get("block_actions", {}).items()
        if isinstance(decision, dict) and decision.get("action") == "fold"
    }
    chart_block_ids = {
        b["id"] for b in normalized.get("blocks", [])
        if isinstance(b, dict) and b.get("mineru_type") == "chart"
    }
    for payload in payloads:
        slug = payload["target_slug"]
        postprocess_section(
            responses[slug],
            slug=slug,
            fold_block_ids=fold_block_ids,
            chart_block_ids=chart_block_ids,
            terms=terms,
            worked_example_block_id=we_block_id,
        )

    # 全部成功后统一落盘：sections/ 只存中文结果 JSON（无独立译文产物）
    sections_root = run_dir / SECTIONS_DIRNAME
    for payload in payloads:
        slug = payload["target_slug"]
        _atomic_write_json(section_path(run_dir, slug), responses[slug])

    collected: list[dict[str, Any]] = []
    for payload in payloads:
        slug = payload["target_slug"]
        for item in responses[slug].get("out_of_glossary", []):
            entry = dict(item)
            entry["section"] = slug
            collected.append(entry)
    oog = merge_out_of_glossary(collected)

    logger.info(
        "rewrite 完成：%d 段已写出至 %s；表外术语 %d 条",
        len(responses), sections_root, len(oog),
    )
    return {"sections": responses, "out_of_glossary": oog}
