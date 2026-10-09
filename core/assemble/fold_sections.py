"""折叠区构建：把 fold 的块放到文末 details 里。
从 transform.py 搬过来。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .references import render_reference_items


def _table_title(block: dict[str, Any]) -> str:
    """表标题：优先原文表题 caption；无表题时用中性页码文案（无内部 id）。"""
    caption = (block.get("caption") or "").strip()
    if caption:
        return caption
    page = (block.get("page_idx") or 0) + 1
    return f"表（第 {page} 页）"


def build_fold_sections(norm: dict[str, Any], plan: dict[str, Any]) -> str:
    """构建文末折叠区（表格/推导/参考文献）"""
    actions = plan["block_actions"]
    fold_blocks = []
    for b in norm["blocks"]:
        act = actions.get(b["id"], {}).get("action")
        if act == "fold":
            fold_blocks.append(b)

    if not fold_blocks:
        return ""

    parts = ["\n\n---\n\n## 附录（折叠区）\n"]

    # 按 fold_group 分组
    groups: dict[str, list[dict]] = {}
    for b in fold_blocks:
        fg = actions[b["id"]].get("fold_group", "repro_data")
        groups.setdefault(fg, []).append(b)

    group_titles = {
        "repro_data": "实验数据与表格原文",
        "derivation": "推导与证明细节",
        "references": "参考文献",
    }

    for fg, blocks in groups.items():
        title = group_titles.get(fg, fg)
        parts.append(f'\n<details>\n<summary>{title}</summary>\n')
        for b in blocks:
            if b["type"] == "table":
                parts.append(f"\n**{_table_title(b)}**\n\n")
                # 完整 HTML/文本，不截断（T016）
                parts.append(b.get("html", "") or b.get("text", ""))
            elif b["type"] == "figure":
                img = b.get("img_path", "") or ""
                parts.append(f"\n![{b.get('caption','')}](images/{Path(img).name})\n")
            elif b["type"] == "reference_list":
                rendered = render_reference_items(list(b.get("items", []) or []))
                parts.append("\n".join(rendered) if rendered else b.get("text", ""))
            else:
                # 其他类型完整保留，不截断（T016）
                parts.append(b.get("text", ""))
            parts.append("\n\n")
        parts.append("</details>\n")

    return "".join(parts)
