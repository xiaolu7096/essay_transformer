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

    # 2. 未分类的节按顺序分配
    has_method = any(v == "method" for v in section_slug.values())
    has_experiments = any(v == "experiments" for v in section_slug.values())

    for sec_id in unclassified_sections:
        sec = next((s for s in sections if s["id"] == sec_id), None)
        if sec is None:
            continue
        level = sec.get("level", 99)

        # 一级子节（level=1 是标题，level=2 是一级子节）
        if level <= 2:
            if not has_method:
                section_slug[sec_id] = "method"
                has_method = True
            elif not has_experiments:
                section_slug[sec_id] = "experiments"
                has_experiments = True
            else:
                # 都有了，归到 method（方法章节通常最多）
                section_slug[sec_id] = "method"
        else:
            # 低级子节跟随父节（简化：找前面最近的 level=2 的节的归属）
            parent_slug = _find_parent_slug(sections, sec_id, section_slug)
            section_slug[sec_id] = parent_slug or "method"

    # 3. 确保每个 slug 至少有内容（空的话给个空列表）
    target_order = []
    for slug in SLUG_ORDER:
        source_ids = [sid for sid, s in section_slug.items() if s == slug]
        target_order.append({
            "target_slug": slug,
            "source_section_ids": source_ids,
            "treatment": SECTION_TREATMENTS.get(slug, ""),
        })

    # 4. 块动作
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

    # 5. 实验章里的 figure（数据图）改 fold
    exp_sec_ids = [
        t["source_section_ids"] for t in target_order if t["target_slug"] == "experiments"
    ][0]
    exp_block_ids = set()
    for sec in sections:
        if sec["id"] in exp_sec_ids:
            exp_block_ids.update(sec["blocks"])
    for bid in exp_block_ids:
        blk = next((b for b in blocks if b["id"] == bid), None)
        if blk and blk["type"] == "figure":
            block_actions[bid] = {"action": "fold", "rationale": "实验数据图放折叠区",
                                   "fold_group": "repro_data"}
        if blk and blk["type"] == "table":
            block_actions[bid] = {"action": "fold", "rationale": "实验表格放折叠区",
                                   "fold_group": "repro_data"}

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

    return {
        "schema_kind": "rule_based_v1",
        "target_order": target_order,
        "block_actions": block_actions,
        "key_formulas": key_formulas,
    }


def _find_parent_slug(
    sections: list[dict[str, Any]],
    sec_id: str,
    section_slug: dict[str, str],
) -> str | None:
    """找给定节前面最近的已分配的同级或高级节的 slug。"""
    target_level = None
    for sec in sections:
        if sec["id"] == sec_id:
            target_level = sec.get("level", 99)
            break
    if target_level is None:
        return None

    # 往前找 level < target_level 且已分配的节
    found = False
    for sec in reversed(sections):
        if sec["id"] == sec_id:
            found = True
            continue
        if found and sec.get("level", 99) < target_level and sec["id"] in section_slug:
            return section_slug[sec["id"]]
    return None
