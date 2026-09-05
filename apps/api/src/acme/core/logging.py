"""Structured logging (ADR-0024).

Every line carries request_id, user_id, service, env and version so a user
report maps to one trace across three runtimes.

Redaction is a processor, not developer discipline. This product's logs would
otherwise be a contact database.
"""

import logging
from typing import Any

import structlog

# Redacted recursively by key name. Add to this list, never remove from it.
SENSITIVE_KEYS = frozenset(
    {
        "email",
        "phone",
        "token",
        "password",
        "authorization",
        "jwt",
        "secret",
        "api_key",
        "note",  # private connection notes (ADR-0003)
        "reply_email",
    }
)

REDACTED = "[redacted]"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (REDACTED if k.lower() in SENSITIVE_KEYS else _redact(v))
            for k, v in value.items()  # type: ignore[union-attr]
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]  # type: ignore[union-attr]
    return value


def redact_processor(
    _logger: Any, _name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Recursive so that logging a whole object cannot bypass redaction."""
    return _redact(dict(event_dict))  # type: ignore[return-value]


def configure_logging(*, level: str, service: str, env: str, version: str) -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper()))

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # OpenTelemetry semantic conventions even without OTel itself,
            # so the eventual migration is configuration (ADR-0024).
            structlog.processors.CallsiteParameterAdder(
                {structlog.processors.CallsiteParameter.MODULE}
            ),
            redact_processor,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper())
        ),
        cache_logger_on_first_use=True,
    )

    structlog.contextvars.bind_contextvars(service=service, env=env, version=version)
