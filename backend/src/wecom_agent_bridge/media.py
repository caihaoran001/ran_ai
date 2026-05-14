from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .security import is_safe_url, safe_url_for_log

IMAGE_MAX_BYTES = 10 * 1024 * 1024
VIDEO_MAX_BYTES = 10 * 1024 * 1024
VOICE_MAX_BYTES = 2 * 1024 * 1024
FILE_MAX_BYTES = 20 * 1024 * 1024
ABSOLUTE_MAX_BYTES = FILE_MAX_BYTES
UPLOAD_CHUNK_SIZE = 512 * 1024
MAX_UPLOAD_CHUNKS = 100
VOICE_SUPPORTED_MIMES = {"audio/amr"}


@dataclass
class MediaCacheEntry:
    path: str
    content_type: str


@dataclass
class PreparedMedia:
    data: bytes
    content_type: str
    file_name: str
    detected_type: str
    final_type: str
    rejected: bool = False
    reject_reason: Optional[str] = None
    downgraded: bool = False
    downgrade_note: Optional[str] = None


def _looks_like_image(data: bytes) -> bool:
    if len(data) < 4:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:2] == b"BM":
        return True
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return True
    return False


def detect_image_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def mime_for_ext(ext: str, fallback: str = "application/octet-stream") -> str:
    return mimetypes.types_map.get(ext.lower(), fallback)


def guess_extension(url: str, content_type: str, fallback: str) -> str:
    ext = mimetypes.guess_extension(content_type) if content_type else None
    if ext:
        return ext
    path_ext = Path(urlparse(url).path).suffix
    if path_ext:
        return path_ext
    return fallback


def guess_filename(url: str, content_disposition: Optional[str], content_type: str) -> str:
    if content_disposition:
        match = re.search(r'filename="?([^";]+)"?', content_disposition)
        if match:
            return match.group(1)
    name = Path(urlparse(url).path).name or "document"
    if "." not in name:
        ext = mimetypes.guess_extension(content_type) or ".bin"
        name = f"{name}{ext}"
    return name


def guess_mime_type(filename: str) -> str:
    mime_type = mimetypes.guess_type(filename)[0]
    if mime_type:
        return mime_type
    if Path(filename).suffix.lower() == ".amr":
        return "audio/amr"
    return "application/octet-stream"


def normalize_content_type(content_type: str, filename: str) -> str:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    guessed = guess_mime_type(filename)
    if not normalized:
        return guessed
    if normalized in {"application/octet-stream", "text/plain"}:
        return guessed
    return normalized


def detect_wecom_media_type(content_type: str) -> str:
    mime_type = str(content_type or "").strip().lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/") or mime_type == "application/ogg":
        return "voice"
    return "file"


def apply_file_size_limits(
    file_size: int, detected_type: str, content_type: Optional[str] = None
) -> dict[str, Any]:
    file_size_mb = file_size / (1024 * 1024)
    normalized_type = str(detected_type or "file").lower()
    normalized_content_type = str(content_type or "").strip().lower()

    if file_size > ABSOLUTE_MAX_BYTES:
        return {
            "final_type": normalized_type,
            "rejected": True,
            "reject_reason": (
                f"File size {file_size_mb:.2f}MB exceeds WeCom's 20MB limit."
            ),
            "downgraded": False,
            "downgrade_note": None,
        }

    if normalized_type == "image" and file_size > IMAGE_MAX_BYTES:
        return {
            "final_type": "file",
            "rejected": False,
            "reject_reason": None,
            "downgraded": True,
            "downgrade_note": (
                f"Image size {file_size_mb:.2f}MB exceeds 10MB; sent as file."
            ),
        }

    if normalized_type == "video" and file_size > VIDEO_MAX_BYTES:
        return {
            "final_type": "file",
            "rejected": False,
            "reject_reason": None,
            "downgraded": True,
            "downgrade_note": (
                f"Video size {file_size_mb:.2f}MB exceeds 10MB; sent as file."
            ),
        }

    if normalized_type == "voice":
        if normalized_content_type and normalized_content_type not in VOICE_SUPPORTED_MIMES:
            return {
                "final_type": "file",
                "rejected": False,
                "reject_reason": None,
                "downgraded": True,
                "downgrade_note": (
                    f"Voice format {normalized_content_type} is unsupported; "
                    "WeCom native voice only supports AMR, so it was sent as file."
                ),
            }
        if file_size > VOICE_MAX_BYTES:
            return {
                "final_type": "file",
                "rejected": False,
                "reject_reason": None,
                "downgraded": True,
                "downgrade_note": (
                    f"Voice size {file_size_mb:.2f}MB exceeds 2MB; sent as file."
                ),
            }

    return {
        "final_type": normalized_type,
        "rejected": False,
        "reject_reason": None,
        "downgraded": False,
        "downgrade_note": None,
    }


def decrypt_file_bytes(encrypted_data: bytes, aes_key: str) -> bytes:
    if not encrypted_data:
        raise ValueError("encrypted_data is empty")
    if not aes_key:
        raise ValueError("aes_key is required")

    aes_key = aes_key + "=" * ((4 - len(aes_key) % 4) % 4)
    key = base64.b64decode(aes_key)
    if len(key) != 32:
        raise ValueError(f"Invalid WeCom AES key length: expected 32 bytes, got {len(key)}")

    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted_data) + decryptor.finalize()

    pad_len = decrypted[-1]
    if pad_len < 1 or pad_len > 32 or pad_len > len(decrypted):
        raise ValueError(f"Invalid PKCS#7 padding value: {pad_len}")
    if any(byte != pad_len for byte in decrypted[-pad_len:]):
        raise ValueError("Invalid PKCS#7 padding: padding bytes mismatch")
    return decrypted[:-pad_len]


class MediaManager:
    """Media cache/download/prepare helpers used by the WeCom bridge."""

    def __init__(self, cache_dir: Path | str = ".wecom_cache", http_client: Any = None):
        self.cache_dir = Path(cache_dir)
        self.http_client = http_client

    def cache_image_from_bytes(self, data: bytes, ext: str = ".jpg") -> str:
        if not _looks_like_image(data):
            snippet = data[:80].decode("utf-8", errors="replace")
            raise ValueError(
                f"Refusing to cache non-image data as {ext} (starts with: {snippet!r})"
            )
        cache_dir = self.cache_dir / "images"
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"img_{__import__('uuid').uuid4().hex[:12]}{ext}"
        path.write_bytes(data)
        return str(path.resolve())

    def cache_document_from_bytes(self, data: bytes, filename: str) -> str:
        cache_dir = self.cache_dir / "documents"
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename or "document.bin").name
        path = cache_dir / f"doc_{__import__('uuid').uuid4().hex[:12]}_{safe_name}"
        path.write_bytes(data)
        return str(path.resolve())

    @staticmethod
    def decode_base64(data: str) -> bytes:
        payload = data.split(",", 1)[-1].strip()
        return base64.b64decode(payload)

    async def download_remote_bytes(
        self, url: str, max_bytes: int = ABSOLUTE_MAX_BYTES
    ) -> tuple[bytes, dict[str, str]]:
        if not is_safe_url(url):
            raise ValueError(f"Blocked unsafe URL: {safe_url_for_log(url)}")

        client = self.http_client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        created_client = client is not self.http_client
        try:
            async with client.stream(
                "GET",
                url,
                headers={
                    "User-Agent": "WeComAgentBridge/0.1",
                    "Accept": "*/*",
                },
            ) as response:
                response.raise_for_status()
                headers = {key.lower(): value for key, value in response.headers.items()}
                content_length = headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                    raise ValueError(
                        f"Remote media exceeds WeCom limit: {content_length} > {max_bytes}"
                    )

                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise ValueError(
                            f"Remote media exceeds WeCom limit while downloading: "
                            f"{len(data)} > {max_bytes}"
                        )
                return bytes(data), headers
        finally:
            if created_client:
                await client.aclose()

    async def cache_media(self, kind: str, media: dict[str, Any]) -> MediaCacheEntry | None:
        if media.get("base64"):
            raw = self.decode_base64(str(media["base64"]))
            if kind == "image":
                ext = detect_image_ext(raw)
                path = self.cache_image_from_bytes(raw, ext)
                return MediaCacheEntry(path, mime_for_ext(ext, fallback="image/jpeg"))
            filename = str(media.get("filename") or media.get("name") or "wecom_file")
            path = self.cache_document_from_bytes(raw, filename)
            return MediaCacheEntry(path, mimetypes.guess_type(filename)[0] or "application/octet-stream")

        url = str(media.get("url") or "").strip()
        if not url:
            return None

        raw, headers = await self.download_remote_bytes(url, max_bytes=ABSOLUTE_MAX_BYTES)
        aes_key = str(media.get("aeskey") or "").strip()
        if aes_key:
            raw = decrypt_file_bytes(raw, aes_key)

        content_type = (
            str(headers.get("content-type") or "").split(";", 1)[0].strip()
            or "application/octet-stream"
        )
        if kind == "image":
            ext = guess_extension(url, content_type, fallback=detect_image_ext(raw))
            path = self.cache_image_from_bytes(raw, ext)
            return MediaCacheEntry(path, content_type or mime_for_ext(ext, "image/jpeg"))

        filename = guess_filename(url, headers.get("content-disposition"), content_type)
        path = self.cache_document_from_bytes(raw, filename)
        return MediaCacheEntry(path, content_type)

    @staticmethod
    def looks_like_url(media_source: str) -> bool:
        parsed = urlparse(str(media_source or ""))
        return parsed.scheme in {"http", "https"}

    async def load_outbound_media(
        self, media_source: str, file_name: Optional[str] = None
    ) -> tuple[bytes, str, str]:
        source = str(media_source or "").strip()
        if not source:
            raise ValueError("media source is required")
        if re.fullmatch(r"<[^>\n]+>", source):
            raise ValueError(f"Media placeholder was not replaced: {source}")

        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            data, headers = await self.download_remote_bytes(source, max_bytes=ABSOLUTE_MAX_BYTES)
            resolved_name = file_name or guess_filename(
                source, headers.get("content-disposition"), headers.get("content-type", "")
            )
            content_type = normalize_content_type(headers.get("content-type", ""), resolved_name)
            return data, content_type, resolved_name

        if parsed.scheme == "file":
            local_path = Path(unquote(parsed.path)).expanduser()
        else:
            local_path = Path(source).expanduser()
        if not local_path.is_absolute():
            local_path = (Path.cwd() / local_path).resolve()
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(f"Media file not found: {local_path}")

        data = local_path.read_bytes()
        resolved_name = file_name or local_path.name
        content_type = normalize_content_type("", resolved_name)
        return data, content_type, resolved_name

    async def prepare_outbound_media(
        self, media_source: str, file_name: Optional[str] = None
    ) -> PreparedMedia:
        data, content_type, resolved_name = await self.load_outbound_media(
            media_source, file_name=file_name
        )
        detected_type = detect_wecom_media_type(content_type)
        size_check = apply_file_size_limits(len(data), detected_type, content_type)
        return PreparedMedia(
            data=data,
            content_type=content_type,
            file_name=resolved_name,
            detected_type=detected_type,
            final_type=size_check["final_type"],
            rejected=bool(size_check["rejected"]),
            reject_reason=size_check["reject_reason"],
            downgraded=bool(size_check["downgraded"]),
            downgrade_note=size_check["downgrade_note"],
        )

