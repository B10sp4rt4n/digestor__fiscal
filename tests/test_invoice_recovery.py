"""Recovery uses original request evidence and read-only PAC calls. Synthetic only."""
import base64
from unittest.mock import Mock
from xml.etree import ElementTree as ET
import pytest
from fastapi import HTTPException
from app.services import invoice_recovery as service
from app.models.evento import EventoFacturacion
from app.models.fiscal_act import FiscalAct
from test_billing_stamp_integration import db, seed, stamp, result, CTX, block_network, api, pg_engine

UUID = '11111111-2222-4333-8444-555555555555'


def uncertain(db, monkeypatch, existing=False):
    if not existing: seed(db)
    captured = {}
    def issue(**kwargs):
        xml = base64.b64decode(kwargs['xml_base64']).decode()
        root = ET.fromstring(xml)
        complement = ET.SubElement(root, '{'+service.CFDI+'}Complemento')
        ET.SubElement(complement, '{'+service.TFD+'}TimbreFiscalDigital', {'UUID': UUID, 'Version': '1.1'})
        captured['xml'] = ET.tostring(root, encoding='unicode')
        raise TimeoutError('Synthetic response lost after provider acceptance')
    sender = Mock(side_effect=issue)
    monkeypatch.setattr(api.timbracfdi_client, 'timbra_cfdi', sender)
    with pytest.raises(HTTPException) as error: stamp(db)
    assert error.value.status_code == 502 and result(db)['stamp_status'] == 'unknown'
    return captured, sender


def returned(xml):
    return {'ok': True, 'status_code': 200, 'provider_response': {'Codigo': 0, 'Xml': xml}}


def test_recovers_exact_uuid_and_xml_once_without_another_stamp(db, monkeypatch):
    captured, sender = uncertain(db, monkeypatch)
    reader = Mock(return_value=returned(captured['xml']))
    monkeypatch.setattr(api.timbracfdi_client, 'retrieve_cfdi', reader)
    service.recover(db, CTX, 'synthetic-draft', UUID)
    stored = result(db)
    assert stored['uuid'] == UUID and base64.b64decode(stored['xml_base64']).decode() == captured['xml']
    service.recover(db, CTX, 'synthetic-draft', UUID)
    assert sender.call_count == reader.call_count == db.query(EventoFacturacion).count() == 1
    rows = db.query(FiscalAct).order_by(FiscalAct.sequence).all()
    service.fiscal_acts.verify(rows)
    assert 'fiscal.stamp_recovered' in rows[-1].payload


@pytest.mark.parametrize('change', ['receiver', 'total', 'concept', 'quantity', 'tax', 'method', 'uuid', 'doctype', 'empty', 'unavailable'])
def test_never_attaches_an_unrelated_or_unavailable_document(db, monkeypatch, change):
    captured, sender = uncertain(db, monkeypatch)
    root = ET.fromstring(captured['xml'])
    if change == 'receiver': root.find('{'+service.CFDI+'}Receptor').set('Rfc', 'BBB010101BBB')
    if change == 'total': root.set('Total', '999.00')
    if change == 'method': root.set('MetodoPago', 'PPD')
    if change in {'concept', 'quantity'}: root.find('{'+service.CFDI+'}Conceptos')[0].set('Descripcion' if change == 'concept' else 'Cantidad', 'changed')
    if change == 'tax': root.find('{'+service.CFDI+'}Impuestos').set('TotalImpuestosTrasladados', '0.00')
    if change == 'uuid': root.find('{'+service.CFDI+'}Complemento')[0].set('UUID', '22222222-2222-4333-8444-555555555555')
    xml = ET.tostring(root, encoding='unicode')
    if change == 'doctype': xml = '<!DOCTYPE x>'+xml
    if change == 'empty': xml = ''
    reader = Mock(side_effect=TimeoutError('Synthetic retrieval outage')) if change == 'unavailable' else Mock(return_value=returned(xml))
    monkeypatch.setattr(api.timbracfdi_client, 'retrieve_cfdi', reader)
    with pytest.raises(HTTPException) as error: service.recover(db, CTX, 'synthetic-draft', UUID)
    assert error.value.status_code == 409
    assert result(db)['stamp_status'] == 'unknown' and db.query(EventoFacturacion).count() == 0
    assert sender.call_count == 1


def test_keeps_processing_and_historical_unknown_requests_protected(db, monkeypatch):
    draft = seed(db, stamp_status='processing')
    reader = Mock(); monkeypatch.setattr(api.timbracfdi_client, 'retrieve_cfdi', reader)
    with pytest.raises(HTTPException): service.recover(db, CTX, draft.id, UUID)
    draft.stamp_status = 'unknown'; db.commit()
    with pytest.raises(HTTPException): service.recover(db, CTX, draft.id, UUID)
    reader.assert_not_called()


def test_tenant_and_changed_draft_cannot_recover(db, monkeypatch):
    from app.core.auth import SecurityContext
    captured, _ = uncertain(db, monkeypatch)
    reader = Mock(return_value=returned(captured['xml'])); monkeypatch.setattr(api.timbracfdi_client, 'retrieve_cfdi', reader)
    with pytest.raises(HTTPException) as error:
        service.recover(db, SecurityContext(tenant_id='other', user_id='other', role='operator'), 'synthetic-draft', UUID)
    assert error.value.status_code == 404
    draft = api._get_draft_or_404(db, CTX.tenant_id, 'synthetic-draft'); draft.customer_rfc = 'BBB010101BBB'; db.commit()
    with pytest.raises(HTTPException): service.recover(db, CTX, 'synthetic-draft', UUID)
    reader.assert_not_called()


def test_postgres_concurrent_recovery_records_one_invoice(pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=pg_engine)
    with Session() as setup: captured, sender = uncertain(setup, monkeypatch, existing=True)
    barrier = Barrier(2)
    def retrieve(*args): barrier.wait(timeout=10); return returned(captured['xml'])
    reader = Mock(side_effect=retrieve); monkeypatch.setattr(api.timbracfdi_client, 'retrieve_cfdi', reader)
    def attempt():
        with Session() as session: service.recover(session, CTX, 'synthetic-draft', UUID)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        for future in futures: future.result(timeout=20)
    with Session() as check:
        assert result(check)['uuid'] == UUID
        assert check.query(EventoFacturacion).count() == 1
        assert len([row for row in check.query(FiscalAct).all() if 'fiscal.stamp_recovered' in row.payload]) == 1
    assert sender.call_count == 1


def test_recovery_audit_failure_rolls_back_xml_and_metric(db, monkeypatch):
    captured, _ = uncertain(db, monkeypatch)
    monkeypatch.setattr(api.timbracfdi_client, 'retrieve_cfdi', Mock(return_value=returned(captured['xml'])))
    monkeypatch.setattr(service.fiscal_acts, 'append', Mock(side_effect=ValueError('Synthetic audit failure')))
    with pytest.raises(ValueError): service.recover(db, CTX, 'synthetic-draft', UUID)
    db.rollback()
    assert result(db)['stamp_status'] == 'unknown'
    assert db.query(EventoFacturacion).count() == 0


def test_corrupt_original_evidence_is_blocked_before_provider_query(db, monkeypatch):
    from sqlalchemy import text
    uncertain(db, monkeypatch)
    db.execute(text("UPDATE billing_fiscal_acts SET payload='{}' WHERE sequence=1")); db.commit(); db.expire_all()
    reader = Mock(); monkeypatch.setattr(api.timbracfdi_client, 'retrieve_cfdi', reader)
    with pytest.raises(HTTPException) as error: service.recover(db, CTX, 'synthetic-draft', UUID)
    assert error.value.status_code == 409
    reader.assert_not_called()


def test_recovery_http_auth_confirmation_and_tenant_contract(db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.auth import get_security_context, SecurityContext
    from app.db.session import get_db
    captured, sender = uncertain(db, monkeypatch)
    reader = Mock(return_value=returned(captured['xml']))
    monkeypatch.setattr(api.timbracfdi_client, 'retrieve_cfdi', reader)
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    path = '/v1/billing/drafts/synthetic-draft/stamp-result/recover'
    body = {'invoice_uuid': UUID, 'confirm': True}
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 401
        app.dependency_overrides[get_security_context] = lambda: SecurityContext(CTX.tenant_id, 'viewer', 'viewer')
        assert client.post(path, json=body).status_code == 403
        app.dependency_overrides[get_security_context] = lambda: SecurityContext('other', 'operator', 'operator')
        assert client.post(path, json=body).status_code == 404
        app.dependency_overrides[get_security_context] = lambda: CTX
        for invalid in [{}, {**body, 'confirm': False}, {**body, 'invoice_uuid': 'inventado'}, {**body, 'company_id': 'other'}]:
            assert client.post(path, json=invalid).status_code == 422
        reader.assert_not_called()
        response = client.post(path, json=body)
        assert response.status_code == 200 and response.json()['uuid'] == UUID
        assert client.post(path, json=body).status_code == 200
    assert sender.call_count == reader.call_count == 1


def test_existing_uuid_on_another_draft_cannot_be_reused_even_with_identical_xml(db, monkeypatch):
    captured, _ = uncertain(db, monkeypatch)
    seed(db, id='other-draft', stamp_status='stamped', status='stamped', stamped_xml_base64=base64.b64encode(captured['xml'].encode()).decode())
    reader = Mock(); monkeypatch.setattr(api.timbracfdi_client, 'retrieve_cfdi', reader)
    with pytest.raises(HTTPException) as error: service.recover(db, CTX, 'synthetic-draft', UUID)
    assert error.value.status_code == 409 and result(db)['stamp_status'] == 'unknown'
    reader.assert_not_called()


def test_generated_folio_binds_identical_drafts_to_distinct_requests(db):
    one = seed(db)
    two = seed(db, id='other-draft')
    assert ET.fromstring(api._build_cfdi_xml(one)).get('Folio') == one.id
    assert ET.fromstring(api._build_cfdi_xml(two)).get('Folio') == two.id
    assert service.business_digest(api._build_cfdi_xml(one)) != service.business_digest(api._build_cfdi_xml(two))
