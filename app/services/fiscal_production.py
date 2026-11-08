"""Production fiscal gates and SAT reads; no implicit invoice or cash creation."""
import base64
from sqlalchemy import inspect, select
from fastapi import HTTPException
from app.core.config import settings
from app.core.fiscal_sandbox import fiscal_operation_is_production
from app.services import production_cancellation as live


def scope(kind, draft, ctx, *, mutate=False):
    if not fiscal_operation_is_production(kind): return
    if draft.emitter_rfc != settings.FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC:
        raise HTTPException(status_code=403, detail='Emisor fuera del alcance fiscal productivo')
    if mutate and ctx.role not in {'admin','superadmin'}:
        raise HTTPException(status_code=403, detail='La operación fiscal productiva requiere administración')


def legacy(db, draft, folio, ctx):
    # Historical cancellation remains a fact even after the pilot switch is retired.
    if not inspect(db.connection()).has_table(live.intents.name): return None
    item = db.execute(select(live.intents).where(live.intents.c.company_id == draft.company_id,
        live.intents.c.draft_id == draft.id, live.intents.c.uuid == folio)).mappings().first()
    if not item: return None
    try: xml = base64.b64decode(draft.stamped_xml_base64,validate=True).decode()
    except (ValueError,TypeError): raise HTTPException(status_code=409,detail='XML histórico no verificable')
    if live.digest(xml) != item['xml_sha256']:
        raise HTTPException(status_code=409,detail='Cambió la evidencia de la cancelación histórica')
    live.history(db,item)
    return live.view(item)


def sat(root, folio, *, cancelable=False):
    try: value = live.sat_status(root,folio)
    except HTTPException: raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail='SAT sin confirmar; no se envió la operación fiscal') from exc
    if value.get('Estado') != 'Vigente':
        raise HTTPException(status_code=409, detail='El comprobante no está vigente ante el SAT')
    if cancelable and value.get('EsCancelable') not in {'Cancelable sin aceptación','Cancelable con aceptación'}:
        raise HTTPException(status_code=409, detail='El SAT no confirmó que el comprobante sea cancelable')
    return value


def cancellation_status(value):
    if value.get('Estado') == 'Cancelado': return 'cancelled'
    state = (value.get('EstatusCancelacion') or '').strip().lower()
    if state == 'solicitud rechazada': return 'rejected'
    if state == 'en proceso': return 'requested'
    return None
