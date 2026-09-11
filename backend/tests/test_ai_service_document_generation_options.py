from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.services.ai_service import AIService, RetryableAIContentError
from app.services.message_insight_service import analyze_message_insight


class _FakeCompletions:
    def __init__(self, response: object | list[object]):
        self.responses = list(response) if isinstance(response, list) else [response]
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


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


def _service(response: object | list[object], **settings_overrides: object) -> tuple[AIService, _FakeCompletions]:
    settings_values = {
        "AI_API_KEY": "test-key",
        "AI_MODEL": "deepseek-flash",
        "AI_DOCUMENT_MODEL": "",
        "AI_DOCUMENT_FALLBACK_MODEL": "",
        "AI_MAX_TOKENS": 1234,
        "AI_DOCUMENT_MAX_TOKENS": 50000,
        **settings_overrides,
    }
    settings = Settings(_env_file=None, **settings_values)
    service = object.__new__(AIService)
    service.settings = settings
    completions = _FakeCompletions(response)
    service._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    service._record_usage = AsyncMock()
    return service, completions


@pytest.mark.asyncio
async def test_document_generation_keeps_thinking_without_json_mode_and_50000_budget() -> None:
    service, completions = _service(_response(content='{"documents": {}}', reasoning="analysis"))

    result = await service.generate_text(
        scenario="intake_doc",
        system_prompt="only output json",
        user_prompt="build documents",
    )

    assert result == '{"documents": {}}'
    request = completions.calls[0]
    assert request["model"] == "deepseek-flash"
    assert request["max_tokens"] == 50000
    assert "response_format" not in request
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
    usage_payload = service._record_usage.await_args.kwargs
    assert usage_payload["request_payload"]["messages"] == [
        {"role": "system", "content": "reply"},
        {"role": "user", "content": "hello"},
    ]
    assert usage_payload["response_payload"] is not None


@pytest.mark.asyncio
async def test_message_insight_retries_empty_final_content_until_non_empty() -> None:
    service, completions = _service(
        [
            _response(content="", reasoning='{"requires_human_intervention":true}'),
            _response(content='{"requires_human_intervention":false}'),
        ]
    )

    result = await service.generate_text(
        scenario="message_insight",
        system_prompt="only output json",
        user_prompt="classify this message",
    )

    assert result == '{"requires_human_intervention":false}'
    assert len(completions.calls) == 2
    usage_payload = service._record_usage.await_args.kwargs
    assert usage_payload["attempts"] == 2
    assert usage_payload["success"] is True
    assert usage_payload["error_type"] is None


@pytest.mark.asyncio
async def test_message_insight_falls_back_after_three_empty_final_contents() -> None:
    service, completions = _service(
        [
            _response(content="", reasoning="analysis"),
            _response(content="", reasoning="analysis"),
            _response(content="", reasoning="analysis"),
        ]
    )

    with pytest.raises(RetryableAIContentError, match="after 3 attempts"):
        await service.generate_text(
            scenario="message_insight",
            system_prompt="only output json",
            user_prompt="classify this message",
        )

    assert len(completions.calls) == 3
    usage_payload = service._record_usage.await_args.kwargs
    assert usage_payload["attempts"] == 3
    assert usage_payload["success"] is False
    assert usage_payload["error_type"] == "RetryableAIContentError"


@pytest.mark.asyncio
async def test_message_insight_classifier_failure_falls_back_to_human(monkeypatch) -> None:
    class _FailingAI:
        async def extract_message_insight(self, **_: object) -> dict[str, object]:
            raise RetryableAIContentError("empty final content after 3 attempts")

    monkeypatch.setattr("app.services.message_insight_service.AIService", _FailingAI)

    insight = await analyze_message_insight(
        source="chat_message",
        content="刚发现怀孕了，早C午B晚A还能继续喝吗？",
    )

    assert insight["requires_human_intervention"] is True
    assert insight["risk_flags"][0]["code"] == "risk_classifier_unavailable"


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


@pytest.mark.asyncio
async def test_document_success_does_not_call_fallback_model() -> None:
    service, completions = _service(
        _response(content='{"documents": {}}'),
        AI_DOCUMENT_FALLBACK_MODEL="deepseek-v4-pro",
    )

    result = await service.generate_text(
        scenario="intake_doc",
        system_prompt="only output json",
        user_prompt="build documents",
    )

    assert result == '{"documents": {}}'
    assert [call["model"] for call in completions.calls] == ["deepseek-flash"]
    assert service.last_model_for("intake_doc") == "deepseek-flash"


@pytest.mark.asyncio
async def test_empty_content_does_not_switch_model() -> None:
    """空正文是内容质量问题，必须留在原模型重试，不能换到备用模型。

    线上主模型和备用模型应按配置独立；一次空正文不能把后续尝试切到备用模型。
    """
    service, completions = _service(
        [
            _response(content="", reasoning="analysis"),
            _response(content='{"documents": {}}'),
        ],
        AI_DOCUMENT_FALLBACK_MODEL="deepseek-v4-flash-lite",
    )

    with pytest.raises(RetryableAIContentError, match="empty final content"):
        await service.generate_text(
            scenario="intake_doc",
            system_prompt="only output json",
            user_prompt="build documents",
        )

    assert [call["model"] for call in completions.calls] == ["deepseek-flash"]


@pytest.mark.asyncio
async def test_truncation_does_not_switch_model() -> None:
    """撞输出上限换模型同样没有帮助，只会把内容交给更弱的模型。"""
    service, completions = _service(
        [
            _response(content="{", finish_reason="length"),
            _response(content='{"documents": {}}'),
        ],
        AI_DOCUMENT_FALLBACK_MODEL="deepseek-v4-flash-lite",
    )

    with pytest.raises(RetryableAIContentError, match="AI_DOCUMENT_MAX_TOKENS"):
        await service.generate_text(
            scenario="intake_doc",
            system_prompt="only output json",
            user_prompt="build documents",
        )

    assert [call["model"] for call in completions.calls] == ["deepseek-flash"]


@pytest.mark.asyncio
async def test_availability_failure_still_switches_model() -> None:
    """「这个模型现在叫不通」仍然换模型：可用性故障是备用模型唯一的用途。"""
    from openai import APITimeoutError

    # 同模型内部先重试 3 次超时（_create_chat_completion_with_retry），耗尽后才轮到备用模型。
    service, completions = _service(
        [
            APITimeoutError(request=SimpleNamespace()),
            APITimeoutError(request=SimpleNamespace()),
            APITimeoutError(request=SimpleNamespace()),
            _response(content='{"documents": {}}'),
        ],
        AI_DOCUMENT_FALLBACK_MODEL="deepseek-v4-pro",
    )

    result = await service.generate_text(
        scenario="intake_doc",
        system_prompt="only output json",
        user_prompt="build documents",
    )

    assert result == '{"documents": {}}'
    assert [call["model"] for call in completions.calls] == [
        "deepseek-flash",
        "deepseek-flash",
        "deepseek-flash",
        "deepseek-v4-pro",
    ]
    assert service.last_model_for("intake_doc") == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_post_validation_failure_can_switch_the_next_attempt_to_fallback() -> None:
    service, completions = _service(
        _response(content='{"documents": {}}'),
        AI_DOCUMENT_FALLBACK_MODEL="deepseek-v4-pro",
    )

    assert service.activate_fallback_model("intake_doc") is True
    await service.generate_text(
        scenario="intake_doc",
        system_prompt="only output json",
        user_prompt="regenerate structurally complete documents",
    )

    assert [call["model"] for call in completions.calls] == ["deepseek-v4-pro"]
    assert service.last_model_for("intake_doc") == "deepseek-v4-pro"
