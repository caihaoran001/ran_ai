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


# --- 表情原图 / 视频封面：只下载存盘，不进多模态识别 ---


class _SingleShotClient:
    """一次性返回固定内容的假媒体网关。"""

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[str] = []

    async def get(self, url: str, headers: dict | None = None) -> _FakeResp:
        self.calls.append(url)
        return _FakeResp(200, self.content, {})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _patch_media_client(monkeypatch, content: bytes) -> _SingleShotClient:
    from types import SimpleNamespace as _NS
    import app.services.ranren_ai.engine as engine_mod

    client = _SingleShotClient(content)
    monkeypatch.setattr(engine_mod, "httpx", _NS(AsyncClient=lambda *a, **kw: client))
    return client


async def _make_inbound_message(db_session, message_type: str, content: str):
    from app.models.message import (
        Message,
        MessageDirection,
        MessagePurpose,
        MessageSenderType,
        MessageStatus,
    )

    message = Message(
        group_id="10926518308089720",
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type=message_type,
        content=content,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    return message


def _sticker_channel_message(url: str = "https://wework.qpic.cn/wwpic3az/wwwx_abc/0"):
    from app.services.channels.base import ChannelMessage

    return ChannelMessage(
        channel="qiwe",
        chat_id="10926518308089720",
        chat_type="group",
        text="[动画表情]",
        message_type="sticker",
        raw_payload={"cmd": 15000, "msgType": 104, "msgData": {"fileHttpUrl": url, "fileAesKey": ""}},
    )


@pytest.mark.asyncio
async def test_sticker_thumbnail_is_downloaded_without_vision_model(db_session, monkeypatch, tmp_path):
    from app.services.ranren_ai.attachments import InboundAttachmentProcessor

    png = b"\x89PNG\r\n\x1a\n" + b"sticker-bytes"
    client = _patch_media_client(monkeypatch, png)
    monkeypatch.chdir(tmp_path)

    message = await _make_inbound_message(db_session, "sticker", "[动画表情]")
    await InboundAttachmentProcessor()._process_inbound_thumbnail(message.id, _sticker_channel_message())
    await db_session.refresh(message)

    info = (message.analysis_json or {}).get("image_info")
    assert info is not None, "表情原图应当存盘并写入 image_info"
    assert info["content_type"] == "image/png"
    assert info["thumbnail_kind"] == "sticker"
    assert (tmp_path / ".wecom_cache/images" / info["filename"]).read_bytes() == png
    assert len(client.calls) == 1
    # 正文保持"[动画表情]"，没有任何视觉识别产生的描述
    assert message.content == "[动画表情]"


@pytest.mark.asyncio
async def test_video_cover_falls_back_to_aes_decryption(db_session, monkeypatch, tmp_path):
    """视频封面走媒体网关，拿到的是密文；按魔数认不出就用 aes key 解一次。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from app.services.channels.base import ChannelMessage
    from app.services.ranren_ai.attachments import InboundAttachmentProcessor

    plain = b"\xff\xd8\xff" + b"cover" * 3 + b"\xff\xd9"
    key_hex = "0123456789abcdef0123456789abcdef"
    padded = plain + bytes([16 - len(plain) % 16]) * (16 - len(plain) % 16)
    encryptor = Cipher(algorithms.AES(bytes.fromhex(key_hex)), modes.ECB()).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()

    _patch_media_client(monkeypatch, encrypted)
    monkeypatch.chdir(tmp_path)

    channel_message = ChannelMessage(
        channel="qiwe",
        chat_id="10926518308089720",
        chat_type="group",
        text="[视频] 时长 40 秒",
        message_type="video",
        raw_payload={
            "cmd": 15000,
            "msgType": 103,
            "msgData": {
                "coverImageHttpUrl": "https://imunion.weixin.qq.com/cgi-bin/mmae-bin/tpdownloadmedia?param=v1_x",
                "duration": 40,
                "fileAesKey": key_hex,
                "fileAuthKey": "v1_auth",
            },
        },
    )
    message = await _make_inbound_message(db_session, "video", "[视频] 时长 40 秒")
    await InboundAttachmentProcessor()._process_inbound_thumbnail(message.id, channel_message)
    await db_session.refresh(message)

    info = (message.analysis_json or {}).get("image_info")
    assert info is not None
    assert info["content_type"] == "image/jpeg"
    assert (tmp_path / ".wecom_cache/images" / info["filename"]).read_bytes() == plain


@pytest.mark.asyncio
async def test_thumbnail_download_failure_leaves_text_placeholder(db_session, monkeypatch, tmp_path):
    """下不到图/链接过期时只留文字占位，不能影响消息本身入库。"""
    from app.services.ranren_ai.attachments import InboundAttachmentProcessor

    _patch_media_client(monkeypatch, b"not-an-image-at-all")
    monkeypatch.chdir(tmp_path)

    message = await _make_inbound_message(db_session, "sticker", "[动画表情]")
    await InboundAttachmentProcessor()._process_inbound_thumbnail(message.id, _sticker_channel_message())
    await db_session.refresh(message)

    assert (message.analysis_json or {}).get("image_info") is None
    assert message.content == "[动画表情]"


@pytest.mark.asyncio
async def test_thumbnail_download_rejects_non_wecom_host(db_session, monkeypatch, tmp_path):
    """链接来自外部回调，非企微/微信图床域名一律不下载(SSRF 防护)。"""
    from app.services.ranren_ai.attachments import InboundAttachmentProcessor

    client = _patch_media_client(monkeypatch, b"\x89PNG\r\n\x1a\nx")
    monkeypatch.chdir(tmp_path)

    message = await _make_inbound_message(db_session, "sticker", "[动画表情]")
    await InboundAttachmentProcessor()._process_inbound_thumbnail(
        message.id, _sticker_channel_message("http://169.254.169.254/latest/meta-data/")
    )
    await db_session.refresh(message)

    assert (message.analysis_json or {}).get("image_info") is None
    assert client.calls == []


def test_detect_image_extension_only_accepts_real_images() -> None:
    from app.services.ranren_ai.attachments import _detect_image_extension

    assert _detect_image_extension(b"\xff\xd8\xffrest") == ".jpg"
    assert _detect_image_extension(b"\x89PNG\r\n\x1a\n") == ".png"
    assert _detect_image_extension(b"GIF89a") == ".gif"
    assert _detect_image_extension(b"RIFF1234WEBP") == ".webp"
    assert _detect_image_extension(b"encrypted-garbage") is None
