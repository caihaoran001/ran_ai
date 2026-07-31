from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.services.ai_service import AIService, RetryableAIContentError


class _FakeCompletions:
    def __init__(self, response: object):
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response


def _response(*, content: str, reasoning: str = "", finish_reason: str = "stop") -> object:
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=200,
        total_tokens=300,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=50),
    )
    message = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish_reason, message=message)],
        usage=usage,
    )


def _service(response: object, **settings_overrides: object) -> tuple[AIService, _FakeCompletions]:
    settings = Settings(
        _env_file=None,
        AI_API_KEY="test-key",
        AI_MODEL="deepseek-v4-flash",
        AI_DOCUMENT_MODEL="deepseek-v4-flash",
        AI_MAX_TOKENS=1234,
        AI_DOCUMENT_MAX_TOKENS=50000,
        **settings_overrides,
    )
    service = object.__new__(AIService)
    service.settings = settings
    completions = _FakeCompletions(response)
    service._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    service._record_usage = AsyncMock()
    return service, completions


@pytest.mark.asyncio
async def test_document_generation_keeps_thinking_json_mode_and_50000_budget() -> None:
    service, completions = _service(_response(content='{"documents": {}}', reasoning="analysis"))

    result = await service.generate_text(
        scenario="intake_doc",
        system_prompt="only output json",
        user_prompt="build documents",
    )

    assert result == '{"documents": {}}'
    request = completions.calls[0]
    assert request["max_tokens"] == 50000
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert request["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_non_document_generation_keeps_global_budget_without_document_options() -> None:
    service, completions = _service(_response(content="normal reply"))

    result = await service.generate_text(
        scenario="generic",
        system_prompt="reply",
        user_prompt="hello",
    )

    assert result == "normal reply"
    request = completions.calls[0]
    assert request["max_tokens"] == 1234
    assert "response_format" not in request
    assert "extra_body" not in request


@pytest.mark.asyncio
async def test_reasoning_is_never_used_as_delivery_document_fallback() -> None:
    service, _ = _service(_response(content="", reasoning='{"documents": {}}'))

    with pytest.raises(RetryableAIContentError, match="empty final content"):
        await service.generate_text(
            scenario="intake_doc",
            system_prompt="only output json",
            user_prompt="build documents",
        )


@pytest.mark.asyncio
async def test_document_truncation_names_document_budget() -> None:
    service, _ = _service(_response(content="{", finish_reason="length"))

    with pytest.raises(RetryableAIContentError, match="AI_DOCUMENT_MAX_TOKENS"):
        await service.generate_text(
            scenario="intake_doc",
            system_prompt="only output json",
            user_prompt="build documents",
        )
