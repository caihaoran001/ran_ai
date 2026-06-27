from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.core.config import Settings


def create_agent_chat_model(settings: Settings):
    return ChatOpenAI(
        model=settings.ai_model,
        api_key=settings.ai_api_key or "missing",
        base_url=settings.ai_base_url,
        temperature=settings.ai_temperature,
        max_tokens=min(settings.ai_max_tokens, 4000),
        timeout=settings.ai_timeout_seconds,
    )
