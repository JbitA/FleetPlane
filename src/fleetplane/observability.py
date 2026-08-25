from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Iterator

_CORRELATION_ID: ContextVar[str | None] = ContextVar("fleetplane_correlation_id", default=None)
_LOGGER_NAME = "fleetplane.operations"


def current_correlation_id() -> str | None:
    return _CORRELATION_ID.get()


@contextmanager
def correlation_context(correlation_id: str | None) -> Iterator[None]:
    token: Token[str | None] | None = None
    if correlation_id is not None:
        token = _CORRELATION_ID.set(correlation_id)
    try:
        yield
    finally:
        if token is not None:
            _CORRELATION_ID.reset(token)


def configure_operation_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def operation_log(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        configure_operation_logging()
    payload: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
    }
    correlation_id = current_correlation_id()
    if correlation_id is not None:
        payload["correlation_id"] = correlation_id
    payload.update({key: value for key, value in fields.items() if value is not None})
    logger.log(level, json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str))
