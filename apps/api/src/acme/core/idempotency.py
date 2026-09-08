"""Idempotency keys.

`contracts/api-conventions.md` says every mutating request carries
`Idempotency-Key` and that a repeat returns the original response without
re-executing. That was documented and NOT implemented: the exchange endpoint
accepted the header and ignored it.

It matters because offline retry makes duplicates the normal case, not an edge
case (ADR-0016). A device that queued an exchange in a basement will resend it,
possibly from two devices, possibly hours later.

WHY THE UNIQUE INDEX IS NOT ENOUGH. It saves `connections`, because the pair is
naturally unique. Nothing protects `POST /v1/cards` - a retried card creation
produces two cards and burns the free-tier limit - or a note update, where a
retry silently overwrites an edit made in between.

WHAT IS STORED. The response body and status, keyed by
`(key, method, path, body-hash)`. The body hash is what distinguishes "the same
request again" from "a different request reusing a key", which is a client bug
and returns 409 rather than silently serving the wrong cached response.

ONLY 2xx IS CACHED. A transient 500 must stay retryable; caching it would pin a
failure for 24 hours and the client could never get past it.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

#: 24 hours. Long enough for a device that was offline overnight, short enough
#: that the keyspace does not grow without bound.
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60

#: Only these methods participate. A GET is already idempotent and storing
#: responses for it would be a cache with none of a cache's invalidation rules.
MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status_code: int
    body: bytes

    def to_json(self) -> str:
        return json.dumps({"status_code": self.status_code, "body": self.body.decode()})

    @staticmethod
    def from_json(raw: str) -> "StoredResponse":
        payload = json.loads(raw)
        return StoredResponse(
            status_code=int(payload["status_code"]),
            body=payload["body"].encode(),
        )


def fingerprint(method: str, path: str, body: bytes) -> str:
    """Identify the request, not just the key.

    Without this, a client that reuses a key for a different request gets the
    FIRST request's response back - which looks like success and is not. With
    it, that is a 409 and a bug the client can find.
    """
    digest = hashlib.sha256()
    digest.update(method.encode())
    digest.update(b"\0")
    digest.update(path.encode())
    digest.update(b"\0")
    digest.update(body)
    return digest.hexdigest()


class IdempotencyStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def _key(user_id: str, idempotency_key: str) -> str:
        # Scoped to the user. Two users must never collide on a key, and a key
        # is client-generated so collisions are otherwise plausible.
        return f"idem:{user_id}:{idempotency_key}"

    async def begin(
        self, user_id: str, key: str, request_fingerprint: str
    ) -> StoredResponse | None:
        """Claim the key, or return the stored response for a repeat.

        SETNX is what makes this safe against concurrency: two devices syncing
        the same queue at the same moment both arrive, one claims the key, and
        the other waits and replays rather than both executing.

        Returns None when the caller should proceed.
        Raises KeyError when the key was reused for a DIFFERENT request.
        """
        redis_key = self._key(user_id, key)
        claimed = await self._redis.set(
            redis_key,
            json.dumps({"state": "in_flight", "fingerprint": request_fingerprint}),
            nx=True,
            ex=IDEMPOTENCY_TTL_SECONDS,
        )
        if claimed:
            return None

        raw = await self._redis.get(redis_key)
        if raw is None:
            # Expired between SETNX and GET. Rare, and proceeding is correct:
            # the whole point of the window is that it eventually ends.
            return None

        stored = json.loads(raw)
        if stored.get("fingerprint") != request_fingerprint:
            raise KeyError("idempotency key reused with a different request")

        if stored.get("state") == "in_flight":
            # The original is still running. Returning its future response is
            # impossible, so the client must retry - and a 409 with a retry
            # hint is more honest than blocking the connection.
            raise RuntimeError("original request still in flight")

        return StoredResponse(status_code=int(stored["status_code"]), body=stored["body"].encode())

    async def complete(
        self,
        user_id: str,
        key: str,
        request_fingerprint: str,
        response: StoredResponse,
    ) -> None:
        """Record the outcome so a repeat replays it.

        2xx ONLY - enforced by the caller. Storing a 500 would pin a transient
        failure for a day and the client could never get past it.
        """
        await self._redis.set(
            self._key(user_id, key),
            json.dumps(
                {
                    "state": "done",
                    "fingerprint": request_fingerprint,
                    "status_code": response.status_code,
                    "body": response.body.decode(),
                }
            ),
            ex=IDEMPOTENCY_TTL_SECONDS,
        )

    async def release(self, user_id: str, key: str) -> None:
        """Drop the claim after a failure, so a retry can actually run.

        Without this, a request that 500s leaves an `in_flight` marker for 24
        hours and every retry is refused - turning one transient error into a
        day-long outage for that operation.
        """
        await self._redis.delete(self._key(user_id, key))


class ResponseCache:
    """Short-lived cache for PUBLIC pages.

    The scan-resolution and link-in-bio endpoints are the highest-traffic
    surface in the product and carry a sub-one-second budget on hotel wifi
    (`handoff/05-web-next.md`).

    The TTL is deliberately SHORT. A card edit must appear quickly, so this
    absorbs the burst when a badge is scanned repeatedly at a stand rather than
    acting as a real cache. Cloudflare does the heavy lifting in front.
    """

    def __init__(self, redis: Redis, *, ttl_seconds: int = 30) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def get(self, key: str) -> Any | None:
        raw = await self._redis.get(f"cache:{key}")
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: Any) -> None:
        await self._redis.set(f"cache:{key}", json.dumps(value), ex=self._ttl)

    async def invalidate(self, key: str) -> None:
        """Called on card edit. Cheap, and the alternative is a stale card at
        exactly the moment someone updated it because it was wrong."""
        await self._redis.delete(f"cache:{key}")
