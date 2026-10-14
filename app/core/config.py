from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_env: str = "dev"
    log_level: str = "INFO"
    data_dir: Path = Path("data")
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3307
    mysql_database: str = "contract_review"
    mysql_user: str = "cra"
    mysql_password: SecretStr
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    llm_enabled: bool = False
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model_name: str = "deepseek-flash"
    llm_timeout_seconds: int = 25
    llm_max_retries: int = 1
    llm_concurrency: int = 4
    ocr_enabled: bool = False
    ocr_base_url: str = "http://127.0.0.1:8100"
    ocr_api_key: SecretStr = SecretStr("")
    ocr_health_path: str = "/health"
    ocr_timeout_seconds: int = 90
    approval_mode: str = "mock"
    approval_base_url: str = "http://127.0.0.1:8000"
    max_upload_mb: int = 50
    task_concurrency: int = 5
    task_timeout_seconds: int = 480

    @property
    def storage_path(self) -> Path:
        return self.data_dir if self.data_dir.is_absolute() else ROOT / self.data_dir

    def validate_startup(self) -> None:
        if not self.mysql_password.get_secret_value():
            raise RuntimeError("请在 .env 中配置 MYSQL_PASSWORD")
        if self.llm_enabled and not self.llm_api_key.get_secret_value():
            raise RuntimeError("请在 .env 中配置 LLM_API_KEY")
        if self.approval_mode != "mock":
            raise RuntimeError("基础环境阶段仅支持 APPROVAL_MODE=mock")


@lru_cache
def get_settings() -> Settings:
    return Settings()
