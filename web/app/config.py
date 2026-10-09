"""应用配置：全部来自环境变量 / web/.env，启动时校验（宪法原则二、四）。"""

from __future__ import annotations

import logging
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("web")

# web/app/config.py -> 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_URL = f"sqlite:///{(REPO_ROOT / 'data' / 'app.db').as_posix()}"
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"

_FERNET_GEN_CMD = (
    'python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)


class Settings(BaseSettings):
    """运行配置。缺失 WEB_JWT_SECRET 时快速失败，绝不使用默认密钥。"""

    model_config = SettingsConfigDict(
        env_file=str(WEB_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    jwt_secret: str = Field(default="", alias="WEB_JWT_SECRET")
    master_key: str = Field(default="", alias="WEB_MASTER_KEY")
    token_expire_minutes: int = Field(default=720, alias="WEB_TOKEN_EXPIRE_MINUTES")
    database_url: str = Field(default="", alias="WEB_DATABASE_URL")
    cors_origins_raw: str = Field(
        default="http://localhost:5173", alias="WEB_CORS_ORIGINS"
    )

    # Spec 2：任务流水线
    fake_mode: bool = Field(default=False, alias="WEB_FAKE_MODE")
    max_concurrent_tasks: int = Field(default=2, alias="WEB_MAX_CONCURRENT_TASKS")
    upload_max_mb: int = Field(default=50, alias="WEB_UPLOAD_MAX_MB")
    runs_dir: Path = Field(default=DEFAULT_RUNS_DIR, alias="WEB_RUNS_DIR")
    mineru_verify_ssl: bool = Field(default=True, alias="WEB_MINERU_VERIFY_SSL")

    # 进程内临时 Fernet 密钥缓存（仅 fake 模式且主密钥为空时使用）
    _temp_fernet_key: bytes | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if not self.jwt_secret.strip():
            raise RuntimeError(
                "WEB_JWT_SECRET 未配置。请复制 web/.env.example 为 web/.env 并填入随机密钥；"
                '生成命令：python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        if self.max_concurrent_tasks < 1:
            raise ValueError("WEB_MAX_CONCURRENT_TASKS 必须 >= 1")
        if self.upload_max_mb < 1:
            raise ValueError("WEB_UPLOAD_MAX_MB 必须 >= 1")
        if not self.master_key.strip() and not self.fake_mode:
            raise RuntimeError(
                "WEB_MASTER_KEY 未配置：真实模式必须提供 Fernet 主密钥用于加密第三方密钥；"
                f"生成命令：{_FERNET_GEN_CMD}（仅 WEB_FAKE_MODE=1 允许留空）"
            )
        return self

    @field_validator("database_url", mode="before")
    @classmethod
    def _default_db(cls, v: str) -> str:
        return v.strip() if v and v.strip() else DEFAULT_DB_URL

    @field_validator("runs_dir", mode="before")
    @classmethod
    def _default_runs_dir(cls, v) -> Path:
        if v is None or (isinstance(v, str) and not v.strip()):
            return DEFAULT_RUNS_DIR
        return Path(v)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    @property
    def fernet_key(self) -> bytes:
        """返回 Fernet 主密钥字节；非法密钥快速失败。

        - 主密钥非空：校验合法性后返回（整个进程唯一加密口径）；
        - 主密钥为空（仅可能发生在 fake 模式，validator 已兜底）：
          首次取用时生成进程内临时密钥并 warning，同一 Settings 实例内缓存。
        """
        raw = self.master_key.strip()
        if raw:
            try:
                key = raw.encode("utf-8")
                Fernet(key)
            except (ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"WEB_MASTER_KEY 不是合法的 Fernet 密钥（{exc}）；"
                    f"生成命令：{_FERNET_GEN_CMD}"
                ) from exc
            return key
        if self._temp_fernet_key is None:
            self._temp_fernet_key = Fernet.generate_key()
            logger.warning(
                "WEB_MASTER_KEY 为空：假模式已启用，本次进程使用临时 Fernet 密钥，"
                "已加密凭据在进程重启后无法解密；生产部署请设置 WEB_MASTER_KEY（%s）",
                _FERNET_GEN_CMD,
            )
        return self._temp_fernet_key


def get_settings() -> Settings:
    """工厂函数，便于测试用环境变量/依赖覆盖重建配置。"""
    return Settings()
