"""Small allow-list boundary for a deliberately read-only release smoke test.

This is not a database backup, migration gate, or authorization replacement.
The allowed business route retains its existing JWT/role/tenant dependencies.
"""
import re


_STAMP_RESULT = re.compile(r"/v1/billing/drafts/[A-Za-z0-9_-]+/stamp-result", re.ASCII)
_HEALTH_PATHS = frozenset({"/health", "/version"})


def validate_maintenance_auth(connector: str, allow_anonymous: bool) -> None:
    if connector.strip().lower() != "jwt" or allow_anonymous:
        raise RuntimeError("Fiscal maintenance requires JWT and anonymous access disabled")


class FiscalMaintenanceMiddleware:
    def __init__(self, app, *, enabled: bool):
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})
            return
        path = scope.get("path", "")
        allowed = (scope.get("method") == "GET" and
                   (path in _HEALTH_PATHS or _STAMP_RESULT.fullmatch(path) is not None))
        if allowed:
            return await self.app(scope, receive, send)
        body = b'{"detail":"Fiscal service in maintenance; operation unavailable"}'
        await send({"type": "http.response.start", "status": 503, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ]})
        await send({"type": "http.response.body", "body": body})
