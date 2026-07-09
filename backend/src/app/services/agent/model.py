from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.core.config import Settings


def create_agent_chat_model(settings: Settings):
    # 缺 key 直接 fail fast：塞 "missing" 只会让首个 LLM 调用报 401，错误信息还指向鉴权而非配置缺失，排查更绕。
    if not settings.ai_api_key:
        raise RuntimeError("AI_API_KEY 未配置，无法创建 Agent 对话模型；请检查后端配置。")
    return ChatOpenAI(
        model=settings.ai_model,
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        temperature=settings.ai_temperature,
        max_tokens=min(settings.ai_max_tokens, 4000),
        timeout=settings.ai_timeout_seconds,
    )
