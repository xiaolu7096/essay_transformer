"""MinerU 云端解析客户端。
从 legacy 版精简而来：去掉 config 依赖，参数直接传；砍掉 Protocol 抽象（核心层
直接用 HTTP 实现，要 mock 的话传 session 就行）。
"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import requests

from .errors import InputError, ServiceError


class MinerUClient:
    """MinerU 云端 vlm 解析 API 客户端。"""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://mineru.net/api/v1",
        model_version: str = "vlm",
        timeout: float = 60.0,
        upload_timeout: float = 300.0,
        poll_interval: float = 3.0,
        max_poll_seconds: float = 600.0,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
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

    def parse(self, pdf_path: str | Path, parsed_dir: str | Path) -> Path:
        """同步执行 申请链接→上传→轮询→下载解压，返回 parsed_dir。"""
        pdf_path = Path(pdf_path)
        parsed_dir = Path(parsed_dir)
        if not pdf_path.is_file():
            raise InputError(f"待解析 PDF 不存在：{pdf_path}")
        parsed_dir.mkdir(parents=True, exist_ok=True)

        headers = self._auth_headers()
        batch_id, upload_url = self._request_upload_url(pdf_path, headers)
        self._upload_pdf(pdf_path, upload_url)
        task = self._poll_until_done(batch_id, headers)
        self._download_and_extract(task, parsed_dir)

        if not self._find_content_list(parsed_dir):
            raise ServiceError(f"MinerU 产物中未找到 content_list.json：{parsed_dir}")
        return parsed_dir

    # ------------------------------------------------------------------
    # 步骤实现
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _request_upload_url(
        self, pdf_path: Path, headers: dict[str, str]
    ) -> tuple[str, str]:
        payload = {"files": [{"name": pdf_path.name}], "model_version": self.model_version}
        data = self._post_json(f"{self.base_url}/file-urls/batch", headers, payload)
        if data.get("code") != 0:
            raise ServiceError(f"MinerU 申请上传链接失败：{data}")
        try:
            batch_id = data["data"]["batch_id"]
            upload_url = data["data"]["file_urls"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise ServiceError(f"MinerU 上传链接响应缺少字段：{data}") from exc
        return batch_id, upload_url

    def _upload_pdf(self, pdf_path: Path, upload_url: str) -> None:
        try:
            resp = self._session.put(
                upload_url, data=pdf_path.read_bytes(), timeout=self.upload_timeout
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise ServiceError(f"上传 PDF 到 MinerU 超时/连接失败：{exc}") from exc
        if resp.status_code not in (200, 201):
            raise ServiceError(
                f"上传 PDF 失败：HTTP {resp.status_code}，{resp.text[:300]}"
            )

    def _poll_until_done(self, batch_id: str, headers: dict[str, str]) -> dict[str, Any]:
        url = f"{self.base_url}/extract-results/batch/{batch_id}"
        deadline = time.monotonic() + self.max_poll_seconds
        while True:
            data = self._get_json(url, headers)
            if data.get("code") != 0:
                raise ServiceError(f"MinerU 轮询接口返回错误：{data}")
            try:
                task = data["data"]["extract_result"][0]
                state = task["state"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ServiceError(f"MinerU 轮询响应结构异常：{data}") from exc
            if state == "done":
                return task
            if state == "failed":
                raise ServiceError(
                    f"MinerU 解析任务失败：{task.get('err_msg') or task}"
                )
            if time.monotonic() >= deadline:
                raise ServiceError(
                    f"MinerU 解析超过 {self.max_poll_seconds:.0f}s 仍未完成"
                    f"（batch_id={batch_id}，state={state}）"
                )
            self._sleep(self.poll_interval)

    def _download_and_extract(self, task: dict[str, Any], parsed_dir: Path) -> None:
        zip_url = task.get("full_zip_url")
        if not zip_url:
            raise ServiceError(f"MinerU done 任务缺少 full_zip_url：{task}")
        try:
            resp = self._session.get(zip_url, timeout=self.upload_timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise ServiceError(f"下载 MinerU 结果 ZIP 超时/连接失败：{exc}") from exc
        if resp.status_code != 200:
            raise ServiceError(f"下载 MinerU 结果 ZIP 失败：HTTP {resp.status_code}")
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extractall(parsed_dir)
        except zipfile.BadZipFile as exc:
            raise ServiceError(f"MinerU 返回的结果不是合法 ZIP：{exc}") from exc

    # ------------------------------------------------------------------
    # requests 薄封装
    # ------------------------------------------------------------------

    def _post_json(
        self, url: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            resp = self._session.post(url, headers=headers, json=payload, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise ServiceError(f"MinerU 请求超时/连接失败：{exc}") from exc
        return self._as_json(resp)

    def _get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        try:
            resp = self._session.get(url, headers=headers, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise ServiceError(f"MinerU 请求超时/连接失败：{exc}") from exc
        return self._as_json(resp)

    @staticmethod
    def _as_json(resp: requests.Response) -> dict[str, Any]:
        if resp.status_code in (401, 403):
            raise InputError(
                f"MinerU 鉴权失败：HTTP {resp.status_code}，API Key 缺失、错误或已失效。"
            )
        if resp.status_code >= 500:
            raise ServiceError(f"MinerU 服务端错误：HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise ServiceError(f"MinerU 请求失败：HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ServiceError(f"MinerU 返回非 JSON：{resp.text[:300]}") from exc
        if not isinstance(data, dict):
            raise ServiceError(f"MinerU 返回 JSON 根不是对象：{type(data).__name__}")
        return data

    @staticmethod
    def _find_content_list(parsed_dir: Path) -> Path | None:
        """定位 content_list.json（可能带 batch_id 前缀）。"""
        direct = parsed_dir / "content_list.json"
        if direct.is_file():
            return direct
        candidates = [
            p
            for p in parsed_dir.glob("*_content_list.json")
            if not p.name.endswith("_v2_content_list.json")
        ]
        return candidates[0] if candidates else None
