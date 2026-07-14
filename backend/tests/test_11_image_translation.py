import asyncio
from types import SimpleNamespace

import pytest

from app.services.ai_service.client import (
    AIService,
    _conservative_image_description,
    _image_description_needs_fallback,
    _normalized_image_timeout,
)
from app.services.ranren_ai.attachments import _download_media_bytes


class _FakeResp:
    def __init__(self, status_code: int, content: bytes, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _ChunkingClient:
    """模拟企微媒体网关：单次响应最多 chunk 字节，>chunk 时以 206 + Content-Range 分片返回。"""

    def __init__(self, full: bytes, chunk: int = 512 * 1024) -> None:
        self.full = full
        self.chunk = chunk
        self.calls: list[tuple[int, int]] = []

    async def get(self, url: str, headers: dict | None = None) -> _FakeResp:
        headers = headers or {}
        start = 0
        rng = headers.get("Range")
        if rng:  # 形如 "bytes=524288-"
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
        end = min(start + self.chunk, len(self.full))
        body = self.full[start:end]
        self.calls.append((start, end))
        total = len(self.full)
        if total <= self.chunk:
            # 小文件：一次性 200 全量返回
            return _FakeResp(200, body, {"accept-ranges": "bytes"})
        return _FakeResp(
            206,
            body,
            {"accept-ranges": "bytes", "content-range": f"bytes {start}-{end - 1}/{total}"},
        )


@pytest.mark.asyncio
async def test_download_media_bytes_reassembles_chunked_response() -> None:
    # ~1.3MB → 需要 3 个 512KB 分片；构造末尾带 JPEG EOI，验证不再被截断
    full = (bytes(range(256)) * 5200) + b"\xff\xd9"
    client = _ChunkingClient(full)

    result = await _download_media_bytes(client, "http://x/img", {}, max_bytes=8 * 1024 * 1024)

    assert result == full
    assert result[-2:] == b"\xff\xd9"  # EOI 完整
    assert len(client.calls) >= 3  # 确实做了多次 Range 续传


@pytest.mark.asyncio
async def test_download_media_bytes_small_file_single_request() -> None:
    full = b"\x89PNG\r\n\x1a\n" + b"a" * (100 * 1024)  # <512KB，单次 200
    client = _ChunkingClient(full)

    result = await _download_media_bytes(client, "http://x/img", {}, max_bytes=8 * 1024 * 1024)

    assert result == full
    assert len(client.calls) == 1  # 未触发续传


@pytest.mark.asyncio
async def test_download_media_bytes_rejects_oversize_before_full_download() -> None:
    full = b"z" * (9 * 1024 * 1024)  # 9MB > 8MB 上限
    client = _ChunkingClient(full)

    with pytest.raises(ValueError, match="too large"):
        await _download_media_bytes(client, "http://x/img", {}, max_bytes=8 * 1024 * 1024)

    assert len(client.calls) == 1  # 读到 Content-Range 总大小即 fail-fast，不再续传


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
            qwen_model="qwen3.7-plus",
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
