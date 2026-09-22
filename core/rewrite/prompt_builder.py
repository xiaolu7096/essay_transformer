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

    # 格式化主线块
    block_texts = []
    for b in main_blocks:
        sec_name = _section_of_block(norm, b["id"])
        header = f"[{b['id']}] type={b['type']}, section={sec_name}, page={(b.get('page_idx') or 0) + 1}"
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

    # 折叠块摘要
    fold_summary = "无"
    if fold_blocks:
        fold_summary = "; ".join(
            f"{b['id']}({b['type']})" for b in fold_blocks[:10]
        )
        if len(fold_blocks) > 10:
            fold_summary += f" ... 共 {len(fold_blocks)} 个"

    user_prompt = template.replace("{{TARGET_SLUG}}", slug)
    user_prompt = user_prompt.replace("{{TREATMENT}}", target.get("treatment", ""))
    user_prompt = user_prompt.replace("{{SECTION_ROLE}}", SECTION_ROLES.get(slug, ""))
    user_prompt = user_prompt.replace("{{SECTION_BLOCKS}}", "\n\n".join(block_texts))
    user_prompt = user_prompt.replace("{{FOLD_BLOCKS_SUMMARY}}", fold_summary)

    return system_prompt, user_prompt
