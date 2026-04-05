import base64
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload

from app.core.auth import SecurityContext, enforce_tenant_scope, role_guard
from app.core.config import settings
from app.db.session import get_db
from app.models.commercial import BillingDraft, BillingDraftItem, ProductCatalog
from app.models.evento import EventoFacturacion
from app.schemas.commercial import (
    BillingDraftCreateRequest,
    BillingDraftResponse,
    BillingDraftStampRequest,
    BillingDraftStampResponse,
    BillingDraftUpdateRequest,
    DraftItemInput,
    DraftItemResponse,
    ProductCreateRequest,
    ProductResponse,
    ProductUpdateRequest,
)
from app.services import timbracfdi_client
from app.services.timbracfdi_client import TimbraCFDIConfigurationError

router = APIRouter(tags=["commercial-v1"])


def _money(value: float | int | None) -> float:
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


_REGIMEN_TEXT_TO_CODE = {
    "general de ley personas morales": "601",
    "personas morales con fines no lucrativos": "603",
    "sueldos y salarios": "605",
    "arrendamiento": "606",
    "demas ingresos": "608",
    "residentes en el extranjero": "610",
    "personas fisicas con actividades empresariales y profesionales": "612",
    "sin obligaciones fiscales": "616",
    "incorporacion fiscal": "621",
    "actividades agricolas ganaderas silvicolas y pesqueras": "622",
    "coordinados": "624",
    "regimen simplificado de confianza": "626",
}


def _normalize_regimen_code(raw: str) -> str:
    val = (raw or "").strip()
    if not val:
        return ""
    m = re.match(r"^(\d{3})", val)
    if m:
        return m.group(1)
    normalized = re.sub(r"[^a-z ]", "", val.lower().replace("\xe9", "e").replace("\xe1", "a").replace("\xed", "i").replace("\xf3", "o").replace("\xfa", "u")).strip()
    if normalized.startswith("regimen "):
        normalized = normalized[len("regimen "):].strip()
    normalized = re.sub(r"^del?\s+", "", normalized)
    for text, code in _REGIMEN_TEXT_TO_CODE.items():
        if text in normalized or normalized in text:
            return code
    return val


def _rate(value: float | int | None) -> float:
    return float(Decimal(str(value or 0)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _serialize_product(product: ProductCatalog) -> ProductResponse:
    return ProductResponse(
        id=product.id,
        company_id=product.company_id,
        sku=product.sku,
        name=product.name,
        description=product.description,
        sat_product_code=product.sat_product_code,
        unit_code=product.unit_code,
        unit_name=product.unit_name,
        price=_money(product.price),
        currency=product.currency,
        tax_rate=_rate(product.tax_rate),
        tax_object=product.tax_object,
        is_active=bool(product.is_active),
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _serialize_item(item: BillingDraftItem) -> DraftItemResponse:
    return DraftItemResponse(
        id=item.id,
        product_id=item.product_id,
        sku=item.sku,
        description=item.description,
        sat_product_code=item.sat_product_code,
        unit_code=item.unit_code,
        quantity=float(item.quantity),
        unit_price=_money(item.unit_price),
        tax_rate=_rate(item.tax_rate),
        tax_object=item.tax_object,
        line_subtotal=_money(item.line_subtotal),
        line_taxes=_money(item.line_taxes),
        line_total=_money(item.line_total),
    )


def _missing_fields(draft: BillingDraft) -> list[str]:
    missing: list[str] = []
    required_map = {
        "customer_name": draft.customer_name,
        "customer_rfc": draft.customer_rfc,
        "customer_zip": draft.customer_zip,
        "customer_regimen": draft.customer_regimen,
        "customer_use_cfdi": draft.customer_use_cfdi,
        "emitter_rfc": draft.emitter_rfc,
        "emitter_name": draft.emitter_name,
        "emitter_regimen": draft.emitter_regimen,
        "place_of_issue": draft.place_of_issue,
    }
    for field_name, value in required_map.items():
        if not str(value or "").strip():
            missing.append(field_name)
    if not draft.items:
        missing.append("items")
    return missing


def _serialize_draft(draft: BillingDraft) -> BillingDraftResponse:
    missing_fields = _missing_fields(draft)
    return BillingDraftResponse(
        id=draft.id,
        company_id=draft.company_id,
        status=draft.status,
        stamp_status=draft.stamp_status,
        customer_name=draft.customer_name,
        customer_rfc=draft.customer_rfc,
        customer_zip=draft.customer_zip,
        customer_regimen=draft.customer_regimen,
        customer_use_cfdi=draft.customer_use_cfdi,
        emitter_rfc=draft.emitter_rfc,
        emitter_name=draft.emitter_name,
        emitter_regimen=draft.emitter_regimen,
        place_of_issue=draft.place_of_issue,
        currency=draft.currency,
        payment_method=draft.payment_method,
        payment_form=draft.payment_form,
        series=draft.series,
        folio=draft.folio,
        notes=draft.notes,
        subtotal=_money(draft.subtotal),
        taxes=_money(draft.taxes),
        total=_money(draft.total),
        ready_to_stamp=len(missing_fields) == 0 and draft.status != "stamped",
        missing_fields=missing_fields,
        items=[_serialize_item(item) for item in draft.items],
        stamped_at=draft.stamped_at,
        created_by=draft.created_by,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def _set_line_totals(item: BillingDraftItem) -> None:
    subtotal = _money((item.quantity or 0) * (item.unit_price or 0))
    taxes = 0.0
    if (item.tax_object or "02") == "02" and (item.tax_rate or 0) > 0:
        taxes = _money(subtotal * float(item.tax_rate or 0))
    item.line_subtotal = subtotal
    item.line_taxes = taxes
    item.line_total = _money(subtotal + taxes)


def _recalculate_draft(draft: BillingDraft) -> None:
    subtotal = 0.0
    taxes = 0.0
    for item in draft.items:
        _set_line_totals(item)
        subtotal += float(item.line_subtotal or 0)
        taxes += float(item.line_taxes or 0)

    draft.subtotal = _money(subtotal)
    draft.taxes = _money(taxes)
    draft.total = _money(subtotal + taxes)

    if draft.stamped_at:
        draft.status = "stamped"
        draft.stamp_status = "stamped"
    elif _missing_fields(draft):
        draft.status = "draft"
        if draft.stamp_status == "stamped":
            draft.stamp_status = "not_sent"
    else:
        draft.status = "ready_to_stamp"
        if draft.stamp_status == "stamped":
            draft.stamp_status = "not_sent"


def _resolve_item_payload(db: Session, company_id: str, payload: DraftItemInput) -> BillingDraftItem:
    product = None
    if payload.product_id:
        product = (
            db.query(ProductCatalog)
            .filter(ProductCatalog.id == payload.product_id, ProductCatalog.company_id == company_id)
            .first()
        )
        if not product:
            raise HTTPException(status_code=404, detail="Producto no encontrado para este tenant.")

    description = payload.description or (product.name if product else None)
    if not description:
        raise HTTPException(status_code=422, detail="Cada partida debe incluir `description` o `product_id`.")

    item = BillingDraftItem(
        company_id=company_id,
        product_id=product.id if product else None,
        sku=payload.sku or (product.sku if product else None),
        description=description,
        sat_product_code=payload.sat_product_code or (product.sat_product_code if product else "78101800"),
        unit_code=payload.unit_code or (product.unit_code if product else "E48"),
        quantity=float(payload.quantity),
        unit_price=float(payload.unit_price if payload.unit_price is not None else (product.price if product else 0.0)),
        tax_rate=float(payload.tax_rate if payload.tax_rate is not None else (product.tax_rate if product else 0.16)),
        tax_object=payload.tax_object or (product.tax_object if product else "02"),
    )
    _set_line_totals(item)
    return item


def _replace_items(db: Session, draft: BillingDraft, company_id: str, payload_items: list[DraftItemInput]) -> None:
    draft.items.clear()
    for payload in payload_items:
        draft.items.append(_resolve_item_payload(db, company_id, payload))
    _recalculate_draft(draft)


def _draft_query(db: Session):
    return db.query(BillingDraft).options(joinedload(BillingDraft.items))


def _get_draft_or_404(db: Session, company_id: str, draft_id: str) -> BillingDraft:
    draft = (
        _draft_query(db)
        .filter(BillingDraft.id == draft_id, BillingDraft.company_id == company_id)
        .first()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Prefactura no encontrada para este tenant.")
    return draft


def _build_preview_html(draft: BillingDraft) -> str:
    missing = _missing_fields(draft)
    customer_rfc = (draft.customer_rfc or "").strip().upper()
    preview_customer_name = draft.customer_name or ""
    preview_customer_zip = draft.customer_zip or ""
    preview_customer_regimen = draft.customer_regimen or ""
    preview_customer_use_cfdi = draft.customer_use_cfdi or ""

    if customer_rfc in {"XAXX010101000", "XEXX010101000"}:
        preview_customer_name = "PUBLICO EN GENERAL"
        preview_customer_zip = draft.place_of_issue or preview_customer_zip
        preview_customer_regimen = "616"
        preview_customer_use_cfdi = "S01"

    rows = "".join(
        f"""
        <tr>
          <td>{escape(item.sku or "-")}</td>
          <td>{escape(item.description)}</td>
          <td class=\"num\">{item.quantity:.2f}</td>
          <td class=\"num\">${item.unit_price:,.2f}</td>
          <td class=\"num\">${item.line_subtotal:,.2f}</td>
        </tr>
        """
        for item in draft.items
    ) or '<tr><td colspan="5">Sin partidas todavía.</td></tr>'

    warning = ""
    if missing:
        warning = (
            "<div class='warning'><strong>Faltan datos para timbrar:</strong> "
            + ", ".join(escape(field) for field in missing)
            + "</div>"
        )

    return f"""
    <!doctype html>
    <html lang="es">
      <head>
        <meta charset="utf-8" />
        <title>Preview prefactura {escape(draft.folio or draft.id)}</title>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 24px; color: #111827; }}
          h1, h2, h3 {{ margin-bottom: 8px; }}
          .meta {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }}
          .box {{ border: 1px solid #d1d5db; border-radius: 8px; padding: 12px; }}
          .watermark {{ color: #b91c1c; font-weight: 700; text-transform: uppercase; margin-bottom: 12px; }}
          .warning {{ background: #fff7ed; border: 1px solid #fdba74; padding: 10px; border-radius: 6px; margin-bottom: 12px; }}
          table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
          th, td {{ border-bottom: 1px solid #e5e7eb; padding: 10px; text-align: left; }}
          .num {{ text-align: right; }}
          .totals {{ margin-top: 18px; width: 320px; margin-left: auto; }}
          .totals td {{ border: none; padding: 6px 0; }}
          @media print {{ .no-print {{ display: none; }} body {{ margin: 12px; }} }}
        </style>
      </head>
      <body>
        <div class="watermark">Documento preliminar / No timbrado</div>
        {warning}
        <div class="meta">
          <div class="box">
            <h3>Emisor</h3>
            <div><strong>{escape(draft.emitter_name or '')}</strong></div>
            <div>RFC: {escape(draft.emitter_rfc or '')}</div>
            <div>Régimen: {escape(draft.emitter_regimen or '')}</div>
            <div>Lugar expedición: {escape(draft.place_of_issue or '')}</div>
          </div>
          <div class="box">
            <h3>Receptor</h3>
            <div><strong>{escape(preview_customer_name)}</strong></div>
            <div>RFC: {escape(draft.customer_rfc or '')}</div>
            <div>CP: {escape(preview_customer_zip)}</div>
            <div>Régimen: {escape(preview_customer_regimen)}</div>
            <div>Uso CFDI: {escape(preview_customer_use_cfdi)}</div>
          </div>
        </div>

        <div class="box">
          <h3>Prefactura {escape((draft.series or '') + '-' + (draft.folio or draft.id[:8]))}</h3>
          <div>Estatus: <strong>{escape(draft.status)}</strong></div>
          <div>Método de pago: {escape(draft.payment_method or '')} | Forma de pago: {escape(draft.payment_form or '')}</div>
          <div>Moneda: {escape(draft.currency or 'MXN')}</div>
          <div>Notas: {escape(draft.notes or '-')}</div>

          <table>
            <thead>
              <tr>
                <th>SKU</th>
                <th>Concepto</th>
                <th class="num">Cantidad</th>
                <th class="num">Precio</th>
                <th class="num">Importe</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>

          <table class="totals">
            <tr><td>Subtotal</td><td class="num">${draft.subtotal:,.2f}</td></tr>
            <tr><td>Impuestos</td><td class="num">${draft.taxes:,.2f}</td></tr>
            <tr><td><strong>Total</strong></td><td class="num"><strong>${draft.total:,.2f}</strong></td></tr>
          </table>
        </div>
      </body>
    </html>
    """


def _build_cfdi_xml(draft: BillingDraft) -> str:
    missing = _missing_fields(draft)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "DRAFT_NOT_READY",
                "message": "La prefactura aún no está lista para timbrar.",
                "missing_fields": missing,
            },
        )

    issue_time = datetime.now(timezone.utc) - timedelta(hours=settings.TIMBRACFDI_EMIT_OFFSET_HOURS)
    fecha = issue_time.strftime("%Y-%m-%dT%H:%M:%S")
    folio = draft.folio or issue_time.strftime("%Y%m%d%H%M%S")
    serie_attr = f' Serie="{escape(draft.series or "PF", quote=True)}"'

    customer_rfc = (draft.customer_rfc or "").strip().upper()
    customer_name = (draft.customer_name or "").strip()
    customer_zip = (draft.customer_zip or "").strip()
    customer_regimen = _normalize_regimen_code((draft.customer_regimen or "").strip())
    customer_use_cfdi = (draft.customer_use_cfdi or "G03").strip()
    informacion_global_xml = ""
    if customer_rfc in {"XAXX010101000", "XEXX010101000"}:
        customer_name = "PUBLICO EN GENERAL"
        customer_zip = (draft.place_of_issue or customer_zip or "32690").strip()
        customer_regimen = "616"
        customer_use_cfdi = "S01"
        informacion_global_xml = (
            f'<cfdi:InformacionGlobal Periodicidad="04" Meses="{issue_time.strftime("%m")}" Año="{issue_time.strftime("%Y")}" />'
        )

    concepts_xml: list[str] = []
    traslados: dict[str, dict[str, float]] = defaultdict(lambda: {"base": 0.0, "importe": 0.0})

    for index, item in enumerate(draft.items, start=1):
        subtotal = _money(item.line_subtotal)
        taxes = _money(item.line_taxes)
        tax_object = item.tax_object or "02"
        quantity = float(item.quantity or 0)
        unit_price = _money(item.unit_price)

        impuestos_xml = ""
        if tax_object == "02" and taxes > 0:
            rate_key = f"{float(item.tax_rate or 0):.6f}"
            traslados[rate_key]["base"] += subtotal
            traslados[rate_key]["importe"] += taxes
            impuestos_xml = f"""
            <cfdi:Impuestos>
              <cfdi:Traslados>
                <cfdi:Traslado Base=\"{subtotal:.2f}\" Impuesto=\"002\" TipoFactor=\"Tasa\" TasaOCuota=\"{rate_key}\" Importe=\"{taxes:.2f}\" />
              </cfdi:Traslados>
            </cfdi:Impuestos>
            """

        concepts_xml.append(
            f"""
            <cfdi:Concepto ClaveProdServ=\"{escape(item.sat_product_code or '78101800', quote=True)}\" NoIdentificacion=\"{escape(item.sku or f'ITEM-{index}', quote=True)}\" Cantidad=\"{quantity:.2f}\" ClaveUnidad=\"{escape(item.unit_code or 'E48', quote=True)}\" Descripcion=\"{escape(item.description, quote=True)}\" ValorUnitario=\"{unit_price:.2f}\" Importe=\"{subtotal:.2f}\" ObjetoImp=\"{escape(tax_object, quote=True)}\">{impuestos_xml}
            </cfdi:Concepto>
            """
        )

    impuestos_globales = ""
    if traslados:
        traslados_xml = "".join(
            f'<cfdi:Traslado Base="{values["base"]:.2f}" Impuesto="002" TipoFactor="Tasa" TasaOCuota="{rate_key}" Importe="{values["importe"]:.2f}" />'
            for rate_key, values in traslados.items()
        )
        impuestos_globales = f"""
        <cfdi:Impuestos TotalImpuestosTrasladados=\"{draft.taxes:.2f}\">
          <cfdi:Traslados>{traslados_xml}</cfdi:Traslados>
        </cfdi:Impuestos>
        """

    xml = f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<cfdi:Comprobante xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\" xmlns:cfdi=\"http://www.sat.gob.mx/cfd/4\" Moneda=\"{escape(draft.currency or 'MXN', quote=True)}\" Total=\"{draft.total:.2f}\" xsi:schemaLocation=\"http://www.sat.gob.mx/cfd/4 http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd\" Exportacion=\"01\" MetodoPago=\"{escape(draft.payment_method or 'PUE', quote=True)}\" TipoDeComprobante=\"I\" SubTotal=\"{draft.subtotal:.2f}\" FormaPago=\"{escape(draft.payment_form or '01', quote=True)}\" LugarExpedicion=\"{escape(draft.place_of_issue or '32690', quote=True)}\" Fecha=\"{fecha}\" Folio=\"{escape(folio, quote=True)}\"{serie_attr} Version=\"4.0\">
  {informacion_global_xml}
  <cfdi:Emisor Rfc=\"{escape(draft.emitter_rfc or '', quote=True)}\" Nombre=\"{escape(draft.emitter_name or '', quote=True)}\" RegimenFiscal=\"{escape(_normalize_regimen_code(draft.emitter_regimen or ''), quote=True)}\" />
  <cfdi:Receptor Rfc=\"{escape(customer_rfc, quote=True)}\" Nombre=\"{escape(customer_name, quote=True)}\" DomicilioFiscalReceptor=\"{escape(customer_zip, quote=True)}\" RegimenFiscalReceptor=\"{escape(customer_regimen, quote=True)}\" UsoCFDI=\"{escape(customer_use_cfdi, quote=True)}\" />
  <cfdi:Conceptos>{''.join(concepts_xml)}
  </cfdi:Conceptos>
  {impuestos_globales}
</cfdi:Comprobante>
"""
    return xml


@router.post(
    "/v1/catalog/products",
    response_model=ProductResponse,
    summary="Crear producto o servicio del catálogo",
    description="Da de alta un producto/servicio tenant-scoped para reutilizarlo en prefacturas.",
)
def create_product(
    body: ProductCreateRequest,
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    company_id = enforce_tenant_scope(ctx, body.company_id)

    existing = (
        db.query(ProductCatalog)
        .filter(ProductCatalog.company_id == company_id, ProductCatalog.sku == body.sku)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Ya existe un producto con ese SKU en este tenant.")

    product = ProductCatalog(
        company_id=company_id,
        sku=body.sku,
        name=body.name,
        description=body.description,
        sat_product_code=body.sat_product_code,
        unit_code=body.unit_code,
        unit_name=body.unit_name,
        price=_money(body.price),
        currency=body.currency,
        tax_rate=float(body.tax_rate),
        tax_object=body.tax_object,
        is_active=body.is_active,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return _serialize_product(product)


@router.get(
    "/v1/catalog/products",
    response_model=list[ProductResponse],
    summary="Listar catálogo del tenant",
)
def list_products(
    company_id: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    ctx: SecurityContext = Depends(role_guard("viewer", "operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, company_id)
    query = db.query(ProductCatalog).filter(ProductCatalog.company_id == cid)
    if active_only:
        query = query.filter(ProductCatalog.is_active.is_(True))
    products = query.order_by(ProductCatalog.name.asc()).all()
    return [_serialize_product(product) for product in products]


@router.get(
    "/v1/catalog/products/{product_id}",
    response_model=ProductResponse,
    summary="Obtener un producto del catálogo",
)
def get_product(
    product_id: str,
    company_id: str | None = Query(default=None),
    ctx: SecurityContext = Depends(role_guard("viewer", "operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, company_id)
    product = (
        db.query(ProductCatalog)
        .filter(ProductCatalog.id == product_id, ProductCatalog.company_id == cid)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado para este tenant.")
    return _serialize_product(product)


@router.patch(
    "/v1/catalog/products/{product_id}",
    response_model=ProductResponse,
    summary="Actualizar producto del catálogo",
)
def update_product(
    product_id: str,
    body: ProductUpdateRequest,
    company_id: str | None = Query(default=None),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, company_id)
    product = (
        db.query(ProductCatalog)
        .filter(ProductCatalog.id == product_id, ProductCatalog.company_id == cid)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado para este tenant.")

    updates = body.model_dump(exclude_unset=True)
    if "sku" in updates and updates["sku"] != product.sku:
        existing = (
            db.query(ProductCatalog)
            .filter(ProductCatalog.company_id == cid, ProductCatalog.sku == updates["sku"], ProductCatalog.id != product_id)
            .first()
        )
        if existing:
            raise HTTPException(status_code=409, detail="Ya existe otro producto con ese SKU en este tenant.")

    for field_name, value in updates.items():
        if field_name == "price" and value is not None:
            value = _money(value)
        setattr(product, field_name, value)

    db.commit()
    db.refresh(product)
    return _serialize_product(product)


@router.post(
    "/v1/billing/drafts",
    response_model=BillingDraftResponse,
    summary="Crear borrador o prefactura",
    description="Crea una prefactura editable con partidas y totales automáticos. Si completas datos del receptor + partidas, queda `ready_to_stamp`.",
)
def create_billing_draft(
    body: BillingDraftCreateRequest,
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, body.company_id)
    now_folio = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    draft = BillingDraft(
        company_id=cid,
        customer_name=body.customer_name,
        customer_rfc=body.customer_rfc,
        customer_zip=body.customer_zip,
        customer_regimen=body.customer_regimen,
        customer_use_cfdi=body.customer_use_cfdi,
        emitter_rfc=body.emitter_rfc or "IIA040805DZ4",
        emitter_name=body.emitter_name or "INDUSTRIA ILUMINADORA DE ALMACENES",
        emitter_regimen=body.emitter_regimen or "626",
        place_of_issue=body.place_of_issue or "32690",
        currency=body.currency,
        payment_method=body.payment_method,
        payment_form=body.payment_form,
        series=body.series,
        folio=body.folio or now_folio,
        notes=body.notes,
        created_by=ctx.user_id,
    )
    db.add(draft)
    db.flush()
    _replace_items(db, draft, cid, body.items)
    _recalculate_draft(draft)
    db.commit()
    draft = _get_draft_or_404(db, cid, draft.id)
    return _serialize_draft(draft)


@router.get(
    "/v1/billing/drafts",
    response_model=list[BillingDraftResponse],
    summary="Listar prefacturas del tenant",
)
def list_billing_drafts(
    company_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="Filtro opcional: draft, ready_to_stamp, stamped."),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: SecurityContext = Depends(role_guard("viewer", "operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, company_id)
    query = _draft_query(db).filter(BillingDraft.company_id == cid)
    if status:
        query = query.filter(BillingDraft.status == status)
    drafts = query.order_by(BillingDraft.updated_at.desc()).limit(limit).all()
    return [_serialize_draft(draft) for draft in drafts]


@router.get(
    "/v1/billing/drafts/{draft_id}",
    response_model=BillingDraftResponse,
    summary="Obtener una prefactura",
)
def get_billing_draft(
    draft_id: str,
    company_id: str | None = Query(default=None),
    ctx: SecurityContext = Depends(role_guard("viewer", "operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, company_id)
    draft = _get_draft_or_404(db, cid, draft_id)
    return _serialize_draft(draft)


@router.patch(
    "/v1/billing/drafts/{draft_id}",
    response_model=BillingDraftResponse,
    summary="Actualizar prefactura",
)
def update_billing_draft(
    draft_id: str,
    body: BillingDraftUpdateRequest,
    company_id: str | None = Query(default=None),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, company_id)
    draft = _get_draft_or_404(db, cid, draft_id)

    updates = body.model_dump(exclude_unset=True, exclude={"items"})
    for field_name, value in updates.items():
        setattr(draft, field_name, value)

    if body.items is not None:
        _replace_items(db, draft, cid, body.items)
    _recalculate_draft(draft)

    db.commit()
    draft = _get_draft_or_404(db, cid, draft_id)
    return _serialize_draft(draft)


@router.get(
    "/v1/billing/drafts/{draft_id}/preview",
    response_class=HTMLResponse,
    summary="Ver preview imprimible antes de timbrar",
    description="Genera una vista HTML lista para imprimir con la marca 'Documento preliminar / No timbrado'.",
)
def preview_billing_draft(
    draft_id: str,
    company_id: str | None = Query(default=None),
    ctx: SecurityContext = Depends(role_guard("viewer", "operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, company_id)
    draft = _get_draft_or_404(db, cid, draft_id)
    return HTMLResponse(content=_build_preview_html(draft))


@router.post(
    "/v1/billing/drafts/{draft_id}/stamp",
    response_model=BillingDraftStampResponse,
    summary="Timbrar la prefactura usando el PAC activo",
    description="Arma un XML CFDI 4.0 básico desde la prefactura y lo envía a TimbraCFDI. Si el PAC responde OK, el borrador queda marcado como `stamped`.",
)
def stamp_billing_draft(
    draft_id: str,
    body: BillingDraftStampRequest,
    company_id: str | None = Query(default=None),
    ctx: SecurityContext = Depends(role_guard("operator", "admin", "superadmin")),
    db: Session = Depends(get_db),
):
    cid = enforce_tenant_scope(ctx, company_id)
    draft = _get_draft_or_404(db, cid, draft_id)

    xml = _build_cfdi_xml(draft)
    xml_base64 = base64.b64encode(xml.encode("utf-8")).decode("ascii")
    support_id = body.id_comprobante or f"draft-{draft.id}"

    try:
        result = timbracfdi_client.timbra_cfdi(xml_base64=xml_base64, id_comprobante=support_id)
    except TimbraCFDIConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No fue posible timbrar la prefactura: {exc}") from exc

    draft.stamped_xml_base64 = xml_base64
    draft.stamped_response_json = json.dumps(result.get("provider_response"), ensure_ascii=False)
    draft.stamp_status = "stamped" if result.get("ok") else "rejected"

    if result.get("ok"):
        draft.stamped_at = datetime.utcnow()
        draft.status = "stamped"
        db.add(
            EventoFacturacion(
                company_id=cid,
                subtotal=draft.subtotal,
                impuestos=draft.taxes,
                total=draft.total,
                metodo_pago=draft.payment_method,
                forma_pago=draft.payment_form,
                evt_hash=f"draft-stamped:{draft.id}:{uuid.uuid4()}",
            )
        )

    db.commit()
    draft = _get_draft_or_404(db, cid, draft.id)

    return BillingDraftStampResponse(
        draft=_serialize_draft(draft),
        ok=bool(result.get("ok")),
        remote_status_code=int(result.get("status_code", 0)),
        provider_response=result.get("provider_response"),
        xml_base64=xml_base64,
    )
