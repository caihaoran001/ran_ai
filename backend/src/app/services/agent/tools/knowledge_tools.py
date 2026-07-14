from __future__ import annotations

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool, StructuredTool

from app.core.config import Settings
from app.db.session import SessionLocal
from app.services.knowledge_index import retrieve_case_context_db


class SearchSuccessCaseArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=300,
        description="A short description of the user's current struggle or discouragement, used to find a similar real success case.",
    )


def build_knowledge_tools(*, settings: Settings) -> list[BaseTool]:
    async def search_success_case(query: str) -> str:
        clean = (query or "").strip()[:300]
        if not clean:
            return "暂时没有找到合适的激励案例。"
        try:
            async with SessionLocal() as session:
                result = await retrieve_case_context_db(session, clean, settings=settings, limit=1)
        except Exception:
            # pgvector 缺失/检索异常都不该拖垮回复,也不触发工具熔断:如实返回“没案例”,让 agent 自己安慰。
            return "暂时没有找到合适的激励案例。"
        return result or "暂时没有找到合适的激励案例。"

    return [
        StructuredTool.from_function(
            coroutine=search_success_case,
            name="search_success_case",
            description=(
                "Retrieve ONE similar real user success story to encourage the user. Call this ONLY when the "
                "user shows discouragement, plateau frustration, self-doubt, low motivation, or an urge to give "
                "up (e.g. '我是不是没救了' / '坚持不下去了' / '怎么又没瘦'). Do NOT call it for ordinary questions, "
                "data lookups, or neutral chat. Returns a coaching header plus a matched case, or a note when no "
                "suitable case exists — in that case just comfort the user in your own words, never fabricate a case. "
                "This tool is a behind-the-scenes source: NEVER tell the user you searched for or failed to find a "
                "case, and never mention a 案例库/case library — just reply naturally as if the encouragement is your own."
            ),
            args_schema=SearchSuccessCaseArgs,
        )
    ]
