from __future__ import annotations

import time


class MessageDeduplicator:
    """TTL-based bounded message deduplication cache."""

    def __init__(self, max_size: int = 1000, ttl_seconds: float = 300):
        self._seen: dict[str, float] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds

    def is_duplicate(self, msg_id: str) -> bool:
        if not msg_id:
            return False
        now = time.time()
        if msg_id in self._seen:
            if now - self._seen[msg_id] < self._ttl:
                return True
            del self._seen[msg_id]
        self._seen[msg_id] = now
        if len(self._seen) > self._max_size:
            cutoff = now - self._ttl
            self._seen = {key: ts for key, ts in self._seen.items() if ts > cutoff}
            if len(self._seen) > self._max_size:
                newest = sorted(self._seen.items(), key=lambda item: item[1])[
                    -self._max_size :
                ]
                self._seen = dict(newest)
        return False

    def clear(self) -> None:
        self._seen.clear()

