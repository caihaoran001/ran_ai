from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

_QR_GENERATE_URL = "https://work.weixin.qq.com/ai/qc/generate"
_QR_QUERY_URL = "https://work.weixin.qq.com/ai/qc/query_result"
_QR_CODE_PAGE = "https://work.weixin.qq.com/ai/qc/gen?source=hermes&scode="
_QR_POLL_INTERVAL = 3
_QR_POLL_TIMEOUT = 300


def qr_scan_for_bot_info(*, timeout_seconds: int = _QR_POLL_TIMEOUT) -> Optional[dict[str, str]]:
    """Run the WeCom QR scan flow to obtain bot_id and secret.

    This mirrors the Hermes helper and uses WeCom admin-console web endpoints,
    not a stable public developer API. It is useful for manual setup, but
    production code should still accept explicit credentials.
    """

    generate_url = f"{_QR_GENERATE_URL}?source=hermes"
    print("Connecting to WeCom...", end="", flush=True)
    try:
        req = urllib.request.Request(
            generate_url, headers={"User-Agent": "WeComAgentBridge/0.1"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error("WeCom QR: failed to fetch QR code: %s", exc)
        print(f" failed: {exc}")
        return None

    data = raw.get("data") or {}
    scode = str(data.get("scode") or "").strip()
    auth_url = str(data.get("auth_url") or "").strip()
    if not scode or not auth_url:
        logger.error("WeCom QR: unexpected response format: %s", raw)
        print(" failed: unexpected response format")
        return None

    print(" done.")
    qr_rendered = False
    try:
        import qrcode as _qrcode

        qr = _qrcode.QRCode()
        qr.add_data(auth_url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
        qr_rendered = True
    except Exception:
        pass

    page_url = f"{_QR_CODE_PAGE}{urllib.parse.quote(scode)}"
    if qr_rendered:
        print(f"\nScan the QR code above, or open this URL:\n{page_url}")
    else:
        print(f"\nOpen this URL in WeCom on your phone:\n{page_url}")
        print("Tip: install qrcode to render the QR code in the terminal.")

    print("Fetching configuration results...", end="", flush=True)
    deadline = time.time() + timeout_seconds
    query_url = f"{_QR_QUERY_URL}?scode={urllib.parse.quote(scode)}"

    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                query_url, headers={"User-Agent": "WeComAgentBridge/0.1"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.debug("WeCom QR poll error: %s", exc)
            time.sleep(_QR_POLL_INTERVAL)
            continue

        print(".", end="", flush=True)
        result_data = result.get("data") or {}
        if str(result_data.get("status") or "").lower() == "success":
            print()
            bot_info = result_data.get("bot_info") or {}
            bot_id = str(bot_info.get("botid") or bot_info.get("bot_id") or "").strip()
            secret = str(bot_info.get("secret") or "").strip()
            if bot_id and secret:
                return {"bot_id": bot_id, "secret": secret}
            print("QR scan succeeded but no bot credentials were returned.")
            return None

        time.sleep(_QR_POLL_INTERVAL)

    print()
    print(f"QR scan timed out after {timeout_seconds} seconds.")
    return None


def main() -> None:
    result = qr_scan_for_bot_info()
    if result:
        print(f"WECOM_BOT_ID={result['bot_id']}")
        print(f"WECOM_SECRET={result['secret']}")

