"""规范化：MinerU content_list.json → normalized.json（T009，data-model.md §2）。

职责（research R2：MinerU 分类只当提示，保留/折叠由 planner 二次判定）：

- 章节树：MinerU ``text_level`` + 标题前导编号（3 / 3.2 / 3.2.1）构建带层级
  的扁平 sections 数组；``kind`` 只按标题关键词做初始判定，判不准写
  ``unclassified``，最终归属留给 planner。
- 类型化块：heading/paragraph/equation（去 ``$$``、解析 ``\\tag{}`` 编号）/
  table（html+caption+footnote）/ figure（mineru_type 原样保留为提示）/
  reference_list（连续 ref_text 聚合，同时进顶层 references）/
  noise（page_number/page_footnote/footer/aside）。
- 每块带稳定 id（``blk-<content_list 下标四位>``）与 page_idx；图片文件
  在 parsed/images/ 缺失时生成降级占位块（FR-026）。
- **单条内容降级（FR-026，T037）**：个别条目结构损坏（公式 latex 为空/
  夹带图片引用、表格缺 table_body、文本段为空等）不再拖垮整篇，转为
  ``parse_gap`` 占位块（带 gap_kind/reason/placeholder），成稿对应位置
  由组装层渲染固定缺失说明；仅整篇级结构损坏（非数组、非对象条目、
  块 id 重复、节引用悬空）才抛 :class:`~essay_transformer.errors.ParseServiceError`。
- **纯扫描件（FR-004 edge）**：解析产物中没有任何可提取文本块时抛
  :class:`~essay_transformer.errors.InputError`（退出码 3），明确提示
  MVP 不含 OCR，请改用带文本层的 PDF。
- 块 id 与 section 引用完整性校验，结构非法抛
  :class:`~essay_transformer.errors.ParseServiceError`。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .errors import InputError, ParseServiceError

__all__ = [
    "normalize_content_list",
    "normalize_run",
    "load_content_list",
    "MISSING_IMAGE_PLACEHOLDER",
    "PARSE_GAP_TYPE",
]

logger = logging.getLogger(__name__)

#: 图片缺失降级占位文案（FR-026；assemble 阶段渲染为固定 blockquote）
MISSING_IMAGE_PLACEHOLDER = "原文图片解析缺失，未编造内容补全。"

#: 单条解析失败的降级块类型（FR-026，T037）
PARSE_GAP_TYPE = "parse_gap"

#: parse_gap 种类 → 占位文案中的中文名
_GAP_KIND_LABEL: dict[str, str] = {
    "equation": "公式",
    "table": "表格",
    "text": "段落",
    "heading": "段落",
}

#: 承载论文文本的块类型；全部为 0 即判定为无文本层扫描件（FR-004）
_TEXT_BEARING_TYPES = {
    "paragraph", "heading", "equation", "table", "reference_list",
}

_NOISE_KINDS = {
    "page_number": "page_number",
    "page_footnote": "page_footnote",
    "footer": "footer",
    "aside_text": "aside",
}

#: 标题前导编号："3.2 Attention" / "7 Conclusion"
_HEADING_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?(?:\s+(.*\S))?\s*$")

#: \tag{1} / \tag*{A.2}，允许一层嵌套花括号
_TAG_RE = re.compile(r"\\tag\*?\s*\{((?:[^{}]|\{[^{}]*\})*)\}")

#: 公式中夹带图片引用的检测
_EQUATION_IMAGE_RE = re.compile(r"!\s*\[|\.(?:jpg|jpeg|png|gif|svg|webp)\b", re.IGNORECASE)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def load_content_list(parsed_dir: Path | str) -> tuple[Path, list[dict[str, Any]]]:
    """定位并读取 MinerU 产物中的 content_list.json（可带 batch_id 前缀）。"""
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
                raise ParseServiceError(f"content_list.json 无法读取/解析：{path}（{exc}）") from exc
            if not isinstance(data, list):
                raise ParseServiceError(f"content_list.json 根不是数组：{path}")
            return path, data
    raise ParseServiceError(f"未找到 content_list.json：{parsed_dir}")


def normalize_run(
    run_dir: Path | str,
    *,
    source: dict[str, str],
    paper_id: str | None = None,
) -> dict[str, Any]:
    """读取 ``runs/<id>/parsed/`` 并写出 ``runs/<id>/normalized.json``，返回文档。"""
    run_dir = Path(run_dir)
    pid = paper_id or run_dir.name
    parsed_dir = run_dir / "parsed"
    _, content_list = load_content_list(parsed_dir)
    doc = normalize_content_list(content_list, paper_id=pid, source=source, parsed_dir=parsed_dir)
    out_path = run_dir / "normalized.json"
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "normalized.json 已写出：%s（%d 块，%d 节，%d 条参考文献）",
        out_path,
        len(doc["blocks"]),
        len(doc["sections"]),
        len(doc["references"]),
    )
    return doc


def normalize_content_list(
    content_list: list[dict[str, Any]],
    *,
    paper_id: str,
    source: dict[str, str],
    parsed_dir: Path | str | None = None,
) -> dict[str, Any]:
    """把 MinerU content_list 数组转为 normalized 文档 dict（data-model.md §2）。"""
    parsed_dir = Path(parsed_dir) if parsed_dir is not None else None

    blocks: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    references: list[str] = []
    title: str | None = None

    used_section_ids: set[str] = set()
    current_section: dict[str, Any] | None = None
    # 连续 ref_text 聚合缓冲：(起始下标, items, page_idx)
    ref_buffer: list[tuple[int, str, int | None]] = []

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
        }
        blocks.append(block)
        references.extend(items)
        if current_section is not None:
            current_section["blocks"].append(block["id"])
        ref_buffer = []

    for idx, raw in enumerate(content_list):
        if not isinstance(raw, dict):
            raise ParseServiceError(f"content_list[{idx}] 不是对象：{raw!r}")
        raw_type = raw.get("type")
        page_idx = raw.get("page_idx")

        # ref_text 先入缓冲，遇非 ref 块再聚合
        if raw_type == "ref_text":
            try:
                text = _require_text(raw, idx)
            except ParseServiceError as exc:
                # FR-026：单条参考文献文本损坏不拖垮整篇（先冲刷已聚合缓冲）
                flush_refs()
                block = _build_gap(idx, raw_type, page_idx, "text", str(exc))
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
                try:
                    block, section = _build_heading(
                        raw, idx, text_level, used_section_ids
                    )
                except ParseServiceError as exc:
                    # FR-026：标题条目损坏 → 占位块，不开启新节
                    block = _build_gap(idx, raw_type, page_idx, "heading", str(exc))
                    blocks.append(block)
                else:
                    blocks.append(block)
                    if section is not None:
                        # 标题块属于它开启的节
                        section["blocks"].append(block["id"])
                        sections.append(section)
                        current_section = section
                    else:
                        # text_level=1：文档标题，不属于任何节
                        title = block["text"]
                # 标题失败生成的 parse_gap 与普通块一样在循环末尾归属当前节
                if block["type"] == PARSE_GAP_TYPE:
                    if current_section is not None:
                        current_section["blocks"].append(block["id"])
                continue
            block = _base_block(idx, "paragraph", "text", page_idx, text_level=None)
            try:
                block["text"] = _require_text(raw, idx)
            except ParseServiceError as exc:
                block = _build_gap(idx, raw_type, page_idx, "text", str(exc))
            blocks.append(block)

        elif raw_type == "equation":
            try:
                block = _build_equation(raw, idx, page_idx)
            except ParseServiceError as exc:
                block = _build_gap(idx, raw_type, page_idx, "equation", str(exc))
            blocks.append(block)

        elif raw_type == "table":
            try:
                block = _build_table(raw, idx, page_idx)
            except ParseServiceError as exc:
                block = _build_gap(idx, raw_type, page_idx, "table", str(exc))
            blocks.append(block)

        elif raw_type in ("image", "chart"):
            block = _build_figure(raw, idx, page_idx, raw_type, parsed_dir)
            blocks.append(block)

        elif raw_type in _NOISE_KINDS:
            block = _base_block(idx, "noise", raw_type, page_idx, text_level=None)
            block["text"] = str(raw.get("text", ""))
            block["noise_kind"] = _NOISE_KINDS[raw_type]
            blocks.append(block)

        else:
            # 未知类型不静默丢弃：有文本按正文保留，否则记为噪声，保证可审计
            text = raw.get("text")
            if isinstance(text, str) and text.strip():
                block = _base_block(
                    idx, "paragraph", str(raw_type or "unknown"), page_idx, None
                )
                block["text"] = text
                blocks.append(block)
                logger.warning("content_list[%d] 未知类型 %r，按 paragraph 保留", idx, raw_type)
            else:
                block = _base_block(idx, "noise", str(raw_type or "unknown"), page_idx, None)
                block["text"] = ""
                block["noise_kind"] = "aside"
                blocks.append(block)
                logger.warning("content_list[%d] 未知类型 %r，按 noise 保留", idx, raw_type)

        # 归属到当前节（noise 与标题块除外；reference_list 已在 flush 时归属）
        if block["type"] != "noise" and current_section is not None:
            current_section["blocks"].append(block["id"])

    flush_refs()

    doc = {
        "paper_id": paper_id,
        "source": source,
        "title": title,
        "authors": [],
        "sections": sections,
        "blocks": blocks,
        "references": references,
        "appendix_section_ids": [s["id"] for s in sections if s["kind"] == "appendix"],
    }
    _validate_integrity(doc)
    _require_text_layer(doc)
    return doc


def _require_text_layer(doc: dict[str, Any]) -> None:
    """FR-004 edge：没有任何可提取文本块即纯扫描/无文本层 PDF，退出码 3。

    MinerU 对纯扫描件（未启用 OCR 时）可能只返回图片/噪声条目；MVP 不含
    OCR，必须明确报错而不是产出空教程；parse_gap 是个别条目的降级，不
    算成功提取的文本。
    """
    text_blocks = [
        b for b in doc.get("blocks", [])
        if isinstance(b, dict) and b.get("type") in _TEXT_BEARING_TYPES
    ]
    if not text_blocks:
        gap_count = sum(
            1 for b in doc.get("blocks", [])
            if isinstance(b, dict) and b.get("type") == PARSE_GAP_TYPE
        )
        detail = (
            f"（另有 {gap_count} 个条目解析降级为占位）" if gap_count else ""
        )
        raise InputError(
            "解析结果中没有任何可提取的文本内容（段落/标题/公式/表格/"
            f"参考文献块均为 0{detail}）：该 PDF 疑似纯扫描图片件、缺少"
            "文本层。MVP 不包含 OCR 能力，请改用带文本层的 PDF 后重试。"
        )


# ---------------------------------------------------------------------------
# 块构建
# ---------------------------------------------------------------------------


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
    """单条原始内容构建失败时的降级占位块（FR-026，T037）。

    保留稳定 id/页码/原始类型与失败原因供审计；planner 只能让其进主线，
    assemble 在成稿对应位置渲染固定缺失说明，绝不编造内容补齐。
    """
    label = _GAP_KIND_LABEL.get(gap_kind, "段落")
    block = _base_block(
        idx, PARSE_GAP_TYPE, str(raw_type or "unknown"), page_idx, None
    )
    block["gap_kind"] = gap_kind
    block["placeholder"] = f"原文{label}解析缺失，未编造内容补全。"
    block["reason"] = reason
    logger.warning("content_list[%d] %s 构建失败，降级 parse_gap：%s",
                   idx, raw_type, reason)
    return block


def _require_text(raw: dict[str, Any], idx: int) -> str:
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ParseServiceError(f"content_list[{idx}] 的 text 为空或非字符串")
    return text


def _build_heading(
    raw: dict[str, Any],
    idx: int,
    text_level: Any,
    used_section_ids: set[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    text = _require_text(raw, idx)
    try:
        level = int(text_level)
    except (TypeError, ValueError) as exc:
        raise ParseServiceError(f"content_list[{idx}] 非法 text_level={text_level!r}") from exc
    block = _base_block(idx, "heading", "text", raw.get("page_idx"), level)
    block["text"] = text
    block["level"] = level

    if level <= 1:
        return block, None

    number, heading_text = _split_heading_number(text)
    if number:
        section_id = "sec-" + number.replace(".", "-")
        section_level = number.count(".") + 1
    else:
        section_id = "sec-" + _slugify(heading_text)
        section_level = level
    section_id = _unique_id(section_id, used_section_ids)

    section = {
        "id": section_id,
        "heading": heading_text,
        "level": section_level,
        "kind": _classify_kind(heading_text),
        "page_start": raw.get("page_idx"),
        "blocks": [],
    }
    return block, section


def _build_equation(raw: dict[str, Any], idx: int, page_idx: int | None) -> dict[str, Any]:
    raw_text = _require_text(raw, idx)
    latex, number = _strip_equation(raw_text)
    if not latex:
        raise ParseServiceError(f"content_list[{idx}] 公式去 $$ 后为空")
    if _EQUATION_IMAGE_RE.search(latex):
        raise ParseServiceError(f"content_list[{idx}] 公式 latex 含图片引用：{latex[:120]}")
    block = _base_block(idx, "equation", "equation", page_idx, None)
    block["latex"] = latex
    if number is not None:
        block["number"] = number
    return block


def _build_table(raw: dict[str, Any], idx: int, page_idx: int | None) -> dict[str, Any]:
    html = raw.get("table_body")
    if not isinstance(html, str) or not html.strip():
        raise ParseServiceError(f"content_list[{idx}] table 缺少 table_body HTML")
    block = _base_block(idx, "table", "table", page_idx, None)
    block["html"] = html
    block["caption"] = _join_lines(raw.get("table_caption"))
    block["footnote"] = _join_lines(raw.get("table_footnote"))
    return block


def _build_figure(
    raw: dict[str, Any],
    idx: int,
    page_idx: int | None,
    mineru_type: str,
    parsed_dir: Path | None,
) -> dict[str, Any]:
    rel_path = raw.get("img_path")
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ParseServiceError(f"content_list[{idx}] {mineru_type} 缺少 img_path")
    caption_key = "image_caption" if mineru_type == "image" else "chart_caption"
    footnote_key = "image_footnote" if mineru_type == "image" else "chart_footnote"

    block = _base_block(idx, "figure", mineru_type, page_idx, None)
    block["caption"] = _join_lines(raw.get(caption_key))
    block["mineru_type"] = mineru_type
    if mineru_type == "chart":
        block["footnote"] = _join_lines(raw.get(footnote_key))
    exists = parsed_dir is not None and (parsed_dir / rel_path).is_file()
    if parsed_dir is None or exists:
        block["img_path"] = rel_path
    else:
        # FR-026：图片缺失不报错中断，生成可被 QA 追踪的降级占位块
        block["img_path"] = None
        block["expected_img_path"] = rel_path
        block["image_missing"] = True
        block["placeholder"] = MISSING_IMAGE_PLACEHOLDER
        logger.warning("content_list[%d] 图片文件缺失：%s", idx, rel_path)
    return block


# ---------------------------------------------------------------------------
# 纯函数 helpers
# ---------------------------------------------------------------------------


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
    """'3.2 Attention' -> ('3.2', 'Attention')；'Abstract' -> (None, 'Abstract')。"""
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
    """按标题关键词做初始 kind 判定；判不准写 unclassified（FR-014 由 planner 终判）。"""
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


# ---------------------------------------------------------------------------
# 完整性校验
# ---------------------------------------------------------------------------


def _validate_integrity(doc: dict[str, Any]) -> None:
    blocks_by_id = {b["id"]: b for b in doc["blocks"]}
    if len(blocks_by_id) != len(doc["blocks"]):
        raise ParseServiceError("normalized 块 id 存在重复")

    ownership: dict[str, str] = {}
    for section in doc["sections"]:
        for block_id in section["blocks"]:
            if block_id not in blocks_by_id:
                raise ParseServiceError(
                    f"节 {section['id']} 引用了不存在的块 id：{block_id}"
                )
            block = blocks_by_id[block_id]
            # reference_list 允许跨节引用（data-model.md §2 校验例外）
            if block["type"] == "reference_list":
                continue
            if block_id in ownership:
                raise ParseServiceError(
                    f"块 {block_id} 同时属于多个节：{ownership[block_id]}、{section['id']}"
                )
            ownership[block_id] = section["id"]

    for block in doc["blocks"]:
        if block["type"] == "equation":
            latex = block.get("latex", "")
            if not latex:
                raise ParseServiceError(f"块 {block['id']} equation.latex 为空")
            if _EQUATION_IMAGE_RE.search(latex):
                raise ParseServiceError(f"块 {block['id']} equation.latex 含图片引用")
