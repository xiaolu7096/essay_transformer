"""公式回填：从 MinerU full.md 提取公式，回填到 normalized 的空 equation 块。
从 transform.py 搬过来。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def backfill_equations(norm: dict[str, Any], full_md_path: str | Path) -> dict[str, Any]:
    """从 full.md 提取块级公式，回填到 text 为空的 equation / parse_gap 块。"""
    full_md_path = Path(full_md_path)
    if not full_md_path.exists():
        return norm

    full_text = full_md_path.read_text(encoding="utf-8")
    eq_matches = re.findall(r'\$\$(.*?)\$\$', full_text, re.DOTALL)
    eqs = [m.strip() for m in eq_matches if m.strip()]
    if not eqs:
        return norm

    # 空块按 page_idx + 原始顺序排序
    empty_blocks = [
        b for b in norm["blocks"]
        if b["type"] in ("equation", "parse_gap") and not b.get("latex", "").strip()
        and not b.get("text", "").strip()
    ]
    empty_blocks.sort(key=lambda b: (b.get("page_idx", 0), _block_index(norm, b["id"])))

    filled = 0
    for eq, block in zip(eqs, empty_blocks):
        block["type"] = "equation"
        block["latex"] = eq
        block["text"] = eq
        filled += 1

    print(f"[backfill] 回填了 {filled} 个公式 (共 {len(eqs)} 个可提取, {len(empty_blocks)} 个空槽位)")
    return norm


def _block_index(norm: dict[str, Any], block_id: str) -> int:
    for i, b in enumerate(norm["blocks"]):
        if b["id"] == block_id:
            return i
    return 99999
