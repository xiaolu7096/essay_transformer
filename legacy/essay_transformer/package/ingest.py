"""论文摄取：本地 PDF / arXiv URL 归一化与下载（T007，research R12）。

- 接受本地 PDF 路径，或 ``arxiv.org/abs/<id>`` / ``arxiv.org/pdf/<id>(.pdf)``
  URL；正则提取 arXiv id（含 vN 版本号），统一从
  ``https://arxiv.org/pdf/<id>`` 下载。
- paper_id 规则（FR-001）：arXiv 用 ``arxiv-<id 去斜杠>``；本地 PDF 用
  文件内容 SHA-256 前 10 位。
- 产物固定落盘 ``runs/<paper_id>/source.pdf``，已存在且仍为合法 PDF 时
  默认复用（断点续跑，FR-032）。
- 坏链 / 非 arXiv URL / 非 PDF / 文件不可读 / 下载 4xx 一律抛
  :class:`~essay_transformer.errors.InputError`（退出码 3，不产生正文成稿）；
  下载超时 / 连接失败 / HTTP 5xx 属外部服务瞬时故障，抛
  :class:`~essay_transformer.errors.ServiceTimeoutError`（退出码 1，可重试）。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from . import config
from .errors import InputError, ServiceTimeoutError

__all__ = [
    "SourceInfo",
    "ingest",
    "normalize_arxiv_id",
    "paper_id_for_arxiv",
    "paper_id_for_file",
    "is_arxiv_url",
]

logger = logging.getLogger(__name__)

#: 匹配 arxiv.org/abs|pdf/<id>(.pdf)，容忍 www./export. 前缀与尾部斜杠/查询
_ARXIV_URL_RE = re.compile(
    r"^https?://(?:www\.|export\.)?arxiv\.org/(?P<kind>abs|pdf)/"
    r"(?P<id>[A-Za-z0-9._/\-]+?)(?:\.pdf)?/?(?:[?#]\S*)?$",
    re.IGNORECASE,
)

#: arXiv id 本体：新版 1706.03762(vN) 或旧版 cond-mat/9901001(vN)
_ARXIV_ID_RE = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[a-z]+(?:-[a-z]+)?/\d{7})(v\d+)?$",
    re.IGNORECASE,
)

_PDF_MAGIC = b"%PDF"


@dataclass(frozen=True)
class SourceInfo:
    """normalized.json 的 ``source`` 字段（data-model.md §2）。"""

    kind: str  # arxiv | local
    ref: str  # arXiv id 或本地 PDF 文件名
    pdf_path: Path
    paper_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "pdf_path": _stored_path(self.pdf_path),
        }


def is_arxiv_url(text: str) -> bool:
    """文本是否为受支持的 arXiv abs/pdf URL（且 id 形态合法）。"""
    match = _ARXIV_URL_RE.match(text.strip())
    return bool(match and _ARXIV_ID_RE.match(match.group("id")))


def normalize_arxiv_id(text: str) -> str:
    """从 arXiv URL 或裸 id 中提取归一化 id（保留 vN）；无法识别抛 InputError。"""
    candidate = text.strip()
    match = _ARXIV_URL_RE.match(candidate)
    if match:
        candidate = match.group("id")
    candidate = candidate.removesuffix(".pdf").strip("/")
    if not _ARXIV_ID_RE.match(candidate):
        raise InputError(
            f"无法识别的 arXiv 链接或 id：{text!r}。\n"
            "支持形态：https://arxiv.org/abs/1706.03762、"
            "https://arxiv.org/pdf/1706.03762v7（可带 .pdf 后缀）"
        )
    return candidate


def paper_id_for_arxiv(arxiv_id: str) -> str:
    """arxiv-<id 去斜杠>（版本号 vN 保留，保证 runs 目录稳定可复用）。"""
    return f"arxiv-{arxiv_id.replace('/', '')}"


def paper_id_for_file(pdf_path: Path) -> str:
    """本地 PDF：文件内容 SHA-256 前 10 位。"""
    digest = hashlib.sha256()
    with pdf_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:10]


def ingest(
    source: str | Path,
    *,
    paper_id: str | None = None,
    runs_dir: Path | None = None,
    session: Any | None = None,
    force: bool = False,
) -> SourceInfo:
    """摄取论文源到 ``runs/<paper_id>/source.pdf`` 并返回 :class:`SourceInfo`。

    ``force=True`` 时忽略已缓存的 source.pdf 重新下载/拷贝；默认断点复用。
    """
    runs_root = Path(runs_dir) if runs_dir is not None else config.RUNS_DIR
    raw = str(source).strip()

    if raw.lower().startswith(("http://", "https://")):
        arxiv_id = _as_arxiv_id(raw)
        pid = paper_id or paper_id_for_arxiv(arxiv_id)
        run_dir = runs_root / pid
        dest = run_dir / "source.pdf"
        if force or not _is_valid_pdf(dest):
            _download_arxiv_pdf(arxiv_id, dest, session=session)
        return SourceInfo(kind="arxiv", ref=arxiv_id, pdf_path=dest, paper_id=pid)

    pdf_path = _as_local_pdf(raw)
    pid = paper_id or paper_id_for_file(pdf_path)
    run_dir = runs_root / pid
    dest = run_dir / "source.pdf"
    if force or not _is_valid_pdf(dest):
        run_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pdf_path.read_bytes())
        logger.info("已拷贝本地 PDF -> %s", dest)
    return SourceInfo(kind="local", ref=pdf_path.name, pdf_path=dest, paper_id=pid)


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _as_arxiv_id(url: str) -> str:
    if not _ARXIV_URL_RE.match(url):
        raise InputError(
            f"不支持的链接：{url!r}。\n"
            "仅支持 arXiv 链接（arxiv.org/abs/<id> 或 arxiv.org/pdf/<id>）；"
            "本地文件请直接传入 PDF 路径。"
        )
    return normalize_arxiv_id(url)


def _as_local_pdf(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.exists():
        raise InputError(f"文件不存在：{raw}")
    if not path.is_file():
        raise InputError(f"不是可读文件：{raw}")
    suffix = path.suffix.lower()
    if suffix and suffix != ".pdf":
        raise InputError(f"仅支持 PDF 文件，收到 {suffix} 后缀：{raw}")
    try:
        with path.open("rb") as fh:
            head = fh.read(5)
    except OSError as exc:
        raise InputError(f"文件不可读：{raw}（{exc}）") from exc
    if not head.startswith(_PDF_MAGIC):
        raise InputError(
            f"文件内容不是合法 PDF（缺少 %PDF 文件头）：{raw}；"
            "文件可能已损坏或实为其他格式。"
        )
    return path


def _download_arxiv_pdf(arxiv_id: str, dest: Path, *, session: Any | None = None) -> None:
    url = config.ARXIV_PDF_URL_TEMPLATE.format(arxiv_id=arxiv_id)
    http = session or requests
    logger.info("下载 arXiv PDF：%s", url)
    try:
        resp = http.get(url, timeout=config.MINERU_UPLOAD_TIMEOUT_SECONDS)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise ServiceTimeoutError(
            f"下载 arXiv PDF 超时/无法连接（退出码 1，可稍后重试）：{url}（{exc}）"
        ) from exc
    if getattr(resp, "status_code", None) is None:  # 注入 session 的极简响应保护
        raise InputError(f"下载 arXiv PDF 响应异常：{url}")
    if resp.status_code == 404:
        raise InputError(f"arXiv 上不存在该论文（404）：{url}")
    if resp.status_code >= 500:
        raise ServiceTimeoutError(
            f"arXiv 服务端错误：HTTP {resp.status_code}（{url}），可稍后重试"
        )
    if resp.status_code != 200:
        raise InputError(f"下载 arXiv PDF 失败：HTTP {resp.status_code}（{url}）")
    content = resp.content
    if not content.startswith(_PDF_MAGIC):
        raise InputError(
            f"下载内容不是合法 PDF（缺少 %PDF 文件头），链接可能指向错误页面：{url}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    logger.info("下载完成 -> %s（%d 字节）", dest, len(content))


def _is_valid_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(5).startswith(_PDF_MAGIC)
    except OSError:
        return False


def _stored_path(path: Path) -> str:
    """落盘 JSON 中的路径：位于仓库根下时存相对 POSIX 路径，否则存绝对路径。"""
    try:
        return path.resolve().relative_to(config.REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()
