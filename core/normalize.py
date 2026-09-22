"""归一化：MinerU content_list.json → normalized 文档 dict。
从 legacy 版精简而来：砍掉过度校验（块重复/多节归属等极端情况不校验），
砍掉细粒度错误分类，保留 parse_gap 降级机制和纯扫描件检测。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import InputError, ServiceError


MISSING_IMAGE_PLACEHOLDER = "原文图片解析缺失，未编造内容补全。"
PARSE_GAP_TYPE = "parse_gap"

_GAP_KIND_LABEL: dict[str, str] = {
    "equation": "公式",
    "table": "表格",
    "text": "段落",
    "heading": "段落",
}

_TEXT_BEARING_TYPES = {
    "paragraph", "heading", "equation", "table", "reference_list",
}

_NOISE_KINDS = {
    "page_number": "page_number",
    "page_footnote": "page_footnote",
    "footer": "footer",
    "aside_text": "aside",
}

_HEADING_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?(?:\s+(.*\S))?\s*$")
_TAG_RE = re.compile(r"\\tag\*?\s*\{((?:[^{}]|\{[^{}]*\})*)\}")
_EQUATION_IMAGE_RE = re.compile(r"!\s*\[|\.(?:jpg|jpeg|png|gif|svg|webp)\b", re.IGNORECASE)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def load_content_list(parsed_dir: str | Path) -> list[dict[str, Any]]:
    """定位并读取 content_list.json（可带 batch_id 前缀）。"""
    parsed_dir = Path(parsed_dir)
    candidates = [parsed_dir / "content_list.json"]
    candidates.extend(
        p
        for p in sorted(parsed_dir.glob("*_content_list.json"))
        if not p.name.endswith("_v2_content_list.json")
    )
    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ServiceError(f"content_list.json 无法读取：{path}（{exc}）") from exc
            if not isinstance(data, list):
                raise ServiceError(f"content_list.json 根不是数组：{path}")
            return data
    raise ServiceError(f"未找到 content_list.json：{parsed_dir}")


def normalize_content_list(
    content_list: list[dict[str, Any]],
    *,
    paper_id: str,
    source: dict[str, str] | None = None,
    parsed_dir: str | Path | None = None,
) -> dict[str, Any]:
    """把 MinerU content_list 数组转为 normalized 文档 dict。"""
    parsed_dir = Path(parsed_dir) if parsed_dir is not None else None

    blocks: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    references: list[str] = []
    title: str | None = None

    used_section_ids: set[str] = set()
    current_section: dict[str, Any] | None = None
    ref_buffer: list[tuple[int, str, int | None]] = []  # (起始下标, text, page_idx)

    def flush_refs() -> None:
        nonlocal ref_buffer
        if not ref_buffer:
            return
        first_idx, _, first_page = ref_buffer[0]
        items = [text for _, text, _ in ref_buffer]
        block = {
            "id": f"blk-{first_idx:04d}",
            "type": "reference_list",
            "page_idx": first_page,
            "text_level": None,
            "raw_type": "ref_text",
            "items": items,
            "text": "\n".join(items),
        }
        blocks.append(block)
        references.extend(items)
        if current_section is not None:
            current_section["blocks"].append(block["id"])
        ref_buffer = []

    for idx, raw in enumerate(content_list):
        if not isinstance(raw, dict):
            # 坏条目直接当 gap，不拖垮整篇
            block = _build_gap(idx, "unknown", None, "text", f"content_list[{idx}] 不是对象")
            blocks.append(block)
            if current_section is not None:
                current_section["blocks"].append(block["id"])
            continue

        raw_type = raw.get("type")
        page_idx = raw.get("page_idx")

        # ref_text 先入缓冲，遇非 ref 块再聚合
        if raw_type == "ref_text":
            text = raw.get("text", "")
            if not isinstance(text, str) or not text.strip():
                flush_refs()
                block = _build_gap(idx, raw_type, page_idx, "text", "ref_text 为空")
                blocks.append(block)
                if current_section is not None:
                    current_section["blocks"].append(block["id"])
                continue
            ref_buffer.append((idx, text, page_idx))
            continue
        flush_refs()

        if raw_type == "text":
            text_level = raw.get("text_level")
            if text_level:
                # 标题块
                text = raw.get("text", "")
                try:
                    level = int(text_level)
                except (TypeError, ValueError):
                    block = _build_gap(idx, raw_type, page_idx, "heading", f"非法 text_level={text_level!r}")
                else:
                    block = _base_block(idx, "heading", "text", page_idx, level)
                    block["text"] = text
                    block["level"] = level

                if block["type"] == PARSE_GAP_TYPE:
                    blocks.append(block)
                elif int(text_level) <= 1:
                    # 文档标题，不属于任何节
                    blocks.append(block)
                    title = block["text"]
                else:
                    blocks.append(block)
                    section = _build_section_from_heading(block, used_section_ids)
                    sections.append(section)
                    current_section = section
                    section["blocks"].append(block["id"])
                if block["type"] == PARSE_GAP_TYPE and current_section is not None:
                    current_section["blocks"].append(block["id"])
                continue

            # 普通段落
            block = _base_block(idx, "paragraph", "text", page_idx, text_level=None)
            text = raw.get("text", "")
            if isinstance(text, str) and text.strip():
                block["text"] = text
            else:
                block = _build_gap(idx, raw_type, page_idx, "text", "段落 text 为空")
            blocks.append(block)

        elif raw_type == "equation":
            raw_text = raw.get("text", "")
            latex, number = _strip_equation(raw_text)
            if not latex or _EQUATION_IMAGE_RE.search(latex):
                block = _build_gap(idx, raw_type, page_idx, "equation",
                                   "公式为空或含图片引用" if not latex else "公式含图片引用")
            else:
                block = _base_block(idx, "equation", "equation", page_idx, None)
                block["latex"] = latex
                block["text"] = latex  # 冗余一份 text，方便下游直接用
                if number is not None:
                    block["number"] = number
            blocks.append(block)

        elif raw_type == "table":
            html = raw.get("table_body")
            if not isinstance(html, str) or not html.strip():
                block = _build_gap(idx, raw_type, page_idx, "table", "table 缺少 table_body")
            else:
                block = _base_block(idx, "table", "table", page_idx, None)
                block["html"] = html
                block["text"] = _join_lines(raw.get("table_caption"))  # 摘要用 caption
                block["caption"] = _join_lines(raw.get("table_caption"))
                block["footnote"] = _join_lines(raw.get("table_footnote"))
            blocks.append(block)

        elif raw_type in ("image", "chart"):
            rel_path = raw.get("img_path")
            if not isinstance(rel_path, str) or not rel_path.strip():
                block = _build_gap(idx, raw_type, page_idx, "text", f"{raw_type} 缺少 img_path")
            else:
                block = _base_block(idx, "figure", raw_type, page_idx, None)
                caption_key = "image_caption" if raw_type == "image" else "chart_caption"
                block["caption"] = _join_lines(raw.get(caption_key))
                block["text"] = block["caption"]  # 摘要用
                block["mineru_type"] = raw_type
                exists = parsed_dir is not None and (parsed_dir / rel_path).is_file()
                if parsed_dir is None or exists:
                    block["img_path"] = rel_path
                else:
                    block["img_path"] = None
                    block["expected_img_path"] = rel_path
                    block["image_missing"] = True
                    block["placeholder"] = MISSING_IMAGE_PLACEHOLDER
            blocks.append(block)

        elif raw_type in _NOISE_KINDS:
            block = _base_block(idx, "noise", raw_type, page_idx, text_level=None)
            block["text"] = str(raw.get("text", ""))
            block["noise_kind"] = _NOISE_KINDS[raw_type]
            blocks.append(block)

        else:
            # 未知类型：有文本按 paragraph 保留，否则记为 noise
            text = raw.get("text")
            if isinstance(text, str) and text.strip():
                block = _base_block(idx, "paragraph", str(raw_type or "unknown"), page_idx, None)
                block["text"] = text
            else:
                block = _base_block(idx, "noise", str(raw_type or "unknown"), page_idx, None)
                block["text"] = ""
                block["noise_kind"] = "aside"
            blocks.append(block)

        # 归属到当前节（noise 与标题块除外；reference_list 已在 flush 时归属）
        if block["type"] not in ("noise", "heading", "reference_list") and current_section is not None:
            current_section["blocks"].append(block["id"])

    flush_refs()

    doc = {
        "paper_id": paper_id,
        "source": source or {},
        "title": title or paper_id,
        "authors": [],
        "sections": sections,
        "blocks": blocks,
        "references": references,
        "appendix_section_ids": [s["id"] for s in sections if s.get("kind") == "appendix"],
    }
    _require_text_layer(doc)
    return doc


# ---------------------------------------------------------------------------
# 内部函数
# ---------------------------------------------------------------------------


def _require_text_layer(doc: dict[str, Any]) -> None:
    """纯扫描件检测：没有任何可提取文本块就报错。"""
    text_blocks = [
        b for b in doc.get("blocks", [])
        if isinstance(b, dict) and b.get("type") in _TEXT_BEARING_TYPES
    ]
    if not text_blocks:
        gap_count = sum(
            1 for b in doc.get("blocks", [])
            if isinstance(b, dict) and b.get("type") == PARSE_GAP_TYPE
        )
        detail = f"（另有 {gap_count} 个条目解析降级为占位）" if gap_count else ""
        raise InputError(
            "解析结果中没有任何可提取的文本内容"
            f"{detail}：该 PDF 疑似纯扫描图片件、缺少文本层。"
        )


def _base_block(
    idx: int,
    block_type: str,
    raw_type: str,
    page_idx: int | None,
    text_level: int | None,
) -> dict[str, Any]:
    return {
        "id": f"blk-{idx:04d}",
        "type": block_type,
        "page_idx": page_idx,
        "text_level": text_level,
        "raw_type": raw_type,
    }


def _build_gap(
    idx: int,
    raw_type: Any,
    page_idx: int | None,
    gap_kind: str,
    reason: str,
) -> dict[str, Any]:
    """单条原始内容构建失败时的降级占位块。"""
    label = _GAP_KIND_LABEL.get(gap_kind, "段落")
    block = _base_block(idx, PARSE_GAP_TYPE, str(raw_type or "unknown"), page_idx, None)
    block["gap_kind"] = gap_kind
    block["placeholder"] = f"原文{label}解析缺失，未编造内容补全。"
    block["text"] = block["placeholder"]
    block["reason"] = reason
    return block


def _build_section_from_heading(
    block: dict[str, Any], used_section_ids: set[str]
) -> dict[str, Any]:
    number, heading_text = _split_heading_number(block["text"])
    if number:
        section_id = "sec-" + number.replace(".", "-")
        section_level = number.count(".") + 1
    else:
        section_id = "sec-" + _slugify(heading_text)
        section_level = block.get("level", 2)
    section_id = _unique_id(section_id, used_section_ids)

    return {
        "id": section_id,
        "heading": heading_text,
        "level": section_level,
        "kind": _classify_kind(heading_text),
        "page_start": block.get("page_idx"),
        "blocks": [],
    }


def _strip_equation(raw_text: str) -> tuple[str, str | None]:
    """去掉 $$ 包裹，解析并移除 \\tag{}，返回 (latex, number|None)。"""
    text = raw_text.strip()
    if text.startswith("$$"):
        text = text[2:]
    if text.endswith("$$"):
        text = text[:-2]
    text = text.strip()

    number: str | None = None
    match = _TAG_RE.search(text)
    if match:
        number = match.group(1).strip()
        text = (text[: match.start()] + text[match.end() :]).strip()
    return text, number


def _split_heading_number(text: str) -> tuple[str | None, str]:
    match = _HEADING_NUMBER_RE.match(text.strip())
    if match and match.group(2):
        return match.group(1), match.group(2).strip()
    return None, text.strip()


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:60] or "section"


def _unique_id(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _join_lines(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _classify_kind(heading_text: str) -> str:
    """按标题关键词做初始 kind 判定；判不准写 unclassified。"""
    h = heading_text.lower()
    if "appendix" in h or "supplementary" in h:
        return "appendix"
    if "abstract" in h:
        return "abstract"
    if "introduction" in h:
        return "introduction"
    if "related work" in h or h == "background" or h.startswith("background"):
        return "related_work"
    if "conclusion" in h or "discussion" in h:
        return "conclusion"
    if "experiment" in h or "results" in h or "evaluation" in h:
        return "experiments"
    return "unclassified"
