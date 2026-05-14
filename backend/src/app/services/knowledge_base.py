from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from llama_index.core import Document, Settings
from llama_index.core.indices.keyword_table import SimpleKeywordTableIndex
from llama_index.core.llms import MockLLM

from app.core.config import Settings as AppSettings
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class KnowledgeBase:
    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or get_settings()
        self._documents = self._load_documents()
        self._index = self._build_index()

    def retrieve(self, query: str) -> str:
        query = query.strip()
        if not query or not self._index:
            return ""
        try:
            retriever = self._index.as_retriever(
                similarity_top_k=self.settings.knowledge_base_top_k
            )
            nodes = retriever.retrieve(query)
        except Exception:
            logger.exception("Knowledge base retrieval failed")
            return ""
        snippets = [node.node.get_content().strip() for node in nodes if node.node.get_content().strip()]
        if not snippets:
            snippets = self._fallback_search(query)
        if not snippets:
            return ""
        return "\n\n".join(f"[知识库命中 {index + 1}]\n{snippet}" for index, snippet in enumerate(snippets))

    def _build_index(self):
        if not self._documents:
            return None
        Settings.llm = MockLLM()
        return SimpleKeywordTableIndex.from_documents(self._documents)

    def _load_documents(self) -> list[Document]:
        files = _knowledge_files(self.settings)
        documents = []
        for file_path in files:
            text = file_path.read_text(encoding="utf-8").strip()
            for section in _split_sections(text):
                documents.append(Document(text=section, metadata={"source": str(file_path)}))
        return documents

    def _fallback_search(self, query: str) -> list[str]:
        query_terms = _query_terms(query)
        if not query_terms:
            return []
        scored: list[tuple[int, str]] = []
        for document in self._documents:
            content = document.text or ""
            score = sum(1 for term in query_terms if term in content)
            if score:
                scored.append((score, content.strip()))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [content for _, content in scored[: self.settings.knowledge_base_top_k]]


@lru_cache
def get_knowledge_base() -> KnowledgeBase:
    return KnowledgeBase()


def retrieve_knowledge(query: str) -> str:
    return get_knowledge_base().retrieve(query)


def _knowledge_files(settings: AppSettings) -> list[Path]:
    kb_dir = Path(settings.knowledge_base_dir) if settings.knowledge_base_dir else _default_kb_dir()
    if not kb_dir.exists():
        return []
    return sorted(
        file_path
        for file_path in kb_dir.iterdir()
        if file_path.is_file() and file_path.suffix.lower() in {".md", ".txt"}
    )


def _default_kb_dir() -> Path:
    return Path(__file__).resolve().parent / "knowledge_base"


def _split_sections(text: str) -> list[str]:
    chunks = re.split(r"(?m)^(?=##\s+)", text)
    sections = [chunk.strip() for chunk in chunks if chunk.strip()]
    if len(sections) <= 1:
        return sections
    title = sections[0] if sections[0].startswith("# ") else ""
    body_sections = sections[1:] if title else sections
    return [f"{title}\n\n{section}".strip() for section in body_sections]


def _query_terms(query: str) -> list[str]:
    terms = set(re.findall(r"#[\w\u4e00-\u9fff]+|[\w\u4e00-\u9fff]{2,}", query))
    for token in ("打卡", "问卷", "画像", "处方单", "执行标准", "提醒", "docx"):
        if token in query:
            terms.add(token)
    return sorted(terms, key=len, reverse=True)
