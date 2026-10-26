"""Dependency-light tests; never import production app/config or connect to a DB."""
import ast
import asyncio
from contextlib import asynccontextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("maintenance", ROOT / "app/core/maintenance.py")
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, method="GET", path="/health", enabled=True,
                      scope_type="http", downstream_status=200, extra=None):
        called, sent = [], []
        async def app(scope, receive, send):
            called.append(scope)
            await send({"type": "http.response.start", "status": downstream_status})
        async def receive():
            raise AssertionError("Blocked request body must not be consumed")
        async def send(item):
            sent.append(item)
        scope = {"type": scope_type, "method": method, "path": path}
        scope.update(extra or {})
        await maintenance.FiscalMaintenanceMiddleware(app, enabled=enabled)(scope, receive, send)
        return called, sent

    async def test_exact_get_allowlist(self):
        for path in ["/health", "/version", "/v1/billing/drafts/abc-123_/stamp-result"]:
            with self.subTest(path=path):
                called, sent = await self.request(path=path)
                self.assertEqual(len(called), 1)
                self.assertEqual(sent[0]["status"], 200)

    async def test_every_non_get_method_is_blocked_even_on_health(self):
        for method in ["POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT", "get", "UNKNOWN"]:
            for path in ["/health", "/v1/billing/drafts/draft/stamp-result", "/v1/billing/drafts/draft/stamp"]:
                with self.subTest(method=method, path=path):
                    called, sent = await self.request(method=method, path=path)
                    self.assertFalse(called)
                    self.assertEqual(sent[0]["status"], 503)

    async def test_unsafe_or_unknown_gets_fail_closed(self):
        for path in ["/", "/docs", "/openapi.json", "/auth/login", "/auth/sandbox/signup",
                     "/v1/billing/provider/status", "/admin/backups/export", "/sync",
                     "/v1/billing/drafts/draft/stamp", "/cancel", "/health/",
                     "/v1/billing/drafts/a/b/stamp-result", "/v1/billing/drafts/../stamp-result",
                     "/v1/billing/drafts/a%2fb/stamp-result", "/v1/billing/drafts/a/stamp-result/",
                     "/v1/billing/drafts/a/stamp-result\n", "/future-mutation-route"]:
            with self.subTest(path=path):
                called, sent = await self.request(path=path)
                self.assertFalse(called)
                self.assertEqual(sent[0]["status"], 503)

    async def test_existing_auth_and_tenant_denials_are_preserved(self):
        for status in [401, 403, 404]:
            called, sent = await self.request(path="/v1/billing/drafts/a/stamp-result", downstream_status=status)
            self.assertEqual(len(called), 1)
            self.assertEqual(sent[0]["status"], status)

    async def test_method_override_headers_do_not_allow_post(self):
        called, sent = await self.request(method="POST", extra={"headers": [(b"x-http-method-override", b"GET")]})
        self.assertFalse(called)
        self.assertEqual(sent[0]["status"], 503)

    async def test_websocket_is_rejected(self):
        called, sent = await self.request(scope_type="websocket")
        self.assertFalse(called)
        self.assertEqual(sent, [{"type": "websocket.close", "code": 1013}])

    async def test_disabled_mode_preserves_normal_routing(self):
        called, sent = await self.request(method="POST", path="/anything", enabled=False)
        self.assertEqual(len(called), 1)
        self.assertEqual(sent[0]["status"], 200)

    async def test_lifespan_passes_through_boundary(self):
        called, _ = await self.request(scope_type="lifespan")
        self.assertEqual(len(called), 1)


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def run_lifespan(self, enabled):
        # Execute the actual lifespan AST from candidate main.py, with inert doubles.
        tree = ast.parse((ROOT / "app/main.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
        module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
        create_all, seed = Mock(), Mock()
        queue = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        outbound = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        ns = {"asynccontextmanager": asynccontextmanager, "FastAPI": object,
              "FISCAL_MAINTENANCE": enabled, "Base": SimpleNamespace(metadata=SimpleNamespace(create_all=create_all)),
              "engine": SimpleNamespace(dialect=SimpleNamespace(name="sqlite")), "_seed_initial_admin": seed, "document_queue": queue,
              "outbound_delivery_worker": outbound,
              "settings": SimpleNamespace(DOCUMENT_QUEUE_WORKERS=1, SYNC_OUTBOUND_AUTOPOLL=True, ALERTS_AUTOPOLL=False)}
        exec(compile(module, "candidate-lifespan", "exec"), ns)
        async with ns["lifespan"](None):
            pass
        return create_all, seed, queue, outbound

    async def test_maintenance_skips_all_startup_and_shutdown_writers(self):
        create_all, seed, queue, outbound = await self.run_lifespan(True)
        create_all.assert_not_called()
        seed.assert_not_called()
        queue.start.assert_not_called()
        queue.stop.assert_not_called()
        outbound.start.assert_not_called()
        outbound.stop.assert_not_called()

    async def test_normal_lifespan_preserved(self):
        create_all, seed, queue, outbound = await self.run_lifespan(False)
        create_all.assert_called_once()
        seed.assert_called_once()
        queue.start.assert_awaited_once_with(1)
        queue.stop.assert_awaited_once()
        outbound.start.assert_awaited_once()
        outbound.stop.assert_awaited_once()

    def test_maintenance_rejects_insecure_auth_configuration(self):
        maintenance.validate_maintenance_auth("jwt", False)
        for connector, anonymous in [("none", False), ("header", False), ("typo", False), ("jwt", True)]:
            with self.subTest(connector=connector, anonymous=anonymous):
                with self.assertRaises(RuntimeError):
                    maintenance.validate_maintenance_auth(connector, anonymous)


if __name__ == "__main__":
    unittest.main()
