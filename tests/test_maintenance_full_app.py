"""Full real-app smoke in a fresh credential-free process, SQLite fixtures only."""
import os
from pathlib import Path
import subprocess
import sys


def test_real_app_import_startup_jwt_and_readonly_routes():
    root = Path(__file__).resolve().parents[1]
    code = r'''
import base64
import socket
from datetime import timedelta
from unittest.mock import patch
import requests
from sqlalchemy.engine import Engine

def forbidden(*args, **kwargs):
    raise AssertionError("Network or startup database access is forbidden")

with patch.object(socket.socket, "connect", forbidden), patch.object(requests.Session, "request", forbidden), patch.object(Engine, "connect", forbidden):
    import app.main as main
    from fastapi.testclient import TestClient
    from app.services.jwt_service import create_access_token
    from app.db.session import get_db
    from app.services import timbracfdi_client
    with patch.object(main.Base.metadata, "create_all", forbidden), patch.object(main, "_seed_initial_admin", forbidden), patch.object(main.document_queue, "start", forbidden), patch.object(main.outbound_delivery_worker, "start", forbidden):
        with TestClient(main.app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/version").status_code == 200
            assert client.post("/auth/login", json={}).status_code == 503
            assert client.post("/v1/billing/drafts/synthetic/stamp", json={}).status_code == 503
            assert client.get("/v1/billing/drafts/synthetic/stamp-result").status_code == 401

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.models.commercial import ProductCatalog, BillingDraft, BillingDraftItem
from app.models.evento import EventoFacturacion
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread":False}, poolclass=StaticPool)
main.Base.metadata.create_all(engine, tables=[ProductCatalog.__table__, BillingDraft.__table__, BillingDraftItem.__table__, EventoFacturacion.__table__])
Session = sessionmaker(bind=engine)
xml = '<c:Comprobante xmlns:c="http://www.sat.gob.mx/cfd/4" xmlns:t="http://www.sat.gob.mx/TimbreFiscalDigital"><c:Complemento><t:TimbreFiscalDigital UUID="11111111-2222-4333-8444-555555555555"/></c:Complemento></c:Comprobante>'
encoded = base64.b64encode(xml.encode()).decode()
with Session() as db:
    db.add(BillingDraft(id="synthetic", company_id="tenant-a", status="stamped", stamp_status="stamped", stamped_xml_base64=encoded))
    db.commit()

def db_override():
    with Session() as db:
        yield db
main.app.dependency_overrides[get_db] = db_override
statements = []
@event.listens_for(engine, "before_cursor_execute")
def check_readonly(conn, cursor, statement, parameters, context, executemany):
    assert statement.lstrip().upper().startswith("SELECT"), "Unexpected write in read-only handler"
    statements.append(statement)

def token(tenant="tenant-a", role="operator", expiry=None):
    return create_access_token({"sub":"synthetic-user", "tenant":tenant, "role":role}, expires_delta=expiry)
path = "/v1/billing/drafts/synthetic/stamp-result"
with patch.object(socket.socket, "connect", forbidden), patch.object(requests.Session, "request", forbidden), patch.object(timbracfdi_client, "timbra_cfdi", forbidden), patch.object(timbracfdi_client, "ping", forbidden), patch.object(main.Base.metadata, "create_all", forbidden), patch.object(main, "_seed_initial_admin", forbidden), patch.object(main.document_queue, "start", forbidden), patch.object(main.outbound_delivery_worker, "start", forbidden):
    with TestClient(main.app) as client:
        good = {"Authorization":"Bearer " + token()}
        response = client.get(path, headers=good)
        assert response.status_code == 200, response.text
        assert response.json() == {"stamp_status":"stamped", "uuid":"11111111-2222-4333-8444-555555555555", "xml_base64":encoded}
        assert client.get(path, headers={"Authorization":"Bearer " + token("tenant-b")}).status_code == 404
        assert client.get(path + "?company_id=tenant-b", headers=good).status_code == 403
        assert client.get(path, headers={"Authorization":"Bearer " + token(role="viewer")}).status_code == 403
        assert client.get(path, headers={"Authorization":"Bearer " + token(expiry=timedelta(seconds=-60))}).status_code == 401
        assert client.get(path, headers={"Authorization":"Bearer invalid"}).status_code == 401
        for method in ["POST", "PATCH", "DELETE", "PUT"]:
            assert client.request(method, path, headers=good).status_code == 503
        assert client.post("/v1/billing/drafts/synthetic/stamp", headers=good, json={}).status_code == 503
        assert client.post("/v1/billing/timbracfdi/timbra", headers=good, json={}).status_code == 503
assert statements
engine.dispose()
print("Full import/startup, real JWT/tenant guards, read-only SQL and no-network smoke passed")
'''
    env = {"PATH": os.defpath, "HOME": "/tmp", "DATABASE_URL": "sqlite:///:memory:",
           "DB_SSL_REQUIRE": "false", "FISCAL_MAINTENANCE_MODE": "true",
           "AUTH_CONNECTOR": "jwt", "AUTH_ALLOW_ANONYMOUS": "false",
           "JWT_SECRET_KEY": "synthetic-test-only-not-a-real-secret", "PYTHONPATH": str(root)}
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, text=True,
                            capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
