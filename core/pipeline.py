"""全流程编排：ingest → parse → normalize → plan → rewrite → assemble。
核心层对外暴露的主入口。支持进度回调和 fake 模式（不调用真实 API）。
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .errors import ETError, InputError
from .ingest import ingest, SourceInfo
from .mineru_client import MinerUClient
from .normalize import normalize_content_list, load_content_list
from .planner import build_plan, SLUG_ORDER, SECTION_HEADINGS, EMPTY_FALLBACK
from .rewrite import (
    chat,
    build_section_prompt,
    backfill_equations,
)
from .assemble import (
    build_fold_sections,
    copy_images,
    check_image_links,
    fix_image_links,
    demote_headings,
    render_source_notes,
)

# 取消异常的固定消息（errors.py 冻结，不在此新增异常类，两处模块各自引用该字面量）
_CANCEL_MESSAGE = "任务已被用户取消"


def _check_cancel(event: threading.Event | None) -> None:
    """阶段边界取消检查：event 为 None 时直接返回；置位则抛 ETError。"""
    if event is not None and event.is_set():
        raise ETError(_CANCEL_MESSAGE)


ProgressCallback = Callable[[str, float, str], None]  # (阶段, 0~1进度, 描述)


@dataclass
class PaperResult:
    """转写结果。"""
    paper_id: str
    title: str
    sections: list[dict[str, str]] = field(default_factory=list)  # [{slug, heading, content}]
    full_markdown: str = ""
    images_dir: Path | None = None
    image_files: list[str] = field(default_factory=list)
    total_chars: int = 0
    dead_links: list[str] = field(default_factory=list)


def transform_paper(
    source: str | Path,
    *,
    output_dir: str | Path | None = None,
    mineru_api_key: str | None = None,
    deepseek_api_key: str | None = None,
    mineru_verify_ssl: bool = True,
    fake: bool = False,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> PaperResult:
    """全流程：从 PDF/arXiv 链接到最终 Markdown。

    Args:
        source: 本地 PDF 路径或 arXiv URL
        output_dir: 输出根目录（默认 runs/<paper_id>/）
        mineru_api_key: MinerU API Key（fake 模式不需要）
        deepseek_api_key: DeepSeek API Key（fake 模式不需要）
        fake: 假模式，不调用真实 API（用于测试流程）
        progress_cb: 进度回调 (stage, progress_0_1, message)
    """
    def _progress(stage: str, p: float, msg: str = "") -> None:
        if progress_cb:
            progress_cb(stage, p, msg)

    # ── 1. Ingest ──────────────────────────────────────────────
    _progress("ingest", 0.0, "摄取论文源...")
    src_info = ingest(source)
    paper_id = src_info.paper_id

    # 确定输出目录
    if output_dir:
        run_dir = Path(output_dir)
    else:
        run_dir = Path("runs") / paper_id
    parsed_dir = run_dir / "parsed"
    images_dir = run_dir / "images"

    _progress("ingest", 0.05, f"paper_id={paper_id}")

    # ── 2. Parse ───────────────────────────────────────────────
    # 取消检查点①：ingest 之后、parse 之前（含长轮询前的最后一道）
    _check_cancel(cancel_event)
    _progress("parse", 0.1, "调用 MinerU 解析...")
    if not fake and not mineru_api_key:
        raise InputError("mineru_api_key 未设置（fake 模式可省略）")

    if not parsed_dir.exists() or not any(parsed_dir.glob("*content_list*")):
        if fake:
            # fake 模式下如果没有已解析产物，报错提示
            raise InputError(
                f"fake 模式下需要已有的解析产物，但在 {parsed_dir} 未找到。"
                "请先真实解析一次，或提供已解析的 parsed/ 目录。"
            )
        client = MinerUClient(
            api_key=mineru_api_key,
            cancel_event=cancel_event,
            verify_ssl=mineru_verify_ssl,
        )
        client.parse(src_info.pdf_path, parsed_dir)
        _progress("parse", 0.25, "MinerU 解析完成")
    else:
        _progress("parse", 0.25, "复用已有解析产物")

    # ── 3. Normalize ───────────────────────────────────────────
    # 取消检查点②：parse 之后、normalize 之前
    _check_cancel(cancel_event)
    _progress("normalize", 0.25, "归一化内容块...")
    content_list = load_content_list(parsed_dir)
    norm = normalize_content_list(
        content_list,
        paper_id=paper_id,
        source={"kind": src_info.kind, "ref": src_info.ref},
        parsed_dir=parsed_dir,
    )
    # 公式回填
    full_md = parsed_dir / "full.md"
    if full_md.exists():
        norm = backfill_equations(norm, full_md)
    title = norm.get("title", paper_id)
    _progress("normalize", 0.35, f"归一化完成：{len(norm['blocks'])} 块，{len(norm['sections'])} 节")

    # ── 4. Plan ────────────────────────────────────────────────
    # 取消检查点③：normalize 之后、plan 之前
    _check_cancel(cancel_event)
    _progress("plan", 0.35, "规划章节结构...")
    plan = build_plan(norm)
    _progress("plan", 0.4, "规划完成")

    # ── 5. Rewrite（逐章） ────────────────────────────────────
    section_contents: dict[str, str] = {}
    total_slugs = len(SLUG_ORDER)
    empty_slugs = set(plan.get("empty_slugs", []))
    for i, slug in enumerate(SLUG_ORDER):
        # 取消检查点④：每章转写循环开头（rewrite 是最耗时阶段）
        _check_cancel(cancel_event)
        progress_start = 0.4 + (i / total_slugs) * 0.5
        _progress("rewrite", progress_start, f"转写 {slug} ({i+1}/{total_slugs})...")

        if slug in empty_slugs:
            # 空章不调 LLM、不构造 prompt，直接填固定文案
            section_contents[slug] = EMPTY_FALLBACK[slug]
            continue

        system_prompt, user_prompt = build_section_prompt(norm, plan, slug)
        content = chat(
            system_prompt,
            user_prompt,
            api_key=deepseek_api_key,
            fake=fake,
        )
        section_contents[slug] = content

    _progress("rewrite", 0.9, "全部章节转写完成")

    # ── 6. Assemble ───────────────────────────────────────────
    # 取消检查点⑤：rewrite 之后、assemble 之前
    _check_cancel(cancel_event)
    _progress("assemble", 0.9, "拼装最终 Markdown...")

    # 拷贝图片
    parsed_images = parsed_dir / "images"
    if parsed_images.exists():
        image_files = copy_images(norm, plan, parsed_dir, images_dir)
    else:
        images_dir.mkdir(parents=True, exist_ok=True)
        image_files = []

    # 拼装
    parts = [f"# {title}\n"]
    total_demoted = 0
    sections_output = []
    for slug in SLUG_ORDER:
        heading = SECTION_HEADINGS[slug]
        parts.append(f"\n{heading}\n\n")
        raw_content = section_contents[slug]
        if slug not in empty_slugs and not raw_content.strip():
            print(f"[warn] 章节 {slug} 的 LLM 返回为空，退回固定文案。")
            raw_content = EMPTY_FALLBACK[slug]
        content, demoted = demote_headings(raw_content)
        total_demoted += demoted
        content = render_source_notes(content)
        parts.append(content)
        parts.append("\n")
        sections_output.append({
            "slug": slug,
            "heading": heading.lstrip("# ").strip(),
            "content": content,
        })

    # 折叠区
    parts.append(build_fold_sections(norm, plan))

    full_md = "".join(parts)

    # 图片死链修复
    full_md, _ = fix_image_links(full_md, images_dir)

    # 死链检查
    dead = check_image_links(full_md, images_dir)

    _progress("assemble", 1.0, f"完成：{len(full_md)} 字符")

    return PaperResult(
        paper_id=paper_id,
        title=title,
        sections=sections_output,
        full_markdown=full_md,
        images_dir=images_dir,
        image_files=image_files,
        total_chars=len(full_md),
        dead_links=dead,
    )
