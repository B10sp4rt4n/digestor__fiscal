"""
Servicio de ingestión de PDFs y ZIPs con CSFs del SAT.
Extrae datos fiscales, genera hashes y valida el QR opcionalmente.
"""
import hashlib
import io
import os
import re
import ssl
import time
import zipfile
from typing import Any, Dict, Optional

import cv2
import fitz
import numpy as np
import requests
import urllib3
from pypdf import PdfReader
from requests.exceptions import SSLError as RequestsSSLError

from app.core.config import settings

# Caché simple en memoria para resultados de QR online
_qr_cache: Dict[str, Optional[bool]] = {}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_text_from_pdf(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _extract_qr_from_pdf(content: bytes) -> Optional[str]:
    """Rasteriza páginas del PDF y busca un QR decodificable."""
    detector = cv2.QRCodeDetector()
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
            data, _, _ = detector.detectAndDecode(img)
            if data:
                return _clean_field(data)

            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            for scale in (1.0, 1.5, 2.0):
                resized = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                data, _, _ = detector.detectAndDecode(resized)
                if data:
                    return _clean_field(data)
    finally:
        doc.close()

    return None


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _clean_field(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = _normalize_whitespace(value)
    value = re.sub(r"\s+:", ":", value)
    return value or None


def _extract_first(text: str, patterns: list[str], flags: int = 0) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return _clean_field(match.group(1))
    return None


def _parse_csf_fields(text: str) -> Dict[str, Any]:
    """Extrae campos clave de la CSF a partir del texto del PDF."""
    flat_text = _normalize_whitespace(text)
    upper_text = flat_text.upper()

    if "CONSTANCIA DE SITUACIÓN FISCAL" not in upper_text and "CONSTANCIA DE SITUACION FISCAL" not in upper_text:
        raise ValueError("El PDF no parece ser una constancia de situación fiscal del SAT.")

    rfc = _extract_first(
        flat_text,
        [
            r"\bRFC[:\s]*([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b",
            r"Registro Federal de Contribuyentes\s*([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b",
        ],
        re.IGNORECASE,
    )
    razon_social = _extract_first(
        text,
        [
            r"Registro Federal de Contribuyentes\s*([A-Z0-9Ñ&.,()\-\s]+?)\s*Nombre,\s*denominación o razón\s*social",
            r"Denominación/Razón Social:\s*([A-Z0-9Ñ&.,()\-\s]+?)\s*(?:Régimen Capital:|NombreComercial:|Nombre Comercial:|Fechainiciodeoperaciones:)",
        ],
        re.IGNORECASE | re.DOTALL,
    )
    regimen = _extract_first(
        flat_text,
        [
            r"Regímenes:\s*Régimen Fecha Inicio Fecha Fin\s*([A-ZÁÉÍÓÚÑa-z0-9\s]+?)\s+\d{2}/\d{2}/\d{4}",
            r"Régimen:\s*([A-ZÁÉÍÓÚÑa-z0-9\s]+?)\s*(?:CódigoPostal:|CURP:|NombreComercial:)",
        ],
        re.IGNORECASE,
    )
    cp = _extract_first(flat_text, [r"Código\s*Postal[:\s]*(\d{5})", r"CódigoPostal[:\s]*(\d{5})"], re.IGNORECASE)
    curp = _extract_first(flat_text, [r"CURP[:\s]*([A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]{2})"], re.IGNORECASE)
    id_cif = _extract_first(flat_text, [r"idCIF[:\s]*(\d+)"], re.IGNORECASE)

    qr_text = _extract_first(flat_text, [r"(https?://[^\s]+(?:verificacion|validador)[^\s]*)"], re.IGNORECASE)

    return {
        "rfc": rfc or "DESCONOCIDO",
        "razon_social": razon_social or "SIN NOMBRE",
        "regimen": regimen,
        "cp": cp,
        "curp": curp,
        "id_cif": id_cif,
        "qr_text": qr_text,
    }


def _validate_qr_format(qr_text: Optional[str]) -> Optional[bool]:
    """Validación local: verifica formato de URL del SAT."""
    if not qr_text:
        return None
    return bool(
        re.match(
            r"https?://([a-z0-9-]+\.)*sat\.gob\.mx/.+(validador|verificacion|qr)",
            qr_text,
            re.IGNORECASE,
        )
    )


def _validate_qr_online(qr_text: Optional[str]) -> Optional[bool]:
    """Intento de verificación HTTP al SAT con caché TTL en memoria."""
    if not qr_text:
        return None
    if qr_text in _qr_cache:
        return _qr_cache[qr_text]

    result: Optional[bool] = None
    for _ in range(settings.SAT_RETRIES):
        try:
            resp = requests.head(qr_text, timeout=settings.SAT_TIMEOUT, allow_redirects=True)
            if resp.status_code == 405:
                resp = requests.get(qr_text, timeout=settings.SAT_TIMEOUT, allow_redirects=True)
            result = resp.status_code < 400
            break
        except RequestsSSLError as exc:
            if settings.SAT_ALLOW_LEGACY_TLS and "dh key too small" in str(exc).lower():
                result = _validate_qr_online_legacy_tls(qr_text)
                break
            result = None
        except Exception:
            # Error de red/TLS/handshake: estado indeterminado, no inválido.
            result = None

    _qr_cache[qr_text] = result
    return result


def _validate_qr_online_legacy_tls(qr_text: str) -> Optional[bool]:
    """Fallback controlado para endpoints legacy con DH pequeño."""
    try:
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        http = urllib3.PoolManager(ssl_context=ctx)
        resp = http.request(
            "GET",
            qr_text,
            timeout=urllib3.Timeout(connect=settings.SAT_TIMEOUT, read=settings.SAT_TIMEOUT),
            redirect=True,
        )
        return resp.status < 400
    except Exception:
        return None


def validate_qr_status(qr_text: Optional[str], validate_online: bool = False) -> Dict[str, Optional[bool]]:
    qr_valid = _validate_qr_format(qr_text)
    qr_online = _validate_qr_online(qr_text) if validate_online else None
    return {"qr_valid": qr_valid, "qr_online": qr_online}


def process_pdf(content: bytes, company_id: str, validate_online: Optional[bool] = None) -> Dict[str, Any]:
    """Procesa un PDF de CSF y devuelve los datos extraídos."""
    text = _extract_text_from_pdf(content)
    fields = _parse_csf_fields(text)
    csf_hash = _sha256(content)

    if not fields.get("qr_text"):
        fields["qr_text"] = _extract_qr_from_pdf(content)

    qr_status = validate_qr_status(
        fields.get("qr_text"),
        validate_online=validate_online if validate_online is not None else settings.SAT_ONLINE_VALIDATION,
    )

    return {
        "company_id": company_id,
        "rfc": fields["rfc"],
        "razon_social": fields["razon_social"],
        "regimen": fields.get("regimen"),
        "cp": fields.get("cp"),
        "curp": fields.get("curp"),
        "id_cif": fields.get("id_cif"),
        "extracted_text": text,
        "csf_hash": csf_hash,
        "qr_text": fields.get("qr_text"),
        "qr_valid": qr_status.get("qr_valid"),
        "qr_online": qr_status.get("qr_online"),
    }


def process_zip(content: bytes, company_id: str, validate_online: Optional[bool] = None) -> list[Dict[str, Any]]:
    """Procesa un ZIP con múltiples PDFs de CSF."""
    results = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".pdf"):
                continue
            pdf_bytes = zf.read(name)
            try:
                data = process_pdf(pdf_bytes, company_id, validate_online=validate_online)
                data["filename"] = name
                results.append(data)
            except Exception as exc:
                results.append({
                    "filename": name,
                    "error": str(exc),
                    "company_id": company_id,
                })
    return results


def save_upload(content: bytes, filename: str) -> str:
    """Guarda el archivo en UPLOAD_DIR y retorna la ruta."""
    upload_dir = settings.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)

    # Sanitiza nombre para evitar path traversal y caracteres peligrosos.
    safe_name = os.path.basename(filename or "upload.pdf")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", safe_name)
    if not safe_name:
        safe_name = "upload.pdf"
    safe_name = f"{int(time.time())}_{safe_name}"

    dest = os.path.join(upload_dir, safe_name)
    with open(dest, "wb") as f:
        f.write(content)
    return dest
