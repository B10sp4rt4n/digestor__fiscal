"""Folios internos: sólo datos sintéticos y PAC simulado."""
import base64
import importlib.util
from pathlib import Path
from unittest.mock import Mock
from xml.etree import ElementTree as ET
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.api.routers import invoice_numbering_v1 as router
from app.core.auth import SecurityContext, get_security_context
from app.db.session import get_db
from app.models.invoice_numbering import InvoiceNumbering, InvoiceFolioReservation, InvoiceNumberingEvent
from app.schemas.invoice_numbering import InvoiceNumberingInput
from app.services import invoice_numbering as service
from test_billing_stamp_integration import db, pg_engine, seed, stamp, CTX, api, block_network

ADMIN = SecurityContext(CTX.tenant_id, 'synthetic-owner', 'admin')


def install(engine):
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/20261111_01_invoice_numbering.py'
    spec = importlib.util.spec_from_file_location('numbering_migration_test', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection)); module.upgrade(); module.upgrade()


@pytest.fixture
def numbering(db, monkeypatch):
    install(db.get_bind())
    monkeypatch.setattr(api.settings, 'FISCAL_NUMBERING_ENABLED', True)
    monkeypatch.setattr(service, 'current_year', lambda: 2026)
    return db


def configure(db, ctx=ADMIN, **changes):
    body = dict(prefix='FAC', include_year=True, reset_annually=False, digits=6, next_number=125, expected_revision=0)
    body.update(changes)
    row = service.save_policy(db, ctx, InvoiceNumberingInput(**body)); db.commit()
    return row


def managed(db):
    return seed(db, numbering_managed=True, folio='synthetic-draft')


def issue(db, draft):
    service.reserve(db, draft); db.commit(); return draft.series, draft.folio


def test_preview_does_not_consume_numbers_and_save_is_audited(numbering):
    policy = configure(numbering, declared_actor='clerk:synthetic-owner')
    for _ in range(2):
        assert service.policy_view(policy)['examples'] == ['FAC-2026-000125', 'FAC-2026-000126', 'FAC-2026-000127']
    assert numbering.query(InvoiceFolioReservation).count() == 0
    event = numbering.query(InvoiceNumberingEvent).one()
    assert event.authenticated_actor == ADMIN.user_id and event.declared_actor == 'clerk:synthetic-owner'
    assert '"before": null' in event.details


@pytest.mark.parametrize('changes', [dict(reset_annually=True, include_year=False), dict(prefix='A|B'),
    dict(prefix='A'*21, include_year=True), dict(digits=0), dict(next_number=0), dict(next_number=1.5), dict(company_id='other')])
def test_schema_rejects_ambiguous_or_invalid_formats(changes):
    with pytest.raises(ValueError):
        InvoiceNumberingInput(**(dict(prefix='FAC', include_year=True, reset_annually=False, digits=6,
            next_number=1, expected_revision=0) | changes))


def test_http_roles_tenant_binding_and_disabled_capability(numbering, monkeypatch):
    app = FastAPI(); app.include_router(router.router); app.dependency_overrides[get_db] = lambda: numbering
    body = dict(prefix='FAC', include_year=True, reset_annually=False, digits=6, next_number=1, expected_revision=0)
    with TestClient(app) as client:
        assert client.get('/v1/billing/invoice-numbering').status_code == 401
        for role in ('viewer', 'operator'):
            app.dependency_overrides[get_security_context] = lambda: SecurityContext(CTX.tenant_id, 'user', role)
            assert client.put('/v1/billing/invoice-numbering', json=body).status_code == 403
        app.dependency_overrides[get_security_context] = lambda: ADMIN
        assert client.put('/v1/billing/invoice-numbering', json=body | {'company_id': 'other'}).status_code == 422
        assert client.put('/v1/billing/invoice-numbering', json=body).status_code == 200
        app.dependency_overrides[get_security_context] = lambda: SecurityContext('other', 'other-owner', 'admin')
        assert client.get('/v1/billing/invoice-numbering?company_id=' + CTX.tenant_id).json()['policy'] is None
        monkeypatch.setattr(api.settings, 'FISCAL_NUMBERING_ENABLED', False)
        assert client.get('/v1/billing/invoice-numbering').json()['available'] is False
        assert client.put('/v1/billing/invoice-numbering', json=body).status_code == 503


def test_retries_reuse_reservation_and_simple_folio_omits_series(numbering, monkeypatch):
    configure(numbering, prefix='', include_year=False)
    draft = managed(numbering)
    captured = []
    def provider(**kwargs):
        root = ET.fromstring(base64.b64decode(kwargs['xml_base64']))
        captured.append(root.attrib)
        if len(captured) == 1:
            return {'ok': False, 'status_code': 200, 'provider_response': {'Codigo': 301, 'Mensaje': 'Synthetic rejection'}}
        complement = ET.SubElement(root, '{http://www.sat.gob.mx/cfd/4}Complemento')
        ET.SubElement(complement, '{http://www.sat.gob.mx/TimbreFiscalDigital}TimbreFiscalDigital',
            {'UUID': '11111111-2222-4333-8444-555555555555'})
        return {'ok': True, 'status_code': 200, 'provider_response': {'Codigo': 0, 'Xml': ET.tostring(root, encoding='unicode')}}
    monkeypatch.setattr(api.timbracfdi_client, 'timbra_cfdi', Mock(side_effect=provider))
    assert stamp(numbering).ok is False
    assert stamp(numbering).ok is True
    assert len(captured) == 2 and all(row['Folio'] == '000125' and 'Serie' not in row for row in captured)
    assert numbering.query(InvoiceFolioReservation).count() == 1
    assert service.get_policy(numbering, ADMIN.tenant_id).next_number == 126
    assert api._serialize_draft(draft).display_folio == '000125'


def test_timeout_retains_reserved_number_and_blocks_second_send(numbering, monkeypatch):
    configure(numbering); managed(numbering)
    pac = Mock(side_effect=TimeoutError('synthetic'))
    monkeypatch.setattr(api.timbracfdi_client, 'timbra_cfdi', pac)
    with pytest.raises(HTTPException): stamp(numbering)
    with pytest.raises(HTTPException): stamp(numbering)
    assert numbering.query(InvoiceFolioReservation).one().folio == '000125'
    assert pac.call_count == 1

def test_release_entrypoint_uses_direct_neon_host_without_changing_application_engine():
    from sqlalchemy import create_engine
    from scripts.install_fiscal_cycle import migration_engine
    application = create_engine('postgresql+psycopg://synthetic:synthetic@ep-example-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require')
    direct = migration_engine(application)
    assert direct.url.host == 'ep-example.us-east-1.aws.neon.tech'
    assert application.url.host == 'ep-example-pooler.us-east-1.aws.neon.tech'
    assert direct.url.username == application.url.username and direct.url.password == application.url.password
    assert direct.url.database == application.url.database and direct.url.query['sslmode'] == 'require'
    direct.dispose(); application.dispose()

def test_postgres_release_installer_migrates_legacy_without_renumbering(pg_engine):
    from sqlalchemy import text
    from scripts.install_fiscal_cycle import install as release_install
    with pg_engine.begin() as connection:
        before = connection.execute(text('SELECT id,series,folio,stamp_status FROM billing_draft')).all()
        connection.execute(text('ALTER TABLE billing_draft DROP COLUMN numbering_managed'))
    release_install(pg_engine)
    release_install(pg_engine)
    with pg_engine.connect() as connection:
        assert connection.execute(text('SELECT id,series,folio,stamp_status FROM billing_draft')).all() == before
        assert connection.scalar(text('SELECT numbering_managed FROM billing_draft')) is False
        assert connection.scalar(text('SELECT count(*) FROM invoice_folio_reservations')) == 0
        assert connection.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname IN ('invoice_folio_reservations_append_only','invoice_numbering_events_append_only') AND tgrelid IN ('invoice_folio_reservations'::regclass,'invoice_numbering_events'::regclass)")) == 2


def test_reserved_number_cannot_be_rewritten_and_configuration_cannot_go_back(numbering):
    configure(numbering); draft = managed(numbering); issue(numbering, draft)
    with pytest.raises(HTTPException):
        api.update_billing_draft(draft.id, api.BillingDraftUpdateRequest(folio='1'), None, CTX, numbering)
    numbering.rollback()
    with pytest.raises(HTTPException): configure(numbering, expected_revision=2, digits=1, next_number=125)
    numbering.rollback()
    assert service.get_policy(numbering, ADMIN.tenant_id).next_number == 126
    assert issue(numbering, draft) == ('FAC-2026', '000125')


@pytest.mark.parametrize('annual, expected', [(True, '000001'), (False, '000126')])
def test_year_transition_uses_distinct_series(numbering, monkeypatch, annual, expected):
    configure(numbering, reset_annually=annual); draft = managed(numbering); issue(numbering, draft)
    monkeypatch.setattr(service, 'current_year', lambda: 2027)
    second = seed(numbering, id='new-year', numbering_managed=True, folio='new-year')
    assert issue(numbering, second) == ('FAC-2027', expected)
    assert issue(numbering, draft) == ('FAC-2026', '000125')


def test_existing_documents_are_preserved_and_numeric_duplicates_are_rejected(numbering):
    legacy = seed(numbering, series='FAC-2026', folio='000125')
    with pytest.raises(HTTPException): configure(numbering, next_number=125)
    numbering.rollback()
    configure(numbering, next_number=126)
    assert not legacy.numbering_managed and legacy.folio == '000125'
    assert service.reserve(numbering, legacy) is None


def test_managed_prefactura_has_no_definitive_folio_until_stamp(numbering):
    configure(numbering)
    response = api.create_billing_draft(api.BillingDraftCreateRequest(), CTX, numbering)
    assert response.numbering_managed and response.display_folio == 'Por asignar al timbrar'
    assert numbering.query(InvoiceFolioReservation).count() == 0
    with pytest.raises(HTTPException):
        api.create_billing_draft(api.BillingDraftCreateRequest(series='MANUAL'), CTX, numbering)


def test_postgres_concurrent_reservations_are_unique_and_evidence_is_immutable(pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    monkeypatch.setattr(service, 'current_year', lambda: 2026)
    install(pg_engine)
    Session = sessionmaker(bind=pg_engine)
    with Session() as setup:
        configure(setup)
        draft = api._get_draft_or_404(setup, CTX.tenant_id, 'synthetic-draft')
        draft.numbering_managed = True; draft.folio = draft.id
        seed(setup, id='second', numbering_managed=True, folio='second')
    barrier = Barrier(2)
    def attempt(draft_id):
        with Session() as session:
            barrier.wait(timeout=10)
            draft = session.query(api.BillingDraft).filter_by(company_id=CTX.tenant_id, id=draft_id).with_for_update().one()
            return issue(session, draft)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ['synthetic-draft', 'second'])) == [('FAC-2026', '000125'), ('FAC-2026', '000126')]
    with Session() as session:
        assert service.get_policy(session, CTX.tenant_id).next_number == 127
    for table in ('invoice_folio_reservations', 'invoice_numbering_events'):
        for statement in (f'UPDATE {table} SET company_id=company_id', f'DELETE FROM {table}', f'TRUNCATE {table}'):
            with pytest.raises(DBAPIError, match='append-only'):
                with pg_engine.begin() as connection: connection.execute(text(statement))

def test_reservation_rolls_back_with_fiscal_state_before_any_provider_call(numbering, monkeypatch):
    configure(numbering); managed(numbering)
    pac = Mock(); monkeypatch.setattr(api.timbracfdi_client, 'timbra_cfdi', pac)
    monkeypatch.setattr(api.fiscal_acts, 'append', Mock(side_effect=ValueError('synthetic audit failure')))
    with pytest.raises(ValueError): stamp(numbering)
    numbering.rollback()
    draft = api._get_draft_or_404(numbering, CTX.tenant_id, 'synthetic-draft')
    assert draft.stamp_status == 'not_sent' and draft.folio == draft.id
    assert numbering.query(InvoiceFolioReservation).count() == 0
    assert service.get_policy(numbering, CTX.tenant_id).next_number == 125
    pac.assert_not_called()


def test_same_format_is_isolated_per_company_and_stale_admin_save_fails(numbering):
    configure(numbering)
    with pytest.raises(HTTPException): configure(numbering)
    numbering.rollback()
    first = managed(numbering)
    assert issue(numbering, first) == ('FAC-2026', '000125')
    other = SecurityContext('synthetic-b', 'other-owner', 'admin')
    configure(numbering, ctx=other)
    second = api.BillingDraft(id='other-draft', company_id=other.tenant_id, numbering_managed=True, folio='other-draft')
    numbering.add(second); numbering.commit()
    assert issue(numbering, second) == ('FAC-2026', '000125')
    assert numbering.query(InvoiceFolioReservation).count() == 2


def test_postgres_initial_admin_saves_conflict_instead_of_overwriting(pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    monkeypatch.setattr(service, 'current_year', lambda: 2026)
    install(pg_engine)
    barrier = Barrier(2)
    def attempt():
        with sessionmaker(bind=pg_engine)() as session:
            barrier.wait(timeout=10)
            try:
                configure(session); return 200
            except HTTPException as failure:
                session.rollback(); return failure.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == [200, 409]


def test_postgres_migration_adds_marker_without_renumbering_legacy(pg_engine):
    from sqlalchemy import text
    with pg_engine.begin() as connection:
        before = connection.execute(text('SELECT id,series,folio,stamp_status FROM billing_draft')).all()
        connection.execute(text('ALTER TABLE billing_draft DROP COLUMN numbering_managed'))
    install(pg_engine)
    with pg_engine.connect() as connection:
        assert connection.execute(text('SELECT id,series,folio,stamp_status FROM billing_draft')).all() == before
        assert connection.scalar(text('SELECT numbering_managed FROM billing_draft')) is False

@pytest.mark.parametrize('attribute', ['Serie', 'Folio'])
def test_provider_cannot_confirm_a_different_reserved_folio(numbering, monkeypatch, attribute):
    configure(numbering); managed(numbering)
    def provider(**kwargs):
        root = ET.fromstring(base64.b64decode(kwargs['xml_base64']))
        root.set(attribute, 'DIFFERENT')
        complement = ET.SubElement(root, '{http://www.sat.gob.mx/cfd/4}Complemento')
        ET.SubElement(complement, '{http://www.sat.gob.mx/TimbreFiscalDigital}TimbreFiscalDigital',
            {'UUID': '11111111-2222-4333-8444-555555555555'})
        return {'ok': True, 'status_code': 200, 'provider_response': {'Codigo': 0, 'Xml': ET.tostring(root, encoding='unicode')}}
    pac = Mock(side_effect=provider)
    monkeypatch.setattr(api.timbracfdi_client, 'timbra_cfdi', pac)
    with pytest.raises(HTTPException): stamp(numbering)
    with pytest.raises(HTTPException): stamp(numbering)
    draft = api._get_draft_or_404(numbering, CTX.tenant_id, 'synthetic-draft')
    assert draft.stamp_status == 'unknown' and draft.stamped_xml_base64 is None
    assert numbering.query(InvoiceFolioReservation).one().folio == '000125'
    assert pac.call_count == 1
