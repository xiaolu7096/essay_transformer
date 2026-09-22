"""标题降级：防止 LLM 输出 ## / # 级标题破坏层级。
从 transform.py 搬过来。
"""
from __future__ import annotations


def demote_headings(md_text: str) -> tuple[str, int]:
    """将章节内容中误写的 `#` / `##` 标题降级为 `###` 起。
    返回 (修复后的文本, 降级数量)
    """
    lines = md_text.split("\n")
    fixed = 0
    for i, line in enumerate(lines):
        if line.startswith("## "):
            lines[i] = "##" + line  # ## → ###
            fixed += 1
        elif line.startswith("# ") and not line.startswith("##"):
            lines[i] = "##" + line  # # → ###
            fixed += 1
    if fixed > 0:
        print(f"[demote] 降级了 {fixed} 个标题（防止 LLM 输出 ## / # 级标题）")
    return "\n".join(lines), fixed
