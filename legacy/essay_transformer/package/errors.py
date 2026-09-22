"""统一异常体系与退出码映射（T013，CP-1 F3）。

契约：``contracts/cli.md`` 退出码表 + ``data-model.md`` §9 failed 记录的
``error_type`` 六值枚举（input/credentials/service_timeout/parse/llm/qa）。

层级设计：

- 所有自定义异常继承 :class:`EssayTransformerError`，自带 ``exit_code`` 与
  ``error_type``，CLI 适配层无需 if/elif 链即可完成退出码映射。
- 退出码 3（前置条件不满足）的共同基类保留 T003 已使用的名字
  :class:`PreconditionError`；:class:`InputError` / :class:`CredentialsError`
  / :class:`GlossaryNotConfirmedError` 是其细分。
- 退出码 1（运行时/外部服务错误）：:class:`ServiceTimeoutError`、
  :class:`ParseServiceError`、:class:`LLMError`。
- 退出码 2（QA 闸门未通过）：:class:`QAFailedError`。

确定性归类（无 "runtime" 兜底，CP-1 F3）：

- 业务异常自带六值之一的 ``error_type``，归类时原样采用；
- 未知异常（无有效 ``error_type``）按 ``failed_stage`` 经
  :data:`STAGE_DEFAULT_ERROR_TYPE` **确定性**映射到六值枚举，异常类型与
  详情写入 failed 记录的 ``message``。

本模块属于核心库，禁止 argparse / UI 框架依赖（宪法原则 I）。
"""

from __future__ import annotations

__all__ = [
    "ERROR_TYPES",
    "STAGE_DEFAULT_ERROR_TYPE",
    "EssayTransformerError",
    "PreconditionError",
    "InputError",
    "CredentialsError",
    "GlossaryNotConfirmedError",
    "ServiceTimeoutError",
    "ParseServiceError",
    "LLMError",
    "LocalIOError",
    "QAFailedError",
    "exit_code_for",
    "error_type_for",
    "default_error_type_for_stage",
]

#: data-model.md §9 合法 error_type 全集（不存在第七个 "runtime" 值）
ERROR_TYPES: tuple[str, ...] = (
    "input",
    "credentials",
    "service_timeout",
    "parse",
    "llm",
    "qa",
)

#: 未知异常按 failed_stage 的确定性归类（依据各阶段主导失败来源）：
#: - ingested/glossary_confirmed：输入/前置校验类 -> input
#: - parsed/normalized：解析服务及其产物结构 -> parse
#: - glossary_drafted/planned/rewritten/example_verified/assembled：
#:   LLM 生成及其产物驱动的阶段 -> llm
#: - qa_passed/done：QA 交付环节 -> qa
#: 凭据缺失与服务超时在任何阶段都由对应业务异常自带 error_type，不经过本表。
STAGE_DEFAULT_ERROR_TYPE: dict[str, str] = {
    "ingested": "input",
    "parsed": "parse",
    "normalized": "parse",
    "glossary_drafted": "llm",
    "glossary_confirmed": "input",
    "planned": "llm",
    "rewritten": "llm",
    "example_verified": "llm",
    "assembled": "llm",
    "qa_passed": "qa",
    "done": "qa",
}

# ---------------------------------------------------------------------------
# 根异常
# ---------------------------------------------------------------------------


class EssayTransformerError(Exception):
    """全部业务异常的根类。

    默认退出码 1（contracts/cli.md 运行时/外部服务错误）；子类按语义覆盖
    ``exit_code`` / ``error_type``。根类自身不预设六值中的某一个
    ``error_type``（值为 None）：未细分的业务异常与未知异常一样，由
    :func:`error_type_for` 按 failed_stage 确定性归类，杜绝 "runtime" 兜底。
    """

    #: CLI 退出码（contracts/cli.md：0 成功 / 1 运行时 / 2 QA / 3 前置条件）
    exit_code: int = 1

    #: job_state.json failed 记录用错误类型（data-model.md §9 六值枚举）；
    #: None 表示未细分，归类时回落到 failed_stage 的确定性映射
    error_type: str | None = None

    #: 失败环节（state.py 的十一阶段之一）；由 CLI 适配层在 mark_failed 时
    #: 注入，用于向用户输出"失败环节+可执行建议"（contracts/cli.md 退出码 1）
    stage: str | None = None


# ---------------------------------------------------------------------------
# 退出码 3：前置条件不满足（不产生正文成稿）
# ---------------------------------------------------------------------------


class PreconditionError(EssayTransformerError):
    """前置条件不满足（退出码 3）。

    T003 ``config.py`` 已使用本类名；统一异常体系建立后作为退出码 3
    家族的共同基类保留，凭据缺失等旧有 raise 点保持兼容。
    """

    exit_code = 3
    error_type = "input"


class InputError(PreconditionError):
    """无效输入：坏链、非 PDF、文件不可读、paper_id 非法等（退出码 3）。"""

    exit_code = 3
    error_type = "input"


class CredentialsError(PreconditionError):
    """凭据缺失/非法：MINERU_API_KEY / DEEPSEEK_API_KEY（退出码 3）。"""

    exit_code = 3
    error_type = "credentials"


class GlossaryNotConfirmedError(PreconditionError):
    """术语表未确认即启动 convert（FR-008，退出码 3）。"""

    exit_code = 3
    error_type = "input"


# ---------------------------------------------------------------------------
# 退出码 1：运行时/外部服务错误
# ---------------------------------------------------------------------------


class ServiceTimeoutError(EssayTransformerError):
    """外部服务超时（MinerU / DeepSeek / 下载等，退出码 1）。"""

    exit_code = 1
    error_type = "service_timeout"


class ParseServiceError(EssayTransformerError):
    """解析服务失败或解析产物结构非法（MinerU 任务 failed、normalize 校验等）。"""

    exit_code = 1
    error_type = "parse"


class LLMError(EssayTransformerError):
    """LLM 调用失败：重试耗尽、5xx、非法 JSON、输出不符契约等（退出码 1）。"""

    exit_code = 1
    error_type = "llm"


class LocalIOError(EssayTransformerError):
    """本地文件系统读写失败（成稿组装/图片拷贝/产物落盘等）。

    L2 挂账：assemble 阶段的本地 IO 错误不得被未知异常映射成 ``llm``；
    六值枚举中没有独立 io 值，失败域归为 ``input``（本地文件/数据问题，
    区别于任何外部服务），但它发生在运行时而非启动前置校验，故退出码为 1。
    """

    exit_code = 1
    error_type = "input"


# ---------------------------------------------------------------------------
# 退出码 2：QA 闸门
# ---------------------------------------------------------------------------


class QAFailedError(EssayTransformerError):
    """确定性 QA 闸门存在 failed 项（退出码 2，成稿仅允许 .unchecked.md）。"""

    exit_code = 2
    error_type = "qa"


# ---------------------------------------------------------------------------
# 映射工具（供 CLI 适配层与 state 失败记录使用）
# ---------------------------------------------------------------------------


def exit_code_for(exc: BaseException) -> int:
    """返回异常对应的 CLI 退出码；非业务异常按运行时错误 1 处理。"""
    code = getattr(exc, "exit_code", None)
    return code if isinstance(code, int) else 1


def default_error_type_for_stage(failed_stage: str) -> str:
    """未知异常在指定失败阶段的确定性 error_type（data-model.md §9 六值）。

    未登记阶段保守归入 ``input``；调用方（state.py）会先校验阶段合法性，
    正常流水线不会走到该分支。
    """
    return STAGE_DEFAULT_ERROR_TYPE.get(failed_stage, "input")


def error_type_for(exc: BaseException, failed_stage: str) -> str:
    """返回 job_state.json failed 记录用 error_type（data-model.md §9 六值）。

    - 异常自带六值之一的 ``error_type`` 时原样采用（业务异常语义优先）；
    - 其余情况（未知异常、未细分的根业务异常）按 failed_stage 确定性映射，
      绝不回退到枚举外的 "runtime" 值。
    """
    etype = getattr(exc, "error_type", None)
    if isinstance(etype, str) and etype in ERROR_TYPES:
        return etype
    return default_error_type_for_stage(failed_stage)
