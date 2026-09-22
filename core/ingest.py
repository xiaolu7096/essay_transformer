"""论文摄取：本地 PDF / arXiv URL → source.pdf。
从 legacy 版精简而来：砍掉细粒度错误分类，保留核心功能。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .errors import InputError, ServiceError


_ARXIV_URL_RE = re.compile(
    r"^https?://(?:www\.|export\.)?arxiv\.org/(?P<kind>abs|pdf)/"
    r"(?P<id>[A-Za-z0-9._/\-]+?)(?:\.pdf)?/?(?:[?#]\S*)?$",
    re.IGNORECASE,
)

_ARXIV_ID_RE = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[a-z]+(?:-[a-z]+)?/\d{7})(v\d+)?$",
    re.IGNORECASE,
)

_PDF_MAGIC = b"%PDF"
ARXIV_PDF_URL_TEMPLATE = "https://arxiv.org/pdf/{arxiv_id}"


@dataclass(frozen=True)
class SourceInfo:
    kind: str  # arxiv | local
    ref: str  # arXiv id 或本地 PDF 文件名
    pdf_path: Path
    paper_id: str


def is_arxiv_url(text: str) -> bool:
    match = _ARXIV_URL_RE.match(text.strip())
    return bool(match and _ARXIV_ID_RE.match(match.group("id")))


def normalize_arxiv_id(text: str) -> str:
    candidate = text.strip()
    match = _ARXIV_URL_RE.match(candidate)
    if match:
        candidate = match.group("id")
    candidate = candidate.removesuffix(".pdf").strip("/")
    if not _ARXIV_ID_RE.match(candidate):
        raise InputError(
            f"无法识别的 arXiv 链接或 id：{text!r}。"
            "支持形态：https://arxiv.org/abs/1706.03762、"
            "https://arxiv.org/pdf/1706.03762v7"
        )
    return candidate


def paper_id_for_arxiv(arxiv_id: str) -> str:
    return f"arxiv-{arxiv_id.replace('/', '')}"


def paper_id_for_file(pdf_path: Path) -> str:
    digest = hashlib.sha256()
    with pdf_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:10]


def ingest(
    source: str | Path,
    *,
    dest_pdf_path: str | Path | None = None,
    force: bool = False,
    session: Any | None = None,
) -> SourceInfo:
    """摄取论文源到 dest_pdf_path，返回 SourceInfo。

    Args:
        source: 本地 PDF 路径或 arXiv URL
        dest_pdf_path: 目标 PDF 路径，None 的话用默认（不推荐，调用方自己管路径）
        force: 是否强制重新下载
        session: 可选 requests session
    """
    raw = str(source).strip()

    if raw.lower().startswith(("http://", "https://")):
        arxiv_id = _extract_arxiv_id(raw)
        pid = paper_id_for_arxiv(arxiv_id)
        dest = Path(dest_pdf_path) if dest_pdf_path else Path(f"runs/{pid}/source.pdf")
        if force or not _is_valid_pdf(dest):
            _download_arxiv_pdf(arxiv_id, dest, session=session)
        return SourceInfo(kind="arxiv", ref=arxiv_id, pdf_path=dest, paper_id=pid)

    pdf_path = Path(raw)
    if not pdf_path.is_file():
        raise InputError(f"文件不存在：{raw}")
    if pdf_path.suffix.lower() not in ("", ".pdf"):
        raise InputError(f"仅支持 PDF 文件，收到 {pdf_path.suffix}：{raw}")
    if not _is_valid_pdf(pdf_path):
        raise InputError(f"文件不是合法 PDF（缺少 %PDF 文件头）：{raw}")

    pid = paper_id_for_file(pdf_path)
    if dest_pdf_path:
        dest = Path(dest_pdf_path)
        if force or not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(pdf_path.read_bytes())
        return SourceInfo(kind="local", ref=pdf_path.name, pdf_path=dest, paper_id=pid)
    # 没指定 dest，直接用原文件
    return SourceInfo(kind="local", ref=pdf_path.name, pdf_path=pdf_path, paper_id=pid)


def _extract_arxiv_id(url: str) -> str:
    if not _ARXIV_URL_RE.match(url):
        raise InputError(
            f"不支持的链接：{url!r}。仅支持 arXiv 链接；本地文件请直接传入 PDF 路径。"
        )
    return normalize_arxiv_id(url)


def _download_arxiv_pdf(arxiv_id: str, dest: Path, *, session: Any | None = None) -> None:
    url = ARXIV_PDF_URL_TEMPLATE.format(arxiv_id=arxiv_id)
    http = session or requests
    try:
        resp = http.get(url, timeout=60)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise ServiceError(f"下载 arXiv PDF 超时/无法连接：{url}（{exc}）") from exc
    if resp.status_code == 404:
        raise InputError(f"arXiv 上不存在该论文（404）：{url}")
    if resp.status_code >= 500:
        raise ServiceError(f"arXiv 服务端错误：HTTP {resp.status_code}（{url}）")
    if resp.status_code != 200:
        raise InputError(f"下载 arXiv PDF 失败：HTTP {resp.status_code}（{url}）")
    content = resp.content
    if not content.startswith(_PDF_MAGIC):
        raise InputError(f"下载内容不是合法 PDF，链接可能指向错误页面：{url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)


def _is_valid_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(5).startswith(_PDF_MAGIC)
    except OSError:
        return False
