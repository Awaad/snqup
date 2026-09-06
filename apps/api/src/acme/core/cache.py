"""Valkey access (ADR-0007).

Used for rate limiting, idempotency keys, token revocation, response caching
and as the ARQ broker.

NOT used for realtime fan-out - that is Postgres LISTEN/NOTIFY (ADR-0006).
This is written down because a future reader will notice LISTEN/NOTIFY handles
pub/sub and conclude Valkey is redundant. It is not.

Nothing here is durable. A total Valkey loss must degrade the system, never
corrupt it: limits reset, idempotency windows are lost, caches go cold, and
jobs re-enqueue from their Postgres source of truth. Any job carrying state
only in its queue payload is a bug.
"""

from redis.asyncio import Redis

from acme.core.config import Settings


def create_redis(settings: Settings) -> Redis:
    client: Redis = Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        health_check_interval=30,
    )
    return client


class RateLimiter:
    """Sliding window over a sorted set.

    Fails OPEN with logging (ADR-0007). A false positive during a live event,
    in front of four hundred people, is worse than an unthrottled hour.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check(self, key: str, *, limit: int, window_seconds: int) -> bool:
        import time

        import structlog

        now = time.time()
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(key, 0, now - window_seconds)
                pipe.zcard(key)
                pipe.zadd(key, {str(now): now})
                pipe.expire(key, window_seconds)
                _, count, _, _ = await pipe.execute()
            return bool(count < limit)
        except Exception as exc:
            structlog.get_logger().warning("rate_limit.unavailable", key=key, error=str(exc))
            return True  # fail open, deliberately
