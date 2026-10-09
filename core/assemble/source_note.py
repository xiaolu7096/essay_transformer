"""来源标注规范化：统一转为非斜体的 <span class="src-note">…</span>（T022）。

兼容两种成稿句式：
(a) 旧斜体句式 `*（原文 §X，第 n 页）*`：去星号、去外层全角括号后包 span；
(b) 新裸句式 `原文 §X，第 n 页`（行尾）：直接包 span。
不匹配固定句式的普通强调/其他内容一律不动。纯标准库正则。

密度控制（SC-008）：LLM 可能在同一 `### ` 小节内重复输出多条标注。
后处理按小节合并为至多一条 span——父节覆盖子节（§5 覆盖 §5.1/§5.2），
页码取并集并压缩为区间（7–8），不丢来源信息；章首区域同理只保留一条。
"""
from __future__ import annotations

import re

# (a) 旧斜体句式：*（原文 …，第 n 页）* / *（原文 …）*
_ITALIC_NOTE_RE = re.compile(r"\*\s*[（(]\s*(原文[^*（）()\n]*?)\s*[）)]\s*\*")
# (b) 新裸句式：行尾（允许句末标点与空白收尾）；(?<!>) 排除已包裹进 span 的内容
_BARE_NOTE_RE = re.compile(
    r"(?<!>)(原文\s*§\s*[^\n*]*?第\s*\d+\s*页)"
    r"(?=[。！？!?）)]*\s*$)",
    re.MULTILINE,
)
_SPAN_RE = re.compile(r'<span class="src-note">(.*?)</span>')
_NOTE_PARSE_RE = re.compile(
    r"原文\s*§\s*([0-9]+(?:\.[0-9]+)*)\s*，第\s*(\d+)\s*页"
)

_SPAN = '<span class="src-note">{}</span>'


def render_source_notes(md: str) -> str:
    """把来源标注统一转为 src-note span；已包裹的不重复处理，并按小节合并去重。"""
    md = _ITALIC_NOTE_RE.sub(lambda m: _SPAN.format(m.group(1).strip()), md)
    md = _BARE_NOTE_RE.sub(lambda m: _SPAN.format(m.group(1).strip()), md)
    return _dedupe_per_subsection(md)


def _dedupe_per_subsection(md: str) -> str:
    # 以 `### ` 切小节；#### 子标题留在所属小节内。parts:
    # [章首区域, '### ', 小节1, '### ', 小节2, ...]
    parts = re.split(r"(?m)^(### )", md)
    out = [_merge_region(parts[0])]
    for i in range(1, len(parts), 2):
        out.append(parts[i] + _merge_region(parts[i + 1]))
    return "".join(out)


def _merge_region(region: str) -> str:
    """同一区域内多条 src-note 合并为一条；0/1 条时原样返回（幂等）。"""
    spans = list(_SPAN_RE.finditer(region))
    if len(spans) <= 1:
        return region

    parsed = [_NOTE_PARSE_RE.fullmatch(s.group(1).strip()) for s in spans]
    if all(parsed):
        text = _build_merged_note(parsed)
    else:
        # 含无法解析的自由文本：退化为保留首条原文，不臆造
        text = spans[0].group(1)

    out = region
    for i in range(len(spans) - 1, -1, -1):
        s = spans[i]
        if i == 0:
            out = out[: s.start()] + _SPAN.format(text) + out[s.end():]
        else:
            out = out[: s.start()] + out[s.end():]
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


def _build_merged_note(matches: list[re.Match]) -> str:
    sections = list(dict.fromkeys(m.group(1) for m in matches))
    pages = sorted({int(m.group(2)) for m in matches})
    tuples = {s: tuple(int(x) for x in s.split(".")) for s in sections}

    kept = [
        s
        for s in sections
        if not any(
            t != tuples[s] and tuples[s][: len(t)] == t for t in tuples.values()
        )
    ]
    sec_text = "、".join(f"§{s}" for s in kept)
    return f"原文 {sec_text}，第 {_format_pages(pages)} 页"


def _format_pages(pages: list[int]) -> str:
    ranges: list[tuple[int, int]] = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        ranges.append((start, prev))
        start = prev = p
    ranges.append((start, prev))
    return "、".join(str(a) if a == b else f"{a}–{b}" for a, b in ranges)
