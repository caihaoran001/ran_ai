"""档案官抽取节点用的 LLM 封装：从同一天聊天原文抽打卡字段，带安全兜底与归一化。"""

from __future__ import annotations

import logging
from typing import Any

from app.services.ai_service import AIService
from app.services.checkin_service import CHECKIN_KEYS

logger = logging.getLogger(__name__)

_PRODUCT_SUBKEYS = ("morning_c", "noon_b", "night_a", "summary")


def _clean_str(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _normalize_checkin_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """把 LLM 输出裁剪到合法 CHECKIN_KEYS，空值剔除。"""
    fields: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return fields
    for key in CHECKIN_KEYS:
        val = raw.get(key)
        if key == "product_schedule":
            if isinstance(val, dict):
                ps = {sub: _clean_str(val.get(sub)) for sub in _PRODUCT_SUBKEYS}
                ps = {k: v for k, v in ps.items() if v}
                if ps:
                    fields[key] = ps
        else:
            cleaned = _clean_str(val)
            if cleaned:
                fields[key] = cleaned
    return fields


async def extract_checkin_fields(*, content: str, service_case_id: int) -> dict[str, Any]:
    """抽取一天聊天里的打卡字段；失败返回空 dict（不阻塞归档流程）。"""
    if not content.strip():
        return {}
    try:
        raw = await AIService().extract_checkin_projection(
            content=content,
            context={"service_case_id": service_case_id},
        )
    except Exception:
        logger.exception("Archivist checkin extraction failed service_case_id=%s", service_case_id)
        return {}
    return _normalize_checkin_fields(raw)
