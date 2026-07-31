"""DeepSeek 账户余额查询。

DeepSeek 的 chat/completions 响应里不含余额，余额只能单独调
`GET {ai_base_url}/user/balance`（Bearer AI_API_KEY）。本模块提供防御性的
异步抓取 + 短缓存，供后台看板显示与低余额告警共用。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.core.config import Settings

logger = logging.getLogger("uvicorn.error")

# 看板每次加载都会打一次端点；用进程内短缓存避免频繁请求 DeepSeek。
_CACHE_TTL_SECONDS = 60.0
_cache: tuple[float, "BalanceSnapshot"] | None = None


@dataclass(frozen=True)
class BalanceSnapshot:
    """一次余额查询结果。抓取失败时返回 available=False，其余字段为兜底值。"""

    available: bool  # 是否成功拿到余额（外部调用成功且解析出 CNY 余额）
    balance_yuan: float | None
    currency: str
    is_available: bool  # DeepSeek 账户是否仍可用于 API 调用（其 is_available 字段）
    fetched_at: float  # epoch 秒


def _looks_like_deepseek(base_url: str) -> bool:
    return "deepseek" in (base_url or "").lower()


async def fetch_deepseek_balance(
    settings: Settings, *, force: bool = False
) -> BalanceSnapshot | None:
    """查询 DeepSeek 账户余额。

    - 非 DeepSeek 端点或缺 key → 返回 None（该功能不适用）。
    - 外部调用失败/超时/解析异常 → 返回 available=False 的快照（不抛出、不误报）。
    - `force=True` 跳过缓存（告警路径用最新值）。
    """
    global _cache
    base_url = (settings.ai_base_url or "").rstrip("/")
    if not base_url or not settings.ai_api_key or not _looks_like_deepseek(base_url):
        return None

    now = time.monotonic()
    if not force and _cache is not None:
        cached_at, snap = _cache
        if now - cached_at < _CACHE_TTL_SECONDS:
            return snap

    snapshot = await _do_fetch(settings, base_url)
    _cache = (now, snapshot)
    return snapshot


async def _do_fetch(settings: Settings, base_url: str) -> BalanceSnapshot:
    fetched_at = time.time()
    try:
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.get(
                f"{base_url}/user/balance",
                headers={"Authorization": f"Bearer {settings.ai_api_key}"},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException as exc:
        logger.warning("deepseek balance query timeout: %s", exc)
        return _failed(fetched_at)
    except httpx.HTTPStatusError as exc:
        logger.warning("deepseek balance http error status=%s", exc.response.status_code)
        return _failed(fetched_at)
    except httpx.RequestError as exc:
        logger.warning("deepseek balance network error: %s", exc)
        return _failed(fetched_at)
    except Exception:
        logger.exception("deepseek balance unexpected error")
        return _failed(fetched_at)

    return _parse(data, fetched_at)


def _parse(data: object, fetched_at: float) -> BalanceSnapshot:
    if not isinstance(data, dict):
        return _failed(fetched_at)
    infos = data.get("balance_infos")
    is_available = bool(data.get("is_available", True))
    if not isinstance(infos, list) or not infos:
        # 账户可能余额为 0 且无 balance_infos；据 is_available 给出 0 元。
        return BalanceSnapshot(
            available=True,
            balance_yuan=0.0,
            currency="CNY",
            is_available=is_available,
            fetched_at=fetched_at,
        )
    # 优先取 CNY，退而取第一条。
    chosen = next(
        (i for i in infos if isinstance(i, dict) and i.get("currency") == "CNY"),
        infos[0] if isinstance(infos[0], dict) else None,
    )
    if not isinstance(chosen, dict):
        return _failed(fetched_at)
    try:
        balance = float(chosen.get("total_balance"))  # total_balance 是字符串
    except (TypeError, ValueError):
        return _failed(fetched_at)
    return BalanceSnapshot(
        available=True,
        balance_yuan=balance,
        currency=str(chosen.get("currency") or "CNY"),
        is_available=is_available,
        fetched_at=fetched_at,
    )


def _failed(fetched_at: float) -> BalanceSnapshot:
    return BalanceSnapshot(
        available=False,
        balance_yuan=None,
        currency="CNY",
        is_available=True,
        fetched_at=fetched_at,
    )
