"""Bound chat JSON before parsing it, including chunked uploads without Content-Length."""

from starlette.responses import JSONResponse

from backend.models.chat_input import MAX_CHAT_BODY_BYTES


class ChatBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        is_chat = path == "/chat" or (path.startswith("/api/clients/") and path.endswith("/chat"))
        if scope["type"] != "http" or scope.get("method") != "POST" or not is_chat:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            declared_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid request length"}, 400)(scope, receive, send)
        if declared_length > MAX_CHAT_BODY_BYTES:
            return await JSONResponse({"detail": "Photo request is too large"}, 413)(scope, receive, send)
        parts, size = [], 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            chunk = event.get("body", b"")
            size += len(chunk)
            if size > MAX_CHAT_BODY_BYTES:
                return await JSONResponse({"detail": "Photo request is too large"}, 413)(scope, receive, send)
            parts.append(chunk)
            if not event.get("more_body", False):
                break
        body = b"".join(parts)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
