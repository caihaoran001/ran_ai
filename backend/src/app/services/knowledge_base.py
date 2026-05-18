from __future__ import annotations

import logging
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

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
        self._category_rules = self._load_category_rules()
        self._index = self._build_index()

    def retrieve(self, query: str) -> str:
        query = query.strip()
        if not query:
            return ""
        category_context = self._category_context(query)
        if not self._index:
            return f"[问答分类框架命中]\n{category_context}" if category_context else ""
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
        if not snippets and not category_context:
            return ""
        blocks = []
        if category_context:
            blocks.append(f"[问答分类框架命中]\n{category_context}")
        blocks.extend(f"[知识库命中 {index + 1}]\n{snippet}" for index, snippet in enumerate(snippets))
        return "\n\n".join(blocks)

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

    def _load_category_rules(self) -> list[dict[str, Any]]:
        path = _default_kb_dir() / "qa_category_rules.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.exception("Failed to load QA category rules")
            return []
        return data if isinstance(data, list) else []

    def _category_context(self, query: str) -> str:
        matches: list[tuple[int, dict[str, Any], list[str]]] = []
        for rule in self._category_rules:
            keywords = [str(item) for item in rule.get("keywords") or [] if str(item)]
            phrases = [str(item) for item in rule.get("typical_user_phrases") or [] if str(item)]
            category_terms = [
                str(rule.get("primary_category") or ""),
                str(rule.get("secondary_category") or ""),
            ]
            hit_keywords = [keyword for keyword in [*keywords, *phrases, *category_terms] if keyword and keyword in query]
            if hit_keywords:
                score = sum(3 if keyword in keywords else 1 for keyword in hit_keywords)
                matches.append((score, rule, hit_keywords))
        if not matches:
            return ""
        matches.sort(key=lambda item: item[0], reverse=True)
        lines = []
        for _, rule, hit_keywords in matches[:3]:
            lines.append(
                "\n".join(
                    [
                        f"- 分类：{rule.get('primary_category')}｜{rule.get('secondary_category')}",
                        f"  命中：{'、'.join(hit_keywords[:8])}",
                        f"  回复策略：{rule.get('reply_strategy')}",
                        f"  风险/转人工：{rule.get('risk_level')}",
                    ]
                )
            )
        return "\n".join(lines)

    def _fallback_search(self, query: str) -> list[str]:
        query_terms = [*_query_terms(query), *self._matched_category_keywords(query)]
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

    def _matched_category_keywords(self, query: str) -> list[str]:
        keywords: set[str] = set()
        for rule in self._category_rules:
            for keyword in rule.get("keywords") or []:
                keyword_text = str(keyword)
                if keyword_text and keyword_text in query:
                    keywords.add(keyword_text)
        return sorted(keywords, key=len, reverse=True)


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
    chunks = re.split(r"(?m)^(?=###{0,1}\s+)", text)
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
