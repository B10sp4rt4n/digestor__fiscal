"""Verify a local public-sandbox checkpoint without any external network calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    args=parser.parse_args();folder=args.evidence_dir.resolve()
    report=json.loads((folder/'report.json').read_text())
    if (report.get('mode')!='public-sandbox-REP-substitution' or report.get('production_changed') is not False
        or report.get('result')!='stopped' or len(report.get('requests',[]))!=1
        or report['requests'][0]!=dict(operation='TimbraCFDI',outcome='unknown')):
        parser.error('Exact unknown public-sandbox checkpoint required')
    os.environ.update(DATABASE_URL='sqlite:///:memory:',DB_URL='sqlite:///:memory:',TIMBRACFDI_ENV='sandbox',
        TIMBRACFDI_USE_SOAP='false',TIMBRACFDI_SANDBOX_BASE_URL='https://pruebas.timbracfdi33.mx:1444/api/v2',
        TIMBRACFDI_TOKEN='',TIMBRACFDI_PRODUCTION_TOKEN='',TIMBRACFDI_SANDBOX_TOKEN='',
        FISCAL_PPD_ENABLED='true',FISCAL_REP_STAMP_ENABLED='true',FISCAL_CORRECTIONS_ENABLED='true',FISCAL_CANCELLATION_ENABLED='true')
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import requests
    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.core.auth import SecurityContext
    from app.api.routers import fiscal_revisions_v1 as api,payments_v1 as payments
    from app.models.fiscal_revision import FiscalRevision
    from app.models.billing_payment import BillingPayment,PaymentReceipt
    from app.models import csf,evento,usuario,sucursal
    from app.services import fiscal_revisions
    calls=[]
    def forbidden(*a,**kw):
        calls.append('unexpected network');raise AssertionError('External network forbidden')
    original=requests.sessions.Session.request;requests.sessions.Session.request=forbidden
    result=dict(mode='verification-only-no-network',live_result='blocked_provider_UUID_required',checks={},network_calls=0)
    engine=create_engine('sqlite:///'+str(folder/'pilot.sqlite'))
    try:
        with Session(engine) as db:
            ctx=SecurityContext('public-rep-recovery-qa','local-pilot','operator')
            row=db.get(FiscalRevision,report['revision_id'])
            assert row.company_id==ctx.tenant_id and row.status=='unknown' and not row.result_uuid and not row.result_xml
            status={}
            for operation in ['stamp','abandon','cancel-original']:
                try:api.action(report['draft_id'],operation,None,ctx,db,report['payment_id'])
                except HTTPException as exc:status[operation]=exc.status_code
            assert status==dict(stamp=409,abandon=409,**{'cancel-original':409}) and not calls
            money=payments.list_payments(report['draft_id'],ctx,db)
            assert len(money['payments'])==1 and money['balance']=='0.00' and money['paid']=='116.00' and not money['fiscal_complete']
            active=api.action(report['draft_id'],'read',None,ctx,db,report['payment_id'])
            assert active['active_uuid']==report['source_receipt_uuid'] and active['pending']
            assert db.query(BillingPayment).count()==1
            receipt=db.query(PaymentReceipt).filter_by(payment_id=report['payment_id']).one()
            assert receipt.uuid==report['source_receipt_uuid'] and receipt.status=='stamped'
            fiscal_revisions.verify(db,row)
            result.update(passed=True,revision_id=row.id,source_uuid=receipt.uuid,status='unknown',
                checks=dict(operations_blocked=status,payment_count=1,paid='116.00',balance='0.00',
                    original_still_active=True,replacement_not_falsely_activated=True,revision_chain_valid=True),
                cause='No PAC response or confirmed replacement UUID was captured. Acceptance is unknown; never resend.',
                transport_diagnostic='Initial harness did not retain the exception detail; no HTTP status is asserted.')
    finally:
        requests.sessions.Session.request=original;engine.dispose()
    (folder/'uncertain-verification.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=='__main__':main()
