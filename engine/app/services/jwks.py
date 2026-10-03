"""jwks.py - cached JWKS lookup for Supabase Auth asymmetric (ES256/RS256) JWTs.

Supabase publishes its current + previous public signing keys at
`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`. Nothing here is secret and no
key material is ever committed or configured by hand: keys are fetched from the
endpoint, selected by the JWT header `kid`, and cached in-process.

Behaviour (deterministic, no LLM):
  * cache      keys are kept for JWKS_TTL_SECONDS; a verification inside the TTL
               makes NO network call.
  * rotation   a `kid` that is not in the cache triggers ONE refresh (so a freshly
               rotated key works immediately), but refreshes are rate-limited to
               one per JWKS_MIN_REFRESH_SECONDS so a flood of bogus `kid`s cannot
               turn this engine into a request amplifier against Supabase.
  * outage     if a refresh fails, keys we already hold keep verifying for up to
               JWKS_MAX_STALE_SECONDS; with no usable keys the lookup raises
               JwksUnavailable (callers map it to 503 - fail closed, never trust).
  * unknown    a `kid` still missing after a refresh raises UnknownKid (401).
Tokens, keys and the JWKS body are never logged.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import httpx
import jwt as pyjwt

log = logging.getLogger("scaffold.jwks")

JWKS_TTL_SECONDS = 600
JWKS_MIN_REFRESH_SECONDS = 30
JWKS_MAX_STALE_SECONDS = 24 * 3600
JWKS_FETCH_TIMEOUT = 5.0

SUPPORTED_ALGS = ("ES256", "RS256")


class JwksUnavailable(RuntimeError):
    """No usable verification keys (endpoint unreachable/invalid and nothing cached)."""


class UnknownKid(LookupError):
    """The token's `kid` is not published in the JWKS (even after a refresh)."""


def jwks_url_for(supabase_url: str) -> str:
    return supabase_url.strip().rstrip("/") + "/auth/v1/.well-known/jwks.json"


def _http_fetch(url: str) -> dict:
    resp = httpx.get(url, timeout=JWKS_FETCH_TIMEOUT, follow_redirects=False)
    resp.raise_for_status()
    return resp.json()


class JwksCache:
    def __init__(
        self,
        fetch: Callable[[str], dict] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch or _http_fetch
        self._clock = clock
        self._lock = threading.Lock()
        # url -> {"keys": {kid: PyJWK}, "fetched": t, "attempted": t}
        self._state: dict[str, dict] = {}

    def clear(self) -> None:
        with self._lock:
            self._state.clear()

    def _refresh(self, url: str, st: dict | None) -> bool:
        now = self._clock()
        try:
            body = self._fetch(url)
            keys: dict[str, pyjwt.PyJWK] = {}
            for jwk in body.get("keys", []):
                kid = jwk.get("kid")
                if not kid or jwk.get("use", "sig") != "sig":
                    continue
                try:
                    keys[kid] = pyjwt.PyJWK(jwk)
                except pyjwt.PyJWTError:
                    continue  # skip an unparseable/unsupported entry, keep the rest
            if not keys:
                raise ValueError("JWKS contained no usable signing keys")
        except Exception as exc:  # network, HTTP status, JSON, shape - all "refresh failed"
            log.warning("jwks refresh failed (%s)", type(exc).__name__)
            if st is not None:
                st["attempted"] = now
            else:
                self._state[url] = {"keys": {}, "fetched": None, "attempted": now}
            return False
        self._state[url] = {"keys": keys, "fetched": now, "attempted": now}
        return True

    def get_key(self, url: str, kid: str) -> pyjwt.PyJWK:
        with self._lock:
            now = self._clock()
            st = self._state.get(url)
            fresh = bool(st and st["fetched"] is not None and now - st["fetched"] < JWKS_TTL_SECONDS)
            if fresh and kid in st["keys"]:
                return st["keys"][kid]

            # Stale cache or unknown kid: refresh, but never more than once per cooldown.
            may_refresh = st is None or now - st["attempted"] >= JWKS_MIN_REFRESH_SECONDS
            if may_refresh:
                self._refresh(url, st)
                st = self._state.get(url)

            if st and st["fetched"] is not None and now - st["fetched"] < JWKS_MAX_STALE_SECONDS:
                if kid in st["keys"]:
                    return st["keys"][kid]
                raise UnknownKid(kid)
            raise JwksUnavailable("no usable JWKS keys available")


# Process-wide cache used by services.auth (tests inject their own via `cache`).
cache = JwksCache()


