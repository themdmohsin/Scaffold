"""Deterministic in-process rate limiting for auth / reason / secret endpoints.

Plain Python on purpose (repo rule #3 — deterministic logic stays deterministic).
A sliding 60-second window per (scope, client IP, credential fingerprint):

  * scope auth    — /auth/*, POST /projects/join, POST .../invite  (credential stuffing)
  * scope reason  — POST /projects/:id/reason                     (the paid LLM call)
  * scope secret  — /environment/request, /environment/pull, value-bearing mutations

The credential is hashed into the key (never kept raw in memory) so one
teammate's token exhaustion cannot lock out another teammate behind the same
NAT, and the limiter's memory never contains a usable credential.

Limits come from settings (SCAFFOLD_RATE_LIMIT_*_PER_MINUTE, disable with
SCAFFOLD_RATE_LIMIT_ENABLED=0) and are read per request, so tests can retune
them at runtime. In-process = per engine replica; a multi-replica deployment
needs an edge limiter too (docs/OPERATIONS.md).
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass

WINDOW_SECONDS = 60.0
MAX_KEYS = 20_000

AUTH_SCOPE = "auth"
REASON_SCOPE = "reason"
SECRET_SCOPE = "secret"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class SlidingWindowLimiter:
    """Thread-safe-enough in-process limiter (single event loop; dict ops are atomic)."""

    def __init__(self, window_seconds: float = WINDOW_SECONDS, max_keys: int = MAX_KEYS) -> None:
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, limit: int, *, now: float | None = None) -> Decision:
        now = time.monotonic() if now is None else now
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_keys:
                self._evict(now)
            hits = deque()
            self._hits[key] = hits

        cutoff = now - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= limit:
            retry_after = max(1, int(hits[0] + self.window - now) + 1)
            return Decision(False, limit, 0, retry_after)

        hits.append(now)
        return Decision(True, limit, max(0, limit - len(hits)), 0)

    def reset(self) -> None:
        self._hits.clear()

    def _evict(self, now: float) -> None:
        """Keep memory bounded: drop keys idle for a full window first."""
        stale = [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]
        for key in stale:
            self._hits.pop(key, None)
        if len(self._hits) >= self.max_keys:
            for key in list(self._hits)[: self.max_keys // 2]:
                self._hits.pop(key, None)


limiter = SlidingWindowLimiter()


def classify(method: str, path: str) -> str | None:
    """Map a request to a rate-limited scope, or None when unlimited."""
    p = path.rstrip("/") or "/"
    if p.startswith("/auth/"):
        return AUTH_SCOPE
    if p == "/projects/join":
        return AUTH_SCOPE
    if method == "POST" and p.endswith("/invite"):
        return AUTH_SCOPE
    if method == "POST" and p.endswith("/reason"):
        return REASON_SCOPE
    if "/environment" in p:
        if p.endswith("/request") or p.endswith("/pull"):
            return SECRET_SCOPE
        if method in ("POST", "PATCH") and "/variables" in p:
            return SECRET_SCOPE
    return None


def limit_for(scope_name: str, settings) -> int:
    if scope_name == AUTH_SCOPE:
        return int(settings.scaffold_rate_limit_auth_per_minute)
    if scope_name == REASON_SCOPE:
        return int(settings.scaffold_rate_limit_reason_per_minute)
    if scope_name == SECRET_SCOPE:
        return int(settings.scaffold_rate_limit_secret_per_minute)
    return 0


def key_for(scope_name: str, client_ip: str, authorization: str | None) -> str:
    fingerprint = hashlib.sha256((authorization or "anonymous").encode("utf-8")).hexdigest()[:12]
    return f"{scope_name}:{client_ip}:{fingerprint}"
