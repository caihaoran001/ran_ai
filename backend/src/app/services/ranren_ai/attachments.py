from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse
import uuid

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.db.session import SessionLocal
from app.models.message import Message
from app.services.ai_service import AIService
from app.services.channels.base import ChannelMessage
from app.services.qiwe_service.non_text import describe_non_text

logger = logging.getLogger("uvicorn.error")

# 入站图片下载大小上限：中图通常几十~一百多 KB；超过此值(极端情况/回退到原图)放弃识别，
# 保护服务器带宽、内存与 Qwen token。如需调整，改这一处即可。
_MAX_INBOUND_IMAGE_BYTES = 8 * 1024 * 1024
# 入站文件下载/文本抽取上限：文件通常比图片小，但仍需防超大文档拖垮带宽/内存/token。
_MAX_INBOUND_FILE_BYTES = 5 * 1024 * 1024
# 抽取文本落库上限（字符）：防止超大文档把 analysis_json 撑爆。
_MAX_FILE_TEXT_CHARS = 12000

# 入站图片下载的 SSRF 域名白名单：file_url 来自 QiWe 回调载荷(外部可构造)，
# 只允许下载企微/微信图床域名，防止被诱导去请求内网或任意第三方地址。
_INBOUND_IMAGE_ALLOWED_HOST_SUFFIXES = (
    ".qpic.cn",
    ".qlogo.cn",
    ".weixin.qq.com",
    ".qq.com",
    ".myqcloud.com",
)
_QIWE_IMAGE_MSG_TYPES = (7, 14, 101)
_IMAGE_PARSE_FAILURE_MARKERS = (
    "解析描述失败",
    "解析描述超时",
    "暂未能识别其内容",
)
_IMAGE_PARSE_FAILED_PROMPT = "识别失败，无法可靠读取。"


def _is_allowed_image_host(file_url: str) -> bool:
    """校验图片下载 URL 的 host 是否落在企微/微信图床白名单内。"""
    parsed = urlparse(file_url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(host == s.lstrip(".") or host.endswith(s) for s in _INBOUND_IMAGE_ALLOWED_HOST_SUFFIXES)


def _raw_payload_items(raw_payload: Any) -> list[dict[str, Any]]:
    if isinstance(raw_payload, list):
        items: list[dict[str, Any]] = []
        for entry in raw_payload:
            items.extend(_raw_payload_items(entry))
        return items
    if not isinstance(raw_payload, dict):
        return []
    data = raw_payload.get("data")
    if isinstance(data, list) and data:
        return [item for item in data if isinstance(item, dict)]
    return [raw_payload]


def _is_image_payload_item(item: dict[str, Any]) -> bool:
    return item.get("msgType") in _QIWE_IMAGE_MSG_TYPES


def _image_payload_items(message: ChannelMessage) -> list[dict[str, Any]]:
    return [item for item in _raw_payload_items(message.raw_payload) if _is_image_payload_item(item)]


def _strip_image_placeholders(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped and stripped != "[图片]":
            lines.append(stripped)
    return "\n".join(lines).strip()


def _image_description_is_available(description: str) -> bool:
    text = (description or "").strip()
    return bool(text) and not any(marker in text for marker in _IMAGE_PARSE_FAILURE_MARKERS)


def _decrypt_image_bytes(encrypted_data: bytes, aes_key_str: str) -> bytes:
    if not encrypted_data:
        raise ValueError("encrypted_data is empty")
    if not aes_key_str:
        raise ValueError("aes_key is required")

    # Hex-decode the key (16 bytes for AES-128)
    if len(aes_key_str) == 32 and all(c in "0123456789abcdefABCDEF" for c in aes_key_str):
        key = bytes.fromhex(aes_key_str)
    else:
        # Fallback to base64 decode
        padded_key = aes_key_str + "=" * ((4 - len(aes_key_str) % 4) % 4)
        key = base64.b64decode(padded_key)

    if len(key) not in (16, 24, 32):
        raise ValueError(f"Invalid AES key length: {len(key)} bytes")

    # WeCom image message callbacks are encrypted using AES-ECB
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted_data) + decryptor.finalize()

    # Identify magic bytes and truncate based on image format to remove PKCS#7 padding and CDN trailing garbage
    if decrypted.startswith(b"\xff\xd8\xff"):
        # JPEG image
        eof_idx = decrypted.rfind(b"\xff\xd9")
        if eof_idx != -1:
            return decrypted[:eof_idx + 2]
    elif decrypted.startswith(b"\x89PNG\r\n\x1a\n"):
        # PNG image
        eof_idx = decrypted.rfind(b"IEND")
        if eof_idx != -1:
            return decrypted[:eof_idx + 8]
    elif decrypted.startswith(b"GIF8"):
        # GIF image
        eof_idx = decrypted.rfind(b"\x3b")
        if eof_idx != -1:
            return decrypted[:eof_idx + 1]

    # Fallback to PKCS#7 unpadding if none of the above matches
    try:
        pad_len = decrypted[-1]
        if 1 <= pad_len <= 16:
            if all(byte == pad_len for byte in decrypted[-pad_len:]):
                return decrypted[:-pad_len]
    except Exception:
        pass

    return decrypted


async def _download_media_bytes(client: Any, url: str, headers: dict[str, str], *, max_bytes: int) -> bytes:
    """下载企微媒体，按需 Range 续传直到取满整份。

    企微媒体网关(tpdownloadmedia)单次响应最多返回 512KB，以 `206 Partial Content` +
    `Content-Range: bytes 0-524287/<total>` 分片返回。若只做一次 GET，就只拿到首片(512KB)，
    大于 512KB 的图片/文件会被截断（图片解码后下半部分渲染成灰块）。这里检测 Content-Range
    的总大小并按 Range 循环续传，把整份拼齐再返回。

    - 首个响应非 206 / 无 Content-Range：视为一次拿全（服务端不分片），直接返回。
    - total 超过 max_bytes：提前 fail-fast，不再续传，避免拖垮带宽/内存。
    - 防御：Range 被忽略(返回 200)按完整处理；空分片/长度对不上/死循环 guard 均显式报错。
    """
    resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    data = bytearray(resp.content)

    total: int | None = None
    content_range = resp.headers.get("content-range")
    if resp.status_code == 206 and content_range and "/" in content_range:
        tail = content_range.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            total = int(tail)

    if total is None:
        # 服务端一次返回完整内容（或不支持分片）
        if len(data) > max_bytes:
            raise ValueError(f"inbound media too large: {len(data)} bytes")
        return bytes(data)

    if total > max_bytes:
        raise ValueError(f"inbound media too large (declared in Content-Range): {total} bytes")

    guard = 0
    while len(data) < total:
        guard += 1
        if guard > 64:
            # 5.4MB/512KB≈11 片；64 是安全上限，防服务端异常导致的死循环
            raise ValueError(f"inbound media chunk loop exceeded guard at {len(data)}/{total} bytes")
        range_headers = dict(headers)
        range_headers["Range"] = f"bytes={len(data)}-"
        chunk_resp = await client.get(url, headers=range_headers)
        chunk_resp.raise_for_status()
        if chunk_resp.status_code == 200:
            # 服务端忽略了 Range，直接返回完整文件
            data = bytearray(chunk_resp.content)
            break
        chunk = chunk_resp.content
        if not chunk:
            raise ValueError(f"inbound media truncated: got {len(data)}/{total} bytes, server returned empty chunk")
        data.extend(chunk)

    if len(data) != total:
        raise ValueError(f"inbound media size mismatch: got {len(data)} bytes, expected {total}")
    return bytes(data)


def _detect_image_extension(data: bytes) -> str | None:
    """按魔数判断这段字节是不是图片，是则返回扩展名，不是返回 None。

    与图片消息那条链路不同：那里默认落 .jpg 即可（一定是图片），这里要拿它来判断
    "拿到的到底是原图还是加密串"，所以必须严格，不能默认。
    """
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return None


_IMAGE_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _file_extension(filename: str) -> str:
    """从文件名取小写扩展名(含点)，取不到返回空串。"""
    name = (filename or "").strip().lower()
    dot = name.rfind(".")
    return name[dot:] if dot != -1 else ""


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_docx_text(data: bytes) -> str:
    """抽取 docx 正文(段落 + 表格)。python-docx 已是项目依赖。"""
    from docx import Document  # 局部导入，避免影响不涉及文件解析的调用路径

    document = Document(io.BytesIO(data))
    parts: list[str] = []
    for para in document.paragraphs:
        text = (para.text or "").strip()
        if text:
            parts.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [(cell.text or "").strip() for cell in row.cells]
            line = " | ".join(cell for cell in cells if cell)
            if line:
                parts.append(line)
    return "\n".join(parts)


class InboundAttachmentProcessor:
    def _attachment_ai_service_cls(self):
        engine_mod = sys.modules.get("app.services.ranren_ai.engine")
        return getattr(engine_mod, "AIService", AIService) if engine_mod else AIService

    def _attachment_http_client_cls(self):
        engine_mod = sys.modules.get("app.services.ranren_ai.engine")
        httpx_mod = getattr(engine_mod, "httpx", httpx) if engine_mod else httpx
        return httpx_mod.AsyncClient

    def _attachment_session_local(self):
        engine_mod = sys.modules.get("app.services.ranren_ai.engine")
        return getattr(engine_mod, "SessionLocal", SessionLocal) if engine_mod else SessionLocal

    async def _describe_inbound_image(
        self,
        *,
        message_id: int,
        image_payload: dict[str, Any],
        service_case_id: int | None,
    ) -> tuple[str, dict[str, Any] | None]:
        try:
            msg_data = image_payload.get("msgData") or {}

            file_url = msg_data.get("fileBigHttpUrl") or msg_data.get("fileMiddleHttpUrl") or msg_data.get("cdnUrl") or msg_data.get("cloudUrl")
            file_aes_key = msg_data.get("fileAesKey") or msg_data.get("aesKey")
            file_auth_key = msg_data.get("fileAuthKey")

            if not file_url:
                raise ValueError("no image file URL found in raw payload")

            # Clean URL
            file_url = file_url.replace("\n", "").replace("\r", "").strip()

            # SSRF 防护：file_url 来自外部回调，仅允许企微/微信图床域名；
            # 不合法直接 raise，落入下方 except 走占位降级(不去请求该地址)。
            if not _is_allowed_image_host(file_url):
                raise ValueError(f"inbound image host not allowed: {file_url}")

            logger.info("Downloading inbound image for message_id=%s from: %s", message_id, file_url)

            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.38(0x1800262c)"
            }
            if file_auth_key:
                headers["authkey"] = file_auth_key.replace("\n", "").replace("\r", "").strip()

            # 企微媒体网关单次仅返回 512KB 分片(206)，_download_media_bytes 按 Range 续传拼齐整图，
            # 否则大于 512KB 的图片会被截断成"上方一角+下方灰块"。大图保护在 helper 内按 total 提前 fail-fast。
            async with self._attachment_http_client_cls()(timeout=30.0, follow_redirects=True) as client:
                encrypted_bytes = await _download_media_bytes(
                    client, file_url, headers, max_bytes=_MAX_INBOUND_IMAGE_BYTES
                )

            if file_aes_key:
                logger.info("Decrypting inbound image for message_id=%s", message_id)
                decrypted_bytes = _decrypt_image_bytes(encrypted_bytes, file_aes_key)
            else:
                decrypted_bytes = encrypted_bytes

            # Detect file extension or default to .jpg
            ext = ".jpg"
            if decrypted_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                ext = ".png"
            elif decrypted_bytes.startswith(b"GIF8"):
                ext = ".gif"
            elif decrypted_bytes.startswith(b"RIFF") and decrypted_bytes[8:12] == b"WEBP":
                ext = ".webp"

            cache_dir = Path(".wecom_cache/images")
            cache_dir.mkdir(parents=True, exist_ok=True)
            filename = f"img_{uuid.uuid4().hex[:16]}{ext}"
            file_path = cache_dir / filename
            file_path.write_bytes(decrypted_bytes)

            logger.info("Saved decrypted image to: %s", file_path)

            # Convert to Base64 for Qwen translation
            base64_str = base64.b64encode(decrypted_bytes).decode("utf-8")
            mime_type = "image/png" if ext == ".png" else "image/jpeg"
            if ext == ".gif":
                mime_type = "image/gif"
            elif ext == ".webp":
                mime_type = "image/webp"

            # Call Qwen Multimodal for image translation
            ai = self._attachment_ai_service_cls()(self.settings)
            description = await ai.translate_image(
                base64_image=base64_str,
                mime_type=mime_type,
                service_case_id=service_case_id,
                message_id=message_id,
            )

            image_info = {
                "local_path": str(file_path.resolve()),
                "filename": filename,
                "width": msg_data.get("width"),
                "height": msg_data.get("height"),
                "file_size": len(decrypted_bytes),
                "content_type": mime_type,
                "parse_status": "ok" if _image_description_is_available(description) else "failed",
            }
            return description, image_info
        except Exception:
            logger.exception("Error processing inbound image for message_id=%s", message_id)
            return "用户发来一张图片（暂未能识别其内容）。", None

    async def _process_inbound_image(
        self,
        message_id: int,
        message: ChannelMessage,
        service_case_id: int | None,
    ) -> dict[str, str]:
        image_items = _image_payload_items(message)
        description, image_info = await self._describe_inbound_image(
            message_id=message_id,
            image_payload=image_items[0] if image_items else {},
            service_case_id=service_case_id,
        )
        has_description = _image_description_is_available(description)
        image_prompt = {
            "status": "ok" if has_description else "failed",
            "content": description if has_description else _IMAGE_PARSE_FAILED_PROMPT,
        }
        try:
            async with self._attachment_session_local()() as session:
                db_msg = await session.get(Message, message_id)
                if db_msg:
                    db_msg.content = description if has_description else "用户发来一张图片（图片解析失败）。"
                    analysis = dict(db_msg.analysis_json or {})
                    if image_info:
                        analysis["image_info"] = image_info
                    analysis["image_parse_status"] = "ok" if has_description else "failed"
                    analysis["image_prompt"] = image_prompt
                    db_msg.analysis_json = analysis
                    session.add(db_msg)
                    await session.commit()
        except Exception:
            logger.exception("Failed to persist image description for message_id=%s", message_id)
        message.text = ""
        return image_prompt

    async def _process_embedded_images_for_text(
        self,
        message_id: int,
        message: ChannelMessage,
        service_case_id: int | None,
        user_text: str,
    ) -> dict[str, str] | None:
        image_items = _image_payload_items(message)
        if not image_items:
            return None

        descriptions: list[str] = []
        image_infos: list[dict[str, Any]] = []
        failed_count = 0
        for idx, image_payload in enumerate(image_items, start=1):
            description, image_info = await self._describe_inbound_image(
                message_id=message_id,
                image_payload=image_payload,
                service_case_id=service_case_id,
            )
            if _image_description_is_available(description):
                if len(image_items) == 1:
                    descriptions.append(description)
                else:
                    descriptions.append(f"图片{idx}：{description}")
            else:
                failed_count += 1
            if image_info:
                image_infos.append(image_info)

        image_content = "\n".join(item.strip("：") for item in descriptions if item.strip("：")).strip()
        if failed_count:
            failed_text = _IMAGE_PARSE_FAILED_PROMPT
            if descriptions:
                failed_text = f"另有{failed_count}张图片{failed_text}"
            image_content = "\n".join(part for part in (image_content, failed_text) if part).strip()
        image_prompt = {
            "status": "ok" if descriptions and not failed_count else "partial_failed" if descriptions else "failed",
            "content": image_content or _IMAGE_PARSE_FAILED_PROMPT,
        }
        persist_parts = []
        if user_text:
            persist_parts.append(user_text)
        if image_content:
            persist_parts.append(f"图片内容：{image_content}")
        persist_content = "\n".join(persist_parts).strip() or "用户发来图片（图片解析失败）。"

        try:
            async with self._attachment_session_local()() as session:
                db_msg = await session.get(Message, message_id)
                if db_msg:
                    db_msg.content = persist_content
                    analysis = dict(db_msg.analysis_json or {})
                    if image_infos:
                        analysis["image_infos"] = image_infos
                        analysis["image_info"] = image_infos[0]
                    analysis["image_context_only"] = not bool(user_text)
                    if descriptions and failed_count:
                        analysis["image_parse_status"] = "partial_failed"
                    elif descriptions:
                        analysis["image_parse_status"] = "ok"
                    else:
                        analysis["image_parse_status"] = "failed"
                    analysis["image_prompt"] = image_prompt
                    db_msg.analysis_json = analysis
                    session.add(db_msg)
                    await session.commit()
        except Exception:
            logger.exception("Failed to persist embedded image descriptions for message_id=%s", message_id)
        message.text = user_text
        return image_prompt

    async def _process_inbound_thumbnail(self, message_id: int, message: ChannelMessage) -> None:
        """把动画表情原图 / 视频封面下载存盘，让后台聊天记录能真的显示出来。

        与图片消息的区别：**不调用视觉模型**，只下载。这类消息本身信息量低（一张表情、
        一帧封面），花钱识别不划算；运营看一眼图就够了。

        下载失败、链接过期、拿到的不是图片，一律只记日志——消息正文已经是"[动画表情]"
        这样的可读占位，少一张图不影响记录完整性。
        """
        async with self._attachment_session_local()() as session:
            existing = await session.get(Message, message_id)
            # 中断重跑时消息可能已经带着图了，别再下一次、多存一份文件。
            if existing and isinstance((existing.analysis_json or {}).get("image_info"), dict):
                return

        hint = None
        for item in _raw_payload_items(message.raw_payload):
            described = describe_non_text(item)
            if described.media and described.media.url:
                hint = described.media
                break
        if hint is None:
            return

        try:
            # SSRF 防护：链接来自外部回调，只允许企微/微信媒体域名（与图片同一白名单）。
            if not _is_allowed_image_host(hint.url):
                raise ValueError(f"inbound thumbnail host not allowed: {hint.url}")

            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.38(0x1800262c)"
            }
            if hint.auth_key:
                headers["authkey"] = hint.auth_key.replace("\n", "").replace("\r", "").strip()

            async with self._attachment_http_client_cls()(timeout=30.0, follow_redirects=True) as client:
                raw_bytes = await _download_media_bytes(
                    client, hint.url, headers, max_bytes=_MAX_INBOUND_IMAGE_BYTES
                )

            # 表情走的是图床直链、本来就是原图；视频封面走媒体网关、可能是加密串。
            # 先按原样验魔数，不是图片再用 aes key 解一次，两条路都兜住。
            image_bytes = raw_bytes
            ext = _detect_image_extension(image_bytes)
            if ext is None and hint.aes_key:
                image_bytes = _decrypt_image_bytes(raw_bytes, hint.aes_key)
                ext = _detect_image_extension(image_bytes)
            if ext is None:
                raise ValueError("downloaded thumbnail is not a recognizable image")

            cache_dir = Path(".wecom_cache/images")
            cache_dir.mkdir(parents=True, exist_ok=True)
            filename = f"thumb_{uuid.uuid4().hex[:16]}{ext}"
            file_path = cache_dir / filename
            file_path.write_bytes(image_bytes)

            # 复用图片消息那套 image_info 结构：/api/messages/{id}/image 与 60 天清理直接生效。
            image_info = {
                "local_path": str(file_path.resolve()),
                "filename": filename,
                "file_size": len(image_bytes),
                "content_type": _IMAGE_MIME_BY_EXT.get(ext, "image/jpeg"),
                "thumbnail_kind": hint.kind,
            }
            async with self._attachment_session_local()() as session:
                db_msg = await session.get(Message, message_id)
                if db_msg:
                    analysis = dict(db_msg.analysis_json or {})
                    analysis["image_info"] = image_info
                    db_msg.analysis_json = analysis
                    session.add(db_msg)
                    await session.commit()
            logger.info(
                "Saved inbound thumbnail message_id=%s kind=%s path=%s bytes=%d",
                message_id,
                hint.kind,
                file_path,
                len(image_bytes),
            )
        except Exception:
            logger.warning(
                "Skip inbound thumbnail message_id=%s kind=%s url=%s",
                message_id,
                hint.kind,
                hint.url[:120],
                exc_info=True,
            )

    async def _process_inbound_file(
        self, message_id: int, message: ChannelMessage, service_case_id: int | None
    ) -> None:
        """下载群内上传的文件、解密并抽取文本（v1 仅 docx），结果写入 analysis_json.file_info。

        与 _process_inbound_image 同层：下载/解密复用图片那套骨架（直链 fileHttpUrl + AES-ECB），
        只是把“视觉识别”换成“按扩展名抽文本”。抽取失败/格式不支持一律走占位降级，不抛到主流程。
        无直链的文件（需走 wxWorkDownloadAsync 异步下载）v1 暂不支持，占位留痕。
        """
        filename = ""
        try:
            raw_payload = message.raw_payload or {}
            data_list = raw_payload.get("data") or [raw_payload]
            item = data_list[0] if data_list else {}
            msg_data = item.get("msgData") or {}

            filename = str(msg_data.get("filename") or msg_data.get("fileName") or "").strip()
            file_url = (
                msg_data.get("fileHttpUrl")
                or msg_data.get("fileBigHttpUrl")
                or msg_data.get("cdnUrl")
                or msg_data.get("cloudUrl")
            )
            file_aes_key = msg_data.get("fileAesKey") or msg_data.get("aesKey")
            file_auth_key = msg_data.get("fileAuthKey")
            declared_size = _safe_int(msg_data.get("fileSize"))

            if not file_url:
                raise ValueError("inbound file has no direct download url (async download unsupported in v1)")

            file_url = file_url.replace("\n", "").replace("\r", "").strip()
            # SSRF 防护：file_url 来自外部回调，仅允许企微/微信媒体域名（与图片同一白名单）。
            if not _is_allowed_image_host(file_url):
                raise ValueError(f"inbound file host not allowed: {file_url}")

            if declared_size and declared_size > _MAX_INBOUND_FILE_BYTES:
                raise ValueError(f"inbound file too large (declared): {declared_size} bytes")

            ext = _file_extension(filename)
            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.38(0x1800262c)"
            }
            if file_auth_key:
                headers["authkey"] = file_auth_key.replace("\n", "").replace("\r", "").strip()

            logger.info("Downloading inbound file message_id=%s filename=%s from: %s", message_id, filename, file_url)
            # 同图片：媒体网关分片返回，需 Range 续传拼齐整份文件(如 docx>512KB)。
            async with self._attachment_http_client_cls()(timeout=60.0, follow_redirects=True) as client:
                encrypted_bytes = await _download_media_bytes(
                    client, file_url, headers, max_bytes=_MAX_INBOUND_FILE_BYTES
                )

            # 文件回调与图片同样是 AES-ECB 加密，_decrypt_image_bytes 末尾的 PKCS#7 去填充对 docx(ZIP) 同样适用。
            file_bytes = _decrypt_image_bytes(encrypted_bytes, file_aes_key) if file_aes_key else encrypted_bytes

            parse_status = "unsupported"
            extracted_text = ""
            if ext == ".docx":
                extracted_text = _extract_docx_text(file_bytes)
                parse_status = "ok" if extracted_text.strip() else "empty"

            marker = f"[文件] {filename}" if filename else "[文件]"
            async with self._attachment_session_local()() as session:
                db_msg = await session.get(Message, message_id)
                if db_msg:
                    db_msg.content = marker
                    analysis = dict(db_msg.analysis_json or {})
                    analysis["file_info"] = {
                        "filename": filename,
                        "file_size": len(file_bytes),
                        "ext": ext,
                        "parse_status": parse_status,
                        "extracted_text": extracted_text[:_MAX_FILE_TEXT_CHARS],
                    }
                    db_msg.analysis_json = analysis
                    session.add(db_msg)
                    await session.commit()
            message.text = marker
            logger.info(
                "Processed inbound file message_id=%s filename=%s parse_status=%s text_len=%d",
                message_id,
                filename,
                parse_status,
                len(extracted_text),
            )
        except Exception:
            logger.exception("Error processing inbound file message_id=%s filename=%s", message_id, filename)
            # 兜底：下载/解密/抽取任一步失败，落一个可读占位并记录 parse_status=error，
            # 供 read_group_file 工具据此如实告知“暂无法读取”，而不是让 Agent 瞎编文件内容。
            marker = f"[文件] {filename}（暂未能读取内容）" if filename else "[文件]（暂未能读取内容）"
            try:
                async with self._attachment_session_local()() as session:
                    db_msg = await session.get(Message, message_id)
                    if db_msg:
                        db_msg.content = marker
                        analysis = dict(db_msg.analysis_json or {})
                        analysis["file_info"] = {
                            "filename": filename,
                            "parse_status": "error",
                            "extracted_text": "",
                        }
                        db_msg.analysis_json = analysis
                        session.add(db_msg)
                        await session.commit()
            except Exception:
                logger.exception("Failed to set fallback content for file message_id=%s", message_id)
            message.text = marker
