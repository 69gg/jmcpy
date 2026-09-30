"""传输层：后端适配、重试与多端点切换、统一会话。"""

from __future__ import annotations

from .backends import (
    AsyncCurlCffiBackend,
    AsyncHttpBackend,
    AsyncHttpxBackend,
    CurlCffiBackend,
    HttpBackend,
    HttpxBackend,
    create_async_backend,
    create_backend,
)
from .response import HttpRequest, Reply
from .retry import Attempt, RetryPlanner
from .session import AsyncHttpSession, HttpSession, Validator

__all__ = [
    "AsyncCurlCffiBackend",
    "AsyncHttpBackend",
    "AsyncHttpSession",
    "AsyncHttpxBackend",
    "Attempt",
    "CurlCffiBackend",
    "HttpBackend",
    "HttpRequest",
    "HttpSession",
    "HttpxBackend",
    "Reply",
    "RetryPlanner",
    "Validator",
    "create_async_backend",
    "create_backend",
]
