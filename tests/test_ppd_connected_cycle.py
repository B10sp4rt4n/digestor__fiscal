"""Synthetic payment completion, request evidence and two-worker boundaries."""
import base64
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.billing_payment import BillingPayment, PaymentReceipt, PaymentAct
from app.schemas.billing_cancellation import RecoverReceipt
from test_payment_cycle import db, seed, record, stamp, body, accepted, api, svc, CTX, no_network, TABLES, pg_engine


def unknown(db, monkeypatch):
    seed(db); item = record(db)
    sender = Mock(side_effect=TimeoutError('Synthetic response lost'))
    monkeypatch.setattr(api.pac, 'timbra_cfdi', sender)
    with pytest.raises(HTTPException): stamp(db, item['id'])
    receipt = db.query(PaymentReceipt).one()
    result = accepted(xml_base64=base64.b64encode(receipt.request_xml.encode()).decode())
    return item, receipt, result, sender


def test_zero_balance_is_not_fiscal_completion_until_both_receipts_are_issued(db, monkeypatch):
    seed(db); first = record(db, body('58.00')); second = record(db, body('58.00'))
    summary = api.list_payments('credit-invoice', CTX, db)
    assert summary['balance'] == '0.00' and summary['economic_settled']
    assert not summary['fiscal_complete'] and summary['receipts_pending_count'] == 2
    monkeypatch.setattr(api.pac, 'timbra_cfdi', Mock(side_effect=accepted))
    stamp(db, first['id'])
    assert api.list_payments('credit-invoice', CTX, db)['receipts_pending_count'] == 1
    stamp(db, second['id'])
    assert api.list_payments('credit-invoice', CTX, db)['fiscal_complete']
    assert db.query(BillingPayment).count() == 2


@pytest.mark.parametrize('change', ['request_xml', 'ledger', 'historical'])
def test_recovery_requires_the_immutable_original_request_before_querying_provider(db, monkeypatch, change):
    item, receipt, response, sender = unknown(db, monkeypatch)
    if change == 'request_xml': receipt.request_xml = receipt.request_xml.replace('Monto="58.00"', 'Monto="1.00"'); db.commit()
    if change == 'ledger': db.execute(text("UPDATE billing_payment_acts SET payload='{}' WHERE sequence=1")); db.commit(); db.expire_all()
    if change == 'historical': db.execute(text('DELETE FROM billing_payment_acts')); db.commit()
    reader = Mock(return_value=response); monkeypatch.setattr(api.pac, 'retrieve_cfdi', reader)
    uuid = api.pac.extract_stamp_uuid(response['provider_response']['Xml'])
    with pytest.raises(HTTPException) as error:
        api.recover_receipt('credit-invoice', item['id'], RecoverReceipt(receipt_uuid=uuid), CTX, db)
    assert error.value.status_code == 409
    reader.assert_not_called(); assert sender.call_count == 1
    assert api.read_receipt('credit-invoice', item['id'], CTX, db)['receipt_status'] == 'unknown'


def test_postgres_cannot_recover_while_the_original_provider_worker_is_active(pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    Base.metadata.create_all(pg_engine, tables=TABLES)
    with sessionmaker(bind=pg_engine)() as db:
        seed(db); item = record(db)
    entered, release, captured = Event(), Event(), {}
    def provider(**kwargs):
        captured['result'] = accepted(**kwargs); entered.set()
        assert release.wait(timeout=10)
        return captured['result']
    sender = Mock(side_effect=provider); reader = Mock()
    monkeypatch.setattr(api.pac, 'timbra_cfdi', sender); monkeypatch.setattr(api.pac, 'retrieve_cfdi', reader)
    def attempt():
        with sessionmaker(bind=pg_engine)() as db: return stamp(db, item['id'])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(attempt)
        try:
            assert entered.wait(timeout=10)
            uuid = api.pac.extract_stamp_uuid(captured['result']['provider_response']['Xml'])
            with sessionmaker(bind=pg_engine)() as db:
                with pytest.raises(HTTPException) as error:
                    api.recover_receipt('credit-invoice', item['id'], RecoverReceipt(receipt_uuid=uuid), CTX, db)
                assert error.value.status_code == 409
        finally: release.set()
        assert future.result(timeout=10)['ok']
    reader.assert_not_called(); assert sender.call_count == 1


def test_postgres_concurrent_unknown_recovery_extends_history_only_once(pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    Base.metadata.create_all(pg_engine, tables=TABLES)
    with sessionmaker(bind=pg_engine)() as db: item, receipt, response, sender = unknown(db, monkeypatch)
    uuid = api.pac.extract_stamp_uuid(response['provider_response']['Xml'])
    barrier = Barrier(2)
    def retrieve(*args): barrier.wait(timeout=10); return response
    monkeypatch.setattr(api.pac, 'retrieve_cfdi', Mock(side_effect=retrieve))
    def attempt():
        with sessionmaker(bind=pg_engine)() as db:
            return api.recover_receipt('credit-invoice', item['id'], RecoverReceipt(receipt_uuid=uuid), CTX, db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        assert all(future.result(timeout=20)['receipt_status'] == 'stamped' for future in futures)
    with sessionmaker(bind=pg_engine)() as db:
        assert db.query(BillingPayment).filter_by(id=item['id']).count() == 1
        assert db.query(PaymentAct).filter_by(payment_id=item['id']).count() == 5
    assert sender.call_count == 1
