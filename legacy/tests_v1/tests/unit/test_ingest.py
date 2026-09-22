"""ingest 下载错误映射测试（CP-1 F2，contracts/cli.md 退出码表）。

- 下载超时 / 连接失败 / HTTP 5xx -> ServiceTimeoutError（退出码 1）
- HTTP 4xx（含 404）/ 非 PDF 内容 -> InputError（退出码 3）
全程离线，注入极简 fake session，不发起真实网络请求。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from essay_transformer.errors import InputError, ServiceTimeoutError
from essay_transformer.ingest import _download_arxiv_pdf


class _FakeResponse:
    def __init__(self, status_code: int = 200, content: bytes = b"%PDF-1.5\n") -> None:
        self.status_code = status_code
        self.content = content


class _FakeSession:
    def __init__(self, *, response: object | None = None, exc: BaseException | None = None) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[tuple[str, float | None]] = []

    def get(self, url: str, timeout: float | None = None):
        self.calls.append((url, timeout))
        if self._exc is not None:
            raise self._exc
        return self._response


def test_download_timeout_raises_service_timeout(tmp_path: Path) -> None:
    session = _FakeSession(exc=requests.exceptions.Timeout("read timed out"))
    with pytest.raises(ServiceTimeoutError) as excinfo:
        _download_arxiv_pdf("1706.03762", tmp_path / "source.pdf", session=session)
    assert excinfo.value.exit_code == 1


def test_download_connection_error_raises_service_timeout(tmp_path: Path) -> None:
    session = _FakeSession(exc=requests.exceptions.ConnectionError("dns failure"))
    with pytest.raises(ServiceTimeoutError) as excinfo:
        _download_arxiv_pdf("1706.03762", tmp_path / "source.pdf", session=session)
    assert excinfo.value.exit_code == 1


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_download_5xx_raises_service_timeout(tmp_path: Path, status_code: int) -> None:
    session = _FakeSession(response=_FakeResponse(status_code=status_code))
    with pytest.raises(ServiceTimeoutError) as excinfo:
        _download_arxiv_pdf("1706.03762", tmp_path / "source.pdf", session=session)
    assert excinfo.value.exit_code == 1
    assert str(status_code) in str(excinfo.value)


@pytest.mark.parametrize("status_code", [404, 403, 410])
def test_download_4xx_raises_input_error(tmp_path: Path, status_code: int) -> None:
    session = _FakeSession(response=_FakeResponse(status_code=status_code))
    with pytest.raises(InputError) as excinfo:
        _download_arxiv_pdf("1706.03762", tmp_path / "source.pdf", session=session)
    assert excinfo.value.exit_code == 3


def test_download_non_pdf_content_raises_input_error(tmp_path: Path) -> None:
    session = _FakeSession(
        response=_FakeResponse(status_code=200, content=b"<html>not a pdf</html>")
    )
    with pytest.raises(InputError) as excinfo:
        _download_arxiv_pdf("1706.03762", tmp_path / "source.pdf", session=session)
    assert excinfo.value.exit_code == 3


def test_download_success_writes_pdf(tmp_path: Path) -> None:
    dest = tmp_path / "source.pdf"
    payload = b"%PDF-1.5\nbinary"
    session = _FakeSession(response=_FakeResponse(status_code=200, content=payload))
    _download_arxiv_pdf("1706.03762", dest, session=session)
    assert dest.read_bytes() == payload
