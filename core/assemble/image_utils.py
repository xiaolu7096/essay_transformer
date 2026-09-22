"""图片相关工具：拷贝 + 死链修复 + 死链检查。
从 transform.py 搬过来。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any


def copy_images(
    norm: dict[str, Any], plan: dict[str, Any], src_dir: str | Path, dst_dir: str | Path
) -> list[str]:
    """拷贝需要的图片到 dst_dir，返回图片文件名列表"""
    src_dir = Path(src_dir)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    actions = plan["block_actions"]

    for b in norm["blocks"]:
        if b["type"] != "figure":
            continue
        act = actions.get(b["id"], {}).get("action", "narrate")
        if act == "drop":
            continue
        img_path = b.get("img_path")
        if not img_path:
            continue
        src = src_dir / img_path
        if src.exists():
            shutil.copy2(src, dst_dir / src.name)
            copied.append(src.name)
    return copied


def check_image_links(md_text: str, images_dir: str | Path) -> list[str]:
    """检查 markdown 中图片链接是否存在，返回死链列表"""
    images_dir = Path(images_dir)
    dead = []
    for m in re.finditer(r"!\[.*?\]\(([^)]+)\)", md_text):
        target = m.group(1)
        if target.startswith("http"):
            continue
        local = images_dir / Path(target).name
        if not local.exists():
            dead.append(target)
    return dead


def fix_image_links(md_text: str, images_dir: str | Path) -> tuple[str, int]:
    """修复图片死链，用最长公共前缀匹配真实文件名。
    返回 (修复后的文本, 修复数量)
    """
    images_dir = Path(images_dir)
    if not images_dir.exists():
        return md_text, 0

    real_files = [p.name for p in images_dir.iterdir() if p.is_file()]
    if not real_files:
        return md_text, 0

    fixed_count = 0

    def _replace(match: re.Match) -> str:
        nonlocal fixed_count
        alt = match.group(1)
        target = match.group(2)
        if target.startswith("http"):
            return match.group(0)
        fname = Path(target).name
        local = images_dir / fname
        if local.exists():
            return match.group(0)
        # 找最长公共前缀
        best = None
        best_score = 0
        for real in real_files:
            score = 0
            for a, b in zip(fname, real):
                if a == b:
                    score += 1
                else:
                    break
            if score > best_score:
                best_score = score
                best = real
        if best and best_score >= 10:
            fixed_count += 1
            return f"![{alt}](images/{best})"
        return match.group(0)

    new_text = re.sub(r"!\[(.*?)\]\(([^)]+)\)", _replace, md_text)
    if fixed_count > 0:
        print(f"[fix_images] 修复了 {fixed_count} 个图片死链")
    return new_text, fixed_count
