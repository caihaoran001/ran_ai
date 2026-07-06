import asyncio
from types import SimpleNamespace

import pytest

from app.services.ai_service.client import (
    AIService,
    _conservative_image_description,
    _image_description_needs_fallback,
    _normalized_image_timeout,
)


def test_image_description_needs_fallback_for_uncertain_text() -> None:
    description = "用户上传了一张体重秤照片，屏幕数字看不清，置信度：低。"

    assert _image_description_needs_fallback(description) is True


def test_image_description_needs_fallback_for_scale_without_number() -> None:
    description = "用户上传了一张体重秤照片，屏幕区域可见但反光明显。"

    assert _image_description_needs_fallback(description) is True


def test_conservative_description_marks_uncertain_result() -> None:
    description = "用户上传了一张餐食照片，可见一份餐食，具体食材无法确认，置信度：低。"

    result = _conservative_image_description(description)

    assert "不要把该识别结果当作确定事实" in result
    assert "请用户确认关键数字或食物细节" in result


def test_normalized_image_timeout_defaults_to_three_minutes() -> None:
    assert _normalized_image_timeout(None) == 180.0
    assert _normalized_image_timeout(0) == 180.0
    assert _normalized_image_timeout(999) == 300.0


@pytest.mark.asyncio
async def test_translate_image_times_out(monkeypatch) -> None:
    for key in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
        monkeypatch.delenv(key, raising=False)
    service = AIService(
        SimpleNamespace(
            qwen_api_key="dummy",
            qwen_model="qwen3.5-plus",
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            ai_api_key="dummy",
            ai_base_url="https://api.example.com/v1",
            ai_timeout_seconds=60,
            qwen_image_timeout_seconds=0.01,
        )
    )
    usage_records = []

    async def slow_translate_once(**kwargs):
        await asyncio.sleep(0.05)
        return "用户上传了一张图片，置信度：高。"

    async def record_usage(**kwargs):
        usage_records.append(kwargs)

    monkeypatch.setattr(service, "_translate_image_once", slow_translate_once)
    monkeypatch.setattr(service, "_record_usage", record_usage)

    result = await service.translate_image(base64_image="abc")

    assert result == "用户上传了一张图片，但解析描述超时。"
    assert usage_records[-1]["scenario"] == "qwen_vl_translate"
    assert usage_records[-1]["error_type"] == "TimeoutError"
    assert usage_records[-1]["success"] is False
