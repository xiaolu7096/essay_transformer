"""集中配置：凭据环境变量、模型/服务参数、路径常量、并发默认值。

宪法约束：

- 凭据只能来自环境变量 ``MINERU_API_KEY`` / ``DEEPSEEK_API_KEY``，严禁硬编码
  （宪法 Additional Constraints 凭据边界；research R13）。
- 本模块属于核心库，禁止使用 argparse 或任何 UI/Web 框架（宪法原则 I）。
- 凭据缺失抛出 :class:`~essay_transformer.errors.CredentialsError`
  （统一异常体系 errors.py 的退出码 3 家族，是 :class:`PreconditionError`
  的子类），由 CLI 适配层映射为退出码 3（contracts/cli.md），错误消息必须
  为中文并含 PowerShell 设置指引。

注意：导入本模块本身不触发凭据校验，以保证离线测试可在无 key 环境下导入核心包；
由 CLI 启动时调用 :func:`validate_credentials`、真实客户端构造时调用对应的
``require_*_api_key`` 完成"启动即校验"（research R13）。
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import CredentialsError, PreconditionError

__all__ = [
    "PreconditionError",
    "MINERU_API_KEY_ENV",
    "DEEPSEEK_API_KEY_ENV",
    "require_mineru_api_key",
    "require_deepseek_api_key",
    "validate_credentials",
    # DeepSeek
    "DEEPSEEK_MODEL",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_EXTRA_BODY",
    "DEEPSEEK_TIMEOUT_SECONDS",
    "DEEPSEEK_MAX_RETRIES",
    "DEEPSEEK_RETRY_BACKOFF_SECONDS",
    "DEEPSEEK_MAX_TOKENS",
    # MinerU
    "MINERU_BASE_URL",
    "MINERU_MODEL_VERSION",
    "MINERU_TIMEOUT_SECONDS",
    "MINERU_UPLOAD_TIMEOUT_SECONDS",
    "MINERU_POLL_INTERVAL_SECONDS",
    # arXiv / 路径 / 并发
    "ARXIV_PDF_URL_TEMPLATE",
    "REPO_ROOT",
    "RUNS_DIR",
    "GLOSSARIES_DIR",
    "SEED_GLOSSARY_PATH",
    "PROMPTS_DIR",
    "DEFAULT_JOBS",
]


# ---------------------------------------------------------------------------
# 凭据（只从环境变量读取）
# ---------------------------------------------------------------------------
# PreconditionError 由 errors.py 统一定义并在上方导入：T003 先于统一异常体系
# 建立，此处保留 re-export 以兼容既有导入路径；凭据缺失实际抛出更精确的
# CredentialsError（同为退出码 3，isinstance(PreconditionError) 仍成立）。

MINERU_API_KEY_ENV = "MINERU_API_KEY"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"

_POWERSHELL_HINT = (
    "凭据只能从环境变量读取。请在 PowerShell 中设置后重试（仅当前会话有效）：\n"
    '  $env:MINERU_API_KEY = "你的 MinerU API Key"\n'
    '  $env:DEEPSEEK_API_KEY = "你的 DeepSeek API Key"\n'
    "如需长期生效，可写入用户级环境变量：\n"
    '  [Environment]::SetEnvironmentVariable("MINERU_API_KEY", "你的 Key", "User")\n'
    '  [Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "你的 Key", "User")'
)


def _read_env(name: str) -> str | None:
    """读取环境变量；空字符串/纯空白视为未设置。"""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _require(name: str) -> str:
    value = _read_env(name)
    if value is None:
        raise CredentialsError(
            f"缺少必需的环境变量 {name}。\n{_POWERSHELL_HINT}"
        )
    return value


def require_mineru_api_key() -> str:
    """返回 MinerU API key；缺失时抛 :class:`CredentialsError`（退出码 3）。"""
    return _require(MINERU_API_KEY_ENV)


def require_deepseek_api_key() -> str:
    """返回 DeepSeek API key；缺失时抛 :class:`CredentialsError`（退出码 3）。"""
    return _require(DEEPSEEK_API_KEY_ENV)


def validate_credentials(*, need_mineru: bool = True, need_deepseek: bool = True) -> None:
    """CLI 启动时一次性校验所需凭据；一次列出全部缺失项（research R13）。

    任一缺失即抛 :class:`CredentialsError`，消息含 PowerShell 设置指引。
    """
    missing: list[str] = []
    if need_mineru and _read_env(MINERU_API_KEY_ENV) is None:
        missing.append(MINERU_API_KEY_ENV)
    if need_deepseek and _read_env(DEEPSEEK_API_KEY_ENV) is None:
        missing.append(DEEPSEEK_API_KEY_ENV)
    if missing:
        names = "、".join(missing)
        raise CredentialsError(
            f"缺少必需的环境变量：{names}。\n{_POWERSHELL_HINT}"
        )


# ---------------------------------------------------------------------------
# DeepSeek 转写模型（research R3/R4）
# ---------------------------------------------------------------------------

#: 固定模型名，集中定义便于将来切换（research R3）
DEEPSEEK_MODEL = "deepseek-flash"

#: OpenAI 兼容端点
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

#: 显式关闭思考模式（转写是受控转换而非难题推理），通过 openai SDK extra_body 透传
DEEPSEEK_EXTRA_BODY = {"thinking": {"type": "disabled"}}

#: 单次请求超时（秒）
DEEPSEEK_TIMEOUT_SECONDS = 120

#: 失败（超时/5xx/429）有限重试次数（不含首次调用）
DEEPSEEK_MAX_RETRIES = 2

#: 429/退避基础秒数，实际退避按重试次数指数增长
DEEPSEEK_RETRY_BACKOFF_SECONDS = 2.0

#: 单次响应最大输出 token（显式给出，避免部分端点默认上限截断长 JSON：
#: planner 对真实论文逐块决策，plan.json 可能数千 token；8192 为 DeepSeek
#: 官方 chat/completions 输出上限）
DEEPSEEK_MAX_TOKENS = 8192


# ---------------------------------------------------------------------------
# MinerU 解析服务（research R1）
# ---------------------------------------------------------------------------

MINERU_BASE_URL = "https://mineru.net/api/v4"

#: MinerU 模型版本：vlm 精准解析
MINERU_MODEL_VERSION = "vlm"

#: 普通 API 请求超时（秒，申请链接/轮询）
MINERU_TIMEOUT_SECONDS = 30

#: 上传 PDF / 下载结果 ZIP 超时（秒）
MINERU_UPLOAD_TIMEOUT_SECONDS = 120

#: 轮询任务状态间隔（秒）
MINERU_POLL_INTERVAL_SECONDS = 3


# ---------------------------------------------------------------------------
# arXiv 归一化（research R12）、路径常量、并发
# ---------------------------------------------------------------------------

#: 统一下载入口；{arxiv_id} 含 vN 版本号时原样保留
ARXIV_PDF_URL_TEMPLATE = "https://arxiv.org/pdf/{arxiv_id}"

#: 仓库根（本文件位于 <root>/essay_transformer/config.py）
REPO_ROOT = Path(__file__).resolve().parent.parent

#: 全部阶段产物根目录：runs/<paper_id>/
RUNS_DIR = REPO_ROOT / "runs"

#: 跨论文种子术语表目录
GLOSSARIES_DIR = REPO_ROOT / "glossaries"

#: 种子术语表文件
SEED_GLOSSARY_PATH = GLOSSARIES_DIR / "seed.yml"

#: LLM prompt 模板目录
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

#: 章节转写默认并发（cli.md convert --jobs 默认值；research R6 有限并发 ≤4）
DEFAULT_JOBS = 4
