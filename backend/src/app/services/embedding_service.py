from __future__ import annotations

import asyncio

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from app.core.config import Settings, get_settings


class EmbeddingService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = AsyncOpenAI(
            api_key=self.settings.embedding_api_key or "missing",
            base_url=self.settings.embedding_base_url,
            timeout=self.settings.ai_timeout_seconds,
        )

    @property
    def configured(self) -> bool:
        return bool(self.settings.embedding_api_key and self.settings.embedding_model)

    @property
    def model(self) -> str:
        return self.settings.embedding_model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.configured:
            raise RuntimeError("EMBEDDING_API_KEY and EMBEDDING_MODEL are required")
        if not texts:
            return []
        response = await self._create_embeddings_with_retry(texts)
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]

    async def _create_embeddings_with_retry(self, texts: list[str]):
        retryable_status_codes = {408, 409, 425, 429, 500, 502, 503, 504}
        max_attempts = 3
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                return await self._client.embeddings.create(
                    model=self.settings.embedding_model,
                    input=texts,
                )
            except APIStatusError as exc:
                last_error = exc
                if exc.status_code not in retryable_status_codes or attempt >= max_attempts - 1:
                    raise
            except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
                last_error = exc
                if attempt >= max_attempts - 1:
                    raise
            await asyncio.sleep(0.5 * (2**attempt))
        if last_error:
            raise last_error
        raise RuntimeError("Embedding request failed")

    async def embed_text(self, text: str) -> list[float]:
        embeddings = await self.embed_texts([text])
        return embeddings[0] if embeddings else []

    async def close(self) -> None:
        await self._client.close()
