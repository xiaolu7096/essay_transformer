"""Prompt 构建：从 prompts/ 加载模板，填入章节数据。
从 transform.py 的 build_section_prompt 搬过来。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..planner import SECTION_ROLES, SLUG_ORDER


PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def get_section_blocks(
    norm: dict[str, Any], plan: dict[str, Any], slug: str
) -> tuple[list[dict], list[dict]]:
    """返回 (主线块列表, 折叠块列表)"""
    target = next(t for t in plan["target_order"] if t["target_slug"] == slug)
    source_ids = target["source_section_ids"]

    real_sections = [s for s in norm["sections"] if s["id"] in source_ids]
    all_block_ids = []
    for sec in real_sections:
        all_block_ids.extend(sec["blocks"])

    blocks_map = {b["id"]: b for b in norm["blocks"]}
    actions = plan["block_actions"]

    main_blocks = []
    fold_blocks = []
    for bid in all_block_ids:
        block = blocks_map.get(bid)
        if block is None:
            continue
        act = actions.get(bid, {}).get("action", "narrate")
        if act == "drop":
            continue
        if act == "fold":
            fold_blocks.append(block)
        else:
            main_blocks.append(block)

    return main_blocks, fold_blocks


def _gap_note_text(rng: dict[str, Any]) -> str:
    """把合并后的 gap range 渲染成发给 LLM 的一条提示（含死例句式）。"""
    p_start = rng["page_start"] + 1
    p_end = rng["page_end"] + 1
    page_text = f"第 {p_start} 页" if p_start == p_end else f"第 {p_start}–{p_end} 页"
    if rng.get("suspected") == "figure":
        return (
            f"[解析缺失·疑似图片] {page_text}该位置原文可能包含一张图、解析未提取。"
            "如确需提及，原样改写为：（此处原文可能含一张图，解析未提取）；"
            "不得编造图中内容，不得写出内部块号或内部字段名等任何工程标识。"
        )
    return (
        f"[解析缺失] {page_text}该位置内容未能解析。"
        "如确需提及，全章至多保留一处、原样改写为：（原文该位置内容未能解析，此处不作补全）；"
        "不得编造缺失内容，不得命令读者重新解析，"
        "不得写出内部块号或内部字段名等任何工程标识。"
    )


def _section_of_block(norm: dict[str, Any], block_id: str) -> str:
    for sec in norm["sections"]:
        if block_id in sec["blocks"]:
            return sec["heading"]
    return "?"


def build_section_prompt(
    norm: dict[str, Any], plan: dict[str, Any], slug: str
) -> tuple[str, str]:
    """构造 (system_prompt, user_prompt)"""
    system_prompt = load_prompt("system.md")
    template = load_prompt("section.md")

    target = next(t for t in plan["target_order"] if t["target_slug"] == slug)
    main_blocks, fold_blocks = get_section_blocks(norm, plan, slug)

    # 格式化主线块（parse_gap 不逐块输出：在首个 gap 位置插一条合并提示）
    gap_ranges = plan.get("gap_ranges", {}).get(slug, [])
    gap_ids_rendered: set[str] = set()
    block_texts = []
    seq = 0
    for b in main_blocks:
        if b["type"] == "parse_gap":
            if gap_ranges and not gap_ids_rendered:
                rng = gap_ranges[0]
                block_texts.append(_gap_note_text(rng))
                gap_ids_rendered = set(rng["block_ids"])
            # 其余 gap 已并入该提示，跳过
            continue
        seq += 1
        sec_name = _section_of_block(norm, b["id"])
        header = f"[块{seq}] type={b['type']}, section={sec_name}, page={(b.get('page_idx') or 0) + 1}"
        if b["type"] == "equation":
            content = b.get("latex", "") or b.get("text", "")
        elif b["type"] == "figure":
            img = b.get("img_path", "") or ""
            cap = b.get("caption", "") or b.get("text", "")
            content = f"image: {img}\ncaption: {cap}"
        elif b["type"] == "table":
            content = b.get("html", "") or b.get("text", "")
        elif b["type"] == "reference_list":
            content = b.get("text", "")[:500]
        else:
            content = b.get("text", "")
        block_texts.append(f"{header}\n{content}")

    # 折叠块摘要（不带内部 id，仅类型与数量）
    fold_summary = "无"
    if fold_blocks:
        fold_summary = "; ".join(
            f"{b['type']}(第{(b.get('page_idx') or 0) + 1}页)" for b in fold_blocks[:10]
        )
        if len(fold_blocks) > 10:
            fold_summary += f" ... 共 {len(fold_blocks)} 个"

    user_prompt = template.replace("{{TARGET_SLUG}}", slug)
    user_prompt = user_prompt.replace("{{TREATMENT}}", target.get("treatment", ""))
    user_prompt = user_prompt.replace("{{SECTION_ROLE}}", SECTION_ROLES.get(slug, ""))
    user_prompt = user_prompt.replace("{{SECTION_BLOCKS}}", "\n\n".join(block_texts))
    user_prompt = user_prompt.replace("{{FOLD_BLOCKS_SUMMARY}}", fold_summary)

    return system_prompt, user_prompt
