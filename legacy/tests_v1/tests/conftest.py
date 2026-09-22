"""pytest 共享夹具与离线 Fake 客户端（T011，research R14/R16）。

测试全程离线：

- :class:`FakeLLMClient` 按 schema_kind 返回录制 JSON，可注入故障；
- :class:`FakeMinerUClient` 把 ``tests/integration/fixtures/mini_parsed``
  （从真实 output/ 截取的 content_list 夹具）复制到目标 parsed 目录，
  模拟"申请链接→PUT→轮询→解压"的最终产物。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
FIXTURES_DIR = TESTS_DIR / "integration" / "fixtures"
MINI_PARSED_DIR = FIXTURES_DIR / "mini_parsed"

# 本机沙箱禁止写系统 %TEMP%，将 pytest tmp_path 重定向到仓库内
# （.pytest_cache/ 已在 .gitignore 中）；必须在任何 tmp_path 夹具创建前生效。
_LOCAL_TMP = REPO_ROOT / ".pytest_cache" / "tmp"
_LOCAL_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_LOCAL_TMP)
os.environ.setdefault("TMP", str(_LOCAL_TMP))
os.environ.setdefault("TEMP", str(_LOCAL_TMP))


class FakeLLMClient:
    """LLMClient 协议的离线录制实现。

    responses 形态（按 schema_kind 查表)：

    - dict：直接返回该 dict 的拷贝；
    - callable：``f(system, user, schema_kind) -> dict``，便于断言入参；
    - list：每次调用弹出下一项（项可以是异常，弹出即抛出）。

    ``failure`` 可注入异常（如 ServiceTimeoutError），注入后每次调用都抛出。
    """

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._responses = responses or {}
        self._failure = failure
        self.calls: list[tuple[str, str, str]] = []

    def complete_json(self, system: str, user: str, schema_kind: str) -> dict[str, Any]:
        self.calls.append((system, user, schema_kind))
        if self._failure is not None:
            raise self._failure
        if schema_kind not in self._responses:
            raise AssertionError(
                f"FakeLLMClient 未录制 schema_kind={schema_kind!r} 的响应；"
                f"已录制：{sorted(self._responses)}"
            )
        recorded = self._responses[schema_kind]
        if callable(recorded):
            result = recorded(system, user, schema_kind)
        elif isinstance(recorded, list):
            if not recorded:
                raise AssertionError(
                    f"FakeLLMClient 的 {schema_kind!r} 响应队列已耗尽"
                )
            item = recorded.pop(0)
            if isinstance(item, BaseException):
                raise item
            result = item
        else:
            result = recorded
        # 返回拷贝避免测试间共享可变状态
        return json.loads(json.dumps(result, ensure_ascii=False))

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def schema_kinds_called(self) -> list[str]:
        return [kind for _, _, kind in self.calls]


class FakeMinerUClient:
    """MinerUClient 协议的离线实现：把录制 parsed 目录复制到目标位置。"""

    def __init__(
        self,
        *,
        source_parsed_dir: Path | str = MINI_PARSED_DIR,
        failure: BaseException | None = None,
    ) -> None:
        self.source_parsed_dir = Path(source_parsed_dir)
        self._failure = failure
        self.calls: list[tuple[Path, Path]] = []

    def parse(self, pdf_path: Path, parsed_dir: Path) -> Path:
        pdf_path = Path(pdf_path)
        parsed_dir = Path(parsed_dir)
        self.calls.append((pdf_path, parsed_dir))
        if self._failure is not None:
            raise self._failure
        if not pdf_path.is_file():
            raise FileNotFoundError(f"FakeMinerUClient 找不到 PDF：{pdf_path}")
        if not self.source_parsed_dir.is_dir():
            raise FileNotFoundError(
                f"FakeMinerUClient 录制目录不存在：{self.source_parsed_dir}"
            )
        parsed_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.source_parsed_dir, parsed_dir, dirs_exist_ok=True)
        return parsed_dir


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mini_content_list() -> list[dict[str, Any]]:
    """读取截取后的小型 content_list（19 块，覆盖全部目标类型）。"""
    path = MINI_PARSED_DIR / "content_list.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def mini_parsed_dir(tmp_path: Path) -> Path:
    """物化一份 mini parsed 目录（含两张存在的桩图片），返回其路径。"""
    target = tmp_path / "parsed"
    shutil.copytree(MINI_PARSED_DIR, target)
    return target


@pytest.fixture()
def make_fake_llm() -> Callable[..., FakeLLMClient]:
    """FakeLLMClient 工厂：``make_fake_llm({...}, failure=...)``。"""

    def _factory(
        responses: dict[str, Any] | None = None,
        *,
        failure: BaseException | None = None,
    ) -> FakeLLMClient:
        return FakeLLMClient(responses, failure=failure)

    return _factory


@pytest.fixture()
def fake_mineru() -> FakeMinerUClient:
    return FakeMinerUClient()
