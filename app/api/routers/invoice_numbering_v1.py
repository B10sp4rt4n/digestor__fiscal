from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.auth import SecurityContext, role_guard
from app.core.config import settings
from app.db.session import get_db
from app.schemas.invoice_numbering import InvoiceNumberingInput
from app.services import invoice_numbering as service

router = APIRouter(tags=['invoice-numbering'])


@router.get('/v1/billing/invoice-numbering')
def read(ctx: SecurityContext = Depends(role_guard('admin', 'superadmin')), db: Session = Depends(get_db)):
    year = service.current_year()
    if not settings.FISCAL_NUMBERING_ENABLED:
        return {'available': False, 'year': year, 'policy': None}
    return {'available': True, 'year': year, 'policy': service.policy_view(service.get_policy(db, ctx.tenant_id), year)}


@router.put('/v1/billing/invoice-numbering')
def update(body: InvoiceNumberingInput, ctx: SecurityContext = Depends(role_guard('admin', 'superadmin')),
           db: Session = Depends(get_db)):
    if not settings.FISCAL_NUMBERING_ENABLED:
        raise HTTPException(503, 'La configuración de folios aún no está habilitada')
    policy = service.save_policy(db, ctx, body)
    result = service.policy_view(policy)
    db.commit()
    return {'available': True, 'year': result['year'], 'policy': result}
