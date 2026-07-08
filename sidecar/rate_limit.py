"""In-memory token-bucket rate limiting for sidecar mutating endpoints (Phase 12)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Simple per-key token bucket (thread-safe)."""

    rate_per_minute: float
    capacity: float | None = None
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        cap = self.capacity if self.capacity is not None else self.rate_per_minute
        self.capacity = cap
        self._tokens = cap
        self._last_refill = time.monotonic()

    def _refill(self, now: float) -> None:
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        added = (self.rate_per_minute / 60.0) * elapsed
        self._tokens = min(self.capacity, self._tokens + added)
        self._last_refill = now

    def consume(self, tokens: float = 1.0) -> bool:
        """Return True if allowed, False if rate limited."""
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def retry_after_sec(self) -> float:
        with self._lock:
            if self._tokens >= 1.0:
                return 0.0
            deficit = 1.0 - self._tokens
            return max(0.0, deficit * 60.0 / self.rate_per_minute)


class RateLimiter:
    """Named buckets for sidecar endpoints."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def configure(self, name: str, rate_per_minute: float, capacity: float | None = None) -> None:
        with self._lock:
            self._buckets[name] = TokenBucket(rate_per_minute, capacity)

    def reset(self, name: str) -> None:
        """Drop per-client buckets for a named limit (test helper)."""
        with self._lock:
            for key in [k for k in self._buckets if k.startswith(f"{name}:")]:
                del self._buckets[key]

    def allow(self, name: str, key: str = "default") -> tuple[bool, float]:
        bucket_key = f"{name}:{key}"
        with self._lock:
            bucket = self._buckets.get(name)
            if bucket is None:
                return True, 0.0
        # Per-client key buckets
        with self._lock:
            if bucket_key not in self._buckets:
                self._buckets[bucket_key] = TokenBucket(
                    bucket.rate_per_minute,
                    bucket.capacity,
                )
            client_bucket = self._buckets[bucket_key]
        ok = client_bucket.consume()
        return ok, client_bucket.retry_after_sec() if not ok else 0.0


_default_limiter = RateLimiter()
_default_limiter.configure("run", rate_per_minute=12.0, capacity=6.0)
_default_limiter.configure("feedback", rate_per_minute=6.0, capacity=3.0)
_default_limiter.configure("fleet_spawn", rate_per_minute=12.0, capacity=6.0)


def get_limiter() -> RateLimiter:
    return _default_limiter
