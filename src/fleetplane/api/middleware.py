from __future__ import annotations

import re
import time
from uuid import uuid4

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fleetplane.observability import correlation_context, operation_log

_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def _correlation_id(headers: Headers) -> str:
    supplied = headers.get("x-correlation-id")
    if supplied and _CORRELATION_PATTERN.fullmatch(supplied):
        return supplied
    return str(uuid4())


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        correlation_id = _correlation_id(headers)
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        started = time.perf_counter()
        status_code = 500

        async def traced_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-correlation-id", correlation_id.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        with correlation_context(correlation_id):
            try:
                await self.app(scope, receive, traced_send)
            finally:
                operation_log(
                    "http.request.completed",
                    method=scope.get("method"),
                    path=scope.get("path"),
                    status_code=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                )


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await JSONResponse({"detail": "request body too large"}, status_code=413)(
                        scope, receive, send
                    )
                    return
            except ValueError:
                await JSONResponse({"detail": "invalid content-length"}, status_code=400)(
                    scope, receive, send
                )
                return
        seen = 0

        async def limited_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, limited_receive, send)
