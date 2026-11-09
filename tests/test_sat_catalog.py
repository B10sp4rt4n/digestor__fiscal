"""Official snapshot and deterministic search; all offline, no PAC traffic."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.services import sat_catalog as sat
from app.api.routers import sat_catalogs_v1 as api, commercial_v1
from app.core.auth import SecurityContext


def test_official_source_and_complete_snapshot():
    meta = sat.metadata()
    assert meta['source'] == 'SAT'
    assert meta['publishedAt'] == '2026-11-06'
    assert meta['counts'] == {'product': 52513, 'unit': 2418}
    assert meta['sourceSha256'] == '6249e37275ce164a1c4ac44e726dcd60858dac5c5604ff6c9ab06005a9199e1a'
    assert meta['sourceUrl'].startswith('http://omawww.sat.gob.mx/')


@pytest.mark.parametrize('code', ['E48','H87','Ohm','Gd','X1w','Xyv','DX'])
def test_exact_unit_keeps_official_xml_case(code):
    result = sat.search('unit', code)['results']
    assert result[0]['code'] == code
    assert result[0]['method'] == 'exact_code'


def test_domain_search_explains_candidates_without_auto_classification():
    response = sat.search('product', 'ThreatDown EDR licencia 12 meses', 'license')
    assert any(row['code'] == '43233205' for row in response['results'])
    assert any(row['domainRelations'] for row in response['results'])
    assert 'selected' not in response
    assert sat.search('unit', 'unidad de servicio')['results'][0]['code'] == 'E48'
    assert len(sat.search('product', '" OR * ( ; DROP TABLE entries; --')['results']) <= 8
    assert sat.lookup('product', '43233205')


def test_validity_and_catalog_change_require_review():
    pair = [{'itemNumber': 1, 'satProductCode': '43233205', 'unitCode': 'E48'}]
    assert sat.validate_pairs(pair)['valid']
    assert not sat.validate_pairs([{**pair[0], 'unitCode': 'ZZZ'}])['valid']
    assert not sat.validate_pairs(pair, '0' * 64)['valid']
    assert not sat.validate_pairs(pair, on=date(2000,1,1))['valid']


def test_corrupt_snapshot_fails_closed(tmp_path, monkeypatch):
    sat.snapshot.cache_clear()
    monkeypatch.setattr(sat, 'DATA', tmp_path)
    (tmp_path / 'sat-search-manifest.json').write_text('{"bundleSha256":"fake"}')
    (tmp_path / 'sat-search.sqlite.gz').write_bytes(b'bad')
    try:
        with pytest.raises(sat.CatalogUnavailable): sat.metadata()
    finally: sat.snapshot.cache_clear()


def test_routes_validate_bounded_queries_and_explicit_pairs():
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[api.read] = lambda: SecurityContext('t','u','viewer')
    with TestClient(app) as client:
        assert client.get('/v1/sat-catalogs/search', params={'kind':'product','q':'43233205'}).json()['results'][0]['code'] == '43233205'
        for params in [{'kind':'bad','q':'a'}, {'kind':'unit','q':'a'*201}, {'kind':'unit','q':'E48','limit':500}]:
            assert client.get('/v1/sat-catalogs/search', params=params).status_code == 422
        assert client.post('/v1/sat-catalogs/validate', json={'items':[]}).status_code == 422
        assert client.post('/v1/sat-catalogs/validate', json={'items':[{'itemNumber':1,'satProductCode':'43233205','unitCode':'Ohm'}]}).json()['valid']


def test_invalid_classification_never_reserves_or_calls_pac(monkeypatch):
    draft = SimpleNamespace(status='ready_to_stamp', stamp_status='not_sent', stamped_at=None, stamped_xml_base64=None,
      items=[SimpleNamespace(sat_product_code='00000000', unit_code='E48')])
    monkeypatch.setattr(commercial_v1, '_get_draft_or_404', lambda *args: draft)
    monkeypatch.setattr(commercial_v1, '_missing_fields', lambda draft: [])
    pac, db = Mock(), Mock()
    monkeypatch.setattr(commercial_v1.timbracfdi_client, 'timbra_cfdi', pac)
    with pytest.raises(HTTPException) as exc:
        commercial_v1.stamp_billing_draft('d', commercial_v1.BillingDraftStampRequest(), None, SecurityContext('t','u','operator'), db)
    assert exc.value.status_code == 422
    pac.assert_not_called(); db.query.assert_not_called()
