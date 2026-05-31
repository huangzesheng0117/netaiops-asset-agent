from contextvars import ContextVar
from typing import Any

_REQUEST_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("request_context", default={})


def set_request_context(ctx: dict[str, Any]):
    return _REQUEST_CONTEXT.set(ctx)


def reset_request_context(token) -> None:
    _REQUEST_CONTEXT.reset(token)


def get_request_context() -> dict[str, Any]:
    return _REQUEST_CONTEXT.get() or {}
