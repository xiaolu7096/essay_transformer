"""成品读取与打包（Spec 003 US2/US3）。

纯函数/只读服务：不打日志、不触网络、不修改任务状态。
- output.md 是不可信外部内容：本模块只做结构解析，不做 HTML 渲染
  （消毒在前端唯一渲染管线 lib/markdown.js 完成）。
- 图片访问严格白名单 + 目录包含校验，杜绝路径逃逸。
- zip 条目名全部由安全段服务端拼装，免疫 Zip Slip。
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path, PurePosixPath

# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------


class ArtifactMissingError(Exception):
    """output.md 缺失、不可读或解析出 0 个章节。"""


class ImageNotFoundError(Exception):
    """图片名非法、越界或文件不存在（对调用方统一 404，不区分原因）。"""


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

_H1_RE = re.compile(r"^# (?!#)(.*)$")
_H2_RE = re.compile(r"^## (?!#)(.*)$")


def _count_sections(md: str) -> int:
    return sum(1 for line in md.splitlines() if _H2_RE.match(line))


def read_output(run_dir: Path) -> str:
    """读取 output.md（UTF-8）；缺失/不可读/0 章统一抛 ArtifactMissingError。"""
    path = run_dir / "output.md"
    try:
        md = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactMissingError("output.md 不可读") from exc
    if _count_sections(md) == 0:
        raise ArtifactMissingError("output.md 无章节结构")
    return md


def parse_reader(md: str, fallback_title: str) -> dict:
    """把成品 Markdown 解析为阅读器视图模型。

    - 首个行首 ``# `` 为标题，缺失用 fallback_title（调用方按
      result_title → source_ref 顺序给值）。
    - 行首恰好 ``## `` 切章（``### `` 等不切），slug 按序 s0/s1…，
      heading 为去掉 ``## `` 的原文。
    - 一级标题与首章之间的前导文并入 s0；content 去首尾空行，
      其余原文保留（<details>、HTML 表格、$...$、相对图片链接）。
    """
    lines = md.splitlines()

    title = fallback_title
    for line in lines:
        m1 = _H1_RE.match(line)
        if m1:
            title = m1.group(1).strip()
            break

    sections: list[dict] = []
    current_heading: str | None = None
    current_buf: list[str] = []

    def flush() -> None:
        if current_heading is None:
            return
        content = "\n".join(current_buf).strip("\n").strip()
        sections.append(
            {
                "slug": f"s{len(sections)}",
                "heading": current_heading,
                "content": content,
            }
        )

    # preamble（标题行起、首个 ## 之前的内容）并入第一章
    preamble: list[str] = []
    seen_first_h2 = False
    for line in lines:
        m2 = _H2_RE.match(line)
        if m2:
            if not seen_first_h2:
                seen_first_h2 = True
                current_heading = m2.group(1).strip()
                current_buf = list(preamble)
            else:
                flush()
                current_heading = m2.group(1).strip()
                current_buf = []
            continue
        if seen_first_h2:
            current_buf.append(line)
        elif not _H1_RE.match(line):
            preamble.append(line)

    if not seen_first_h2:
        raise ArtifactMissingError("成品无章节结构")
    flush()

    return {
        "title": title,
        "total_chars": len(md),
        "sections": sections,
    }


# --------------------------------------------------------------------------
# 图片
# --------------------------------------------------------------------------

_IMAGE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def resolve_image(images_dir: Path, name: str) -> Path:
    """校验图片名并返回真实文件路径；任何不满足条件一律 ImageNotFoundError。"""
    if not name or not _IMAGE_NAME_RE.fullmatch(name):
        raise ImageNotFoundError("非法图片名")
    ext = Path(name).suffix  # 大小写敏感：.SVG/.JPG 不在白名单
    if ext not in _IMAGE_EXTS:
        raise ImageNotFoundError("不支持的图片类型")

    base = images_dir.resolve()
    candidate = (base / name).resolve()
    # 目录包含校验：解析后必须仍在 images/ 之下
    if not candidate.is_relative_to(base):
        raise ImageNotFoundError("路径越界")
    if not candidate.is_file():
        raise ImageNotFoundError("图片不存在")
    return candidate


# --------------------------------------------------------------------------
# 文件名安全
# --------------------------------------------------------------------------

# Windows 保留设备名（主名部分，无视扩展名）
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_FORBIDDEN_RE = re.compile(r'[\\/:*?"<>|\x00-\x08\x0b\x0c\x0e-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_NAME_LEN = 80


def safe_filename(title: str, task_id: str) -> str:
    """把成品标题净化为跨平台安全的文件/目录名（不含扩展名）。

    先折叠所有空白（含制表/换行）为单空格 → 去其余控制字符与
    ``\\/:*?"<>|`` → trim 首尾点/空格 → Windows 保留名回退 →
    限长 80；净化后为空回退 ``paper-{id 前 8 位}``。
    """
    cleaned = _WHITESPACE_RE.sub(" ", title or "").strip()
    cleaned = _FORBIDDEN_RE.sub("", cleaned).rstrip(" .").lstrip()

    primary = cleaned.split(".", 1)[0].strip().upper()
    # ".abc" 这类以点开头的名字 primary 为空，但名字本身合法，保留
    if not cleaned or (primary and primary in _RESERVED):
        return f"paper-{task_id[:8]}"

    if len(cleaned) > _MAX_NAME_LEN:
        cleaned = cleaned[:_MAX_NAME_LEN].rstrip(" .")
    return cleaned


# --------------------------------------------------------------------------
# zip 打包
# --------------------------------------------------------------------------

def build_artifact_zip(task, run_dir: Path) -> tuple[str, bytes]:
    """内存打包 ``<base>/<base>.md`` + ``<base>/images/<安全名>``。

    返回 (base 名, zip 字节)。无裸目录项；死链自然缺席；svg 不收；
    output.md 缺失抛 ArtifactMissingError。
    """
    base = safe_filename(task.result_title or "", task.id)

    md_path = run_dir / "output.md"
    try:
        md_bytes = md_path.read_bytes()
    except OSError as exc:
        raise ArtifactMissingError("output.md 不可读") from exc

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        md_arc = f"{base}/{base}.md"
        _assert_safe_arcname(md_arc)
        zf.writestr(md_arc, md_bytes)

        images_dir = run_dir / "images"
        if images_dir.is_dir():
            for child in sorted(images_dir.iterdir()):
                if not child.is_file():
                    continue
                name = child.name
                if not _IMAGE_NAME_RE.fullmatch(name):
                    continue
                if Path(name).suffix not in _IMAGE_EXTS:
                    continue  # svg 及其余非白名单类型不收
                img_arc = f"{base}/images/{name}"
                _assert_safe_arcname(img_arc)
                zf.writestr(img_arc, child.read_bytes())

    return base, buf.getvalue()


def _assert_safe_arcname(arcname: str) -> None:
    p = PurePosixPath(arcname)
    assert not p.is_absolute()
    assert ".." not in p.parts
    assert all("\\" not in part and ":" not in part for part in p.parts)
