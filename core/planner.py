"""规划：把 normalized 文档映射到 7 章结构 + 给每个块标动作。
简化版：用标题关键词匹配规则，覆盖 80% 以上的论文。不依赖 LLM，零成本。
"""
from __future__ import annotations

from typing import Any


SLUG_ORDER: tuple[str, ...] = (
    "tldr",
    "intro",
    "conclusion",
    "related",
    "method",
    "experiments",
    "wrapup",
)

SECTION_HEADINGS: dict[str, str] = {
    "tldr": "## 0 · 30 秒摘要",
    "intro": "## 1 · 这篇论文要解决什么问题（引言精讲）",
    "conclusion": "## 2 · 结论：先看它最后说了什么",
    "related": "## 3 · 背景与相关工作",
    "method": "## 4 · 核心方法",
    "experiments": "## 5 · 实验说明了什么",
    "wrapup": "## 6 · 总评",
}

SECTION_ROLES: dict[str, str] = {
    "tldr": "30 秒读完的摘要，点到即止，不展开细节",
    "intro": "展开动机与背景，让读者明白这篇论文为什么值得读",
    "conclusion": "把论文的核心结论前置，读者先知道答案再看过程",
    "related": "压缩处理，只讲理解本文必需的前人工作",
    "method": "教程式充分展开，结构图导读 + 核心公式讲解",
    "experiments": "结论级表达为主，只讲对比结果和意义，完整数据放折叠区",
    "wrapup": "总评收束，讲贡献、局限与适用边界",
}

SECTION_TREATMENTS: dict[str, str] = {
    "tldr": "brief",
    "intro": "expand",
    "conclusion": "front_load_extract",
    "related": "compress",
    "method": "tutorial",
    "experiments": "summary",
    "wrapup": "conclusion_only",
}

# 每个 slug 对应的 kind 关键词（按优先级排序，先匹配到先分配）
_SLUG_KIND_MAP: list[tuple[str, list[str]]] = [
    ("tldr", ["abstract"]),
    ("intro", ["introduction"]),
    ("related", ["related_work"]),
    ("conclusion", ["conclusion"]),
    ("experiments", ["experiments"]),
    # method 没有固定 kind，用剩余的非 appendix 节
]

# 层级组判定：命中即整组归 experiments，否则归 method
_EXPERIMENT_WORDS = (
    "experiment",
    "evaluation",
    "result",
    "benchmark",
    "metric",
    "accuracy",
    "dataset",
    "ablation",
    "baseline",
    "performance comparison",
)

# figure 折叠判据（T017）：架构/流程类图保留 narrate；数值结果图 fold
_ARCHITECTURE_WORDS = (
    "architecture",
    "framework",
    "overview",
    "pipeline",
    "structure",
    "model",
)
_DATA_CHART_WORDS = (
    "chart",
    "plot",
    "curve",
    "bar chart",
    "bar graph",
    "result",
    "accuracy",
    "%",
)

# 空章固定文案（FR-006）：一句面向读者的中性说明，正常正文口吻
EMPTY_FALLBACK: dict[str, str] = {
    "tldr": "原文未提供可供提炼的摘要，建议直接从引言与结论两章了解全文。",
    "intro": "本文未设独立的引言章节，研究背景与动机散见于正文各节。",
    "conclusion": "本文未设独立的结论章节，作者的核心主张已随方法与讨论部分给出。",
    "related": "本文未设独立的相关工作章节，相关背景与前人方法已随正文介绍。",
    "method": "本文未包含可供单独成章的方法描述，核心做法请参考引言与实验两章。",
    "experiments": "本文以综述或方法阐述为主，未包含独立的实验评测章节。",
    "wrapup": "原文未提供独立的总结性评述，贡献与适用边界请结合正文自行判断。",
}


def build_plan(norm: dict[str, Any]) -> dict[str, Any]:
    """基于规则构建 plan.json。

    策略：
    1. 先按 kind 匹配已知章节（abstract → tldr, introduction → intro 等）
    2. 剩余的非 appendix 非 references 节，按出现顺序分配：
       - 第一大段 → method（核心方法）
       - 后面的 → experiments（如果没匹配到 experiments kind）
       - 其余的 → related 或 method 里混着
    3. 块动作：paragraph/heading/equation/figure → narrate
                 table/reference_list → fold
                 noise/parse_gap → drop
    """
    sections = norm.get("sections", [])
    blocks = norm.get("blocks", [])

    # 1. 按 kind 给每节预分配 slug
    section_slug: dict[str, str] = {}  # sec_id -> slug
    unclassified_sections: list[str] = []

    for sec in sections:
        kind = sec.get("kind", "unclassified")
        heading = sec.get("heading", "").lower()

        # 先看 kind
        matched = None
        for slug, kinds in _SLUG_KIND_MAP:
            if kind in kinds:
                matched = slug
                break

        # kind 没匹配上，用标题关键词再试一次（kind 可能判不准）
        if not matched:
            if "abstract" in heading:
                matched = "tldr"
            elif "introduction" in heading and "related" not in heading:
                matched = "intro"
            elif "related work" in heading or "background" in heading:
                matched = "related"
            elif "conclusion" in heading or "discussion" in heading:
                matched = "conclusion"
            elif "experiment" in heading or "evaluation" in heading or "results" in heading:
                matched = "experiments"
            elif "appendix" in heading or "supplementary" in heading:
                matched = "appendix"  # 特殊标记，后面处理

        if matched == "appendix":
            # appendix 的节统一归到 wrapup
            section_slug[sec["id"]] = "wrapup"
        elif matched:
            # 可能有多节匹配同一个 slug（比如实验章有好几个子节），都归进去
            if sec["id"] not in section_slug:
                section_slug[sec["id"]] = matched
        else:
            unclassified_sections.append(sec["id"])

    # 2. 未分类节按"层级组"分配（FR-002）：
    #    以数字编号最靠左的顶级未分类节（如 2）为锚，全部后代节（2.1/2.1.3）
    #    强制同组；无编号的顶级节各自成组。整组一次性判 method/experiments。
    blocks_map = {b["id"]: b for b in blocks}
    unclassified_set = set(unclassified_sections)
    assigned: set[str] = set()

    for sec in sections:
        anchor_id = sec["id"]
        if anchor_id not in unclassified_set or anchor_id in assigned:
            continue
        members = _group_members(sections, anchor_id, unclassified_set, assigned)
        slug = _classify_group(members, sections, blocks_map)
        for member in members:
            section_slug[member] = slug

    # 3. 块基础动作（空章判定依赖动作；实验章 figure 改 fold 在第 5 步追加）
    block_actions: dict[str, dict[str, str]] = {}
    for blk in blocks:
        bid = blk["id"]
        btype = blk["type"]

        if btype in ("noise",):
            block_actions[bid] = {"action": "drop", "rationale": "噪声块"}
        elif btype == "table":
            block_actions[bid] = {"action": "fold", "rationale": "表格数据放折叠区",
                                   "fold_group": "repro_data"}
        elif btype == "reference_list":
            block_actions[bid] = {"action": "fold", "rationale": "参考文献放折叠区",
                                   "fold_group": "references"}
        elif btype == "parse_gap":
            # 缺失块保留进主线（会显示缺失说明，不编造）
            block_actions[bid] = {"action": "narrate", "rationale": "解析缺失占位，进主线显式说明"}
        else:
            # paragraph / heading / equation / figure → 进主线
            block_actions[bid] = {"action": "narrate", "rationale": "主线内容"}

    # 4. 确保每个 slug 至少有内容（空的话给个空列表）+ 空章判定（FR-001）
    target_order = []
    empty_slugs: list[str] = []
    for slug in SLUG_ORDER:
        source_ids = [sid for sid, s in section_slug.items() if s == slug]
        empty = not _has_substantive_blocks(
            source_ids, sections, blocks_map, block_actions
        )
        if empty:
            empty_slugs.append(slug)
        target_order.append({
            "target_slug": slug,
            "source_section_ids": source_ids,
            "treatment": SECTION_TREATMENTS.get(slug, ""),
            "empty": empty,
        })

    # 4.5 为每个 slug 计算 gap_ranges（不改原始 blocks）：同小节/同页相邻 gap 合并
    gap_ranges_by_slug = _build_gap_ranges(target_order, sections, blocks_map)

    # 5. figure 折叠按自身 caption 判定（T017）：数值图表词 → fold；
    #    架构/流程类图保留 narrate；拿不准默认 narrate（宁可多留图）。
    #    table 一律 fold 已在第 3 步处理，此处不再按章节改判。
    for blk in blocks:
        if blk["type"] != "figure":
            continue
        if _is_data_chart_caption(blk.get("caption", "") or ""):
            block_actions[blk["id"]] = {
                "action": "fold",
                "rationale": "数值数据图（曲线/柱状/结果图）放折叠区",
                "fold_group": "repro_data",
            }

    # 6. 找关键公式（method 章里的 equation 都算，取前 5 个）
    method_sec_ids = [
        t["source_section_ids"] for t in target_order if t["target_slug"] == "method"
    ][0]
    method_block_ids = set()
    for sec in sections:
        if sec["id"] in method_sec_ids:
            method_block_ids.update(sec["blocks"])
    key_formulas = []
    for blk in blocks:
        if blk["id"] in method_block_ids and blk["type"] == "equation":
            key_formulas.append(blk["id"])
            if len(key_formulas) >= 5:
                break

    # 标记被合并进 gap_ranges 的 gap（动作不变，仅记 merged_into）
    for slug, ranges in gap_ranges_by_slug.items():
        for r_idx, r in enumerate(ranges):
            for bid in r["block_ids"]:
                block_actions[bid]["merged_into"] = f"{slug}#gap-{r_idx}"

    return {
        "schema_kind": "rule_based_v1",
        "target_order": target_order,
        "block_actions": block_actions,
        "key_formulas": key_formulas,
        "empty_slugs": empty_slugs,
        "gap_ranges": gap_ranges_by_slug,
    }


def _build_gap_ranges(
    target_order: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    blocks_map: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """FR-011/SC-004：每个 slug 的全部 parse_gap 合并为至多一条 range。

    合并后携带页码范围；任一 gap 疑似图位则整段带 suspected="figure"。
    空章同样合并（但 Phase 3 不为其构造 prompt，提示不会出现）。
    """
    secs_by_id = {s["id"]: s for s in sections}
    ranges_by_slug: dict[str, list[dict[str, Any]]] = {}
    for target in target_order:
        slug = target["target_slug"]
        gap_ids: list[str] = []
        for sid in target["source_section_ids"]:
            for bid in secs_by_id[sid].get("blocks", []):
                blk = blocks_map.get(bid)
                if blk is not None and blk["type"] == "parse_gap":
                    gap_ids.append(bid)
        if not gap_ids:
            ranges_by_slug[slug] = []
            continue
        pages = [blocks_map[b].get("page_idx") or 0 for b in gap_ids]
        suspected = "figure" if any(
            blocks_map[b].get("suspected") == "figure" for b in gap_ids
        ) else None
        rng = {
            "label": "疑似图片缺失" if suspected == "figure" else "解析缺失区间",
            "page_start": min(pages),
            "page_end": max(pages),
            "suspected": suspected,
            "block_ids": gap_ids,
        }
        ranges_by_slug[slug] = [rng]
    return ranges_by_slug


def _number_of(sec: dict[str, Any]) -> str | None:
    """从 section id 反推数字编号（sec-2-1 → "2.1"）；非编号节返回 None。"""
    sid = sec["id"]
    if not sid.startswith("sec-"):
        return None
    tail = sid[4:]
    parts = tail.split("-")
    if parts and all(p.isdigit() for p in parts):
        return ".".join(parts)
    return None


def _group_members(
    sections: list[dict[str, Any]],
    anchor_id: str,
    unclassified: set[str],
    assigned: set[str],
) -> list[str]:
    """收集锚点节及其全部未分类后代节（文档顺序内）。"""
    anchor = next(s for s in sections if s["id"] == anchor_id)
    anchor_num = _number_of(anchor)
    anchor_level = anchor.get("level", 99)

    members = [anchor_id]
    assigned.add(anchor_id)

    if anchor_num is None:
        # 无编号顶级节：收纳文档顺序上、遇到下一个同级或更高级节之前的未分类后代
        seen_anchor = False
        for sec in sections:
            if sec["id"] == anchor_id:
                seen_anchor = True
                continue
            if not seen_anchor:
                continue
            if sec.get("level", 99) <= anchor_level:
                break
            if sec["id"] in unclassified and sec["id"] not in assigned:
                members.append(sec["id"])
                assigned.add(sec["id"])
        return members

    # 编号节：编号前缀匹配（"2" 锚匹配 "2.1"/"2.1.3"）
    prefix = anchor_num + "."
    for sec in sections:
        sid = sec["id"]
        num = _number_of(sec)
        if num and num.startswith(prefix) and sid in unclassified and sid not in assigned:
            members.append(sid)
            assigned.add(sid)
    return members


def _classify_group(
    members: list[str],
    sections: list[dict[str, Any]],
    blocks_map: dict[str, dict[str, Any]],
) -> str:
    """整组一次性判 slug：命中评测词 → experiments，否则 method（FR-002/FR-004）。"""
    secs_by_id = {s["id"]: s for s in sections}
    anchor_id = members[0]
    anchor = secs_by_id[anchor_id]
    anchor_num = _number_of(anchor)

    # 锚点自身有编号（如 4.1）且其父级已分到 experiments/method 时，以父级标题为依据
    if anchor_num and "." in anchor_num:
        parent_num = anchor_num.rsplit(".", 1)[0]
        parent_id = "sec-" + parent_num.replace(".", "-")
        parent = secs_by_id.get(parent_id)
        if parent is not None and _heading_hits_experiment(parent.get("heading", "")):
            return "experiments"

    for sid in members:
        sec = secs_by_id[sid]
        if _heading_hits_experiment(sec.get("heading", "")):
            return "experiments"
        # 组内块标题/caption 命中评测词（figure caption 等；表格不参与，表格一律 fold）
        for bid in sec.get("blocks", []):
            blk = blocks_map.get(bid)
            if blk is not None and blk["type"] != "table" and _heading_hits_experiment(
                blk.get("caption", "") or ""
            ):
                return "experiments"
    return "method"


def _heading_hits_experiment(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in _EXPERIMENT_WORDS)


def _is_data_chart_caption(caption: str) -> bool:
    """T017：按 caption 判数值数据图。架构/流程类词优先 → 保留 narrate；
    其次命中数值图表词 → fold；拿不准默认 narrate（False）。"""
    low = caption.lower()
    if any(w in low for w in _ARCHITECTURE_WORDS):
        return False
    return any(w in low for w in _DATA_CHART_WORDS)


def _has_substantive_blocks(
    source_ids: list[str],
    sections: list[dict[str, Any]],
    blocks_map: dict[str, dict[str, Any]],
    block_actions: dict[str, dict[str, str]],
) -> bool:
    """FR-001：存在 ≥1 个非 heading、非 parse_gap、非 fold/drop 的正文块。"""
    secs_by_id = {s["id"]: s for s in sections}
    for sid in source_ids:
        for bid in secs_by_id[sid].get("blocks", []):
            blk = blocks_map.get(bid)
            if blk is None:
                continue
            if blk["type"] in ("heading", "parse_gap"):
                continue
            if block_actions[bid]["action"] in ("fold", "drop"):
                continue
            return True
    return False
