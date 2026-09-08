"""Idempotency, rate limiting and the response cache.

These were DOCUMENTED and not implemented: `api-conventions.md` promised
Idempotency-Key on every mutating endpoint, and the exchange route accepted the
header and ignored it.

Offline retry makes duplicates the normal case, not an edge case (ADR-0016), so
a promise that is not kept here loses or duplicates real work.
"""

import os

import pytest
from redis.asyncio import Redis

from acme.core.cache import RateLimiter
from acme.core.idempotency import (
    IdempotencyStore,
    ResponseCache,
    StoredResponse,
    fingerprint,
)

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def redis():
    client = Redis.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


class TestIdempotency:
    async def test_a_repeat_replays_the_stored_response(self, redis: Redis) -> None:
        store = IdempotencyStore(redis)
        fp = fingerprint("POST", "/v1/exchanges", b'{"token":"abc"}')

        assert await store.begin("user1", "key1", fp) is None
        await store.complete("user1", "key1", fp, StoredResponse(201, b'{"id":"x"}'))

        replay = await store.begin("user1", "key1", fp)
        assert replay is not None
        assert replay.status_code == 201
        assert replay.body == b'{"id":"x"}'

    async def test_the_same_key_with_a_different_body_is_refused(self, redis: Redis) -> None:
        """A client reusing a key for a different request is a BUG.

        Without the fingerprint it would silently receive the first request's
        response, which looks like success and is not.
        """
        store = IdempotencyStore(redis)
        first = fingerprint("POST", "/v1/cards", b'{"display_name":"A"}')
        second = fingerprint("POST", "/v1/cards", b'{"display_name":"B"}')

        await store.begin("user1", "key1", first)
        await store.complete("user1", "key1", first, StoredResponse(201, b"{}"))

        with pytest.raises(KeyError):
            await store.begin("user1", "key1", second)

    async def test_an_in_flight_original_refuses_rather_than_blocking(self, redis: Redis) -> None:
        """Two devices syncing the same offline queue arrive together.

        One claims the key; the other cannot be handed a response that does not
        exist yet, so it is told to retry rather than holding a connection open.
        """
        store = IdempotencyStore(redis)
        fp = fingerprint("POST", "/v1/exchanges", b"{}")

        assert await store.begin("user1", "key1", fp) is None
        with pytest.raises(RuntimeError):
            await store.begin("user1", "key1", fp)

    async def test_keys_are_scoped_per_user(self, redis: Redis) -> None:
        """Keys are client-generated, so collisions between users are plausible
        and would serve one user another's response."""
        store = IdempotencyStore(redis)
        fp = fingerprint("POST", "/v1/cards", b"{}")

        await store.begin("user1", "shared-key", fp)
        # Same key, different user: must be free to proceed.
        assert await store.begin("user2", "shared-key", fp) is None

    async def test_release_lets_a_failed_request_be_retried(self, redis: Redis) -> None:
        """Without release, one transient 500 blocks every retry of that
        operation for 24 hours - turning a blip into a day-long outage."""
        store = IdempotencyStore(redis)
        fp = fingerprint("POST", "/v1/exchanges", b"{}")

        await store.begin("user1", "key1", fp)
        await store.release("user1", "key1")

        assert await store.begin("user1", "key1", fp) is None

    def test_fingerprint_distinguishes_path_and_method(self) -> None:
        body = b"{}"
        assert fingerprint("POST", "/a", body) != fingerprint("POST", "/b", body)
        assert fingerprint("POST", "/a", body) != fingerprint("PATCH", "/a", body)
        assert fingerprint("POST", "/a", body) == fingerprint("POST", "/a", body)


class TestRateLimiter:
    async def test_requests_under_the_limit_pass(self, redis: Redis) -> None:
        limiter = RateLimiter(redis)
        for _ in range(5):
            assert await limiter.check("k", limit=5, window_seconds=60) is True

    async def test_the_limit_is_enforced(self, redis: Redis) -> None:
        limiter = RateLimiter(redis)
        for _ in range(3):
            await limiter.check("k", limit=3, window_seconds=60)
        assert await limiter.check("k", limit=3, window_seconds=60) is False

    async def test_keys_do_not_share_a_budget(self, redis: Redis) -> None:
        limiter = RateLimiter(redis)
        for _ in range(3):
            await limiter.check("a", limit=3, window_seconds=60)
        assert await limiter.check("b", limit=3, window_seconds=60) is True

    async def test_it_fails_open_when_redis_is_unreachable(self) -> None:
        """ADR-0007. A false positive during a live event, in front of four
        hundred people, is worse than an unthrottled hour."""
        dead = Redis.from_url("redis://127.0.0.1:6390/0", socket_connect_timeout=0.1)
        limiter = RateLimiter(dead)
        assert await limiter.check("k", limit=1, window_seconds=60) is True
        await dead.aclose()


class TestResponseCache:
    async def test_values_round_trip(self, redis: Redis) -> None:
        cache = ResponseCache(redis, ttl_seconds=30)
        await cache.set("card:slug:sarah", {"display_name": "Sarah"})
        assert (await cache.get("card:slug:sarah"))["display_name"] == "Sarah"

    async def test_a_miss_returns_none_rather_than_raising(self, redis: Redis) -> None:
        assert await ResponseCache(redis).get("card:slug:nobody") is None

    async def test_invalidation_is_immediate(self, redis: Redis) -> None:
        """Called on card edit. A stale card at the moment someone corrected it
        is exactly the wrong time to serve one."""
        cache = ResponseCache(redis)
        await cache.set("k", {"v": 1})
        await cache.invalidate("k")
        assert await cache.get("k") is None
