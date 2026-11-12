"""Synthetic contracts only. No application imports, SQL or network."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from app.schemas.sat_metadata import MetadataBatch, prepare_cima_batch


def payload():
    return {
        'batch_id': '11111111-1111-4111-8111-111111111111',
        'scope': {'tenant_id': 'synthetic-tenant', 'empresa_id': 'synthetic-company',
                  'empresa_rfc': 'AAA010101AAA', 'direction': 'emitido',
                  'period_start': '2026-10-01', 'period_end': '2026-10-31'},
        'coverage': 'partial', 'phase': 'validated',
        'observations': [{
            'cfdi_uuid': '22222222-2222-4222-8222-222222222222',
            'emisor_rfc': 'AAA010101AAA', 'receptor_rfc': 'BBB010101BBB',
            'fecha_emision': '2026-10-10T12:00:00',
            'fecha_certificacion': '2026-10-10T12:01:00',
            'rfc_pac': 'CCC010101CCC', 'total': '116.00',
            'tipo_comprobante': 'I', 'estatus_fiscal': 'vigente',
            'observed_at': '2026-11-12T04:00:00Z',
            'raw_ref': 'synthetic/package-1/row-1', 'raw_sha256': 'a' * 64,
        }],
    }


def export(data):
    return prepare_cima_batch(MetadataBatch.model_validate(data),
                             authenticated_tenant='synthetic-tenant',
                             authorized_company=('synthetic-company', 'AAA010101AAA'))


def test_replay_preserves_identity_but_new_status_appends_observation():
    data = payload()
    first = export(data)['records'][0]
    data['batch_id'] = '33333333-3333-4333-8333-333333333333'
    assert export(data)['records'][0] == first
    data['observations'][0]['estatus_fiscal'] = 'cancelado'
    new = export(data)['records'][0]
    assert new['document_identity'] == first['document_identity']
    assert new['idempotency_key'] != first['idempotency_key']


@pytest.mark.parametrize('field,value', [('tenant_id', 'other'), ('empresa_id', 'other'),
                                        ('empresa_rfc', 'BBB010101BBB')])
def test_scope_cannot_be_selected_by_uploaded_document(field, value):
    data = payload()
    data['scope'][field] = value
    with pytest.raises(ValueError):
        export(data)


def test_received_scope_is_explicit_and_same_uuid_is_scoped_by_tenant():
    data = payload()
    data['scope'].update(direction='recibido', empresa_rfc='BBB010101BBB')
    batch = MetadataBatch.model_validate(data)
    out = prepare_cima_batch(batch, authenticated_tenant='synthetic-tenant',
                            authorized_company=('synthetic-company', 'BBB010101BBB'))
    assert out['records'][0]['direccion'] == 'recibido'
    changed = deepcopy(data)
    changed['scope']['tenant_id'] = 'tenant-b'
    other = prepare_cima_batch(MetadataBatch.model_validate(changed), authenticated_tenant='tenant-b',
                              authorized_company=('synthetic-company', 'BBB010101BBB'))
    assert other['records'][0]['document_identity'] != out['records'][0]['document_identity']


@pytest.mark.parametrize('amount', [116.0, 'NaN', 'Infinity', '-1', '1,160.00'])
def test_total_is_lossless_decimal_text(amount):
    data = payload()
    data['observations'][0]['total'] = amount
    with pytest.raises(ValidationError):
        export(data)


def test_metadata_never_invents_currency_tax_cash_or_full_xml_contract():
    out = export(payload())
    row = out['records'][0]
    assert row['total'] == '116.00'
    assert row['enrichment_required'] is True
    assert not {'moneda', 'subtotal', 'iva', 'paid', 'balance', 'xml'} & row.keys()
    assert row['fecha_emision'] == '2026-10-10T12:00:00'
    assert out['coverage'] == 'partial'


@pytest.mark.parametrize('mutation', ['duplicate', 'period', 'reversed', 'wide', 'naive', 'date_conflict', 'extra'])
def test_invalid_evidence_is_blocked(mutation):
    data = payload()
    if mutation == 'duplicate': data['observations'] *= 2
    if mutation == 'period': data['observations'][0]['fecha_emision'] = '2026-09-30T12:00:00'
    if mutation == 'reversed': data['scope']['period_end'] = '2026-09-30'
    if mutation == 'wide': data['scope']['period_end'] = '2026-12-01'
    if mutation == 'naive': data['observations'][0]['observed_at'] = '2026-11-12T04:00:00'
    if mutation == 'date_conflict': data['observations'][0]['fecha_cancelacion'] = '2026-10-11T12:00:00'
    if mutation == 'extra': data['observations'][0]['moneda'] = 'MXN'
    with pytest.raises(ValidationError): export(data)


def test_complete_requires_manifest_and_count_and_failed_batch_cannot_export():
    data = payload()
    data['coverage'] = 'complete'
    with pytest.raises(ValidationError): export(data)
    data.update(manifest_ref='synthetic/manifest', manifest_sha256='b' * 64, expected_count=1)
    assert export(data)['coverage'] == 'complete'
    data['expected_count'] = 2
    with pytest.raises(ValidationError): export(data)
    data.update(coverage='unknown', phase='failed')
    with pytest.raises(ValueError): export(data)
