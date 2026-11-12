"""Representación preliminar de datos guardados; no calcula ni emite CFDI."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fpdf import FPDF


NAVY = (20, 42, 66)
BLUE = (26, 91, 128)
INK = (35, 49, 64)
MUTED = (101, 116, 132)
LIGHT = (243, 246, 249)
LINE = (220, 228, 235)
MARGIN = 17
WIDTH = 176
BOTTOM = 271
COLS = (8, 84, 18, 32, 34)


def local_now():
    return datetime.now(ZoneInfo('America/Mexico_City'))


def clean(value, fallback='-'):
    text = str(value).strip() if value is not None else ''
    for char in ('\u2010', '\u2011', '\u2012', '\u2013', '\u2014', '\u2212'):
        text = text.replace(char, '-')
    return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32) or fallback


def amount(value):
    return '${:,.2f}'.format(Decimal(str(value or 0)))


def quantity(value):
    text = format(Decimal(str(value or 0)), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def unit_amount(value):
    number = Decimal(str(value or 0))
    places = min(6, max(2, -number.normalize().as_tuple().exponent))
    return '${:,.{}f}'.format(number, places)


class PrefacturaPDF(FPDF):
    def __init__(self, draft, folio, estimated, generated_at, uuid_cfdi):
        super().__init__(orientation='P', unit='mm', format='A4')
        self.draft, self.folio, self.estimated = draft, clean(folio), estimated
        self.generated_at, self.uuid_cfdi = generated_at, uuid_cfdi
        self.stamped = draft.stamp_status == 'stamped'
        self.face = 'Helvetica'
        for directory in ('/usr/share/fonts/truetype/dejavu', '/usr/local/share/fonts', '/app/fonts'):
            regular, bold = Path(directory) / 'DejaVuSans.ttf', Path(directory) / 'DejaVuSans-Bold.ttf'
            if regular.is_file() and bold.is_file():
                self.add_font('Document', '', str(regular))
                self.add_font('Document', 'B', str(bold))
                self.face = 'Document'
                break
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(False)
        self.alias_nb_pages()
        self.set_title(('CFDI timbrado ' if self.stamped else 'Prefactura ') + self.folio)
        self.set_author(clean(draft.emitter_name, 'Emisor'))
        self.set_creator('Digestor Fiscal')
        self.set_creation_date(generated_at)

    def text_value(self, value, fallback='-'):
        text = clean(value, fallback)
        return text if self.face == 'Document' else text.encode('latin-1', 'replace').decode('latin-1')

    def font(self, size=8.5, bold=False, color=INK):
        self.set_font(self.face, 'B' if bold else '', size)
        self.set_text_color(*color)

    def lines(self, text, width, size=8.5, bold=False):
        self.font(size, bold)
        return self.multi_cell(width, 4.2, self.text_value(text), dry_run=True, output='LINES')

    def block(self, x, y, width, text, size=8.5, bold=False, color=INK, height=4.5):
        lines = self.lines(text, width, size, bold)
        self.font(size, bold, color)
        for line in lines:
            self.set_xy(x, y)
            self.cell(width, height, line)
            y += height
        return y

    def label(self, x, y, width, text):
        return self.block(x, y, width, text, 6.8, True, MUTED, 4)

    def fit(self, x, y, width, text, size=8.5, bold=False, color=INK, align='R'):
        text = self.text_value(text, '')
        self.font(size, bold, color)
        while self.get_string_width(text) > width - 1 and size > 5.5:
            size -= .5
            self.font(size, bold, color)
        self.set_xy(x, y)
        self.cell(width, 5, text, align=align)

    def header(self):
        self.set_draw_color(*BLUE)
        self.set_line_width(.7)
        self.line(MARGIN, 16, MARGIN + WIDTH, 16)
        if self.page_no() > 1:
            end_y = self.block(MARGIN, 20, 123, self.draft.emitter_name, 8, True, NAVY, 4)
            self.fit(142, 20, 51, self.folio, 10, True, BLUE)
            self.set_y(max(33, end_y + 6))
            return
        title = 'CFDI TIMBRADO' if self.stamped else 'PREFACTURA'
        self.label(MARGIN, 22, 109, 'EMISOR')
        left_y = self.block(MARGIN, 28, 110, self.draft.emitter_name, 14, True, NAVY, 6.5)
        left_y = self.block(MARGIN, left_y + 3, 110, 'RFC: ' + clean(self.draft.emitter_rfc), 8.5)
        left_y = self.block(MARGIN, left_y + 1, 110, 'Régimen fiscal: ' + clean(self.draft.emitter_regimen)
                            + '   |   CP de expedición: ' + clean(self.draft.place_of_issue), 7.4, color=MUTED)
        self.fit(134, 22, 59, title, 12, True, BLUE)
        self.block(134, 31, 59, 'Próximo folio estimado:' if self.estimated else 'Folio:', 7.5, color=MUTED)
        self.fit(134, 38, 59, self.folio, 22, True, NAVY)
        self.fit(134, 48, 59, self.generated_at.strftime('%d/%m/%Y'), 8, color=MUTED)
        y = max(62, left_y + 5)
        self.set_draw_color(*LINE)
        self.set_line_width(.25)
        self.line(MARGIN, y, MARGIN + WIDTH, y)
        self.set_y(y + 7)

    def footer(self):
        self.set_draw_color(*LINE)
        self.set_line_width(.2)
        self.line(MARGIN, 278, MARGIN + WIDTH, 278)
        self.font(6.5, color=MUTED)
        self.set_xy(MARGIN, 281)
        self.cell(135, 4, self.generated_at.strftime('Generado el %d/%m/%Y a las %H:%M (CDMX)'))
        self.set_xy(155, 281)
        self.cell(38, 4, f'Página {self.page_no()} de {{nb}}', align='R')
        self.set_xy(MARGIN, 285)
        self.cell(WIDTH, 4, 'Representación de consulta' if self.stamped else 'Documento preliminar - No es un CFDI', align='L')

    def room(self, height):
        if self.get_y() + height > BOTTOM:
            self.add_page()

    def recipient(self):
        d = self.draft
        name_lines = self.lines(d.customer_name, WIDTH - 12, 11, True)
        details = 'RFC: ' + clean(d.customer_rfc) + '   |   Código postal: ' + clean(d.customer_zip)
        fiscal = 'Régimen fiscal: ' + clean(d.customer_regimen) + '   |   Uso CFDI: ' + clean(d.customer_use_cfdi)
        height = 21 + len(name_lines) * 5.5
        y = self.get_y()
        self.set_fill_color(*LIGHT)
        self.rect(MARGIN, y, WIDTH, height, style='F')
        self.label(23, y + 4, WIDTH - 12, 'RECEPTOR')
        next_y = self.block(23, y + 9, WIDTH - 12, d.customer_name, 11, True, NAVY, 5.5)
        self.block(23, next_y + 1, WIDTH - 12, details, 8)
        self.block(23, next_y + 6, WIDTH - 12, fiscal, 7.5, color=MUTED)
        self.set_y(y + height + 6)

    def payment(self):
        d = self.draft
        forms = {'01': 'Efectivo', '02': 'Cheque nominativo', '03': 'Transferencia electrónica de fondos',
                 '04': 'Tarjeta de crédito', '28': 'Tarjeta de débito', '99': 'Por definir'}
        methods = {'PUE': 'Pago en una sola exhibición', 'PPD': 'Pago en parcialidades o diferido'}
        entries = [('MONEDA', clean(d.currency), 30),
                   ('MÉTODO DE PAGO', clean(d.payment_method) + ' - ' + methods.get(d.payment_method, 'Consultar clave'), 71),
                   ('FORMA DE PAGO', clean(d.payment_form) + ' - ' + forms.get(d.payment_form, 'Consultar clave'), 75)]
        y, x, end = self.get_y(), MARGIN, self.get_y()
        for label, value, width in entries:
            self.label(x, y, width - 4, label)
            end = max(end, self.block(x, y + 5, width - 4, value, 7.5, height=4))
            x += width
        self.set_y(end + 8)

    def table_head(self):
        self.room(12)
        y, x = self.get_y(), MARGIN
        self.set_fill_color(*NAVY)
        self.rect(MARGIN, y, WIDTH, 8, style='F')
        for label, width in zip(('#', 'CONCEPTO', 'CANT.', 'PRECIO UNITARIO', 'IMPORTE'), COLS):
            self.font(6.8, True, (255, 255, 255))
            self.set_xy(x + 2, y + 1.5)
            self.cell(width - 4, 5, label, align='L' if label in ('#', 'CONCEPTO') else 'R')
            x += width
        self.set_y(y + 8)

    def items(self):
        self.table_head()
        for index, item in enumerate(self.draft.items or [], 1):
            lines = self.lines(item.description, COLS[1] - 6, 8.3)
            metadata = 'Clave SAT: ' + clean(item.sat_product_code) + '   |   Unidad: ' + clean(item.unit_code)
            if item.sku:
                metadata += '\nReferencia: ' + clean(item.sku)
            meta_lines = self.lines(metadata, COLS[1] - 6, 6.8)
            full_height = max(17, len(lines) * 4.2 + len(meta_lines) * 3.4 + 8)
            next_header_height = max(33, 26 + len(self.lines(self.draft.emitter_name, 123, 8, True)) * 4) + 8
            # Una partida normal pasa completa a la hoja siguiente; sólo se
            # divide cuando su descripción supera por sí sola una hoja.
            if full_height <= BOTTOM - next_header_height and self.get_y() + full_height > BOTTOM:
                self.add_page(); self.table_head()
            first = True
            while lines:
                meta_height = len(meta_lines) * 3.4
                if BOTTOM - self.get_y() < 20 + meta_height:
                    self.add_page(); self.table_head()
                capacity = max(1, int((BOTTOM - self.get_y() - 8 - meta_height) / 4.2))
                chunk, lines = lines[:capacity], lines[capacity:]
                row_height = max(17, len(chunk) * 4.2 + (meta_height if not lines else 3.4) + 8)
                y, x = self.get_y(), MARGIN
                self.set_fill_color(*(LIGHT if index % 2 == 0 else (255, 255, 255)))
                self.rect(MARGIN, y, WIDTH, row_height, style='F')
                self.fit(x + 1, y + 3, COLS[0] - 2, str(index), 7.2, align='L')
                x += COLS[0]
                for offset, line in enumerate(chunk):
                    self.block(x + 3, y + 3 + offset * 4.2, COLS[1] - 6, line, 8.3, height=4.2)
                meta_y = y + 4 + len(chunk) * 4.2
                self.block(x + 3, meta_y, COLS[1] - 6, '\n'.join(meta_lines) if not lines else '(El concepto continúa en la siguiente página)',
                           6.8, color=MUTED, height=3.4)
                x += COLS[1]
                for value, width in zip((quantity(item.quantity), unit_amount(item.unit_price), amount(item.line_subtotal)), COLS[2:]):
                    self.fit(x + 2, y + 3, width - 4, value if first else '', 8.3)
                    x += width
                self.set_draw_color(*LINE)
                self.set_line_width(.2)
                self.line(MARGIN, y + row_height, MARGIN + WIDTH, y + row_height)
                self.set_y(y + row_height)
                first = False
        if not self.draft.items:
            self.block(MARGIN + 3, self.get_y() + 4, WIDTH - 6, 'Sin conceptos registrados.', 8, color=MUTED)
            self.set_y(self.get_y() + 12)

    def totals_and_notes(self):
        d = self.draft
        self.set_y(self.get_y() + 8)
        grouped = {}
        for item in d.items or []:
            if item.line_taxes:
                key = quantity(Decimal(str(item.tax_rate or 0)) * 100)
                grouped[key] = grouped.get(key, Decimal(0)) + Decimal(str(item.line_taxes))
        tax_rows = [('IVA (' + rate + '%)', amount(value)) for rate, value in grouped.items()]
        if not tax_rows or sum(grouped.values()) != Decimal(str(d.taxes or 0)):
            tax_rows = [('Impuestos', amount(d.taxes))]
        rows = [('Subtotal', amount(d.subtotal))] + tax_rows
        self.room(29 + 7 * len(rows))
        y = self.get_y()
        notes_y = y
        x, width = 117, 76
        for label, value in rows:
            self.block(x + 3, y, 35, label, 8, color=MUTED)
            self.fit(x + 37, y, width - 40, value, 9)
            y += 7
        self.set_fill_color(*NAVY)
        self.rect(x, y + 1, width, 14, style='F')
        self.block(x + 4, y + 5, 27, 'TOTAL ' + clean(d.currency), 8, True, (255, 255, 255))
        self.fit(x + 29, y + 5, width - 33, amount(d.total), 14, True, (255, 255, 255))
        notes = self.lines(d.notes, 88, 7.5) if d.notes else []
        if len(notes) <= 7:
            if notes:
                self.label(MARGIN, notes_y, 88, 'REFERENCIA / OBSERVACIONES')
                self.block(MARGIN, notes_y + 6, 88, d.notes, 7.5, color=MUTED, height=4)
            self.set_y(y + 24)
        else:
            self.set_y(y + 24)
            self.label(MARGIN, self.get_y(), WIDTH, 'OBSERVACIONES')
            self.set_y(self.get_y() + 6)
            for line in self.lines(d.notes, WIDTH, 7.5):
                self.room(5)
                next_y = self.block(MARGIN, self.get_y(), WIDTH, line, 7.5, color=MUTED, height=4)
                self.set_y(next_y)
            self.set_y(self.get_y() + 5)
        if not self.stamped:
            notice = 'Documento preliminar - No es un CFDI.'
            if self.estimated:
                notice += ' El número se confirma al iniciar el timbrado y puede cambiar.'
            self.room(17)
            y = self.get_y()
            self.set_fill_color(*LIGHT)
            self.rect(MARGIN, y, WIDTH, 15, style='F')
            self.block(MARGIN + 4, y + 3, WIDTH - 8, notice, 7.5, color=MUTED, height=4)
        elif self.uuid_cfdi:
            self.room(12)
            self.block(MARGIN, self.get_y(), WIDTH, 'UUID: ' + self.uuid_cfdi, 8, color=MUTED)


def render_prefactura_pdf(draft, *, display_folio, estimated_folio=None, generated_at=None, uuid_cfdi=''):
    generated_at = generated_at or local_now()
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    generated_at = generated_at.astimezone(ZoneInfo('America/Mexico_City'))
    pdf = PrefacturaPDF(draft, estimated_folio or display_folio, bool(estimated_folio), generated_at, uuid_cfdi)
    pdf.add_page()
    pdf.recipient()
    pdf.payment()
    pdf.items()
    pdf.totals_and_notes()
    return bytes(pdf.output())
