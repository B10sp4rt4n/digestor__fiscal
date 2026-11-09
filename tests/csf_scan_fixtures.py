"""Reproducible fictitious CSFs. Every page says synthetic/no fiscal validity."""
import cv2
import fitz
import numpy as np

IDENTITY = """DOCUMENTO SINTETICO DE PRUEBA - SIN VALIDEZ FISCAL
CONSTANCIA DE SITUACION FISCAL
Datos de Identificacion del Contribuyente
RFC: AAA010101AAA
Denominación/Razón Social: EMPRESA SINTETICA DEMO
Nombre Comercial: DEMO SINTETICO
idCIF: 12345678901
Estatus en el padrón: ACTIVO
Datos del domicilio registrado
Código Postal: 01234
Tipo de Vialidad: CALLE
Nombre de Vialidad: REFORMA
Número Exterior: 123
Nombre de la Colonia: CENTRO
Nombre del Municipio o Demarcación Territorial: CUAUHTEMOC
Nombre de la Entidad Federativa: CIUDAD DE MEXICO
"""
REGIME = """DOCUMENTO SINTETICO DE PRUEBA - SIN VALIDEZ FISCAL
Regímenes
Régimen: General de Ley Personas Morales 01/01/2020
"""


def native_pdf(text=None):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((30, 40), text if text is not None else IDENTITY + REGIME, fontsize=11)
    data = doc.tobytes(); doc.close()
    return data


def scan_pdf(rotation=0, blur=0, unreadable=False, mixed=False, multiple=False, overlay=False, text_override=None):
    target = fitz.open()
    if mixed:
        source = fitz.open(stream=native_pdf(IDENTITY), filetype="pdf")
        target.insert_pdf(source); source.close()
    texts = [REGIME] if mixed else [IDENTITY, REGIME] if multiple else [IDENTITY + REGIME]
    if text_override is not None:
        texts = [text_override]
    for text in texts:
        source = fitz.open(stream=native_pdf(text), filetype="pdf")
        pix = source[0].get_pixmap(matrix=fitz.Matrix(2.5, 2.5), colorspace=fitz.csGRAY)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        if blur:
            image = cv2.GaussianBlur(image, (5, 5), blur)
        if unreadable:
            image = cv2.resize(image, (100, 140), interpolation=cv2.INTER_AREA)
            image = cv2.GaussianBlur(image, (5, 5), 2)
        if rotation:
            image = np.rot90(image, rotation // 90).copy()
        _, encoded = cv2.imencode(".png", image)
        height, width = image.shape
        page = target.new_page(width=width / 2.5, height=height / 2.5)
        page.insert_image(page.rect, stream=encoded.tobytes())
        if overlay:
            page.insert_text((5, 10), "SYNTHETIC OVERLAY", fontsize=6)
        source.close()
    data = target.tobytes(deflate=True); target.close()
    return data
