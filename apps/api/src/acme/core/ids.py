"""Identifier generation (ADR-0010).

Three distinct concepts, deliberately not interchangeable:

  new_id()      internal primary key. UUIDv7, time-ordered so inserts stay
                sequential on append-only tables like connections.
  new_token()   public capability token. Opaque, no embedded timestamp, and
                short enough to keep QR density low.
  Short links   base62 over a sequence, generated in the link domain.

Public surfaces never expose an internal id: a v7 leaks its creation time.
"""

import secrets
from uuid import UUID

import uuid_utils


def new_id() -> UUID:
    """Application-generated UUIDv7.

    Generated here rather than by the database so the id exists before the
    insert. That is what makes offline-created records and idempotent sync
    work (ADR-0016) - the client generates it and it becomes the server id.
    """
    return UUID(str(uuid_utils.uuid7()))


def new_token() -> str:
    """Opaque public token, ~22 chars.

    Not a UUID: a v7 would leak creation time, and 36 characters produces a
    denser, less reliably scannable QR code.
    """
    return secrets.token_urlsafe(16)
