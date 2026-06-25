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

    app_name: str = "燃人AI 1.0"
    api_prefix: str = "/api"
    environment: str = "development"
    frontend_public_base_url: str = Field(
        default="http://localhost:3002",
        validation_alias="FRONTEND_PUBLIC_BASE_URL",
    )
    backend_admin_api_key: str = Field(
        default="change-me-internal-api-key",
        validation_alias="BACKEND_ADMIN_API_KEY",
    )
    cors_allowed_origins: str = Field(default="", validation_alias="CORS_ALLOWED_ORIGINS")

    database_url: str = Field(
        default="postgresql+asyncpg://ran_ai:ran_ai_password@localhost:54322/ran_ai",
        validation_alias="DATABASE_URL",
    )

    ai_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias="AI_BASE_URL",
    )
    ai_api_key: str = Field(default="", validation_alias="AI_API_KEY")
    ai_model: str = Field(default="deepseek-v4-pro", validation_alias="AI_MODEL")
    # 交付文档/阶段报告专用模型；留空则回退 ai_model，避免聊天回复和风控被一起切到更慢模型。
    ai_document_model: str = Field(default="", validation_alias="AI_DOCUMENT_MODEL")
    # 风险/意图判断(intervention_risk)专用模型；留空则回退 ai_model。判断准确度最关键，可单独配更强模型。
    ai_risk_model: str = Field(default="", validation_alias="AI_RISK_MODEL")
    ai_temperature: float = Field(default=0.3, validation_alias="AI_TEMPERATURE")
    ai_max_tokens: int = Field(default=8000, validation_alias="AI_MAX_TOKENS")

    # 通义千问多模态模型设置
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="QWEN_BASE_URL",
    )
    qwen_api_key: str = Field(default="", validation_alias="QWEN_API_KEY")
    qwen_model: str = Field(default="qwen3-vl-flash", validation_alias="QWEN_MODEL")
    ai_timeout_seconds: float = Field(default=60.0, validation_alias="AI_TIMEOUT_SECONDS")
    ai_max_concurrent_requests: int = Field(
        default=10,
        validation_alias="AI_MAX_CONCURRENT_REQUESTS",
    )
    ai_prompt_cost_per_1m_tokens: float = Field(
        default=0.0,
        validation_alias="AI_PROMPT_COST_PER_1M_TOKENS",
    )
    ai_completion_cost_per_1m_tokens: float = Field(
        default=0.0,
        validation_alias="AI_COMPLETION_COST_PER_1M_TOKENS",
    )
    document_output_dir: str = Field(
        default="generated_documents",
        validation_alias="DOCUMENT_OUTPUT_DIR",
    )
    knowledge_base_dir: str = Field(default="", validation_alias="KNOWLEDGE_BASE_DIR")
    knowledge_base_top_k: int = Field(default=3, validation_alias="KNOWLEDGE_BASE_TOP_K")
    embedding_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="EMBEDDING_BASE_URL",
    )
    embedding_api_key: str = Field(default="", validation_alias="EMBEDDING_API_KEY")
    embedding_model: str = Field(default="text-embedding-v4", validation_alias="EMBEDDING_MODEL")
    knowledge_evolution_enabled: bool = Field(default=False, validation_alias="KNOWLEDGE_EVOLUTION_ENABLED")
    knowledge_evolution_interval_seconds: float = Field(
        default=3600.0,
        validation_alias="KNOWLEDGE_EVOLUTION_INTERVAL_SECONDS",
    )
    knowledge_evolution_lookback_hours: int = Field(default=24, validation_alias="KNOWLEDGE_EVOLUTION_LOOKBACK_HOURS")
    knowledge_evolution_qa_at: str = Field(default="00:00", validation_alias="KNOWLEDGE_EVOLUTION_QA_AT")
    knowledge_evolution_case_days: str = Field(default="3,7,14,21,28", validation_alias="KNOWLEDGE_EVOLUTION_CASE_DAYS")

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

    qiwei_enabled: bool = Field(default=False, validation_alias="QIWEI_ENABLED")
    qiwei_token: str = Field(default="", validation_alias="QIWEI_TOKEN")
    qiwei_guid: str = Field(default="", validation_alias="QIWEI_GUID")
    qiwei_callback_secret: str = Field(default="", validation_alias="QIWEI_CALLBACK_SECRET")
    qiwei_staff_senders: str = Field(default="", validation_alias="QIWEI_STAFF_SENDERS")
    qiwei_handoff_at_user_id: str = Field(
        default="7881300647007710",
        validation_alias="QIWEI_HANDOFF_AT_USER_ID",
    )
    qiwei_base_url: str = Field(
        default="http://manager.qiweapi.com/qiwe",
        validation_alias="QIWEI_BASE_URL",
    )
    qiwei_batch_window_seconds: float = Field(
        default=8.0,
        validation_alias="QIWEI_BATCH_WINDOW_SECONDS",
    )

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
    reminder_message: str = Field(
        default="现在已经到 {time} 了，还没有收到你的打卡，请补一下。",
        validation_alias="REMINDER_MESSAGE",
    )
    inactive_followup_enabled: bool = Field(default=True, validation_alias="INACTIVE_FOLLOWUP_ENABLED")
    inactive_followup_hours: int = Field(default=24, validation_alias="INACTIVE_FOLLOWUP_HOURS")
    inactive_followup_message: str = Field(
        default="今天先稳住一个小动作就好。你这边如果遇到执行卡点，可以直接发群里，我来帮你一起拆小一点。",
        validation_alias="INACTIVE_FOLLOWUP_MESSAGE",
    )
    outcome_snapshots_enabled: bool = Field(default=True, validation_alias="OUTCOME_SNAPSHOTS_ENABLED")

    auto_send_documents: bool = Field(
        default=False,
        validation_alias="AUTO_SEND_DOCUMENTS",
    )
    faq_vector_distance_threshold: float = Field(
        default=0.35,
        validation_alias="FAQ_VECTOR_DISTANCE_THRESHOLD",
    )
    case_vector_distance_threshold: float = Field(
        default=0.45,
        validation_alias="CASE_VECTOR_DISTANCE_THRESHOLD",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
