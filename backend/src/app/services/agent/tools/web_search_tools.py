from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool, StructuredTool

from app.core.config import Settings

_DISALLOWED_PATTERNS = [
    r"症状|疾病|诊断|治疗|药|处方|医院|医生|高血压|糖尿病|甲减|怀孕|哺乳",
    r"减肥|瘦身|营养建议|保健品|功效|副作用|体重|腰围|睡眠障碍|便秘|腹泻|心悸",
    r"手机号|微信号|邮箱|身份证|住址|真实姓名|私人账号",
]


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=120, description="Low-risk public lifestyle or interest search query.")


def build_web_search_tools(*, settings: Settings) -> list[BaseTool]:
    async def web_search_lifestyle(query: str) -> dict[str, Any]:
        clean = _sanitize_query(query)
        if not settings.web_search_enabled:
            return {"ok": False, "error": "联网搜索未开启。"}
        if not settings.tavily_api_key:
            return {"ok": False, "error": "TAVILY_API_KEY 未配置。"}
        if _is_disallowed(clean):
            return {"ok": False, "error": "该问题涉及健康、身体指标或隐私范围，不能联网搜索。"}
        max_results = max(1, min(int(settings.web_search_max_results or 5), 5))
        try:
            async with httpx.AsyncClient(timeout=settings.web_search_timeout_seconds) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": settings.tavily_api_key,
                        "query": clean,
                        "search_depth": "basic",
                        "topic": "general",
                        "max_results": max_results,
                        "include_answer": True,
                        "include_raw_content": False,
                        "safe_search": True,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            return {"ok": False, "error": f"搜索失败：{type(exc).__name__}"}
        return {
            "ok": True,
            "query": clean,
            "answer": data.get("answer") or "",
            "results": [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "content": (item.get("content") or "")[:500],
                    "score": item.get("score"),
                }
                for item in (data.get("results") or [])[:max_results]
            ],
        }

    return [
        StructuredTool.from_function(
            coroutine=web_search_lifestyle,
            name="web_search_lifestyle",
            description="Search low-risk public lifestyle, culture, travel, work, study, sports, and entertainment information.",
            args_schema=WebSearchArgs,
        )
    ]


def _sanitize_query(query: str) -> str:
    clean = re.sub(r"\s+", " ", (query or "").strip())
    clean = _normalize_current_query_year(clean)
    return clean[:120]


def _is_disallowed(query: str) -> bool:
    return any(re.search(pattern, query, flags=re.IGNORECASE) for pattern in _DISALLOWED_PATTERNS)


def _normalize_current_query_year(query: str) -> str:
    """Guard against the model turning "现在/最新/今年" into a stale year."""
    if not query:
        return query
    if not re.search(r"现在|当前|最近|最新|今年|热播|热门|排行榜", query):
        return query
    current_year = datetime.now(ZoneInfo("Asia/Shanghai")).year

    def replace_year(match: re.Match[str]) -> str:
        year = int(match.group(1))
        if year < current_year:
            return f"{current_year}年"
        return match.group(0)

    normalized = re.sub(r"(20\d{2})年", replace_year, query)
    if not re.search(r"20\d{2}年", normalized):
        normalized = f"{current_year}年 {normalized}"
    return normalized
