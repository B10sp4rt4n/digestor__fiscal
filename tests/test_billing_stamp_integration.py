"""Synthetic billing DB tests. No production URL, credentials or PAC calls."""
import base64
from datetime import datetime
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routers import commercial_v1 as api
from app.core.auth import SecurityContext
from app.db.base import Base
from app.models.commercial import BillingDraft, BillingDraftItem, ProductCatalog
from app.models.fiscal_act import FiscalAct
from app.models.csf import CSF  # register related mappers, not created in SQLite
from app.models.sucursal import Sucursal
from app.models.usuario import Usuario
from app.models.evento import EventoFacturacion

STAMPED_XML = '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"><cfdi:Complemento><tfd:TimbreFiscalDigital UUID="11111111-2222-4333-8444-555555555555"/></cfdi:Complemento></cfdi:Comprobante>'
CTX = SecurityContext(tenant_id="synthetic-a", user_id="test-operator", role="operator")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = [ProductCatalog.__table__, BillingDraft.__table__, BillingDraftItem.__table__, EventoFacturacion.__table__, FiscalAct.__table__]
    Base.metadata.create_all(engine, tables=tables)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("External network is forbidden in fiscal tests")
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    monkeypatch.setattr(api.settings, "AUTH_CONNECTOR", "jwt")


def seed(db, **overrides):
    draft = BillingDraft(
        id="synthetic-draft", company_id=CTX.tenant_id, status="ready_to_stamp",
        customer_name="SYNTHETIC CUSTOMER", customer_rfc="AAA010101AAA",
        customer_zip="01000", customer_regimen="616", customer_use_cfdi="S01",
        emitter_rfc="EKU9003173C9", emitter_name="SYNTHETIC EMITTER", emitter_regimen="601",
        place_of_issue="01000", subtotal=100, taxes=16, total=116,
        items=[BillingDraftItem(company_id=CTX.tenant_id, description="Synthetic service", quantity=1,
                                unit_price=100, line_subtotal=100, line_taxes=16, line_total=116)],
    )
    for k, v in overrides.items():
        setattr(draft, k, v)
    db.add(draft)
    db.commit()
    return draft


def stamp(db):
    return api.stamp_billing_draft("synthetic-draft", api.BillingDraftStampRequest(), None, CTX, db)


def result(db, ctx=CTX):
    return api.get_billing_draft_stamp_result("synthetic-draft", None, ctx, db)


@pytest.mark.parametrize("response", [
    {"ok": False, "status_code": 500, "provider_response": {"raw": "upstream failed"}},
    {"ok": False, "status_code": 502, "provider_response": {"Codigo": 301, "Mensaje": "proxy error"}},
    {"ok": False, "status_code": 408, "provider_response": {"Codigo": 301}},
    {"ok": False, "status_code": 200, "provider_response": "unexpected response"},
    {"ok": False, "status_code": 400, "provider_response": {"raw": "malformed SOAP"}},
    {"ok": True, "status_code": 200, "provider_response": {"Codigo": 0}},
    {"ok": True, "status_code": 200, "provider_response": {"Codigo": 0, "Xml": "TimbreFiscalDigital UUID=\"fake\""}},
])
def test_ambiguous_response_keeps_reservation(db, monkeypatch, response):
    seed(db)
    pac = Mock(return_value=response)
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", pac)
    with pytest.raises(HTTPException) as exc:
        stamp(db)
    assert exc.value.status_code == 502
    assert result(db) == {"stamp_status": "unknown", "uuid": None, "xml_base64": None}
    with pytest.raises(HTTPException) as retry:
        stamp(db)
    assert retry.value.status_code == 409
    assert pac.call_count == 1


@pytest.mark.parametrize("response", [
    {"ok": False, "status_code": 400, "provider_response": {"Categoria": "Validacion", "CodigoSat": "CFDI40139", "Mensaje": "Synthetic rejection"}},
    {"ok": True, "status_code": 200, "provider_response": {"Codigo": 301, "Mensaje": "Synthetic rejection"}},
])
def test_explicit_rejection_releases_for_correction(db, monkeypatch, response):
    seed(db)
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", Mock(return_value=response))
    assert stamp(db).ok is False
    assert result(db)["stamp_status"] == "rejected"
    updated = api.update_billing_draft("synthetic-draft", api.BillingDraftUpdateRequest(notes="corrected"), None, CTX, db)
    assert updated.notes == "corrected"


def test_timeout_and_read_only_consultation(db, monkeypatch):
    seed(db)
    pac = Mock(side_effect=TimeoutError("synthetic timeout"))
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", pac)
    with pytest.raises(HTTPException):
        stamp(db)
    for _ in range(2):
        assert result(db)["stamp_status"] == "unknown"
    with pytest.raises(HTTPException) as edit:
        api.update_billing_draft("synthetic-draft", api.BillingDraftUpdateRequest(notes="unsafe"), None, CTX, db)
    assert edit.value.status_code == 409
    assert pac.call_count == 1


def test_success_result_contract_preserves_xml_and_blocks_repeat(db, monkeypatch):
    seed(db)
    pac = Mock(return_value={"ok": True, "status_code": 200, "provider_response": {"Codigo": 0, "Xml": STAMPED_XML}})
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", pac)
    assert stamp(db).ok is True
    read = result(db)
    assert set(read) == {"stamp_status", "uuid", "xml_base64"}
    assert read["stamp_status"] == "stamped"
    assert read["uuid"] == "11111111-2222-4333-8444-555555555555"
    assert base64.b64decode(read["xml_base64"]).decode() == STAMPED_XML
    with pytest.raises(HTTPException) as retry:
        stamp(db)
    assert retry.value.status_code == 409
    assert pac.call_count == 1
    assert db.query(EventoFacturacion).count() == 1


@pytest.mark.parametrize("overrides", [
    {"stamped_xml_base64": base64.b64encode(STAMPED_XML.encode()).decode()},
    {"stamped_at": datetime(2026, 1, 1)},
    {"status": "stamped"},
])
def test_historical_evidence_blocks_stamp_and_edit(db, monkeypatch, overrides):
    draft = seed(db, **overrides)
    original = (draft.stamped_xml_base64, draft.stamped_at, draft.status)
    pac = Mock()
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", pac)
    with pytest.raises(HTTPException) as retry:
        stamp(db)
    assert retry.value.status_code == 409
    with pytest.raises(HTTPException) as edit:
        api.update_billing_draft("synthetic-draft", api.BillingDraftUpdateRequest(notes="unsafe"), None, CTX, db)
    assert edit.value.status_code == 409
    db.refresh(draft)
    assert (draft.stamped_xml_base64, draft.stamped_at, draft.status) == original
    pac.assert_not_called()


def test_cross_tenant_result_is_not_found(db):
    seed(db)
    with pytest.raises(HTTPException) as exc:
        result(db, SecurityContext(tenant_id="synthetic-b", user_id="other", role="operator"))
    assert exc.value.status_code == 404


def test_fiscal_act_chain_binds_request_and_result_in_same_transactions(db, monkeypatch):
    import json
    from app.services import fiscal_acts
    seed(db)
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", Mock(return_value={
        "ok": True, "status_code": 200, "provider_response": {"Codigo": 0, "Xml": STAMPED_XML}}))
    stamp(db)
    rows = db.query(FiscalAct).order_by(FiscalAct.sequence).all()
    payloads = [json.loads(row.payload) for row in rows]
    assert [p["action_or_state"] for p in payloads] == ["fiscal.stamp_reserved", "fiscal.request_prepared", "fiscal.stamp_confirmed"]
    assert payloads[-1]["evidence_refs"]["result_xml_sha256"] == fiscal_acts.digest(STAMPED_XML)
    assert payloads[-1]["evidence_refs"]["request_xml_sha256"] == payloads[1]["evidence_refs"]["request_xml_sha256"]
    assert all(p["tenant_id"] == CTX.tenant_id and p["actors"][0]["actor_id"] == CTX.user_id for p in payloads)
    assert all(p["memory"]["status"] == "local_only" for p in payloads)
    fiscal_acts.verify(rows)
    with pytest.raises(HTTPException): stamp(db)
    assert db.query(FiscalAct).count() == 3


def test_rejection_correction_and_new_attempt_extend_history_without_rewriting(db, monkeypatch):
    import json
    from app.services import fiscal_acts
    seed(db)
    pac = Mock(side_effect=[
        {"ok": True, "status_code": 200, "provider_response": {"Codigo": 301, "Mensaje": "Synthetic rejection"}},
        {"ok": True, "status_code": 200, "provider_response": {"Codigo": 0, "Xml": STAMPED_XML}},
    ])
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", pac)
    assert stamp(db).ok is False
    original = [(row.id, row.payload, row.content_hash) for row in db.query(FiscalAct).order_by(FiscalAct.sequence)]
    api.update_billing_draft("synthetic-draft", api.BillingDraftUpdateRequest(customer_name="CORRECTED CUSTOMER"), None, CTX, db)
    assert stamp(db).ok is True
    rows = db.query(FiscalAct).order_by(FiscalAct.sequence).all()
    assert [(row.id, row.payload, row.content_hash) for row in rows[:3]] == original
    assert json.loads(rows[3].payload)["action_or_state"] == "fiscal.draft_edited"
    assert len(rows) == 7
    fiscal_acts.verify(rows)


def test_failed_audit_before_provider_rolls_back_reservation(db, monkeypatch):
    seed(db)
    pac = Mock()
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", pac)
    monkeypatch.setattr(api.fiscal_acts, "append", Mock(side_effect=ValueError("audit unavailable")))
    with pytest.raises(ValueError): stamp(db)
    db.rollback()
    assert api._get_draft_or_404(db, CTX.tenant_id, "synthetic-draft").stamp_status == "not_sent"
    pac.assert_not_called()


def test_failed_result_audit_leaves_persisted_reservation_and_prepared_request(db, monkeypatch):
    from app.services import fiscal_acts
    original = fiscal_acts.append
    def fail_result(*args, **kwargs):
        if args[3] == "stamp_confirmed": raise ValueError("audit persistence failure")
        return original(*args, **kwargs)
    seed(db)
    pac = Mock(return_value={"ok": True, "status_code": 200, "provider_response": {"Codigo": 0, "Xml": STAMPED_XML}})
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", pac)
    monkeypatch.setattr(api.fiscal_acts, "append", fail_result)
    with pytest.raises(ValueError): stamp(db)
    db.rollback()
    assert result(db)["stamp_status"] == "processing"
    assert db.query(FiscalAct).count() == 2
    with pytest.raises(HTTPException) as error: stamp(db)
    assert error.value.status_code == 409
    assert pac.call_count == 1


def test_ledger_refuses_orm_rewrites_and_detects_raw_tampering(db, monkeypatch):
    from sqlalchemy import text
    from app.services import fiscal_acts
    seed(db)
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", Mock(return_value={
        "ok": True, "status_code": 200, "provider_response": {"Codigo": 301, "Mensaje": "Synthetic rejection"}}))
    stamp(db)
    row = db.query(FiscalAct).order_by(FiscalAct.sequence).first()
    row.payload = "{}"
    with pytest.raises(ValueError): db.commit()
    db.rollback()
    db.execute(text("UPDATE billing_fiscal_acts SET payload='{}' WHERE sequence=1"))
    db.commit()
    db.expire_all()
    pac = Mock()
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", pac)
    with pytest.raises(ValueError): stamp(db)
    db.rollback()
    pac.assert_not_called()


def test_draft_creation_records_only_local_observed_history(db):
    request = api.BillingDraftCreateRequest(customer_name="SYNTHETIC", customer_rfc="AAA010101AAA",
        customer_zip="01000", customer_regimen="601", customer_use_cfdi="G03",
        emitter_rfc="EKU9003173C9", emitter_name="SYNTHETIC", emitter_regimen="601", place_of_issue="01000",
        payment_method="PUE", payment_form="03", items=[api.DraftItemInput(
            description="Synthetic", quantity=1, unit_price=100, sat_product_code="80101500", unit_code="E48")])
    created = api.create_billing_draft(request, CTX, db)
    assert created.ready_to_stamp
    assert db.query(FiscalAct).filter_by(draft_id=created.id, company_id=CTX.tenant_id).count() == 1

# PostgreSQL concurrency checks opt in only to a named, disposable loopback DB.
# Never accept DATABASE_URL (the application may use a production URL there).
@pytest.fixture
def pg_engine():
    import os
    import uuid
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    value = os.getenv("BILLING_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Requires disposable loopback PostgreSQL")
    url = make_url(value)
    if (url.drivername != "postgresql+psycopg" or url.host not in ("127.0.0.1", "localhost", "::1")
            or url.database != "digestor_fiscal_test"):
        pytest.fail("Fiscal concurrency tests require the named disposable loopback database")
    admin = create_engine(url)
    schema = "fiscal_test_" + uuid.uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine, tables=[
            ProductCatalog.__table__, BillingDraft.__table__, BillingDraftItem.__table__,
            CSF.__table__, Sucursal.__table__, Usuario.__table__, EventoFacturacion.__table__, FiscalAct.__table__,
        ])
        with sessionmaker(bind=engine)() as db:
            seed(db)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_two_postgres_connections_only_one_pac_call(pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock, local
    from sqlalchemy import text
    reads = local()
    barrier = Barrier(2)
    pids = set()
    lock = Lock()
    original_get = api._get_draft_or_404

    def simultaneous_read(db, company, draft_id):
        draft = original_get(db, company, draft_id)
        if not getattr(reads, "seen", False):
            reads.seen = True
            with lock:
                pids.add(db.scalar(text("SELECT pg_backend_pid()")))
            barrier.wait(timeout=15)
        return draft

    monkeypatch.setattr(api, "_get_draft_or_404", simultaneous_read)
    pac = Mock(return_value={"ok": True, "status_code": 200, "provider_response": {"Codigo": 0, "Xml": STAMPED_XML}})
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", pac)

    def attempt():
        with sessionmaker(bind=pg_engine)() as db:
            try:
                return 200 if stamp(db).ok else 400
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(outcomes) == [200, 409]
    assert len(pids) == 2
    assert pac.call_count == 1
    monkeypatch.setattr(api, "_get_draft_or_404", original_get)
    with sessionmaker(bind=pg_engine)() as db:
        assert result(db)["stamp_status"] == "stamped"
        assert db.query(EventoFacturacion).count() == 1


def test_postgres_patch_during_provider_call_cannot_change_invoice(pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()

    def provider(**kwargs):
        entered.set()
        assert release.wait(timeout=15)
        return {"ok": True, "status_code": 200, "provider_response": {"Codigo": 0, "Xml": STAMPED_XML}}

    pac = Mock(side_effect=provider)
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", pac)

    def attempt():
        with sessionmaker(bind=pg_engine)() as db:
            return stamp(db)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(attempt)
        try:
            assert entered.wait(timeout=15)
            with sessionmaker(bind=pg_engine)() as db:
                with pytest.raises(HTTPException) as exc:
                    api.update_billing_draft("synthetic-draft", api.BillingDraftUpdateRequest(customer_name="UNSAFE"), None, CTX, db)
                assert exc.value.status_code == 409
        finally:
            release.set()
        assert future.result(timeout=15).ok
    with sessionmaker(bind=pg_engine)() as db:
        draft = api._get_draft_or_404(db, CTX.tenant_id, "synthetic-draft")
        assert draft.customer_name == "SYNTHETIC CUSTOMER"
        assert draft.stamp_status == "stamped"
    assert pac.call_count == 1


def test_postgres_edit_lock_serializes_stamp_reservation(pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import event
    locked, release, claiming = Event(), Event(), Event()

    def after_query(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement and not locked.is_set():
            locked.set()
            assert release.wait(timeout=15)

    def before_query(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE billing_draft SET stamp_status="):
            claiming.set()

    event.listen(pg_engine, "after_cursor_execute", after_query)
    event.listen(pg_engine, "before_cursor_execute", before_query)
    sent = []

    def provider(**kwargs):
        sent.append(base64.b64decode(kwargs["xml_base64"]).decode())
        return {"ok": True, "status_code": 200, "provider_response": {"Codigo": 0, "Xml": STAMPED_XML}}

    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", Mock(side_effect=provider))

    def edit():
        with sessionmaker(bind=pg_engine)() as db:
            return api.update_billing_draft("synthetic-draft", api.BillingDraftUpdateRequest(customer_name="CORRECTED CUSTOMER"), None, CTX, db)

    def attempt():
        with sessionmaker(bind=pg_engine)() as db:
            return stamp(db)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            patch = pool.submit(edit)
            stamping = None
            try:
                assert locked.wait(timeout=15)
                stamping = pool.submit(attempt)
                assert claiming.wait(timeout=15)
                assert sent == []
            finally:
                release.set()
            patch.result(timeout=15)
            assert stamping.result(timeout=15).ok
        assert len(sent) == 1
        assert 'Nombre="CORRECTED CUSTOMER"' in sent[0]
    finally:
        event.remove(pg_engine, "after_cursor_execute", after_query)
        event.remove(pg_engine, "before_cursor_execute", before_query)


def test_stamp_result_http_auth_scope_and_contract(db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.auth import get_security_context
    from app.db.session import get_db
    draft = seed(db, status="stamped", stamp_status="stamped",
                 stamped_xml_base64=base64.b64encode(STAMPED_XML.encode()).decode())
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    pac = Mock()
    monkeypatch.setattr(api.timbracfdi_client, "timbra_cfdi", pac)
    path = "/v1/billing/drafts/synthetic-draft/stamp-result"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        app.dependency_overrides[get_security_context] = lambda: SecurityContext(CTX.tenant_id, CTX.user_id, "viewer")
        assert client.get(path).status_code == 403
        app.dependency_overrides[get_security_context] = lambda: SecurityContext("synthetic-b", "other", "operator")
        assert client.get(path).status_code == 404
        app.dependency_overrides[get_security_context] = lambda: CTX
        assert client.get(path + "?company_id=synthetic-b").status_code == 403
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {
            "stamp_status": "stamped", "uuid": "11111111-2222-4333-8444-555555555555",
            "xml_base64": draft.stamped_xml_base64,
        }
        assert client.post(path).status_code == 405
    pac.assert_not_called()


def test_postgres_migration_preserves_draft_and_blocks_rewrites(pg_engine, monkeypatch):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/20261025_01_fiscal_acts.py'
    spec = importlib.util.spec_from_file_location('fiscal_migration_pg', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pg_engine.begin() as conn:
        # Exercise production DDL, including an existing draft and no ledger yet.
        conn.execute(text('DROP TABLE billing_fiscal_acts'))
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        module.upgrade()
        assert conn.scalar(text('SELECT count(*) FROM billing_draft')) == 1
        assert conn.scalar(text('SELECT count(*) FROM billing_fiscal_acts')) == 0
    monkeypatch.setattr(api.timbracfdi_client, 'timbra_cfdi', Mock(return_value={
        'ok': True, 'status_code': 200, 'provider_response': {'Codigo': 0, 'Xml': STAMPED_XML}}))
    with sessionmaker(bind=pg_engine)() as db:
        assert stamp(db).ok
    for statement in (
        "UPDATE billing_fiscal_acts SET payload='{}'",
        'DELETE FROM billing_fiscal_acts',
        'TRUNCATE billing_fiscal_acts',
    ):
        with pytest.raises(DBAPIError, match='append-only'):
            with pg_engine.begin() as conn:
                conn.execute(text(statement))
    with pytest.raises(DBAPIError, match='foreign key'):
        with pg_engine.begin() as conn:
            conn.execute(text("""INSERT INTO billing_fiscal_acts
                (id,company_id,draft_id,sequence,payload,content_hash,recorded_at)
                VALUES ('invalid','other-tenant','synthetic-draft',1,'{}','fake',CURRENT_TIMESTAMP)"""))
    with pg_engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM billing_fiscal_acts')) == 3
