from __future__ import annotations

import json
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
KEYWORD_WHITELIST_FILE = "keyword_whitelist.json"
MAX_QUERY_TERMS = 12


class KnowledgeBase:
    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or get_settings()
        self._documents = self._load_documents()
        self._index = self._build_index()

    def retrieve(self, query: str) -> str:
        query = query.strip()
        if not query:
            return ""
        if not self._index:
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
    chunks = re.split(r"(?m)^(?=###{0,1}\s+)", text)
    sections = [chunk.strip() for chunk in chunks if chunk.strip()]
    if len(sections) <= 1:
        return sections
    title = sections[0] if sections[0].startswith("# ") else ""
    body_sections = sections[1:] if title else sections
    return [f"{title}\n\n{section}".strip() for section in body_sections]


@lru_cache
def _keyword_whitelist() -> tuple[str, ...]:
    path = _default_kb_dir() / KEYWORD_WHITELIST_FILE
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Keyword whitelist JSON is invalid: %s", path)
        return ()
    keywords = payload.get("keywords") if isinstance(payload, dict) else None
    if not isinstance(keywords, list):
        logger.warning("Keyword whitelist missing keywords list: %s", path)
        return ()
    cleaned = set()
    for keyword in keywords:
        if keyword is None:
            continue
        value = str(keyword).strip()
        if value:
            cleaned.add(value)
    return tuple(sorted(cleaned, key=lambda item: (-len(item), item)))


def _legacy_query_terms(query: str) -> list[str]:
    import jieba

    tags = re.findall(r"#[\w\u4e00-\u9fff]+", query)
    words = [
        word.strip()
        for word in jieba.lcut(query)
        if len(word.strip()) >= 2 and re.match(r"[\w\u4e00-\u9fff]", word.strip())
    ]
    terms = list(set(tags + words))
    return sorted(terms, key=len, reverse=True)[:MAX_QUERY_TERMS]


def _query_terms(query: str) -> list[str]:
    import jieba

    query_text = query.strip()
    if not query_text:
        return []

    tags = re.findall(r"#[\w\u4e00-\u9fff]+", query_text)
    whitelist = _keyword_whitelist()
    if not whitelist:
        return _legacy_query_terms(query_text)

    terms = set(tags)
    query_lower = query_text.lower()
    whitelist_by_lower = {keyword.lower(): keyword for keyword in whitelist}

    # 先做业务短语直匹配，避免 jieba 把“早C午B晚A”“不吃晚餐”等关键表达切碎。
    for keyword in whitelist:
        if keyword in query_text or keyword.lower() in query_lower:
            terms.add(keyword)

    # 再允许 jieba 切出的词命中白名单；未入白名单的泛词不参与关键词检索。
    for word in jieba.lcut(query_text):
        token = word.strip()
        if len(token) < 2 or not re.match(r"[\w\u4e00-\u9fff]", token):
            continue
        matched_keyword = whitelist_by_lower.get(token.lower())
        if matched_keyword:
            terms.add(matched_keyword)

    return sorted(terms, key=lambda item: (-len(item), item))[:MAX_QUERY_TERMS]
