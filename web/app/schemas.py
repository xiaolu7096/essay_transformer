"""请求/响应契约（Pydantic）。前后端契约以 OpenAPI 为准（宪法原则六）。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, field_validator

# 用户名：字母/数字/下划线/中文，3–30 字符。
# 中文区间用 chr() 构造，保证源码中不出现中文字面量（research D1）。
_CJK_RANGE = chr(0x4E00) + "-" + chr(0x9FFF)
USERNAME_PATTERN = r"^[A-Za-z0-9_" + _CJK_RANGE + r"]{3,30}$"

UsernameStr = Annotated[
    str,
    BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v),
    StringConstraints(strip_whitespace=False, pattern=USERNAME_PATTERN),
]
PasswordStr = Annotated[str, Field(min_length=8, max_length=64)]


class UserCreate(BaseModel):
    username: UsernameStr
    password: PasswordStr


class UserOut(BaseModel):
    id: str
    username: str
    created_at: datetime


class LoginRequest(BaseModel):
    # 登录对用户名规则不做注册级强校验；非空即可，凭据对错统一走 401
    username: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


# --------------------------------------------------------------------------
# Spec 2 US1：第三方密钥
# --------------------------------------------------------------------------

ServiceName = Literal["mineru", "deepseek"]


class CredentialUpsertRequest(BaseModel):
    secret: Annotated[str, StringConstraints(min_length=1, max_length=200)]

    @field_validator("secret")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("密钥不能为空，长度 1-200 个字符")
        # 存储口径：去除首尾空白；长度按去空白后计
        if not 1 <= len(stripped) <= 200:
            raise ValueError("密钥不能为空，长度 1-200 个字符")
        return stripped


class CredentialStatusOut(BaseModel):
    service: ServiceName
    configured: bool
    mask: str | None


class CredentialListOut(BaseModel):
    credentials: list[CredentialStatusOut]


# --------------------------------------------------------------------------
# Spec 2 US2/US3：任务
# --------------------------------------------------------------------------

SourceKind = Literal["arxiv", "upload"]
TaskStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
StageName = Literal["ingest", "parse", "normalize", "plan", "rewrite", "assemble"]
FailureCategory = Literal["input", "service", "system"]


class FromUrlRequest(BaseModel):
    # 额外字段（如请求体夹带 fake_mode）一律忽略：开关唯一来源是服务端配置（FR-013）
    model_config = {"extra": "ignore"}

    source: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class TaskFailure(BaseModel):
    category: FailureCategory
    code: str
    message: str


class TaskResult(BaseModel):
    title: str
    section_count: int
    total_chars: int


class TaskOut(BaseModel):
    """任务统一形态（列表项与详情同形；不含任何路径或密文字段）。"""

    id: str
    source_kind: SourceKind
    source_ref: str
    status: TaskStatus
    stage: StageName | None = None
    progress: float
    message: str | None = None
    fake_mode: bool
    failure: TaskFailure | None = None
    result: TaskResult | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskListOut(BaseModel):
    tasks: list[TaskOut]


# --------------------------------------------------------------------------
# Spec 3 US1：实时事件帧载荷（TaskOut 同口径 + 信封 seq；无路径/密文字段）
# --------------------------------------------------------------------------


class TaskEventPayload(BaseModel):
    """SSE snapshot/update/terminal 三帧统一载荷。

    字段命名、枚举与 failure/result 嵌套与 TaskOut 逐字一致；
    seq 为任务内单调水位（snapshot 时可能为 0）。
    """

    model_config = {"extra": "ignore"}

    id: str
    status: TaskStatus
    stage: StageName | None = None
    progress: float
    message: str | None = None
    fake_mode: bool
    failure: TaskFailure | None = None
    result: TaskResult | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    seq: int = Field(ge=0)


# --------------------------------------------------------------------------
# Spec 3 US2：成品阅读器视图模型（不含任何服务器路径）
# --------------------------------------------------------------------------


class ReaderSection(BaseModel):
    slug: str
    heading: str
    content: str


class ReaderOut(BaseModel):
    model_config = {"extra": "ignore"}

    task_id: str
    title: str
    fake_mode: bool
    total_chars: int
    sections: list[ReaderSection]
