"""转换任务状态机：job_state.json 读写（T008，data-model.md §9，research R11）。

状态链：

    ingested → parsed → normalized → glossary_drafted → glossary_confirmed
            → planned → rewritten → example_verified → assembled
            → qa_passed → done

- 每阶段完成后调用 :meth:`JobState.mark_reached` 落盘；失败调用
  :meth:`JobState.mark_failed`，记录 failed_stage/error_type/message/updated_at。
- 默认断点续跑：``has_reached(stage)`` 为真的阶段被跳过（FR-032）；
  ``--from-stage`` 通过 :meth:`JobState.reset_from` 回退到指定阶段的前一阶段。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import (
    ERROR_TYPES,
    EssayTransformerError,
    InputError,
    default_error_type_for_stage,
    error_type_for,
)

__all__ = ["STAGES", "JOB_STATE_FILENAME", "FailedRecord", "JobState"]

logger = logging.getLogger(__name__)

#: 严格有序的全部阶段（索引即先后顺序）
STAGES: tuple[str, ...] = (
    "ingested",
    "parsed",
    "normalized",
    "glossary_drafted",
    "glossary_confirmed",
    "planned",
    "rewritten",
    "example_verified",
    "assembled",
    "qa_passed",
    "done",
)

JOB_STATE_FILENAME = "job_state.json"


@dataclass(frozen=True)
class FailedRecord:
    """data-model.md §9 的 failed 记录。"""

    failed_stage: str
    error_type: str  # 仅允许 data-model.md §9 六值：input/credentials/service_timeout/parse/llm/qa
    message: str
    updated_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "failed_stage": self.failed_stage,
            "error_type": self.error_type,
            "message": self.message,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FailedRecord":
        failed_stage = str(data["failed_stage"])
        raw_type = data.get("error_type")
        # 旧版可能写出已废止的 "runtime" 或缺字段：按失败阶段确定性纠正为六值
        error_type = (
            str(raw_type) if isinstance(raw_type, str) and raw_type in ERROR_TYPES
            else default_error_type_for_stage(failed_stage)
        )
        return cls(
            failed_stage=failed_stage,
            error_type=error_type,
            message=str(data.get("message", "")),
            updated_at=str(data.get("updated_at", "")),
        )


def _now() -> str:
    # 契约：时间一律本地时区
    return datetime.now().isoformat(timespec="seconds")


def _failure_message(exc: BaseException) -> str:
    """业务异常保留原始消息；未知异常补异常类型名，细节不丢失（CP-1 F3）。"""
    detail = str(exc)
    if isinstance(exc, EssayTransformerError):
        return detail
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


class JobState:
    """单个论文转换任务（runs/<paper_id>/）的状态读写器。"""

    def __init__(
        self,
        run_dir: Path | str,
        *,
        paper_id: str | None = None,
        stage: str | None = None,
        failed: FailedRecord | None = None,
        updated_at: str | None = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.paper_id = paper_id or self.run_dir.name
        self._stage: str | None = stage
        self.failed: FailedRecord | None = failed
        self.updated_at: str | None = updated_at

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.run_dir / JOB_STATE_FILENAME

    @classmethod
    def load(cls, run_dir: Path | str, *, paper_id: str | None = None) -> "JobState":
        """读取状态；文件不存在时返回空状态（stage=None，尚未进入任何阶段）。"""
        path = Path(run_dir) / JOB_STATE_FILENAME
        if not path.is_file():
            return cls(run_dir, paper_id=paper_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        stage = data.get("stage")
        if stage is not None and stage not in STAGES:
            raise InputError(f"job_state.json 含未知阶段 {stage!r}：{path}")
        failed_raw = data.get("failed")
        failed = FailedRecord.from_dict(failed_raw) if isinstance(failed_raw, dict) else None
        return cls(
            run_dir,
            paper_id=data.get("paper_id", paper_id),
            stage=stage,
            failed=failed,
            updated_at=data.get("updated_at"),
        )

    def save(self) -> Path:
        """原子写入 job_state.json（tmp 文件替换，避免中断产生半截 JSON）。"""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "paper_id": self.paper_id,
            "stage": self._stage,
            "updated_at": self.updated_at or _now(),
            "failed": self.failed.as_dict() if self.failed else None,
        }
        tmp_path = self.state_path.with_name(f".{JOB_STATE_FILENAME}.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp_path, self.state_path)
        return self.state_path

    # ------------------------------------------------------------------
    # 状态机操作
    # ------------------------------------------------------------------

    @property
    def stage(self) -> str | None:
        """已到达的最高阶段；None 表示尚未开始。"""
        return self._stage

    def has_reached(self, stage: str) -> bool:
        self._require_known_stage(stage)
        if self._stage is None:
            return False
        return STAGES.index(self._stage) >= STAGES.index(stage)

    def mark_reached(self, stage: str) -> None:
        """标记某阶段完成（单调推进；重复标记同阶段只刷新时间）。

        从 failed 状态恢复并推进时清空 failed 记录。
        """
        self._require_known_stage(stage)
        if self._stage is not None and STAGES.index(stage) < STAGES.index(self._stage):
            raise ValueError(
                f"状态机不允许回退：当前阶段 {self._stage!r}，不能标记为 {stage!r}"
            )
        self._stage = stage
        self.failed = None
        self.updated_at = _now()
        self.save()

    def mark_failed(self, stage: str, exc: BaseException) -> FailedRecord:
        """在指定阶段记录失败（状态停在失败前已到达的最高阶段）。

        error_type 确定性映射（CP-1 F3）：业务异常取自身六值 error_type；
        未知异常按 stage 映射，异常类型名与详情写入 message，不丢失排查线索。
        """
        self._require_known_stage(stage)
        record = FailedRecord(
            failed_stage=stage,
            error_type=error_type_for(exc, stage),
            message=_failure_message(exc),
            updated_at=_now(),
        )
        self.failed = record
        self.updated_at = record.updated_at
        self.save()
        logger.warning("阶段 %s 失败：%s", stage, record.error_type)
        return record

    def reset_from(self, from_stage: str) -> None:
        """``--from-stage``：回退到目标阶段的前一阶段（目标阶段及其后全部重跑）。"""
        self._require_known_stage(from_stage)
        idx = STAGES.index(from_stage)
        if idx == 0:
            raise InputError("--from-stage 不能从第一个阶段开始重跑")
        self._stage = STAGES[idx - 1]
        self.failed = None
        self.updated_at = _now()
        self.save()

    def should_execute(self, stage: str, *, from_stage: str | None = None) -> bool:
        """判定某阶段本次是否需要执行。

        - 指定 from_stage：阶段序号 ≥ from_stage 即执行；
        - 默认续跑：尚未到达该阶段才执行。
        """
        self._require_known_stage(stage)
        if from_stage is not None:
            self._require_known_stage(from_stage)
            return STAGES.index(stage) >= STAGES.index(from_stage)
        return not self.has_reached(stage)

    @staticmethod
    def _require_known_stage(stage: str) -> None:
        if stage not in STAGES:
            raise InputError(f"未知阶段 {stage!r}，允许值：{', '.join(STAGES)}")
