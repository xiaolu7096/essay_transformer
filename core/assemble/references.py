"""参考文献渲染：编号 + arXiv/DOI/URL 链接化 + OCR 最小清理。

纯标准库正则；只处理高置信白名单，不命中的条目原样保留（T018）。
"""
from __future__ import annotations

import re

# 已自带编号（"[12] ..." / "12. ..." / "12] ..."）
_LEAD_NUM_RE = re.compile(r"^\s*\[?\d+[\].]\s+")
# OCR 残片：结尾形如 "d di Preprint i 5" / "Preprint i" / "Preprint, 12"
_PREPRINT_RESIDUE_RE = re.compile(
    r"(?:\b\w{0,3}\s+)?Preprint\b[^\w.]*\w?\s*\d*\s*$", re.IGNORECASE
)
_ARXIV_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s)]+?)(?=[.,;]?\s|$)")
# 负向后顾：不匹配 Markdown 链接目标（其 URL 紧邻 "("），避免二次嵌套
_URL_RE = re.compile(r"(?<!\()https?://[^\s)]+")


def render_reference_items(items: list[str]) -> list[str]:
    """逐条渲染：清理 OCR 残片 → 链接化 → 补编号，返回 Markdown 行列表。"""
    out: list[str] = []
    for idx, raw in enumerate(items, start=1):
        text = _clean_ocr(raw)
        text = _linkify(text)
        if not _LEAD_NUM_RE.match(raw):
            text = f"[{idx}] {text}"
        out.append(text)
    return out


def _clean_ocr(text: str) -> str:
    """折叠重复空白；移除高置信的结尾 'Preprint ... 数字' OCR 残片。"""
    text = re.sub(r"\s+", " ", text).strip()
    return _PREPRINT_RESIDUE_RE.sub("", text).rstrip(" ,.;")


def _linkify(text: str) -> str:
    """arXiv → DOI → URL 顺序转 Markdown 链接（arXiv 先于 URL，避免误吞）。"""
    text = _ARXIV_RE.sub(
        lambda m: f"[arXiv 摘要 {m.group(1)}{m.group(2) or ''}]"
        f"(https://arxiv.org/abs/{m.group(1)}{m.group(2) or ''})",
        text,
    )
    text = _DOI_RE.sub(lambda m: f"[{m.group(1)}](https://doi.org/{m.group(1)})", text)
    text = _URL_RE.sub(lambda m: f"[{m.group(0)}]({m.group(0)})", text)
    return text
