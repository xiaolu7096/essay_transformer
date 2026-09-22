"""MinerU 精准解析客户端抽象与 HTTP 实现（T006，research R1）。

由仓库根旧版一次性脚本 ``parse_paper.py``（T038 已删除）验证过的最小流程
重构而来：

1. ``POST /file-urls/batch`` 申请批量上传链接（vlm 模型）
2. ``PUT`` 上传 PDF（官方要求不要设置 Content-Type）
3. 轮询 ``GET /extract-results/batch/{batch_id}`` 直到 done/failed
4. 下载结果 ZIP 并解压到 ``runs/<paper_id>/parsed/``

安全/架构约束：

- token 只从 ``config.require_mineru_api_key()``（环境变量
  ``MINERU_API_KEY``）读取，严禁硬编码（research R13）；旧脚本已在 T038
  删除，任何 getpass 交互兜底不得迁入核心包（挂账 L4）。
- 业务模块只依赖 :class:`MinerUClient` Protocol，真实实现只由 CLI 构造；
  ``session`` / ``sleep`` 可注入以支持离线 Fake。
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

import requests

from . import config
from .errors import CredentialsError, InputError, ParseServiceError, ServiceTimeoutError

__all__ = ["MinerUClient", "HTTPMinerUClient"]

logger = logging.getLogger(__name__)

#: 运行时凭据被服务拒绝（401/403）时的可执行建议（退出码 3，FR-031/US4）
_MINERU_KEY_HINT = (
    "MinerU 拒绝了当前凭据。请在 PowerShell 中设置/轮换有效的 MinerU API Key 后重试：\n"
    '  $env:MINERU_API_KEY = "你的 MinerU API Key"\n'
    '如需长期生效：[Environment]::SetEnvironmentVariable("MINERU_API_KEY", "你的 Key", "User")'
)


@runtime_checkable
class MinerUClient(Protocol):
    """论文解析客户端协议：提交本地 PDF，产物解压到 parsed_dir 并返回该目录。"""

    def parse(self, pdf_path: Path, parsed_dir: Path) -> Path:
        ...


class HTTPMinerUClient:
    """MinerU 云端 vlm 解析 API 的 requests 实现。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = config.MINERU_BASE_URL,
        model_version: str = config.MINERU_MODEL_VERSION,
        timeout: float = config.MINERU_TIMEOUT_SECONDS,
        upload_timeout: float = config.MINERU_UPLOAD_TIMEOUT_SECONDS,
        poll_interval: float = config.MINERU_POLL_INTERVAL_SECONDS,
        max_poll_seconds: float = 600.0,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key  # 延迟到首次请求时读取凭据，保证导入/构造可离线单测
        self.base_url = base_url.rstrip("/")
        self.model_version = model_version
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self.poll_interval = poll_interval
        self.max_poll_seconds = max_poll_seconds
        self._session = session or requests.Session()
        self._sleep = sleep

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def parse(self, pdf_path: Path, parsed_dir: Path) -> Path:
        """同步执行 申请链接→上传→轮询→下载解压，返回 parsed_dir。"""
        pdf_path = Path(pdf_path)
        parsed_dir = Path(parsed_dir)
        if not pdf_path.is_file():
            # 输入类问题（退出码 3）：CLI 路径已由 ingest 先行校验，
            # 此处守卫仅服务直接调用核心库的场景（T048 挂账 L3 闭环）。
            raise InputError(f"待解析 PDF 不存在或不可读：{pdf_path}")

        parsed_dir.mkdir(parents=True, exist_ok=True)
        headers = self._auth_headers()

        batch_id, upload_url = self._request_upload_url(pdf_path, headers)
        self._upload_pdf(pdf_path, upload_url)
        task = self._poll_until_done(batch_id, headers)
        self._download_and_extract(task, parsed_dir)

        if not self._find_content_list(parsed_dir):
            raise ParseServiceError(
                f"MinerU 产物解压完成但未找到 content_list.json：{parsed_dir}"
            )
        return parsed_dir

    # ------------------------------------------------------------------
    # 步骤实现
    # ------------------------------------------------------------------

    def _key(self) -> str:
        return self._api_key or config.require_mineru_api_key()

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key()}",
            "Content-Type": "application/json",
        }

    def _request_upload_url(self, pdf_path: Path, headers: dict[str, str]) -> tuple[str, str]:
        payload = {"files": [{"name": pdf_path.name}], "model_version": self.model_version}
        data = self._post_json(f"{self.base_url}/file-urls/batch", headers, payload)
        if data.get("code") != 0:
            raise ParseServiceError(f"MinerU 申请上传链接失败：{data}")
        try:
            batch_id = data["data"]["batch_id"]
            upload_url = data["data"]["file_urls"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise ParseServiceError(f"MinerU 上传链接响应缺少字段：{data}") from exc
        logger.info("MinerU batch_id=%s", batch_id)
        return batch_id, upload_url

    def _upload_pdf(self, pdf_path: Path, upload_url: str) -> None:
        try:
            # 官方要求上传时不要设置 Content-Type，故此处不沿用鉴权头
            resp = self._session.put(
                upload_url, data=pdf_path.read_bytes(), timeout=self.upload_timeout
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise ServiceTimeoutError(f"上传 PDF 到 MinerU 超时/连接失败：{exc}") from exc
        if resp.status_code not in (200, 201):
            raise ParseServiceError(
                f"上传 PDF 失败：HTTP {resp.status_code}，{resp.text[:300]}"
            )

    def _poll_until_done(self, batch_id: str, headers: dict[str, str]) -> dict[str, Any]:
        url = f"{self.base_url}/extract-results/batch/{batch_id}"
        deadline = time.monotonic() + self.max_poll_seconds
        while True:
            data = self._get_json(url, headers)
            if data.get("code") != 0:
                raise ParseServiceError(f"MinerU 轮询接口返回错误：{data}")
            try:
                task = data["data"]["extract_result"][0]
                state = task["state"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ParseServiceError(f"MinerU 轮询响应结构异常：{data}") from exc
            logger.info("MinerU state=%s %s", state, task.get("extract_progress", ""))
            if state == "done":
                return task
            if state == "failed":
                raise ParseServiceError(
                    f"MinerU 解析任务失败：{task.get('err_msg') or task}"
                )
            if time.monotonic() >= deadline:
                raise ServiceTimeoutError(
                    f"MinerU 解析超过 {self.max_poll_seconds:.0f}s 仍未完成"
                    f"（batch_id={batch_id}，state={state}）"
                )
            self._sleep(self.poll_interval)

    def _download_and_extract(self, task: dict[str, Any], parsed_dir: Path) -> None:
        zip_url = task.get("full_zip_url")
        if not zip_url:
            raise ParseServiceError(f"MinerU done 任务缺少 full_zip_url：{task}")
        try:
            resp = self._session.get(zip_url, timeout=self.upload_timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise ServiceTimeoutError(f"下载 MinerU 结果 ZIP 超时/连接失败：{exc}") from exc
        if resp.status_code != 200:
            raise ParseServiceError(
                f"下载 MinerU 结果 ZIP 失败：HTTP {resp.status_code}"
            )
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extractall(parsed_dir)
        except zipfile.BadZipFile as exc:
            raise ParseServiceError(f"MinerU 返回的结果不是合法 ZIP：{exc}") from exc

    # ------------------------------------------------------------------
    # requests 薄封装（统一超时/状态码异常映射）
    # ------------------------------------------------------------------

    def _post_json(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._session.post(url, headers=headers, json=payload, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise ServiceTimeoutError(f"MinerU 请求超时/连接失败：{exc}") from exc
        return self._as_json(resp)

    def _get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        try:
            resp = self._session.get(url, headers=headers, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise ServiceTimeoutError(f"MinerU 请求超时/连接失败：{exc}") from exc
        return self._as_json(resp)

    @staticmethod
    def _as_json(resp: requests.Response) -> dict[str, Any]:
        if resp.status_code == 401 or resp.status_code == 403:
            # 凭据无效/被拒属前置条件类（退出码 3），不得伪装成解析服务故障（T048）
            raise CredentialsError(
                f"MinerU 鉴权失败：HTTP {resp.status_code}，"
                f"MINERU_API_KEY 缺失、错误或已失效。\n{_MINERU_KEY_HINT}"
            )
        if resp.status_code >= 500:
            raise ParseServiceError(f"MinerU 服务端错误：HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise ParseServiceError(f"MinerU 请求失败：HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ParseServiceError(f"MinerU 返回非 JSON：{resp.text[:300]}") from exc
        if not isinstance(data, dict):
            raise ParseServiceError(f"MinerU 返回 JSON 根不是对象：{type(data).__name__}")
        return data

    @staticmethod
    def _find_content_list(parsed_dir: Path) -> Path | None:
        """定位 MinerU 产物中的 content_list.json（可能带 batch_id 前缀）。"""
        direct = parsed_dir / "content_list.json"
        if direct.is_file():
            return direct
        candidates = [
            p
            for p in parsed_dir.glob("*_content_list.json")
            if not p.name.endswith("_v2_content_list.json")
        ]
        return candidates[0] if candidates else None
