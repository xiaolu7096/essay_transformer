#!/usr/bin/env python3
"""
精简版论文转写入口。
链路: normalized.json + plan.json -> 逐章 LLM 转写 -> 拼装 Markdown
不依赖 glossary / worked_example / QA 闸门。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

from openai import OpenAI

# ── 常量 ──────────────────────────────────────────────────────────
RUNS_DIR = Path("runs")
PROMPTS_DIR = Path("prompts_v2")

SLUG_ORDER = ["tldr", "intro", "conclusion", "related", "method", "experiments", "wrapup"]

SECTION_HEADINGS = {
    "tldr": "## 0 · 30 秒摘要",
    "intro": "## 1 · 这篇论文要解决什么问题（引言精讲）",
    "conclusion": "## 2 · 结论：先看它最后说了什么",
    "related": "## 3 · 背景与相关工作",
    "method": "## 4 · 核心方法",
    "experiments": "## 5 · 实验说明了什么",
    "wrapup": "## 6 · 总评",
}

SECTION_ROLES = {
    "tldr": "30 秒读完的摘要，点到即止，不展开细节",
    "intro": "展开动机与背景，让读者明白这篇论文为什么值得读",
    "conclusion": "把论文的核心结论前置，读者先知道答案再看过程",
    "related": "压缩处理，只讲理解本文必需的前人工作",
    "method": "教程式充分展开，结构图导读 + 核心公式讲解",
    "experiments": "结论级表达为主，只讲对比结果和意义，完整数据放折叠区",
    "wrapup": "总评收束，讲贡献、局限与适用边界",
}

# ── 工具函数 ──────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def backfill_equations(norm: dict, full_md_path: Path) -> dict:
    """从 MinerU full.md 中提取块级公式，回填到 normalized.json 里
    text 为空的 equation / parse_gap 块中。
    策略：按公式在全文中出现的顺序，依次填入按 page_idx 排序的空 equation 块。
    """
    if not full_md_path.exists():
        return norm

    full_text = full_md_path.read_text(encoding="utf-8")
    # 提取所有 $$...$$ 块级公式
    eq_matches = re.findall(r'\$\$(.*?)\$\$', full_text, re.DOTALL)
    eqs = [m.strip() for m in eq_matches if m.strip()]
    if not eqs:
        return norm

    # 收集 normalized 中空的 equation/parse_gap 块，按 page_idx 和原始顺序排序
    empty_blocks = [
        b for b in norm["blocks"]
        if b["type"] in ("equation", "parse_gap") and not b.get("text", "").strip()
    ]
    empty_blocks.sort(key=lambda b: (b["page_idx"], norm["blocks"].index(b)))

    # 按顺序一一回填
    filled = 0
    for eq, block in zip(eqs, empty_blocks):
        block["type"] = "equation"
        block["text"] = eq
        filled += 1

    print(f"[backfill] 回填了 {filled} 个公式 (共 {len(eqs)} 个可提取, {len(empty_blocks)} 个空槽位)")
    return norm


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def slugify_title(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title.lower().strip())
    s = re.sub(r"[-\s]+", "-", s)
    return s[:60].strip("-")


# ── 数据选取 ──────────────────────────────────────────────────────

def get_section_blocks(norm: dict, plan: dict, slug: str) -> tuple[list[dict], list[dict]]:
    """返回 (主线块列表, 折叠块列表)"""
    target = next(t for t in plan["target_order"] if t["target_slug"] == slug)
    source_ids = target["source_section_ids"]

    # 收集该章所有块 id（过滤合成 id，只取真实节）
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


def section_of_block(norm: dict, block_id: str) -> str:
    """找块所属的章节标题"""
    for sec in norm["sections"]:
        if block_id in sec["blocks"]:
            return sec["heading"]
    return "?"


# ── LLM 调用 ──────────────────────────────────────────────────────

def chat(system: str, user: str, model: str = "deepseek-flash", fake: bool = False) -> str:
    """薄 LLM 调用。fake=True 时返回占位文本，不花钱。"""
    if fake:
        return f"[FAKE OUTPUT] 这是假 LLM 输出占位。\n\nsystem 长度: {len(system)}\nuser 长度: {len(user)}"

    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
    )
    return resp.choices[0].message.content


# ── Prompt 构造 ───────────────────────────────────────────────────

def build_section_prompt(norm: dict, plan: dict, slug: str) -> tuple[str, str]:
    """构造 system + user prompt"""
    system_prompt = load_prompt("system.md")
    template = load_prompt("section.md")

    target = next(t for t in plan["target_order"] if t["target_slug"] == slug)
    main_blocks, fold_blocks = get_section_blocks(norm, plan, slug)

    # 格式化主线块
    block_texts = []
    for b in main_blocks:
        sec_name = section_of_block(norm, b["id"])
        header = f"[{b['id']}] type={b['type']}, section={sec_name}, page={b['page_idx'] + 1}"
        if b["type"] == "equation":
            content = b.get("text", "")
        elif b["type"] == "figure":
            img = b.get("img_path", "") or b.get("image_path", "")
            cap = b.get("caption", "") or b.get("text", "")
            content = f"image: {img}\ncaption: {cap}"
        elif b["type"] == "table":
            content = b.get("text", "") or b.get("html", "")
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


# ── 拼装 ──────────────────────────────────────────────────────────

def build_fold_sections(norm: dict, plan: dict) -> str:
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
                parts.append(f"\n**{b['id']}**\n\n")
                parts.append(b.get("text", "")[:3000])
            elif b["type"] == "figure":
                img = b.get("img_path", "") or b.get("image_path", "")
                parts.append(f"\n![{b.get('caption','')}](images/{Path(img).name})\n")
            elif b["type"] == "reference_list":
                parts.append(b.get("text", ""))
            else:
                parts.append(b.get("text", "")[:2000])
            parts.append("\n\n")
        parts.append("</details>\n")

    return "".join(parts)


def copy_images(norm: dict, plan: dict, src_dir: Path, dst_dir: Path) -> list[str]:
    """拷贝需要的图片到 dist 目录，返回图片文件名列表"""
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    actions = plan["block_actions"]

    for b in norm["blocks"]:
        if b["type"] != "figure":
            continue
        act = actions.get(b["id"], {}).get("action", "narrate")
        if act in ("drop",):
            continue
        img_path = b.get("img_path") or b.get("image_path")
        if not img_path:
            continue
        src = src_dir / img_path
        if src.exists():
            shutil.copy2(src, dst_dir / src.name)
            copied.append(src.name)
    return copied


def check_image_links(md_text: str, images_dir: Path) -> list[str]:
    """检查 markdown 中图片链接是否存在，返回死链列表"""
    dead = []
    for m in re.finditer(r"!\[.*?\]\(([^)]+)\)", md_text):
        target = m.group(1)
        if target.startswith("http"):
            continue
        local = images_dir / Path(target).name
        if not local.exists():
            dead.append(target)
    return dead


def fix_image_links(md_text: str, images_dir: Path) -> tuple[str, int]:
    """修复 markdown 中的图片死链，用最长公共前缀匹配真实文件名。
    返回 (修复后的文本, 修复数量)
    """
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
        # 找最长公共前缀的真实文件
        best = None
        best_score = 0
        for real in real_files:
            # 计算公共前缀长度
            score = 0
            for a, b in zip(fname, real):
                if a == b:
                    score += 1
                else:
                    break
            if score > best_score:
                best_score = score
                best = real
        # 阈值：至少匹配 10 个字符（hash 文件名通常前半段就够了）
        if best and best_score >= 10:
            fixed_count += 1
            return f"![{alt}](images/{best})"
        return match.group(0)

    new_text = re.sub(r"!\[(.*?)\]\(([^)]+)\)", _replace, md_text)
    if fixed_count > 0:
        print(f"[fix_images] 修复了 {fixed_count} 个图片死链")
    return new_text, fixed_count


def demote_headings(md_text: str) -> tuple[str, int]:
    """将章节内容中误写的 `#` / `##` 标题降级为 `###` 起。
    返回 (修复后的文本, 降级数量)
    """
    lines = md_text.split("\n")
    fixed = 0
    for i, line in enumerate(lines):
        # 行首的 `#` 或 `##` 标题降级
        if line.startswith("## "):
            lines[i] = "##" + line  # ## → ###（前面多一个 #）
            fixed += 1
        elif line.startswith("# ") and not line.startswith("##"):
            lines[i] = "##" + line  # # → ###
            fixed += 1
    if fixed > 0:
        print(f"[demote] 降级了 {fixed} 个标题（防止 LLM 输出 ## / # 级标题）")
    return "\n".join(lines), fixed


# ── 主命令 ────────────────────────────────────────────────────────

def run_rerun(paper_id: str, section: str, fake: bool = False) -> Path:
    """只重转一章，输出到 dist_v2/<section>_preview.md"""
    run_dir = RUNS_DIR / paper_id
    norm = load_json(run_dir / "normalized.json")
    norm = backfill_equations(norm, run_dir / "parsed" / "full.md")
    plan = load_json(run_dir / "plan.json")

    system_prompt, user_prompt = build_section_prompt(norm, plan, section)
    result = chat(system_prompt, user_prompt, fake=fake)

    dist_dir = run_dir / "dist_v2"
    dist_dir.mkdir(parents=True, exist_ok=True)
    images_dir = dist_dir / "images"
    # 图片死链修复（预览模式也修）
    result, _ = fix_image_links(result, images_dir)
    out_path = dist_dir / f"{section}_preview.md"
    out_path.write_text(result, encoding="utf-8")
    print(f"[rerun] {section} 章已写入 {out_path} ({len(result)} 字符)")
    return out_path


def run_build(paper_id: str, fake: bool = False) -> Path:
    """全文转写 + 拼装"""
    run_dir = RUNS_DIR / paper_id
    norm = load_json(run_dir / "normalized.json")
    norm = backfill_equations(norm, run_dir / "parsed" / "full.md")
    plan = load_json(run_dir / "plan.json")

    dist_dir = run_dir / "dist_v2"
    images_dir = dist_dir / "images"

    # 逐章转写
    section_contents: dict[str, str] = {}
    for slug in SLUG_ORDER:
        print(f"[build] 转写 {slug} ...")
        system_prompt, user_prompt = build_section_prompt(norm, plan, slug)
        content = chat(system_prompt, user_prompt, fake=fake)
        section_contents[slug] = content

    # 拷贝图片（img_path 是 images/xxx.jpg，所以基目录是 parsed/）
    parsed_base = run_dir / "parsed"
    copied = copy_images(norm, plan, parsed_base, images_dir)
    print(f"[build] 拷贝图片 {len(copied)} 张")

    # 拼装
    title = norm.get("title", paper_id)
    parts = [f"# {title}\n"]
    total_demoted = 0
    for slug in SLUG_ORDER:
        parts.append(f"\n{SECTION_HEADINGS[slug]}\n\n")
        # 标题降级兜底（防止 LLM 输出 ## / # 级标题）
        content, demoted = demote_headings(section_contents[slug])
        total_demoted += demoted
        parts.append(content)
        parts.append("\n")
    if total_demoted > 0:
        print(f"[build] 共降级 {total_demoted} 个误写的章节标题")

    # 折叠区
    parts.append(build_fold_sections(norm, plan))

    full_md = "".join(parts)

    # 图片死链自动修复
    full_md, fixed = fix_image_links(full_md, images_dir)

    out_name = f"{slugify_title(title)}.md"
    out_path = dist_dir / out_name
    out_path.write_text(full_md, encoding="utf-8")

    # 死链检查（警告，不阻断）
    dead = check_image_links(full_md, images_dir)
    if dead:
        print(f"[warn] 图片死链 {len(dead)} 个: {dead[:5]}")

    print(f"[build] 完成: {out_path} ({len(full_md)} 字符)")
    return out_path


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="精简版论文转写")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rerun = sub.add_parser("rerun", help="重转单章")
    p_rerun.add_argument("paper_id")
    p_rerun.add_argument("--section", required=True, choices=SLUG_ORDER)
    p_rerun.add_argument("--fake", action="store_true", help="假 LLM，不花钱")

    p_build = sub.add_parser("build", help="全文转写 + 拼装")
    p_build.add_argument("paper_id")
    p_build.add_argument("--fake", action="store_true", help="假 LLM，不花钱")

    args = parser.parse_args()

    if args.command == "rerun":
        run_rerun(args.paper_id, args.section, fake=args.fake)
    elif args.command == "build":
        run_build(args.paper_id, fake=args.fake)


if __name__ == "__main__":
    main()
