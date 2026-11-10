"""Read-only SAT observations. Never changes CFDI, payment or cancellation state."""
import json
import re
from datetime import datetime, timezone
from fastapi import HTTPException
from app.models.audit_log import AuditLog
from app.models.billing_cancellation import CancellationAct
from app.services import audit_service, billing_cancellations as cancellations, fiscal_production as production
from app.services.fiscal_acts import digest

SOURCE = 'SAT ConsultaCFDIService'


def normalized(value):
    if not isinstance(value, dict): return None
    # Sandbox PAC replies identify the queried UUID separately; caller checks it.
    if 'provider_response' in value:
        if not value.get('ok') or value.get('status_code') != 200 or str((value.get('provider_response') or {}).get('Codigo')) != '0': return None
        value = (value.get('provider_response') or {}).get('RespuestaSat') or {}
        value = {'Estado': value.get('EstadoSat'), 'CodigoEstatus': value.get('CodigoEstatusSat'),
                 'EsCancelable': value.get('EsCancelableSat'), 'EstatusCancelacion': value.get('EstatusCancelacionSat')}
    code = value.get('CodigoEstatus') or ''
    if not isinstance(code,str): return None
    state = value.get('Estado')
    if re.match(r'^N\s*-?\s*602\b', code): state = 'No localizado'
    elif not (code.startswith('S -') and state in ('Vigente', 'Cancelado')): return None
    return {key: value[key][:500] if isinstance(value.get(key),str) else None
            for key in ('CodigoEstatus', 'EsCancelable', 'EstatusCancelacion')} | {'Estado': state}


def document(db, draft, payment_id, expected_uuid):
    # Validates tenant, original identity, document kind, and effective revision evidence.
    active_root, active_uuid, active_hash = cancellations.target(db, draft, payment_id)
    if active_uuid == expected_uuid: return active_root, active_uuid, active_hash
    encoded = draft.stamped_xml_base64
    if payment_id:
        from app.models.billing_payment import PaymentReceipt
        receipt = db.query(PaymentReceipt).filter_by(company_id=draft.company_id, payment_id=payment_id).first()
        encoded = receipt.xml_base64 if receipt else None
    root, folio, content_hash = cancellations.parse_document(encoded)
    if folio != expected_uuid: raise HTTPException(409, 'El UUID seleccionado cambió; recarga el expediente')
    return root, folio, content_hash


def preserved(db, draft, payment_id, folio, content_hash, ctx):
    result = []
    row = cancellations.find(db, draft, payment_id, document_uuid=folio)
    if row:
        if row.document_hash != content_hash: raise HTTPException(409, 'Cambió la evidencia del comprobante')
        acts = db.query(CancellationAct).filter_by(company_id=draft.company_id, cancellation_id=row.id).order_by(CancellationAct.sequence).all()
        for act in acts:
            if digest(act.payload) != act.content_hash: raise HTTPException(409, 'Evidencia SAT histórica inconsistente')
            payload = json.loads(act.payload)
            if payload.get('action') != 'status_consulted': continue
            raw = payload.get('evidence', {}).get('status_response')
            if isinstance(raw, dict) and 'provider_response' in raw:
                if str((raw.get('provider_response') or {}).get('RespuestaSat', {}).get('UUID', '')).lower() != folio: continue
            value = normalized(raw)
            if value: result.append({'id':act.id, 'checked_at':payload.get('recorded_at'), 'outcome':'answered',
                'response':value, 'source': SOURCE if 'Estado' in raw else 'SAT reportado por PAC de pruebas', 'origin':'cancellation_history'})
    if not payment_id:
        legacy = production.legacy(db, draft, folio, ctx)
        if legacy and normalized(legacy.get('sat_response')):
            from app.services import production_cancellation as live
            from sqlalchemy import select
            item = db.execute(select(live.intents).where(live.intents.c.company_id==draft.company_id, live.intents.c.uuid==folio)).mappings().first()
            acts, _ = live.history(db, item)
            dated = [json.loads(a['payload']) for a in acts if json.loads(a['payload']).get('action') in ('reserved','sat_consulted')]
            result.append({'id':'legacy:'+folio, 'checked_at':dated[-1]['recorded_at'] if dated else None,
                'outcome':'answered','response':normalized(legacy['sat_response']), 'source':SOURCE,'origin':'cancellation_history'})
    return result


def read(db, draft, payment_id, folio, content_hash, ctx):
    query = db.query(AuditLog).filter_by(company_id=draft.company_id, action='sat_status_consulted',
        entity_type='sat_document', entity_id=draft.id+':'+folio).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    rows = query.limit(20).all()
    last_success = query.filter_by(status='success').first()
    if last_success and all(row.id != last_success.id for row in rows): rows.append(last_success)
    observations = []
    for row in rows:
        if audit_service._compute_content_hash(row.action,row.entity_type,row.entity_id,row.details) != row.content_hash:
            raise HTTPException(409, 'Evidencia de consulta SAT inconsistente')
        data=json.loads(row.details)
        if data.get('document_hash') != content_hash: raise HTTPException(409, 'Cambió el XML consultado')
        observations.append({'id':row.id, **{key:data.get(key) for key in ('checked_at','source','outcome','response','error')}, 'origin':'status_query'})
    observations += preserved(db,draft,payment_id,folio,content_hash,ctx)
    observations.sort(key=lambda row: row.get('checked_at') or '',reverse=True)
    return {'document_uuid':folio,'history':observations[:20],'latest_attempt':observations[0] if observations else None,
        'last_answer':next((row for row in observations if row['outcome']=='answered'),None)}


def consult(db, draft, payment_id, root, folio, content_hash, ctx):
    previous=read(db,draft,payment_id,folio,content_hash,ctx)
    latest=previous['latest_attempt']
    if latest and latest.get('checked_at'):
        at=datetime.fromisoformat(latest['checked_at'].replace('Z','+00:00'))
        if 0 <= (datetime.now(timezone.utc)-at).total_seconds() < 15: return {**previous,'cached':True}
    db.commit()  # No database locks across the SAT request.
    try:
        value=normalized(production.live.sat_status(root,folio,allow_not_found=True))
        if value is None: raise ValueError('Unsupported SAT response')
        outcome,error='answered',None
    except Exception:
        value,outcome,error=None,'query_failed','No fue posible confirmar la consulta SAT. El último resultado conservado no cambia.'
    # Re-authorize the same document after the external read, before recording evidence.
    from app.api.routers.payments_v1 import get_draft
    draft=get_draft(db,draft.id,ctx)
    if document(db,draft,payment_id,folio)[2]!=content_hash: raise HTTPException(409,'Cambió el XML durante la consulta')
    audit_service.create_audit_log(db,company_id=draft.company_id,user_id=ctx.user_id,
        action='sat_status_consulted',entity_type='sat_document',entity_id=draft.id+':'+folio,
        details={'document_hash':content_hash,'payment_id':payment_id,'document_uuid':folio,
            'checked_at':datetime.now(timezone.utc).isoformat(),'source':SOURCE,'outcome':outcome,'response':value,'error':error},
        status='success' if outcome=='answered' else 'error')
    return {**read(db,draft,payment_id,folio,content_hash,ctx),'cached':False}
