"""Synthetic FastAPI wiring check, using installed versions, no real DB/PAC."""
from pathlib import Path
import importlib.util
import unittest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("maintenance", ROOT / "app/core/maintenance.py")
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class WiringTests(unittest.TestCase):
    def test_boundary_executes_before_routes_and_redirects(self):
        app = FastAPI()
        writes = []
        @app.get("/health")
        def health():
            return {"status": "ok"}
        @app.post("/v1/billing/drafts/{draft_id}/stamp")
        def stamp(draft_id: str):
            writes.append(draft_id)
            return {"stamped": True}
        @app.get("/v1/billing/drafts/{draft_id}/stamp-result")
        def result(draft_id: str):
            raise HTTPException(status_code=401, detail="Existing authorization boundary")
        app.add_middleware(maintenance.FiscalMaintenanceMiddleware, enabled=True)
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(client.get("/health/", follow_redirects=False).status_code, 503)
            self.assertEqual(client.post("/v1/billing/drafts/test/stamp").status_code, 503)
            self.assertEqual(client.get("/v1/billing/drafts/test/stamp-result").status_code, 401)
            self.assertEqual(client.get("/docs").status_code, 503)
            self.assertEqual(client.post("/auth/login").status_code, 503)
            self.assertFalse(writes)


if __name__ == "__main__":
    unittest.main()
