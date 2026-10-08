"""Bounded in-memory token-bucket rate limiting for abuse-prone endpoints.

Single-process by design (SQLite deployment, no external infra): per
``(bucket, identity)`` token buckets in a module-level dict, capped and
pruned so memory stays bounded. Identities are client IPs for anonymous
endpoints and ``user:<id>`` for authenticated ones. Exceeding a bucket
raises 429 with a ``Retry-After`` header and a generic body.

Limits are tunable per bucket via ``RATE_LIMIT_<BUCKET>`` (requests per
minute, capacity equals the rate) read at call time, so tests and ops can
adjust without reloading.
"""

import math
import os
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status

from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.models import User

MAX_TRACKED_BUCKETS = 10_000
STALE_AFTER_SECONDS = 3600.0
TOO_MANY_REQUESTS_DETAIL = "Too many requests, please try again later"


@dataclass(frozen=True)
class BucketSpec:
    capacity: int
    refill_per_minute: float


DEFAULT_BUCKETS: dict[str, BucketSpec] = {
    "login": BucketSpec(5, 5),
    "register": BucketSpec(3, 3),
    "forgot_password": BucketSpec(3, 3),
    "reset_password": BucketSpec(5, 5),
    "refresh": BucketSpec(30, 30),
    "v1_transcription": BucketSpec(30, 30),
    "chat": BucketSpec(20, 20),
    "upload": BucketSpec(10, 10),
}


class TokenBucket:
    __slots__ = ("capacity", "refill_per_second", "tokens", "updated_at")

    def __init__(self, spec: BucketSpec, now: float):
        self.capacity = spec.capacity
        self.refill_per_second = spec.refill_per_minute / 60.0
        self.tokens = float(spec.capacity)
        self.updated_at = now

    def try_consume(self, now: float) -> float:
        """Consume one token; return 0.0 on success, else seconds to next refill."""
        self._refill(now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        return (1.0 - self.tokens) / self.refill_per_second

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(float(self.capacity), self.tokens + elapsed * self.refill_per_second)
        self.updated_at = now


_buckets: dict[tuple[str, str], TokenBucket] = {}


def reset_all() -> None:
    _buckets.clear()


def _env_spec(bucket: str, default: BucketSpec) -> BucketSpec:
    raw = os.getenv(f"RATE_LIMIT_{bucket.upper()}")
    if not raw:
        return default
    try:
        per_minute = float(raw)
    except ValueError:
        return default
    if per_minute <= 0:
        return default
    return BucketSpec(max(1, math.ceil(per_minute)), per_minute)


def spec_for(bucket: str) -> BucketSpec:
    return _env_spec(bucket, DEFAULT_BUCKETS[bucket])


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _prune_stale(now: float) -> None:
    if len(_buckets) < MAX_TRACKED_BUCKETS:
        return
    for key in [k for k, b in _buckets.items() if now - b.updated_at > STALE_AFTER_SECONDS]:
        _buckets.pop(key, None)


def enforce(bucket: str, identity: str) -> None:
    now = time.monotonic()
    _prune_stale(now)
    key = (bucket, identity)
    token_bucket = _buckets.get(key)
    if token_bucket is None:
        token_bucket = TokenBucket(spec_for(bucket), now)
        _buckets[key] = token_bucket
    retry_after = token_bucket.try_consume(now)
    if retry_after > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=TOO_MANY_REQUESTS_DETAIL,
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


def ip_rate_limit(bucket: str):
    async def _dependency(request: Request) -> None:
        enforce(bucket, client_key(request))

    return _dependency


def user_rate_limit(bucket: str):
    async def _dependency(request: Request, user: User = Depends(get_current_user)) -> None:
        enforce(bucket, f"user:{user.id}")

    return _dependency
