"""成稿组装（T025，contracts/output-markdown.md 全文硬性样式）。

把七段 ``sections/<slug>.json``（rewrite 已过 schema + 业务校验）与
worked_example.json、normalized.json 组装为单个 GitHub 风格 Markdown：

- 固定七段标题与顺序（FR-012），段标题恒存在；
- 公式卡四件套 + 手算示例（verification=passed 才渲染，missing 渲染固定
  缺失占位，FR-017/026）；
- 保留图从 ``parsed/`` 拷贝到 ``dist/images/`` 并改相对引用；源文件缺失
  不产生死链，改渲染固定占位（FR-016/026）；
- 三类 ``<details>`` 折叠统一模板（derivation/repro_data/references），
  集中在正文末 ``---`` 之后；references 折叠以 normalized 解析原文为
  权威内容（完整无缺失，FR-022/023），有参考文献却无折叠即报错；
- supplement 统一 callout（📖/💡，整块 blockquote、无溯源）；
- 事实性段落末尾追加斜体溯源 ``*（原文 §x，p.n）*``（FR-024）；
- 同时落 ``out_of_glossary.json``（term 小写去重、locations 聚合）；
- 文件名为标题 kebab-case 截 60 字符；QA 失败由 :func:`rename_for_qa`
  改名为 ``.unchecked.md``。

本地文件 IO 失败统一抛 :class:`~essay_transformer.errors.LocalIOError`
（退出码 1、error_type=input 家族，避免被误判为 llm/qa）。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from .errors import InputError, LocalIOError
from .planner import SLUG_ORDER, load_normalized, load_plan
from .rewrite import WORKED_EXAMPLE_ID, merge_out_of_glossary, resolve_real_section_ids
from .worked_example import WORKED_EXAMPLE_FILENAME, TOY_DISCLAIMER

__all__ = [
    "DIST_DIRNAME",
    "IMAGES_DIRNAME",
    "OUT_OF_GLOSSARY_FILENAME",
    "SECTION_HEADINGS",
    "SUPPLEMENT_HEADERS",
    "MISSING_PLACEHOLDER",
    "slugify_title",
    "assemble_run",
    "rename_for_qa",
]

logger = logging.getLogger(__name__)

DIST_DIRNAME = "dist"
IMAGES_DIRNAME = "images"
OUT_OF_GLOSSARY_FILENAME = "out_of_glossary.json"

#: 七段固定二级标题（顺序硬性，FR-012）
SECTION_HEADINGS: dict[str, str] = {
    "tldr": "## 30 秒摘要",
    "intro": "## 这篇论文要解决什么问题（引言精讲）",
    "conclusion": "## 结论：先看它最后说了什么",
    "related": "## 背景与相关工作",
    "method": "## 核心方法",
    "experiments": "## 实验说明了什么",
    "wrapup": "## 总评",
}

SUPPLEMENT_HEADERS: dict[str, str] = {
    "background": "📖 **补充知识（非论文原文）**",
    "analogy": "💡 **打个比方（非论文原文）**",
}

#: FR-026 固定缺失占位（带位置时由 _missing_blockquote 填充括号内容）
MISSING_PLACEHOLDER = "原文该位置{where}解析缺失，未编造内容补全。"

_FOLD_SUMMARY_PREFIX = {
    "derivation": "推导与证明：{title}（点击展开）",
    "repro_data": "复现参考：{title}（点击展开）",
}

_IMAGE_LINK_RE = re.compile(r"!\[(?P<alt>.*?)\]\((?P<target>[^)]+)\)")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_NUMERIC_SECTION_RE = re.compile(r"^sec-(\d(?:-\d+)*)$")


# ---------------------------------------------------------------------------
# 纯渲染函数
# ---------------------------------------------------------------------------


def slugify_title(title: str) -> str:
    """英文标题 kebab-case 截 60 字符（output-markdown §8）。"""
    slug = _SLUG_RE.sub("-", str(title).strip().lower()).strip("-")
    return (slug[:60].rstrip("-")) or "paper"


def _missing_blockquote(where: str = "") -> str:
    inner = MISSING_PLACEHOLDER.format(where=f"（{where}）" if where else "")
    return f"> ⚠️ {inner}"


def section_label(section: dict[str, Any]) -> str:
    """sec-3-2 → §3.2；非数字 id 回退为节标题。"""
    sid = section.get("id", "")
    match = _NUMERIC_SECTION_RE.match(sid)
    if match:
        return "§" + match.group(1).replace("-", ".")
    heading = section.get("heading") or sid
    return f"§{heading}"


def build_trace_index(
    normalized: dict[str, Any],
) -> tuple[dict[str, str], dict[str, int | None]]:
    """block_id → 章节显示标签；block_id → 0 基页码。"""
    owner: dict[str, str] = {}
    sections = {s["id"]: s for s in normalized.get("sections", [])}
    for section in normalized.get("sections", []):
        for bid in section.get("blocks", []):
            owner.setdefault(bid, section_label(section))
    pages = {
        b["id"]: b.get("page_idx")
        for b in normalized.get("blocks", [])
        if isinstance(b, dict) and "id" in b
    }
    return owner, pages


def _trace_parts(
    source_block_ids: list[str],
    owner: dict[str, str],
    pages: dict[str, int | None],
) -> list[str]:
    parts: list[str] = []
    seen: set[str] = set()
    for bid in source_block_ids:
        label = owner.get(bid)
        if label is None:
            continue
        page = pages.get(bid)
        page_part = f"，p.{page + 1}" if isinstance(page, int) else ""
        part = f"{label}{page_part}"
        if part not in seen:
            seen.add(part)
            parts.append(part)
    return parts


def render_trace(source_block_ids: list[str], owner: dict[str, str],
                 pages: dict[str, int | None]) -> str:
    """``*（原文 §3.2，p.4；§3.3，p.5）*`，按来源去重保序。"""
    parts = _trace_parts(source_block_ids, owner, pages)
    if not parts:
        return ""
    return "*（原文 " + "；".join(parts) + "）*"


def render_supplement(block: dict[str, Any]) -> str:
    header = SUPPLEMENT_HEADERS.get(
        block.get("supplement_kind"), SUPPLEMENT_HEADERS["background"]
    )
    lines = [f"> {header}", ">"]
    for line in str(block.get("markdown", "")).splitlines() or [""]:
        lines.append(f"> {line}".rstrip())
    return "\n".join(lines)


def _format_matrix(matrix: list[list[float]]) -> str:
    return "\n".join(
        "[" + ", ".join(f"{float(x):.4f}".rstrip("0").rstrip(".") for x in row) + "]"
        for row in matrix
    )


def render_worked_example(example: dict[str, Any]) -> str:
    """verification=passed 的手算示例（output-markdown §3）。"""
    setup = example.get("setup", {})
    tokens = setup.get("tokens", [])
    d_k = setup.get("d_k")
    lines = [
        "**手算小例子（3 个词走一遍）**",
        "",
        f"> ⚠️ {TOY_DISCLAIMER}。输入设定："
        + " / ".join(str(t) for t in tokens)
        + f"，d_k = {d_k}。",
        "",
    ]
    for idx, step in enumerate(example.get("steps", []), start=1):
        lines.append(
            f"{idx}. **{step.get('name', '')}**"
            f"（`{step.get('expr', '')}`）：{step.get('explain_zh', '')}"
        )
        matrix = step.get("matrix")
        if isinstance(matrix, list) and matrix:
            lines.append("")
            lines.append("```")
            lines.append(_format_matrix(matrix))
            lines.append("```")
        lines.append("")
    result = example.get("result")
    if isinstance(result, list) and result:
        lines.extend(["**最终输出**", "", "```", _format_matrix(result), "```"])
    return "\n".join(lines).rstrip()


def render_missing_worked_example(formula_block_id: str, verification: dict) -> str:
    diffs = verification.get("diffs") if isinstance(verification, dict) else None
    text = _missing_blockquote(f"手算示例（公式块 {formula_block_id}）")
    lines = [
        "**手算小例子（3 个词走一遍）**",
        "",
        text,
        "",
        "> 该算例经 numpy 独立复算未通过，已重试并显式标记缺失，未编造/放行错误算例。",
    ]
    if diffs:
        lines.extend([">", "> 复算差异（调试用）："])
        for diff in diffs[:5]:
            lines.append(f"> - {diff}")
    return "\n".join(lines)


def render_fold(
    group: str,
    blocks: list[dict[str, Any]],
    references: list[str] | None = None,
) -> str:
    """把同组 fold_ref 渲染成一个或多个 <details>（references 合并为一个）。

    references 折叠的内容以 normalized.references 解析原文为唯一权威来源
    （FR-022/023：完整、无缺失、不采信 LLM 转述），故 ``content_markdown``
    不参与该组渲染。
    """
    pieces: list[str] = []
    if group == "references":
        entries = [str(r).strip() for r in (references or []) if str(r).strip()]
        content = "\n\n".join(entries)
        summary = f"参考文献（共 {len(entries)} 条，点击展开）"
        pieces.append(
            "<details>\n<summary>" + summary + "</summary>\n\n"
            + content + "\n\n</details>"
        )
        return "\n\n".join(pieces)

    template = _FOLD_SUMMARY_PREFIX[group]
    for block in blocks:
        title = str(block.get("title", "")).strip() or "未命名折叠区"
        summary = template.format(title=title)
        content = str(block.get("content_markdown", "")).strip()
        pieces.append(
            "<details>\n<summary>" + summary + "</summary>\n\n"
            + content + "\n\n</details>"
        )
    return "\n\n".join(pieces)


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{path.name} 无法读取/解析：{path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise InputError(f"{path.name} 根结构不是对象：{path}")
    return data


def _load_sections(run_dir: Path) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    sections_root = run_dir / "sections"
    for slug in SLUG_ORDER:
        path = sections_root / f"{slug}.json"
        if not path.is_file():
            raise InputError(
                f"缺少转写章节 {path.name}：{path}；请先完成 rewrite 阶段。"
            )
        data = _read_json(path)
        if data.get("target_slug") != slug:
            raise InputError(f"{path} 的 target_slug={data.get('target_slug')!r}，与文件名不一致")
        if not isinstance(data.get("blocks"), list):
            raise InputError(f"{path} 缺少 blocks 数组")
        sections[slug] = data
    return sections


def _load_worked_example(run_dir: Path) -> dict[str, Any]:
    path = run_dir / WORKED_EXAMPLE_FILENAME
    if not path.is_file():
        raise InputError(
            f"缺少 {WORKED_EXAMPLE_FILENAME}：{path}；请先完成 worked_example 阶段。"
        )
    return _read_json(path)


def _header(normalized: dict[str, Any]) -> str:
    title = normalized.get("title") or normalized.get("paper_id") or "未命名论文"
    source = normalized.get("source", {}) if isinstance(
        normalized.get("source"), dict
    ) else {}
    authors = normalized.get("authors") or []
    author_text = "、".join(str(a) for a in authors) if authors else "作者信息未解析"
    if source.get("kind") == "arxiv" and source.get("ref"):
        ref_text = f"https://arxiv.org/abs/{source['ref']}"
    elif source.get("pdf_path"):
        ref_text = Path(str(source["pdf_path"])).name
    else:
        ref_text = "原文链接未解析"
    pages = [
        b.get("page_idx") for b in normalized.get("blocks", [])
        if isinstance(b, dict) and isinstance(b.get("page_idx"), int)
    ]
    page_count = max(pages) + 1 if pages else None
    page_text = f"共 {page_count} 页" if page_count else "页数未知"
    return (
        f"# {title}\n\n"
        f"> 作者：{author_text} ｜ 原文：{ref_text} ｜ {page_text}"
    )


def _copy_image(src: Path, dst: Path) -> None:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    except OSError as exc:
        raise LocalIOError(f"图片拷贝失败：{src} → {dst}（{exc}）") from exc


def _local_missing_figure(block: dict[str, Any], where: str) -> str:
    return _missing_blockquote(where)


def render_blocks(
    slug: str,
    blocks: list[dict[str, Any]],
    *,
    normalized: dict[str, Any],
    plan: dict[str, Any],
    worked_example: dict[str, Any],
    run_dir: Path,
    copied_images: set[str],
    owner: dict[str, str],
    pages: dict[str, int | None],
) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    """渲染单个七段的块；返回（正文行, 抽出的 fold_ref 分桶）。"""
    out: list[str] = []
    folds: dict[str, list[dict[str, Any]]] = {
        "derivation": [], "repro_data": [], "references": []
    }
    dist_images = run_dir / DIST_DIRNAME / IMAGES_DIRNAME
    parsed_dir = run_dir / "parsed"

    formula_labels = {
        kf.get("block_id"): kf.get("label")
        for kf in plan.get("key_formulas", [])
        if isinstance(kf, dict)
    }

    for block in blocks:
        kind = block.get("kind")
        sources = list(block.get("source_block_ids", []))
        trace = render_trace(sources, owner, pages)
        where = "；".join(_trace_parts(sources, owner, pages))

        if kind == "fold_ref":
            group = block.get("fold_group")
            if group in folds:
                folds[group].append(block)
            continue

        if kind == "supplement":
            out.append(render_supplement(block))
            continue

        if kind in ("paragraph", "tldr"):
            out.append(str(block.get("markdown", "")).strip())
            if trace:
                out.extend(["", trace])
            continue

        if kind == "table_explained":
            out.append(str(block.get("markdown_table", "")).strip())
            explanation = str(block.get("explanation", "")).strip()
            if explanation:
                out.extend(["", explanation])
            if trace:
                out.extend(["", trace])
            continue

        if kind == "figure":
            rel_path = str(block.get("img_path", "")).replace("\\", "/")
            src_path = parsed_dir / rel_path
            if rel_path and src_path.is_file():
                dst_path = dist_images / Path(rel_path).name
                _copy_image(src_path, dst_path)
                copied_images.add(Path(rel_path).name)
                caption = str(block.get("rewritten_caption", "")).strip()
                out.append(f"![{caption}]({IMAGES_DIRNAME}/{Path(rel_path).name})")
                caption_body = str(block.get("markdown", "")).strip()
                body = caption_body or caption
                if body:
                    out.extend(["", f"**图解**：{body}"])
                if trace:
                    out.extend(["", trace])
            else:
                out.append(_local_missing_figure(block, where))
            continue

        if kind == "formula_card":
            title = str(block.get("markdown", "")).strip()
            if not title:
                label = None
                for bid in sources:
                    label = formula_labels.get(bid)
                    if label:
                        break
                title = str(label or "关键公式").strip()
            out.append(f"### {title}")
            out.extend(["", "$$", str(block.get("latex", "")).strip(), "$$", ""])
            breakdown = block.get("symbol_breakdown") or []
            if breakdown:
                out.append("**每个符号是什么意思**")
                out.append("")
                for item in breakdown:
                    out.append(f"- {item.get('symbol', '')}：{item.get('meaning', '')}")
                out.append("")
            intuition = str(block.get("intuition", "")).strip()
            if intuition:
                out.extend(["**直觉讲解**", "", intuition, ""])
            if block.get("worked_example_ref") == WORKED_EXAMPLE_ID:
                verification = worked_example.get("verification", {})
                status = verification.get("status") if isinstance(
                    verification, dict
                ) else None
                if status == "passed":
                    out.extend([render_worked_example(worked_example), ""])
                else:
                    out.extend([
                        render_missing_worked_example(
                            str(worked_example.get("formula_block_id", "")),
                            verification if isinstance(verification, dict) else {},
                        ),
                        "",
                    ])
            if trace:
                out.extend([trace, ""])
            continue

        # 未知 kind：rewrite 校验已拦截，这里防御性报错而非静默丢内容
        raise InputError(f"sections/{slug}.json 含未知块 kind={kind!r}")

    return out, folds


def _render_parse_gap_notices(
    slug: str,
    *,
    plan: dict[str, Any],
    normalized: dict[str, Any],
    owner: dict[str, str],
    pages: dict[str, int | None],
) -> list[str]:
    """收集本目标段源节中进主线（narrate/condense）的 parse_gap 块。

    FR-026（T037）：解析缺失说明不经过 LLM，由组装层按 planner 决策
    确定性渲染，按 normalized 原文顺序追加在该段正文之后，位置取自
    溯源索引（§节，p.页），缺失内容绝不编造。
    """
    entry = next(
        (e for e in plan.get("target_order", [])
         if isinstance(e, dict) and e.get("target_slug") == slug),
        None,
    )
    if entry is None:
        return []
    sections_by_id = {
        s["id"]: s for s in normalized.get("sections", []) if isinstance(s, dict)
    }
    real_sids = set(
        resolve_real_section_ids(
            list(entry.get("source_section_ids", []) or []), sections_by_id
        )
    )
    sid_owner: dict[str, str] = {}
    for section in normalized.get("sections", []):
        for bid in section.get("blocks", []):
            sid_owner.setdefault(bid, section["id"])
    actions = plan.get("block_actions", {})

    lines: list[str] = []
    for block in normalized.get("blocks", []):
        if not isinstance(block, dict) or block.get("type") != "parse_gap":
            continue
        bid = block.get("id")
        if sid_owner.get(bid) not in real_sids:
            continue
        decision = actions.get(bid) if isinstance(actions, dict) else None
        if not isinstance(decision, dict) or decision.get("action") not in (
            "narrate", "condense"
        ):
            continue
        where = "；".join(_trace_parts([bid], owner, pages))
        suffix = f"（{where}）" if where else ""
        lines.append(
            f"> ⚠️ {str(block.get('placeholder', '')).strip()}{suffix}"
        )
    return lines


def _copy_fold_images(content: str, *, run_dir: Path, copied: set[str]) -> str:
    """拷贝折叠内容引用的图片（如训练曲线图）；源缺失则替换为固定占位。"""
    dist_images = run_dir / DIST_DIRNAME / IMAGES_DIRNAME
    parsed_dir = run_dir / "parsed"

    def replace(match: re.Match) -> str:
        target = match.group("target").strip()
        name = Path(target.replace("\\", "/")).name
        src = parsed_dir / IMAGES_DIRNAME / name
        if src.is_file():
            _copy_image(src, dist_images / name)
            copied.add(name)
            return f"![{match.group('alt')}]({IMAGES_DIRNAME}/{name})"
        return _missing_blockquote("折叠区图片")

    return _IMAGE_LINK_RE.sub(replace, content)


def _write_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise LocalIOError(f"成稿写入失败：{path}（{exc}）") from exc


def assemble_run(run_dir: Path | str) -> dict[str, Any]:
    """组装 dist/<slug>.md + dist/images/ + out_of_glossary.json。"""
    run_dir = Path(run_dir)
    normalized = load_normalized(run_dir)
    plan = load_plan(run_dir)
    section_docs = _load_sections(run_dir)
    worked_example = _load_worked_example(run_dir)

    owner, pages = build_trace_index(normalized)
    copied_images: set[str] = set()

    body_lines: list[str] = [_header(normalized), ""]
    fold_buckets: dict[str, list[dict[str, Any]]] = {
        "derivation": [], "repro_data": [], "references": []
    }

    collected_oog: list[dict[str, Any]] = []
    for slug in SLUG_ORDER:
        doc = section_docs[slug]
        body_lines.append(SECTION_HEADINGS[slug])
        body_lines.append("")
        lines, folds = render_blocks(
            slug, doc.get("blocks", []),
            normalized=normalized, plan=plan, worked_example=worked_example,
            run_dir=run_dir, copied_images=copied_images, owner=owner, pages=pages,
        )
        body_lines.extend(lines)
        # FR-026（T037）：parse_gap 固定缺失说明确定性注入段末
        gap_notices = _render_parse_gap_notices(
            slug, plan=plan, normalized=normalized, owner=owner, pages=pages
        )
        if gap_notices:
            body_lines.extend(gap_notices)
        body_lines.append("")
        for group, bucket in fold_buckets.items():
            bucket.extend(folds[group])
        for item in doc.get("out_of_glossary", []):
            entry = dict(item)
            entry["section"] = slug
            collected_oog.append(entry)

    # 折叠区统一沉到文末
    fold_zone: list[str] = []
    references = [
        str(r).strip()
        for r in normalized.get("references", [])
        if str(r).strip()
    ]
    for group in ("derivation", "repro_data", "references"):
        blocks = fold_buckets[group]
        if group == "references":
            # FR-022/023：解析出参考文献就必须有折叠区承接，杜绝静默缺失
            if references and not blocks:
                raise InputError(
                    f"normalized 解析出 {len(references)} 条参考文献，"
                    "但七段转写未产出 references 折叠块"
                    "（planner 须把 reference_list 块 fold 到 references，FR-022/023）"
                )
            if not references:
                continue
        if not blocks:
            continue
        if group != "references":
            for block in blocks:
                if "content_markdown" in block:
                    block["content_markdown"] = _copy_fold_images(
                        str(block.get("content_markdown", "")),
                        run_dir=run_dir, copied=copied_images,
                    )
        fold_zone.append(render_fold(group, blocks, references))
    if fold_zone:
        body_lines.extend(["---", "", "\n\n".join(fold_zone), ""])

    markdown = "\n".join(body_lines).rstrip() + "\n"

    # 先落附属产物，成稿 Markdown 最后写出：任何前置步骤失败都不得留下
    # "看似成功"的完整成稿（T036）
    oog_records = merge_out_of_glossary(collected_oog)
    oog_path = run_dir / OUT_OF_GLOSSARY_FILENAME
    try:
        oog_path.write_text(
            json.dumps(oog_records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        raise LocalIOError(f"out_of_glossary.json 写入失败：{oog_path}（{exc}）") from exc

    dist_dir = run_dir / DIST_DIRNAME
    file_slug = slugify_title(normalized.get("title") or normalized.get("paper_id") or "paper")
    md_path = dist_dir / f"{file_slug}.md"
    _write_text(md_path, markdown)

    logger.info(
        "成稿已组装：%s（图片 %d 张，表外词 %d 条）",
        md_path, len(copied_images), len(oog_records),
    )
    return {
        "markdown_path": md_path,
        "images": sorted(copied_images),
        "out_of_glossary_path": oog_path,
        "out_of_glossary": oog_records,
        "worked_example_status": (
            worked_example.get("verification", {}).get("status")
            if isinstance(worked_example.get("verification"), dict) else None
        ),
    }


def rename_for_qa(markdown_path: Path | str, *, passed: bool) -> Path:
    """QA failed 时把成稿改名为 ``.unchecked.md``；passed 时保持/恢复规范名。"""
    path = Path(markdown_path)
    name = path.name
    if name.endswith(".unchecked.md"):
        canonical = path.with_name(name[: -len(".unchecked.md")] + ".md")
    else:
        canonical = path
    if passed:
        if path != canonical and path.exists():
            path.rename(canonical)
        return canonical
    unchecked = canonical.with_name(canonical.name[:-3] + ".unchecked.md")
    if canonical.exists():
        canonical.rename(unchecked)
    return unchecked
