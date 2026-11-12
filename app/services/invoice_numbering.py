"""El llamador confirma la transacción; ninguna reserva cruza una llamada al PAC."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import text, func
from app.models.commercial import BillingDraft
from app.models.invoice_numbering import InvoiceNumbering, InvoiceFolioReservation, InvoiceNumberingEvent
from app.schemas.invoice_numbering import MAX_NUMBER


def current_year():
    return datetime.now(ZoneInfo('America/Mexico_City')).year


def lock_company(db, company_id):
    # También serializa el alta inicial, cuando aún no existe una fila que bloquear.
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                   {'key': 'digestor:invoice-numbering:' + company_id})


def series_for(policy, year):
    return '-'.join(part for part in [policy.prefix, str(year) if policy.include_year else ''] if part)


def next_for(policy, year):
    if year < policy.period_year:
        raise HTTPException(409, 'Revisa el reloj del sistema antes de asignar folios')
    return 1 if policy.reset_annually and year > policy.period_year else policy.next_number


def policy_view(policy, year=None):
    if not policy:
        return None
    year = year or current_year()
    number = next_for(policy, year)
    series = series_for(policy, year)
    return {'prefix': policy.prefix, 'include_year': policy.include_year, 'reset_annually': policy.reset_annually,
            'digits': policy.digits, 'next_number': number, 'revision': policy.revision,
            'series': series, 'year': year,
            'examples': ['-'.join(p for p in [series, str(n).zfill(policy.digits)] if p)
                         for n in range(number, min(number + 3, MAX_NUMBER + 1))]}


def get_policy(db, company_id, lock=False):
    query = db.query(InvoiceNumbering).filter_by(company_id=company_id)
    return (query.with_for_update() if lock else query).populate_existing().first()


def high_water(db, company_id, series):
    # Incluye documentos anteriores al módulo, aun cancelados o con resultado incierto.
    # Comparar el valor numérico también impide reciclar 0001 como 1.
    values = db.query(BillingDraft.folio).filter(BillingDraft.company_id == company_id,
        func.coalesce(BillingDraft.series, 'PF') == series).all()
    reserved = db.query(InvoiceFolioReservation.number).filter_by(company_id=company_id, series=series).all()
    return max([0] + [int(value) for (value,) in values if value and value.isascii() and value.isdigit()]
               + [number for (number,) in reserved])


def save_policy(db, ctx, body):
    lock_company(db, ctx.tenant_id)
    policy = get_policy(db, ctx.tenant_id, lock=True)
    if body.expected_revision != (policy.revision if policy else 0):
        raise HTTPException(409, 'La numeración cambió. Recarga antes de guardar')
    before = policy_view(policy)
    year = current_year()
    if policy is None:
        policy = InvoiceNumbering(company_id=ctx.tenant_id, revision=0)
    for name in ('prefix', 'include_year', 'reset_annually', 'digits', 'next_number'):
        setattr(policy, name, getattr(body, name))
    policy.period_year = year
    if body.next_number <= high_water(db, ctx.tenant_id, series_for(policy, year)):
        raise HTTPException(409, 'El próximo número debe ser mayor a los folios ya utilizados en esa serie')
    policy.revision += 1
    db.add(policy)
    db.add(InvoiceNumberingEvent(company_id=ctx.tenant_id, authenticated_actor=ctx.user_id,
        declared_actor=body.declared_actor,
        details=json.dumps({'before': before, 'after': policy_view(policy)}, ensure_ascii=False, sort_keys=True)))
    db.flush()
    return policy


def reserve(db, draft):
    if not draft.numbering_managed:
        return None
    lock_company(db, draft.company_id)
    previous = db.query(InvoiceFolioReservation).filter_by(company_id=draft.company_id, draft_id=draft.id).first()
    if previous:
        if (draft.series, draft.folio) != (previous.series, previous.folio):
            raise HTTPException(409, 'El folio reservado cambió; requiere revisión')
        return previous
    policy = get_policy(db, draft.company_id, lock=True)
    if policy is None:
        raise HTTPException(409, 'Configura la numeración de esta empresa antes de timbrar')
    year = current_year()
    series = series_for(policy, year)
    number = max(next_for(policy, year), high_water(db, draft.company_id, series) + 1)
    if number > MAX_NUMBER:
        raise HTTPException(409, 'La serie agotó sus números; configura otra serie')
    row = InvoiceFolioReservation(company_id=draft.company_id, draft_id=draft.id, series=series,
        folio=str(number).zfill(policy.digits), number=number, policy_revision=policy.revision)
    db.add(row)
    draft.series, draft.folio = row.series, row.folio
    policy.next_number, policy.period_year = number + 1, year
    policy.revision += 1
    db.flush()
    return row


def estimated_folio(db, draft):
    """Consulta orientativa: no reserva números ni modifica el borrador."""
    if not draft.numbering_managed or draft.folio != draft.id or draft.stamp_status == 'stamped':
        return None
    policy = get_policy(db, draft.company_id)
    if policy is None:
        return None
    year = current_year()
    if year < policy.period_year:
        return None
    series = series_for(policy, year)
    number = max(next_for(policy, year), high_water(db, draft.company_id, series) + 1)
    if number > MAX_NUMBER:
        return None
    return '-'.join(part for part in [series, str(number).zfill(policy.digits)] if part)


def display_folio(draft):
    if draft.numbering_managed and draft.folio == draft.id:
        return 'Por asignar al timbrar'
    series = draft.series or ('' if draft.numbering_managed else 'PF')
    return '-'.join(part for part in [series, draft.folio or draft.id[:8]] if part)
