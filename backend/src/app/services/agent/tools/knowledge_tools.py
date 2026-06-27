from __future__ import annotations

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool, StructuredTool

from app.core.config import Settings
from app.db.session import SessionLocal
from app.services.knowledge_base import retrieve_knowledge
from app.services.knowledge_index import retrieve_indexed_knowledge


class SearchKnowledgeArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="Business knowledge query.")


def build_knowledge_tools(*, settings: Settings) -> list[BaseTool]:
    async def search_knowledge_base(query: str) -> str:
        clean = (query or "").strip()[:300]
        if not clean:
            return "KNOWLEDGE_SEARCH_ERROR: query is empty"
        async with SessionLocal() as session:
            result = await retrieve_indexed_knowledge(session, clean, settings=settings)
        if result:
            return result
        return retrieve_knowledge(clean) or "未检索到相关知识。"

    return [
        StructuredTool.from_function(
            coroutine=search_knowledge_base,
            name="search_knowledge_base",
            description="Search the existing approved business knowledge base and markdown fallback.",
            args_schema=SearchKnowledgeArgs,
        )
    ]
