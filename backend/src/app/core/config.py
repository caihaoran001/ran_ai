from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "燃AI 1.0"
    api_prefix: str = "/api"
    environment: str = "development"
    frontend_public_base_url: str = Field(
        default="http://localhost:3002",
        validation_alias="FRONTEND_PUBLIC_BASE_URL",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://ran_ai:ran_ai_password@localhost:54322/ran_ai",
        validation_alias="DATABASE_URL",
    )

    ai_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias="AI_BASE_URL",
    )
    ai_api_key: str = Field(default="", validation_alias="AI_API_KEY")
    ai_model: str = Field(default="deepseek-v4-flash", validation_alias="AI_MODEL")
    ai_temperature: float = Field(default=0.3, validation_alias="AI_TEMPERATURE")
    ai_max_tokens: int = Field(default=1800, validation_alias="AI_MAX_TOKENS")
    ai_timeout_seconds: float = Field(default=60.0, validation_alias="AI_TIMEOUT_SECONDS")
    document_output_dir: str = Field(
        default="generated_documents",
        validation_alias="DOCUMENT_OUTPUT_DIR",
    )
    knowledge_base_dir: str = Field(default="", validation_alias="KNOWLEDGE_BASE_DIR")
    knowledge_base_top_k: int = Field(default=3, validation_alias="KNOWLEDGE_BASE_TOP_K")

    wecom_bot_id: str = Field(default="", validation_alias="WECOM_BOT_ID")
    wecom_secret: str = Field(default="", validation_alias="WECOM_SECRET")
    wecom_websocket_url: str = Field(
        default="wss://openws.work.weixin.qq.com",
        validation_alias="WECOM_WEBSOCKET_URL",
    )
    wecom_dm_policy: str = Field(default="allowlist", validation_alias="WECOM_DM_POLICY")
    wecom_allowed_users: str = Field(default="", validation_alias="WECOM_ALLOWED_USERS")
    wecom_group_policy: str = Field(
        default="allowlist",
        validation_alias="WECOM_GROUP_POLICY",
    )
    wecom_group_allow_from: str = Field(
        default="",
        validation_alias="WECOM_GROUP_ALLOW_FROM",
    )
    wecom_groups_json: str = Field(default="", validation_alias="WECOM_GROUPS_JSON")
    wecom_worker_enabled: bool = Field(default=False, validation_alias="WECOM_WORKER_ENABLED")

    reminder_enabled: bool = Field(default=False, validation_alias="REMINDER_ENABLED")
    reminder_check_interval_seconds: float = Field(
        default=30.0,
        validation_alias="REMINDER_CHECK_INTERVAL_SECONDS",
    )
    reminder_at: str = Field(default="12:00", validation_alias="REMINDER_AT")
    reminder_timezone: str = Field(
        default="Asia/Shanghai",
        validation_alias="REMINDER_TIMEZONE",
    )
    reminder_weekdays: str = Field(default="1,2,3,4,5", validation_alias="REMINDER_WEEKDAYS")
    reminder_required_pattern: str = Field(
        default="^#打卡",
        validation_alias="REMINDER_REQUIRED_PATTERN",
    )
    reminder_message: str = Field(
        default="现在已经到 {time} 了，还没有收到你的打卡，请补一下。",
        validation_alias="REMINDER_MESSAGE",
    )

    auto_send_documents: bool = Field(
        default=False,
        validation_alias="AUTO_SEND_DOCUMENTS",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
