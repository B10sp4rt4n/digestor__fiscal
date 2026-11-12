"""Lectura y geometría de PDFs sintéticos; sin base productiva ni PAC."""
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import fitz
from pypdf import PdfReader

from app.services.prefactura_pdf import render_prefactura_pdf


def item(**changes):
    return SimpleNamespace(**(dict(description='Licencia empresarial con protección de endpoints, evaluación de vulnerabilidades y soporte. Descripción completa sin recorte.',
        sku='SKU-SYNTHETIC', sat_product_code='43233205', unit_code='E48', quantity=40,
        unit_price=790, tax_rate=.16, tax_object='02', line_subtotal=31600, line_taxes=5056,
        line_total=36656) | changes))


def draft(**changes):
    return SimpleNamespace(**(dict(emitter_name='EMISOR SINTÉTICO DE PRUEBAS', emitter_rfc='AAA010101AAA',
        emitter_regimen='626', place_of_issue='11000', customer_name='RECEPTOR SINTÉTICO',
        customer_rfc='BBB010101BBB', customer_zip='05348', customer_regimen='601', customer_use_cfdi='G03',
        currency='MXN', payment_method='PUE', payment_form='03', stamp_status='not_sent',
        subtotal=31600, taxes=5056, total=36656, notes='Referencia comercial de prueba', items=[item()]) | changes))


def render(value=None, **kwargs):
    return render_prefactura_pdf(value or draft(), display_folio='Por asignar al timbrar',
        generated_at=datetime(2026, 11, 12, 0, 30, tzinfo=timezone.utc), **kwargs)


def text(pdf):
    return '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)


def check_bounds(pdf):
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        for page in doc:
            for word in page.get_text('words'):
                x0, y0, x1, y1, *_ = word
                assert x0 >= 16 * 72 / 25.4 and x1 <= 194 * 72 / 25.4, word
                assert y0 >= 17 * 72 / 25.4 and y1 <= 292 * 72 / 25.4, word


def test_professional_prefactura_preserves_complete_concept_and_stored_amounts():
    pdf = render(estimated_folio='AF-000117')
    content = text(pdf)
    assert len(PdfReader(BytesIO(pdf)).pages) == 1
    assert 'AF-000117' in content and 'Próximo folio estimado:' in content
    assert 'Descripción completa sin recorte.' in content
    assert all(value in content for value in ['AAA010101AAA', 'BBB010101BBB', '43233205', 'E48',
        '$790.00', '$31,600.00', '$5,056.00', '$36,656.00', 'Transferencia electrónica de fondos'])
    assert 'Página 1 de 1' in content
    check_bounds(pdf)


def test_generation_date_and_metadata_use_mexico_city_day():
    pdf = render()
    assert '11/11/2026 a las 18:30 (CDMX)' in text(pdf)
    assert '12/11/2026' not in text(pdf)
    assert PdfReader(BytesIO(pdf)).metadata.creation_date.day == 11
    assert 'No es un CFDI' in text(pdf)
    assert 'Próximo folio estimado:' not in text(pdf)


def test_long_documents_repeat_table_headers_preserve_every_row_and_total_once():
    value = draft(items=[item(description=f'CONCEPTO-{i:03d}: ' + 'Detalle de licencia y servicios completos. ' * 8) for i in range(45)],
                  notes='Observaciones extensas: ' + 'Conservar estas condiciones de referencia. ' * 50)
    pdf = render(value, estimated_folio='SERIE-LARGA-2026-999999999999')
    reader = PdfReader(BytesIO(pdf))
    content = text(pdf)
    assert len(reader.pages) > 3
    for i in range(45):
        assert f'CONCEPTO-{i:03d}:' in content
    assert content.count('TOTAL MXN') == 1
    assert 'El concepto continúa' not in content
    assert 'Impuestos' in content  # Totales guardados no se recalculan a partir de la maqueta.
    for index, page in enumerate(reader.pages, 1):
        assert f'Página {index} de {len(reader.pages)}' in page.extract_text()
    check_bounds(pdf)


def test_one_extremely_long_concept_can_continue_across_pages_without_truncation():
    value = draft(items=[item(description='INICIO-CONCEPTO ' + 'Detalle comercial completo. ' * 500 + ' FIN-CONCEPTO')])
    pdf = render(value, estimated_folio='AF-117')
    content = text(pdf)
    assert len(PdfReader(BytesIO(pdf)).pages) > 1
    assert 'INICIO-CONCEPTO' in content and 'FIN-CONCEPTO' in content
    assert 'El concepto continúa' in content
    assert content.count('$31,600.00') == 2  # Importe y subtotal, sin duplicar por cada continuación.
    check_bounds(pdf)


def test_fractional_quantity_precision_and_zero_are_not_replaced():
    value = draft(items=[item(quantity=0, unit_price=.123456)])
    pdf = render(value)
    content = text(pdf)
    assert '$0.123456' in content
    check_bounds(pdf)


def test_stamped_representation_keeps_folio_uuid_and_omits_preliminary_notice():
    pdf = render_prefactura_pdf(draft(stamp_status='stamped'), display_folio='FAC-0000125',
        uuid_cfdi='11111111-2222-4333-8444-555555555555')
    content = text(pdf)
    assert 'CFDI TIMBRADO' in content and 'FAC-0000125' in content
    assert '11111111-2222-4333-8444-555555555555' in content
    assert 'No es un CFDI' not in content and 'Próximo folio estimado:' not in content
    check_bounds(pdf)
