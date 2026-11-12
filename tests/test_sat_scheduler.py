"""Synthetic schedules: no SAT/PAC calls, money changes or production database."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import UUID
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker
from app.core.auth import SecurityContext
from app.models.sat_tracking_job import SatTrackingJob
from app.models.audit_log import AuditLog
from app.models.billing_payment import BillingPayment
from app.services import sat_scheduler as scheduler, sat_tracking, audit_service
from app.api.routers import sat_tracking_v1 as api
from test_sat_tracking import db, prepared, no_network, CTX, FOLIO, answer
from test_billing_stamp_integration import pg_engine


@pytest.fixture
def enrolled(db, prepared, monkeypatch):
    SatTrackingJob.__table__.create(db.bind)
    for name, value in {'SAT_TRACKING_AUTOPOLL':True,'SAT_TRACKING_TENANTS':[CTX.tenant_id],
        'FISCAL_MAINTENANCE_MODE':False, 'AUTH_CONNECTOR':'jwt','AUTH_ALLOW_ANONYMOUS':False}.items():
        monkeypatch.setattr(scheduler.settings, name, value)
    moment = [datetime(2026, 11, 10, 18, tzinfo=timezone.utc)]
    monkeypatch.setattr(scheduler, 'now', lambda:moment[0].replace(tzinfo=None))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None): return moment[0]
    monkeypatch.setattr(sat_tracking, 'datetime', Clock)
    return prepared, moment


def enable(db, draft, enabled=True, ctx=CTX):
    return api.schedule(draft.id,None,UUID(FOLIO),api.ScheduleRequest(enabled=enabled),ctx,db)


def run(db):
    return scheduler.run_batch(sessionmaker(bind=db.bind))


def test_enrollment_is_explicit_idempotent_and_does_not_query(db,enrolled,monkeypatch):
    draft,_=enrolled
    query=Mock(side_effect=AssertionError('No network during enrollment'));monkeypatch.setattr(sat_tracking.production.live,'sat_status',query)
    assert run(db)==0
    assert enable(db,draft)['schedule']['enabled']
    assert enable(db,draft)['schedule']['enabled']
    assert db.query(SatTrackingJob).count()==1
    assert db.query(AuditLog).filter_by(action='sat_schedule_changed').count()==1
    assert not enable(db,draft,False)['schedule']['enabled']
    assert run(db)==0
    query.assert_not_called()


@pytest.mark.parametrize('case',['disabled','tenant','sandbox','maintenance','anonymous','header','emitter','viewer'])
def test_enrollment_boundaries_before_network(db,enrolled,monkeypatch,case):
    draft,_=enrolled;ctx=CTX
    changes={'disabled':('SAT_TRACKING_AUTOPOLL',False),'tenant':('SAT_TRACKING_TENANTS',[]),
        'sandbox':('TIMBRACFDI_ENV','sandbox'),'maintenance':('FISCAL_MAINTENANCE_MODE',True),
        'anonymous':('AUTH_ALLOW_ANONYMOUS',True),'header':('AUTH_CONNECTOR','header'),
        'emitter':('FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC','OTHER')}
    if case=='viewer':ctx=SecurityContext(CTX.tenant_id,'viewer','viewer')
    else:monkeypatch.setattr(scheduler.settings,*changes[case])
    with pytest.raises(HTTPException):enable(db,draft,ctx=ctx)
    assert db.query(SatTrackingJob).count()==0


def test_worker_preserves_document_and_records_source_trigger_and_next_due(db,enrolled,monkeypatch):
    draft,moment=enrolled;enable(db,draft)
    original=(draft.stamp_status,draft.stamped_xml_base64,draft.total)
    query=Mock(return_value=answer('Cancelado'));monkeypatch.setattr(sat_tracking.production.live,'sat_status',query)
    assert run(db)==1 and run(db)==0
    result=api.read(draft.id,None,UUID(FOLIO),CTX,db)
    assert result['last_answer']['response']['Estado']=='Cancelado'
    assert result['last_answer']['trigger']=='scheduled'
    assert result['last_answer']['source']==sat_tracking.SOURCE
    assert result['schedule']['next_check_at']=='2026-11-11T00:00:00Z'
    db.refresh(draft);assert (draft.stamp_status,draft.stamped_xml_base64,draft.total)==original
    assert db.query(BillingPayment).count()==0
    assert audit_service.verify_audit_chain(db,CTX.tenant_id)==(True,None)
    moment[0]+=timedelta(hours=6)
    assert run(db)==1 and query.call_count==2


def test_failed_query_retries_with_backoff_preserving_last_answer(db,enrolled,monkeypatch):
    draft,moment=enrolled;enable(db,draft)
    query=Mock(return_value=answer());monkeypatch.setattr(sat_tracking.production.live,'sat_status',query)
    run(db);moment[0]+=timedelta(hours=6)
    query.side_effect=TimeoutError('private transport data')
    for delay in [900,1800,3600]:
        assert run(db)==1
        row=db.query(SatTrackingJob).populate_existing().one()
        assert row.next_check_at==scheduler.now()+timedelta(seconds=delay)
        result=api.read(draft.id,None,UUID(FOLIO),CTX,db)
        assert result['last_answer']['response']['Estado']=='Vigente'
        assert result['latest_attempt']['outcome']=='query_failed'
        assert 'private transport data' not in str(result)
        assert run(db)==0
        moment[0]+=timedelta(seconds=delay)


def test_crashed_worker_lease_survives_new_session_then_expires(db,enrolled,monkeypatch):
    draft,moment=enrolled;enable(db,draft)
    claim=scheduler.claim(db,scheduler.now())
    query=Mock(return_value=answer());monkeypatch.setattr(sat_tracking.production.live,'sat_status',query)
    assert claim and run(db)==0
    moment[0]+=timedelta(seconds=scheduler.LEASE_SECONDS+1)
    scheduler.process(db,*claim)
    query.assert_not_called()
    assert run(db)==1 and query.call_count==1
    scheduler.process(db,*claim)  # Old worker must not act after its token was replaced.
    assert query.call_count==1


def test_pause_during_sat_read_cannot_be_undone_by_old_worker(db,enrolled,monkeypatch):
    draft,_=enrolled;enable(db,draft)
    def response(*args,**kwargs):
        with sessionmaker(bind=db.bind)() as control:
            enable(control,control.get(type(draft),draft.id),False)
        return answer()
    monkeypatch.setattr(sat_tracking.production.live,'sat_status',Mock(side_effect=response))
    assert run(db)==1
    row=db.query(SatTrackingJob).populate_existing().one()
    assert not row.enabled and row.lease_token is None
    assert api.read(draft.id,None,UUID(FOLIO),CTX,db)['last_answer']['response']['Estado']=='Vigente'


def test_changed_xml_pauses_without_query(db,enrolled,monkeypatch):
    import base64
    draft,_=enrolled;enable(db,draft)
    xml=base64.b64decode(draft.stamped_xml_base64).replace(b'Comprobante ',b'Comprobante LugarExpedicionExtra="synthetic" ',1)
    draft.stamped_xml_base64=base64.b64encode(xml).decode();db.commit()
    query=Mock(side_effect=AssertionError('Changed XML must not be queried'));monkeypatch.setattr(sat_tracking.production.live,'sat_status',query)
    assert run(db)==1
    row=db.query(SatTrackingJob).populate_existing().one()
    assert not row.enabled and row.last_outcome=='document_changed'
    query.assert_not_called()


def test_revoked_tenant_is_skipped_and_can_still_pause(db,enrolled,monkeypatch):
    draft,_=enrolled;enable(db,draft)
    monkeypatch.setattr(scheduler.settings,'SAT_TRACKING_TENANTS',[])
    assert run(db)==0
    view=api.read(draft.id,None,UUID(FOLIO),CTX,db)['schedule']
    assert view['enabled'] and not view['running'] and not view['available']
    assert not enable(db,draft,False)['schedule']['enabled']


def test_foreign_tenant_cannot_read_or_change_schedule(db,enrolled):
    draft,_=enrolled;enable(db,draft)
    other=SecurityContext('another-tenant','actor','superadmin')
    with pytest.raises(HTTPException):enable(db,draft,False,other)
    with pytest.raises(HTTPException):api.read(draft.id,None,UUID(FOLIO),other,db)
    assert db.query(SatTrackingJob).one().enabled


def test_manual_consultation_works_without_new_schema(db,prepared,monkeypatch):
    monkeypatch.setattr(scheduler.settings,'SAT_TRACKING_AUTOPOLL',False)
    monkeypatch.setattr(sat_tracking.production.live,'sat_status',Mock(return_value=answer()))
    assert api.consult(prepared.id,None,UUID(FOLIO),CTX,db)['schedule']['available'] is False


def test_http_schedule_worker_and_history_flow_with_role_and_uuid_guards(db,enrolled,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.auth import get_security_context
    draft,_=enrolled
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[api.get_db]=lambda:db
    path=f'/v1/billing/drafts/{draft.id}/sat-status/schedule?expected_uuid={FOLIO}'
    with TestClient(app) as client:
        assert client.post(path,json={'enabled':True}).status_code==401
        app.dependency_overrides[get_security_context]=lambda:SecurityContext(CTX.tenant_id,'viewer','viewer')
        assert client.post(path,json={'enabled':True}).status_code==403
        app.dependency_overrides[get_security_context]=lambda:CTX
        assert client.post(path,json={'enabled':'true'}).status_code==422
        assert client.post(path,json={'enabled':True,'tenant':'foreign'}).status_code==422
        assert client.post(path,json={'enabled':True}).json()['schedule']['enabled']
        query=Mock(return_value=answer());monkeypatch.setattr(sat_tracking.production.live,'sat_status',query)
        assert run(db)==1
        result=client.get(path.replace('/schedule?','?')).json()
        assert result['last_answer']['trigger']=='scheduled' and result['schedule']['running']
        assert not client.post(path,json={'enabled':False}).json()['schedule']['enabled']
        assert query.call_count==1


def test_receipt_schedule_remains_bound_to_payment_and_preserves_cash(db,enrolled,monkeypatch):
    import base64
    from xml.etree import ElementTree as ET
    from test_payment_cycle import record
    from app.models.billing_payment import PaymentReceipt
    draft,_=enrolled
    monkeypatch.setattr(api.settings,'TIMBRACFDI_ENV','sandbox')
    payment=record(db)
    receipt=db.query(PaymentReceipt).filter_by(payment_id=payment['id']).one()
    root=ET.fromstring(base64.b64decode(draft.stamped_xml_base64));root.set('TipoDeComprobante','P')
    folio='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
    root.find('.//{http://www.sat.gob.mx/TimbreFiscalDigital}TimbreFiscalDigital').set('UUID',folio)
    receipt.status='stamped';receipt.uuid=folio;receipt.xml_base64=base64.b64encode(ET.tostring(root)).decode();db.commit()
    before=db.query(BillingPayment).one().amount
    monkeypatch.setattr(api.settings,'TIMBRACFDI_ENV','production')
    api.schedule(draft.id,payment['id'],UUID(folio),api.ScheduleRequest(enabled=True),CTX,db)
    query=Mock(return_value=answer());monkeypatch.setattr(sat_tracking.production.live,'sat_status',query)
    assert run(db)==1
    assert api.read(draft.id,payment['id'],UUID(folio),CTX,db)['last_answer']['trigger']=='scheduled'
    assert db.query(BillingPayment).one().amount==before
    assert api.read(draft.id,None,UUID(FOLIO),CTX,db)['history']==[]


def test_two_postgres_workers_claim_once(pg_engine,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from test_payment_cycle import TABLES, seed
    from app.db.base import Base
    Base.metadata.create_all(pg_engine,tables=TABLES+[AuditLog.__table__,SatTrackingJob.__table__])
    factory=sessionmaker(bind=pg_engine)
    with factory() as db:
        draft=seed(db)
        for name,value in {'SAT_TRACKING_AUTOPOLL':True,'SAT_TRACKING_TENANTS':[CTX.tenant_id],
            'TIMBRACFDI_ENV':'production','FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC':draft.emitter_rfc,
            'AUTH_CONNECTOR':'jwt','AUTH_ALLOW_ANONYMOUS':False}.items():monkeypatch.setattr(scheduler.settings,name,value)
        enable(db,draft)
    def attempt(_):
        with factory() as db:return scheduler.claim(db,scheduler.now())
    with ThreadPoolExecutor(max_workers=2) as pool:claims=list(pool.map(attempt,range(2)))
    assert sum(value is not None for value in claims)==1
    query=Mock(return_value=answer());monkeypatch.setattr(sat_tracking.production.live,'sat_status',query)
    for value in claims:
        if value:
            with factory() as db:scheduler.process(db,*value)
    assert query.call_count==1


def test_additive_schedule_migration_is_rerunnable(db,enrolled):
    import importlib.util
    from pathlib import Path
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    from sqlalchemy import inspect
    SatTrackingJob.__table__.drop(db.bind)
    path=Path(__file__).resolve().parents[1]/'migrations/versions/20261110_01_sat_tracking.py'
    spec=importlib.util.spec_from_file_location('schedule_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with db.bind.begin() as conn:
        module.op=Operations(MigrationContext.configure(conn));module.upgrade();module.upgrade()
        assert inspect(conn).has_table('sat_tracking_jobs')
    assert db.query(SatTrackingJob).count()==0
    assert db.get(type(enrolled[0]),enrolled[0].id).stamped_xml_base64
