"""In-process sliding-window rate limiting for the chat API."""

import time
from collections import defaultdict, deque


class SlidingWindowLimiter:

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_prune = 0.0

    def retry_after(self, key: str) -> float | None:
        """Seconds the caller must wait, or None when the request is allowed."""
        now = time.monotonic()
        hits = self._hits[key]

        cutoff = now - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= self.max_requests:
            return hits[0] + self.window_seconds - now

        return None

    def record(self, key: str) -> None:
        self._hits[key].append(time.monotonic())
        self._prune()

    def _prune(self) -> None:
        """Drop idle keys so the dict does not grow without bound."""
        now = time.monotonic()
        if now - self._last_prune < self.window_seconds:
            return

        self._last_prune = now
        cutoff = now - self.window_seconds

        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]
