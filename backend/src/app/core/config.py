from __future__ import annotations

import logging
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
    # 应用日志级别。默认 INFO：不配这个的话，uvicorn 不给 root logger 挂 handler，
    # app 自己 logger.info(...) 会被 Python 兜底逻辑丢弃(只剩 WARNING+)，
    # 导致档案官/提醒等后台任务的运行日志在生产完全不可见。
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
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
    # 风险/意图判断(message_insight 场景)专用模型；留空则回退 ai_model。判断准确度最关键，可单独配更强模型。
    ai_risk_model: str = Field(default="", validation_alias="AI_RISK_MODEL")
    ai_temperature: float = Field(default=0.3, validation_alias="AI_TEMPERATURE")
    ai_max_tokens: int = Field(default=8000, validation_alias="AI_MAX_TOKENS")

    # 通义千问多模态模型设置
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="QWEN_BASE_URL",
    )
    qwen_api_key: str = Field(default="", validation_alias="QWEN_API_KEY")
    qwen_model: str = Field(default="qwen3.7-plus", validation_alias="QWEN_MODEL")
    qwen_image_timeout_seconds: float = Field(default=180.0, validation_alias="QWEN_IMAGE_TIMEOUT_SECONDS")
    # Qwen image translation pricing (China mainland, CNY per 1M tokens).
    # The tier is selected by request input tokens; image OCR requests are usually under 128K.
    qwen_prompt_cost_under_128k_per_1m_tokens: float = Field(
        default=2.0,
        validation_alias="QWEN_PROMPT_COST_UNDER_128K_PER_1M_TOKENS",
    )
    qwen_completion_cost_under_128k_per_1m_tokens: float = Field(
        default=8.0,
        validation_alias="QWEN_COMPLETION_COST_UNDER_128K_PER_1M_TOKENS",
    )
    qwen_prompt_cost_128k_to_256k_per_1m_tokens: float = Field(
        default=2.0,
        validation_alias="QWEN_PROMPT_COST_128K_TO_256K_PER_1M_TOKENS",
    )
    qwen_completion_cost_128k_to_256k_per_1m_tokens: float = Field(
        default=12.0,
        validation_alias="QWEN_COMPLETION_COST_128K_TO_256K_PER_1M_TOKENS",
    )
    qwen_prompt_cost_over_256k_per_1m_tokens: float = Field(
        default=4.0,
        validation_alias="QWEN_PROMPT_COST_OVER_256K_PER_1M_TOKENS",
    )
    qwen_completion_cost_over_256k_per_1m_tokens: float = Field(
        default=24.0,
        validation_alias="QWEN_COMPLETION_COST_OVER_256K_PER_1M_TOKENS",
    )
    ai_timeout_seconds: float = Field(default=300.0, validation_alias="AI_TIMEOUT_SECONDS")
    ai_max_concurrent_requests: int = Field(
        default=10,
        validation_alias="AI_MAX_CONCURRENT_REQUESTS",
    )
    agent_max_llm_calls: int = Field(default=5, validation_alias="AGENT_MAX_LLM_CALLS")
    agent_max_tool_calls: int = Field(default=8, validation_alias="AGENT_MAX_TOOL_CALLS")
    agent_timeout_seconds: float = Field(default=40.0, validation_alias="AGENT_TIMEOUT_SECONDS")
    web_search_enabled: bool = Field(default=False, validation_alias="WEB_SEARCH_ENABLED")
    web_search_provider: str = Field(default="tavily", validation_alias="WEB_SEARCH_PROVIDER")
    tavily_api_key: str = Field(default="", validation_alias="TAVILY_API_KEY")
    web_search_timeout_seconds: float = Field(default=8.0, validation_alias="WEB_SEARCH_TIMEOUT_SECONDS")
    web_search_max_results: int = Field(default=5, validation_alias="WEB_SEARCH_MAX_RESULTS")
    ai_prompt_cost_per_1m_tokens: float = Field(
        default=3.0,
        validation_alias="AI_PROMPT_COST_PER_1M_TOKENS",
    )
    ai_completion_cost_per_1m_tokens: float = Field(
        default=6.0,
        validation_alias="AI_COMPLETION_COST_PER_1M_TOKENS",
    )
    # DeepSeek 硬盘缓存命中的输入单价（元/百万 token），远低于未命中价(flash 0.02 / pro 0.025)。
    # <=0 视为未配置：命中 token 回退按未命中价计，即保持历史行为不打折。
    ai_prompt_cache_hit_cost_per_1m_tokens: float = Field(
        default=0.0,
        validation_alias="AI_PROMPT_CACHE_HIT_COST_PER_1M_TOKENS",
    )
    # DeepSeek 高峰时段(北京 9:00–12:00、14:00–18:00)价格倍率。官方"高峰 2×"尚未生效，
    # 默认 1.0 = 不加价；官方正式启用后改成 2.0 即可，无需改代码。仅作用于 DeepSeek(非 Qwen)。
    ai_peak_price_multiplier: float = Field(
        default=1.0,
        validation_alias="AI_PEAK_PRICE_MULTIPLIER",
    )
    # DeepSeek 账户余额低于阈值时向指定群告警。仅 DeepSeek(ai_base_url 指向 deepseek)可查余额。
    ai_balance_alert_enabled: bool = Field(
        default=False,
        validation_alias="AI_BALANCE_ALERT_ENABLED",
    )
    # 余额(元)低于此值即触发告警；同时用于后台看板"余额偏低"变红判定，单一真相源。
    ai_balance_alert_threshold_yuan: float = Field(
        default=10.0,
        validation_alias="AI_BALANCE_ALERT_THRESHOLD_YUAN",
    )
    # 告警推送目标群/人的 channel chat_id（留空则不推送，只在看板显示）。
    ai_balance_alert_group_id: str = Field(
        default="",
        validation_alias="AI_BALANCE_ALERT_GROUP_ID",
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
    qiwei_shutdown_drain_seconds: float = Field(
        default=60.0,
        validation_alias="QIWEI_SHUTDOWN_DRAIN_SECONDS",
    )

    # 注意：12点泛化文字催打卡(check_once)已退役并入打卡追问(check_checkin_followup_v2_once)。
    # 本开关现仅剩门控阶段报告生成(check_stage_reports_once)，reminder_at/reminder_message 已不再使用。
    reminder_enabled: bool = Field(default=False, validation_alias="REMINDER_ENABLED")
    agent_reminders_enabled: bool = Field(default=True, validation_alias="AGENT_REMINDERS_ENABLED")
    reminder_check_interval_seconds: float = Field(
        default=30.0,
        validation_alias="REMINDER_CHECK_INTERVAL_SECONDS",
    )
    # 定时提醒「过点即弃」阈值：PENDING 提醒的 remind_at 已过去超过这么多分钟，就不再补发
    # （退下期间到点 / 部署重启补跑积压），标 SKIPPED 并把循环序列顺延到下一次。
    reminder_stale_after_minutes: float = Field(
        default=20.0,
        validation_alias="REMINDER_STALE_AFTER_MINUTES",
    )
    reminder_at: str = Field(default="12:00", validation_alias="REMINDER_AT")  # 已弃用(随 check_once 退役)
    reminder_timezone: str = Field(
        default="Asia/Shanghai",
        validation_alias="REMINDER_TIMEZONE",
    )
    reminder_weekdays: str = Field(default="1,2,3,4,5", validation_alias="REMINDER_WEEKDAYS")
    reminder_message: str = Field(  # 已弃用(随 check_once 退役，不再使用)
        default="现在已经到 {time} 了，还没有收到你的打卡，请补一下。",
        validation_alias="REMINDER_MESSAGE",
    )
    outcome_snapshots_enabled: bool = Field(default=True, validation_alias="OUTCOME_SNAPSHOTS_ENABLED")
    # 群体共同洞察：每日在指定小时检查一次，实际只在周期到期或新增用户达阈值时调用模型。
    population_insight_enabled: bool = Field(
        default=True,
        validation_alias="POPULATION_INSIGHT_ENABLED",
    )
    population_insight_auto_hour: int = Field(
        default=3,
        ge=0,
        le=23,
        validation_alias="POPULATION_INSIGHT_AUTO_HOUR",
    )
    # 档案官（Archivist）总开关：聊天转打卡的后台扫描。默认开；缺省不注入也为开，
    # 需临时停用时在 compose environment 设 ARCHIVIST_ENABLED=false。
    archivist_enabled: bool = Field(default=True, validation_alias="ARCHIVIST_ENABLED")
    # 打卡追问(H5 完整替代)：外部确定性触发，日五项/矛盾核对/次晨空腹提醒分窗口发送，每天最多 3 条。
    #  - 里程碑前夜：晚间只提醒用户次晨起床如厕后空腹测量并打卡，不要求当晚称重。
    #  - 日五项：次日早上回顾昨日执行单中尚未回答的项。
    # 会发送真实群消息，默认 False=不追问；设 CHECKIN_FOLLOWUP_ENABLED=true 开启。
    checkin_followup_enabled: bool = Field(default=False, validation_alias="CHECKIN_FOLLOWUP_ENABLED")
    # 傍晚窗口[start,end)：① 不活跃兜底按 (case_id,日期) 在窗口内确定性伪随机落一个发送时刻(规避定点风控)；
    # ② 日常缺口追问的发送时间闸——仅当当前时刻落在此窗口内才发缺口追问(早C/午B 此时必已过点、晚A 未到点)。
    checkin_followup_window_start: str = Field(
        default="19:30", validation_alias="CHECKIN_FOLLOWUP_WINDOW_START"
    )
    checkin_followup_window_end: str = Field(
        default="20:30", validation_alias="CHECKIN_FOLLOWUP_WINDOW_END"
    )

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
    knowledge_dedupe_faq_distance_threshold: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        validation_alias="KNOWLEDGE_DEDUPE_FAQ_DISTANCE_THRESHOLD",
    )
    knowledge_dedupe_case_distance_threshold: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        validation_alias="KNOWLEDGE_DEDUPE_CASE_DISTANCE_THRESHOLD",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_logging(settings: Settings | None = None) -> None:
    """给 root logger 挂一个 handler，让 app 自身的 logger.info(...) 能真正输出。

    幂等：已配过 handler(如 uvicorn 已接管或重复调用)则只调级别，不重复加 handler。
    两个入口都要调用——API(app.main:create_app) 与 scheduler(app.workers.scheduler)。
    """
    settings = settings or get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
