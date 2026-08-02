"""Rate limiting + multi-tenant identity (SaaS layer).

Per-identity request limiting. Backend:
  * Upstash Redis (fixed-window via INCR/EXPIRE) when configured — correct
    across serverless instances.
  * In-memory sliding window otherwise — best-effort (per warm instance).

Config (env):
  DISTIL_RATE_LIMIT   requests per window (default 60)
  DISTIL_RATE_WINDOW  window seconds (default 60)
  DISTIL_API_KEYS     comma list of tenant API keys (in addition to CONNECTOR_API_KEY)
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from threading import Lock


def allowed_keys() -> set[str]:
    keys = {k.strip() for k in os.getenv("DISTIL_API_KEYS", "").split(",") if k.strip()}
    ck = os.getenv("CONNECTOR_API_KEY")
    if ck:
        keys.add(ck)
    return keys


def auth_required() -> bool:
    return bool(allowed_keys())


def is_valid_key(key: str | None) -> bool:
    keys = allowed_keys()
    return (not keys) or (key in keys)


class RateLimiter:
    def __init__(self):
        self.limit = int(os.getenv("DISTIL_RATE_LIMIT", "60"))
        self.window = int(os.getenv("DISTIL_RATE_WINDOW", "60"))
        self._mem: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()
        self._upstash = None
        # Accept either the classic Upstash naming or Vercel Marketplace's
        # "Upstash for Redis" naming (KV_REST_API_*).
        url = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("KV_REST_API_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("KV_REST_API_TOKEN")
        if url and token:
            self._upstash = (url.rstrip("/"), token)

    def _upstash_check(self, identity: str) -> dict:
        import httpx

        url, token = self._upstash
        bucket = int(time.time() // self.window)
        key = f"rl:{identity}:{bucket}"
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(timeout=5.0) as c:
            count = c.post(url, headers=headers, json=["INCR", key]).json()["result"]
            if count == 1:
                c.post(url, headers=headers, json=["EXPIRE", key, str(self.window)])
        remaining = max(0, self.limit - count)
        return {"allowed": count <= self.limit, "remaining": remaining,
                "limit": self.limit, "reset_in": self.window}

    def _mem_check(self, identity: str) -> dict:
        now = time.time()
        with self._lock:
            dq = self._mem[identity]
            while dq and dq[0] <= now - self.window:
                dq.popleft()
            allowed = len(dq) < self.limit
            if allowed:
                dq.append(now)
            remaining = max(0, self.limit - len(dq))
        return {"allowed": allowed, "remaining": remaining,
                "limit": self.limit, "reset_in": self.window}

    def check(self, identity: str) -> dict:
        try:
            if self._upstash:
                return self._upstash_check(identity)
        except Exception:
            pass
        return self._mem_check(identity)


limiter = RateLimiter()
