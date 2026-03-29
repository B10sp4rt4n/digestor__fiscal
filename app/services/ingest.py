"""
Servicio de ingestión de PDFs y ZIPs con CSFs del SAT.
Extrae datos fiscales, genera hashes y valida el QR opcionalmente.

Parser híbrido:
  - Fast path: regex segmentado por secciones del documento CSF
  - Fallback:   Groq LLM (llama-3.1-8b-instant) con structured output via instructor
                cuando algún campo clave no se puede extraer con regex.
"""
import hashlib
import io
import logging
import os
import re
import ssl
import time
import unicodedata
import zipfile
from typing import Any, Dict, Optional

import cv2
import fitz
import numpy as np
import requests
import urllib3
from pydantic import BaseModel, Field
from pypdf import PdfReader
from requests.exceptions import SSLError as RequestsSSLError

from app.core.config import settings

logger = logging.getLogger(__name__)

# Caché simple en memoria para resultados de QR online
_qr_cache: Dict[str, Optional[bool]] = {}
_cp_geo_cache: Dict[str, Optional[Dict[str, Any]]] = {}
_address_geo_cache: Dict[str, Optional[Dict[str, Any]]] = {}


def _norm_geo_text(value: Optional[str]) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFD", value)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


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


def _clean_multiline_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    lines = []
    for raw_line in value.replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        line = re.sub(r"\s+:", ":", line)
        lines.append(line)
    return "\n".join(lines).strip() or None


def _extract_first(text: str, patterns: list[str], flags: int = 0) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return _clean_field(match.group(1))
    return None


def _normalize_label(label: str) -> str:
    normalized = unicodedata.normalize("NFD", label)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", "", normalized)
    return normalized


def _extract_colon_pairs(text: str) -> Dict[str, str]:
    """Extrae pares clave:valor incluso cuando vienen múltiples campos en la misma línea."""
    pairs: Dict[str, str] = {}
    for raw_line in text.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if ":" not in line:
            continue
        for match in re.finditer(
            r"([A-Za-zÁÉÍÓÚÑáéíóúñ0-9/().\- ]{2,90}):\s*([^:]+?)(?=(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ0-9/().\- ]{2,90}:)|$)",
            line,
        ):
            key = _normalize_label(match.group(1))
            value = _clean_field(match.group(2))
            if key and value and key not in pairs:
                pairs[key] = value
    return pairs


def _pick_from_pairs(pairs: Dict[str, str], aliases: list[str]) -> Optional[str]:
    for alias in aliases:
        value = pairs.get(_normalize_label(alias))
        if value:
            return value
    return None


def _build_crm_autofill(fields: Dict[str, Any], pairs: Dict[str, str]) -> Dict[str, str]:
    crm: Dict[str, str] = {
        "tax_id": fields.get("rfc") or "",
        "legal_name": fields.get("razon_social") or "",
        "trade_name": _pick_from_pairs(pairs, ["Nombre Comercial", "NombreComercial"]) or "",
        "tax_regime": fields.get("regimen") or "",
        "postal_code": fields.get("cp") or _pick_from_pairs(pairs, ["Código Postal", "CódigoPostal"]) or "",
        "curp": fields.get("curp") or "",
        "cif_id": fields.get("id_cif") or _pick_from_pairs(pairs, ["idCIF"]) or "",
        "status_padron": _pick_from_pairs(pairs, ["Estatus en el padrón", "Estatusenelpadrón"]) or "",
        "start_operations_date": _pick_from_pairs(
            pairs,
            ["Fecha de inicio de operaciones", "Fechainiciodeoperaciones"],
        ) or "",
        "last_status_change_date": _pick_from_pairs(
            pairs,
            ["Fecha de último cambio de estado", "Fechadeúltimocambiodeestado"],
        ) or "",
        "street_type": _pick_from_pairs(pairs, ["Tipo de Vialidad", "TipodeVialidad"]) or "",
        "street_name": _pick_from_pairs(pairs, ["Nombre de Vialidad", "NombredeVialidad"]) or "",
        "ext_number": _pick_from_pairs(pairs, ["Número Exterior", "NumeroExterior", "NúmeroExterior"]) or "",
        "int_number": _pick_from_pairs(pairs, ["Número Interior", "NumeroInterior", "NúmeroInterior"]) or "",
        "neighborhood": _pick_from_pairs(pairs, ["Nombre de la Colonia", "NombredelaColonia"]) or "",
        "locality": _pick_from_pairs(pairs, ["Nombre de la Localidad", "NombredelaLocalidad", "Nombre delaLocalidad"]) or "",
        "municipality": _pick_from_pairs(
            pairs,
            [
                "Nombre del Municipio o Demarcación Territorial",
                "NombredelMunicipiooDemarcaciónTerritorial",
                "Nombre delMunicipio oDemarcación Territorial",
                "NombredelMunicipiooDemarcacionTerritorial",
                "Nombre delMunicipio oDemarcacion Territorial",
                "CATALOGO Nombre del Municipio o Demarcación Territorial",
                "CATALOGO Nombre del Municipio o Demarcacion Territorial",
                "CATALOGONombre delMunicipio oDemarcación Territorial",
                "CATALOGONombre delMunicipio oDemarcacion Territorial",
            ],
        ) or "",
        "state": _pick_from_pairs(pairs, ["Nombre de la Entidad Federativa", "NombredelaEntidadFederativa"]) or "",
        "between_street": _pick_from_pairs(pairs, ["Entre Calle", "EntreCalle"]) or "",
        "and_street": _pick_from_pairs(pairs, ["Y Calle", "YCalle"]) or "",
        "qr_url": fields.get("qr_text") or "",
    }
    return {k: v for k, v in crm.items() if v}


def _lookup_cp_geolocation(cp: Optional[str]) -> Optional[Dict[str, Any]]:
    """Geolocaliza un código postal MX usando Zippopotam.us."""
    if not settings.GEO_CP_ENABLED:
        return None
    if not cp or not re.fullmatch(r"\d{5}", cp):
        return None
    if cp in _cp_geo_cache:
        return _cp_geo_cache[cp]

    try:
        resp = requests.get(f"https://api.zippopotam.us/mx/{cp}", timeout=settings.GEO_CP_TIMEOUT)
        if resp.status_code != 200:
            _cp_geo_cache[cp] = None
            return None
        payload = resp.json()
        places = payload.get("places") or []
        if not places:
            _cp_geo_cache[cp] = None
            return None

        place = places[0]
        latitude = place.get("latitude")
        longitude = place.get("longitude")
        try:
            latitude = float(latitude) if latitude is not None else None
            longitude = float(longitude) if longitude is not None else None
        except (TypeError, ValueError):
            latitude = None
            longitude = None

        geo = {
            "cp": cp,
            "country": "MX",
            "state": place.get("state"),
            "city": place.get("place name"),
            "latitude": latitude,
            "longitude": longitude,
            "source": "zippopotam",
            "confidence": 0.55,
        }
        _cp_geo_cache[cp] = geo
        return geo
    except Exception:
        _cp_geo_cache[cp] = None
        return None


def _lookup_address_geolocation(crm_autofill: Dict[str, str], cp: Optional[str]) -> Optional[Dict[str, Any]]:
    """Geocodifica usando el catálogo oficial INEGI (gaia.inegi.org.mx/wscatgeo, sin API key)."""
    if not settings.GEO_ADDRESS_ENABLED:
        return None

    municipality = (crm_autofill.get("municipality") or "").strip()
    state = (crm_autofill.get("state") or "").strip()
    locality = (crm_autofill.get("locality") or "").strip()
    postal_code = (crm_autofill.get("postal_code") or cp or "").strip()

    if not (municipality and state):
        return None

    cache_key = f"inegi|{municipality}|{state}|{locality}".lower()
    if cache_key in _address_geo_cache:
        return _address_geo_cache[cache_key]

    INEGI_BASE = "https://gaia.inegi.org.mx/wscatgeo"
    target_state_norm = _norm_geo_text(state)
    target_mun_norm = _norm_geo_text(municipality)
    target_loc_norm = _norm_geo_text(locality or municipality)

    def _name_match(lhs: str, rhs: str) -> bool:
        if not lhs or not rhs:
            return False
        if lhs == rhs or lhs in rhs or rhs in lhs:
            return True
        lhs_compact = lhs.replace(" ", "")
        rhs_compact = rhs.replace(" ", "")
        return lhs_compact == rhs_compact or lhs_compact in rhs_compact or rhs_compact in lhs_compact

    try:
        # 1. Buscar estado
        r_states = requests.get(
            f"{INEGI_BASE}/mgee/",
            timeout=settings.GEO_ADDRESS_TIMEOUT,
            headers={"User-Agent": settings.GEO_USER_AGENT},
        )
        if r_states.status_code != 200:
            _address_geo_cache[cache_key] = None
            return None

        best_state_code: Optional[str] = None
        best_state_score = 0.0
        for s in (r_states.json() or {}).get("datos") or []:
            s_name = _norm_geo_text(s.get("nom_agee") or "")
            if not s_name:
                continue
            if target_state_norm == s_name:
                score = 1.0
            elif _name_match(target_state_norm, s_name):
                score = 0.8
            else:
                continue
            if score > best_state_score:
                best_state_score = score
                best_state_code = s.get("cve_agee")

        if not best_state_code or best_state_score < 0.7:
            _address_geo_cache[cache_key] = None
            return None

        # 2. Buscar municipio dentro del estado
        r_muns = requests.get(
            f"{INEGI_BASE}/mgem/{best_state_code}",
            timeout=settings.GEO_ADDRESS_TIMEOUT,
            headers={"User-Agent": settings.GEO_USER_AGENT},
        )
        if r_muns.status_code != 200:
            _address_geo_cache[cache_key] = None
            return None

        best_mun_code: Optional[str] = None
        best_mun_score = 0.0
        for m in (r_muns.json() or {}).get("datos") or []:
            m_name = _norm_geo_text(m.get("nom_agem") or "")
            if not m_name:
                continue
            if target_mun_norm == m_name:
                score = 1.0
            elif _name_match(target_mun_norm, m_name):
                score = 0.8
            else:
                continue
            if score > best_mun_score:
                best_mun_score = score
                best_mun_code = m.get("cvegeo")

        if not best_mun_code or best_mun_score < 0.7:
            _address_geo_cache[cache_key] = None
            return None

        # 3. Buscar localidades del municipio
        r_locs = requests.get(
            f"{INEGI_BASE}/v2/localidades/{best_mun_code}",
            timeout=settings.GEO_ADDRESS_TIMEOUT,
            headers={"User-Agent": settings.GEO_USER_AGENT},
        )
        if r_locs.status_code != 200:
            _address_geo_cache[cache_key] = None
            return None

        locs_list = (r_locs.json() or {}).get("datos") or []
        if not locs_list:
            _address_geo_cache[cache_key] = None
            return None

        # Elegir localidad que mejor coincida; si no hay match usar cabecera municipal
        best_loc = None
        best_loc_score = 0.0
        for loc in locs_list:
            l_name = _norm_geo_text(loc.get("nomgeo") or "")
            if not l_name:
                continue
            if target_loc_norm == l_name:
                score = 1.0
            elif _name_match(target_loc_norm, l_name):
                score = 0.7
            else:
                continue
            if score > best_loc_score:
                best_loc_score = score
                best_loc = loc

        if not best_loc:
            # Usar cabecera municipal o primer registro
            for loc in locs_list:
                if str(loc.get("cve_loc") or "") == "0001":
                    best_loc = loc
                    break
            if not best_loc:
                best_loc = locs_list[0]
            best_loc_score = 0.5

        try:
            lat_f = float(best_loc.get("latitud"))
            lon_f = float(best_loc.get("longitud"))
        except (TypeError, ValueError):
            _address_geo_cache[cache_key] = None
            return None

        confidence = round(
            min(0.40 + best_state_score * 0.15 + best_mun_score * 0.30 + best_loc_score * 0.15, 0.98),
            3,
        )
        geo = {
            "cp": postal_code or None,
            "country": "MX",
            "state": state,
            "city": municipality,
            "locality": locality or None,
            "latitude": lat_f,
            "longitude": lon_f,
            "source": "inegi_oficial",
            "confidence": confidence,
        }
        _address_geo_cache[cache_key] = geo
        return geo

    except Exception as exc:
        logger.warning("INEGI geocoding falló: %s", exc)
        _address_geo_cache[cache_key] = None
        return None


def _resolve_geolocation(cp: Optional[str], crm_autofill: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Usa INEGI oficial; evita fallbacks por CP que pueden apuntar al municipio equivocado."""
    geo = _lookup_address_geolocation(crm_autofill, cp)
    if geo:
        return geo
    return None


# ---------------------------------------------------------------------------
# Structured output schema para Groq / instructor
# ---------------------------------------------------------------------------

class _CSFExtracted(BaseModel):
    rfc: Optional[str] = Field(None, description="RFC del contribuyente, ej. XAXX010101000")
    razon_social: Optional[str] = Field(None, description="Nombre completo o razón social del contribuyente")
    regimen: Optional[str] = Field(None, description="Régimen fiscal principal, ej. Régimen Simplificado de Confianza")
    cp: Optional[str] = Field(None, description="Código postal del domicilio fiscal, 5 dígitos")
    curp: Optional[str] = Field(None, description="CURP de 18 caracteres si aplica, o null")
    id_cif: Optional[str] = Field(None, description="Número idCIF de la constancia")
    qr_text: Optional[str] = Field(None, description="URL completa del QR de verificación del SAT")


class _CSFFormattedText(BaseModel):
    formatted_text: str = Field(
        ...,
        description="Texto completo de la CSF reestructurado en bloques legibles sin inventar datos.",
    )


class _FieldSuggestion(BaseModel):
    field: str
    current_value: Optional[str] = None
    suggested_value: Optional[str] = None
    needs_correction: bool = False
    reason: Optional[str] = None
    confidence: float = 0.0


class _FieldSuggestionResponse(BaseModel):
    suggestions: list[_FieldSuggestion] = Field(default_factory=list)


_groq_client = None


def _get_groq_client():
    """Inicializa el cliente instructor+groq de forma lazy."""
    global _groq_client
    if _groq_client is not None:
        return _groq_client
    if not settings.GROQ_API_KEY:
        return None
    try:
        import instructor
        from groq import Groq
        _groq_client = instructor.from_groq(Groq(api_key=settings.GROQ_API_KEY))
        return _groq_client
    except Exception as exc:
        logger.warning("No se pudo inicializar cliente Groq: %s", exc)
        return None


def _parse_csf_via_groq(text: str) -> Optional[_CSFExtracted]:
    """Llama a Groq con structured output para extraer campos de la CSF."""
    client = _get_groq_client()
    if client is None:
        return None
    # Limitar texto a ~3000 chars para reducir tokens
    snippet = text[:3000]
    try:
        result = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            response_model=_CSFExtracted,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un extractor de datos fiscales mexicanos. "
                        "Del texto de una Constancia de Situación Fiscal del SAT, "
                        "extrae los campos solicitados con exactitud. "
                        "Devuelve null en campos que no encuentres. "
                        "No inventes datos."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Extrae los datos fiscales del siguiente texto:\n\n{snippet}",
                },
            ],
            max_tokens=512,
        )
        return result
    except Exception as exc:
        logger.warning("Groq fallback falló: %s", exc)
        return None


def _format_csf_text_via_groq(text: str) -> Optional[str]:
    """Reestructura el texto completo del PDF usando Groq sin alterar el contenido factual."""
    client = _get_groq_client()
    if client is None:
        return None

    snippet = text[:6000]
    try:
        result = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            response_model=_CSFFormattedText,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un normalizador de texto de Constancias de Situación Fiscal del SAT. "
                        "Tu tarea es reestructurar TODO el texto recibido para hacerlo legible. "
                        "No resumas. No omitas contenido. No inventes. "
                        "Solo corrige espacios, saltos de línea, etiquetas pegadas y secciones. "
                        "Mantén todos los datos fiscales y devuelve texto plano."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Reorganiza el siguiente texto completo del PDF en formato legible por secciones, "
                        "separando etiquetas y valores sin perder información:\n\n"
                        f"{snippet}"
                    ),
                },
            ],
            max_tokens=1200,
        )
        return _clean_multiline_text(result.formatted_text)
    except Exception as exc:
        logger.warning("Groq fallback para texto completo falló: %s", exc)
        return None


def _suggest_field_corrections_via_groq(
    crm_autofill: Dict[str, str],
    extracted_text: str,
) -> list[Dict[str, Any]]:
    """Sugiere correcciones por campo con IA usando llamada directa a Groq (sin instructor)."""
    if not settings.AI_FIELD_CORRECTION_ENABLED:
        return []

    if not settings.GROQ_API_KEY:
        return []

    fields_payload = [
        {"field": k, "current_value": v}
        for k, v in crm_autofill.items()
        if v
    ]
    if not fields_payload:
        return []

    import json as _json

    snippet = extracted_text[:5000]
    prompt = (
        "Eres un validador de calidad de datos fiscales de México.\n"
        "Recibirás campos extraídos de una Constancia de Situación Fiscal (CSF) del SAT.\n"
        "Tu tarea: sugerir correcciones SOLO cuando haya error evidente de OCR o texto pegado "
        "(ej. 'INGNACIO' → 'IGNACIO', 'CIUDADDEMEXICO' → 'CIUDAD DE MEXICO').\n"
        "No cambies valores correctos. No inventes datos.\n\n"
        "Devuelve ÚNICAMENTE un JSON válido con este formato exacto (sin texto adicional):\n"
        '[\n'
        '  {"field": "nombre_campo", "current_value": "valor_actual", '
        '"suggested_value": "valor_sugerido", "reason": "razón breve", "confidence": 0.9},\n'
        '  ...\n'
        ']\n'
        "Si no hay correcciones que hacer, devuelve: []\n\n"
        f"Campos actuales:\n{_json.dumps(fields_payload, ensure_ascii=False)}\n\n"
        f"Texto fuente (recortado):\n{snippet}"
    )

    try:
        from groq import Groq
        raw_client = Groq(api_key=settings.GROQ_API_KEY)
        response = raw_client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.1,
        )
        raw_text = response.choices[0].message.content or ""
        # Extraer bloque JSON de la respuesta
        json_match = re.search(r"\[.*\]", raw_text, re.DOTALL)
        if not json_match:
            return []
        suggestions = _json.loads(json_match.group(0))
        output: list[Dict[str, Any]] = []
        for item in suggestions:
            if not isinstance(item, dict):
                continue
            output.append({
                "field": str(item.get("field", "")),
                "current_value": str(item.get("current_value", "")),
                "suggested_value": str(item.get("suggested_value", "")),
                "reason": str(item.get("reason", "")),
                "confidence": round(float(item.get("confidence", 0)), 3),
            })
        return output
    except Exception as exc:
        logger.warning("Corrector IA por campo falló: %s", exc)
        return []


def _suggest_field_corrections_via_openai(
    crm_autofill: Dict[str, str],
    extracted_text: str,
) -> list[Dict[str, Any]]:
    """Valida los campos individualmente con OpenAI y devuelve sugerencias de corrección."""
    if not settings.AI_FIELD_VALIDATION_ENABLED:
        return []
    if not settings.OPENAI_API_KEY:
        return []

    fields_payload = [
        {"field": key, "current_value": value}
        for key, value in crm_autofill.items()
        if value
    ]
    if not fields_payload:
        return []

    import json as _json

    context_text = format_extracted_csf_text(extracted_text) or extracted_text
    snippet = context_text[:7000]

    prompt = (
        "Eres un auditor de calidad de datos fiscales mexicanos.\n"
        "Debes revisar CADA campo del payload de forma independiente usando como única fuente de verdad el texto de una CSF SAT.\n"
        "Corrige solo errores evidentes de OCR, texto pegado, truncado o pérdida de espacios.\n"
        "No inventes datos y no cambies campos correctos.\n"
        "Ejemplos válidos de corrección: 'SANFRANCISCO' -> 'SAN FRANCISCO', '28DENOVIEMBRE DE1996' -> '28 DE NOVIEMBRE DE 1996', 'EMILIANO' -> 'EMILIANO ZAPATA' si el texto lo confirma.\n\n"
        "Devuelve ÚNICAMENTE un JSON válido con esta forma exacta:\n"
        '{"suggestions":[{"field":"...","current_value":"...","suggested_value":"...","needs_correction":true,"reason":"...","confidence":0.91}]}\n'
        "Incluye un objeto por cada campo del payload revisado.\n"
        "Si un campo está correcto, pon needs_correction=false y suggested_value igual al valor actual.\n\n"
        f"Campos a revisar:\n{_json.dumps(fields_payload, ensure_ascii=False)}\n\n"
        f"Texto fuente:\n{snippet}"
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "Valida campos fiscales uno por uno y responde solo JSON.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.1,
        )
        raw_text = response.choices[0].message.content or "{}"
        payload = _json.loads(raw_text)
        suggestions = payload.get("suggestions") or []
        output: list[Dict[str, Any]] = []
        for item in suggestions:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "").strip()
            current_value = str(item.get("current_value") or "").strip()
            suggested_value = str(item.get("suggested_value") or "").strip()
            needs_correction = bool(item.get("needs_correction"))
            reason = str(item.get("reason") or "").strip()
            try:
                confidence = round(float(item.get("confidence") or 0.0), 3)
            except (TypeError, ValueError):
                confidence = 0.0
            if not field:
                continue
            if needs_correction and suggested_value and suggested_value != current_value:
                output.append({
                    "field": field,
                    "current_value": current_value,
                    "suggested_value": suggested_value,
                    "reason": reason,
                    "confidence": confidence,
                })
        return output
    except Exception as exc:
        logger.warning("OpenAI field-by-field validation falló: %s", exc)
        return []


def _suggest_field_corrections(
    crm_autofill: Dict[str, str],
    extracted_text: str,
) -> list[Dict[str, Any]]:
    """Usa OpenAI como validador primario y Groq como fallback."""
    suggestions = _suggest_field_corrections_via_openai(crm_autofill, extracted_text)
    if suggestions:
        return suggestions
    return _suggest_field_corrections_via_groq(crm_autofill, extracted_text)


def _build_corrected_json(
    crm_autofill: Dict[str, str],
    ai_field_corrections: list[Dict[str, Any]],
    min_confidence: Optional[float] = None,
) -> Dict[str, str]:
    """Aplica sugerencias IA de confianza suficiente sobre el payload CRM y retorna JSON corregido."""
    corrected: Dict[str, str] = dict(crm_autofill or {})
    if not ai_field_corrections:
        return corrected

    if min_confidence is not None:
        threshold = float(min_confidence)
    elif settings.AI_FIELD_VALIDATION_ENABLED:
        threshold = float(settings.AI_FIELD_VALIDATION_MIN_CONFIDENCE)
    else:
        threshold = float(settings.AI_FIELD_CORRECTION_MIN_CONFIDENCE)
    for suggestion in ai_field_corrections:
        if not isinstance(suggestion, dict):
            continue
        field = (suggestion.get("field") or "").strip()
        suggested_value = suggestion.get("suggested_value")
        if not field or suggested_value is None:
            continue
        try:
            confidence = float(suggestion.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < threshold:
            continue
        value = str(suggested_value).strip()
        if value:
            corrected[field] = value

    return corrected


def _split_sections(text: str) -> Dict[str, str]:
    """
    Segmenta el texto del CSF en secciones nominadas.
    Las secciones del SAT son fijas: encabezado, datos del contribuyente,
    domicilio fiscal y regímenes.
    """
    section_markers = [
        ("contribuyente", r"Datos del Contribuyente|Información del Contribuyente"),
        ("domicilio", r"Domicilio Fiscal|Domicilio"),
        ("regimenes", r"Regímenes|Régimen Fiscal"),
        ("actividades", r"Actividades Económicas|Actividad Económica"),
    ]
    sections: Dict[str, str] = {"header": text}
    remaining = text
    for name, pattern in section_markers:
        match = re.search(pattern, remaining, re.IGNORECASE)
        if match:
            sections["header"] = remaining[: match.start()]
            remaining = remaining[match.start():]
            sections[name] = remaining
    return sections


def format_extracted_csf_text(text: Optional[str]) -> str:
    """Reorganiza el texto completo extraído del PDF para hacerlo legible por secciones."""
    if not text:
        return ""

    original_text = text
    formatted = text.replace("\r", "\n")
    formatted = re.sub(r"[ \t]+", " ", formatted)

    # Corrige etiquetas frecuentes que el PDF del SAT suele pegar sin espacios.
    glued_labels = {
        r"Fechainiciodeoperaciones:": "Fecha de inicio de operaciones:",
        r"Estatusenelpadrón:": "Estatus en el padrón:",
        r"Fechadeúltimocambiodeestado:": "Fecha de último cambio de estado:",
        r"Fechadeultimocambiodeestado:": "Fecha de último cambio de estado:",
        r"CódigoPostal:": "Código Postal:",
        r"CodigoPostal:": "Código Postal:",
        r"TipodeVialidad:": "Tipo de Vialidad:",
        r"NombredeVialidad:": "Nombre de Vialidad:",
        r"NúmeroExterior:": "Número Exterior:",
        r"NumeroExterior:": "Número Exterior:",
        r"NúmeroInterior:": "Número Interior:",
        r"NumeroInterior:": "Número Interior:",
        r"NombredelaColonia:": "Nombre de la Colonia:",
        r"NombredelaLocalidad:": "Nombre de la Localidad:",
        r"Nombre delaLocalidad:": "Nombre de la Localidad:",
        r"NombredelMunicipiooDemarcaciónTerritorial:": "Nombre del Municipio o Demarcación Territorial:",
        r"NombredelMunicipiooDemarcacionTerritorial:": "Nombre del Municipio o Demarcación Territorial:",
        r"Nombre delMunicipio oDemarcación Territorial:": "Nombre del Municipio o Demarcación Territorial:",
        r"Nombre delMunicipio oDemarcacion Territorial:": "Nombre del Municipio o Demarcación Territorial:",
        r"CATALOGONombre delMunicipio oDemarcación Territorial:": "CATALOGO Nombre del Municipio o Demarcación Territorial:",
        r"CATALOGONombre delMunicipio oDemarcacion Territorial:": "CATALOGO Nombre del Municipio o Demarcación Territorial:",
        r"NombredelaEntidadFederativa:": "Nombre de la Entidad Federativa:",
        r"EntreCalle:": "Entre Calle:",
        r"YCalle:": "Y Calle:",
        r"NombreComercial:": "Nombre Comercial:",
        r"Denominación/RazónSocial:": "Denominación/Razón Social:",
        r"Denominacion/RazonSocial:": "Denominación/Razón Social:",
    }
    for source, target in glued_labels.items():
        formatted = re.sub(source, target, formatted, flags=re.IGNORECASE)

    # Separa etiquetas encadenadas que a veces vienen pegadas tras otro valor.
    chained_labels = [
        r"RFC:",
        r"Denominación/Razón Social:",
        r"Régimen Capital:",
        r"Nombre Comercial:",
        r"Fecha de inicio de operaciones:",
        r"Estatus en el padrón:",
        r"Fecha de último cambio de estado:",
        r"Código Postal:",
        r"Tipo de Vialidad:",
        r"Nombre de Vialidad:",
        r"Número Exterior:",
        r"Número Interior:",
        r"Nombre de la Colonia:",
        r"Nombre de la Localidad:",
        r"Nombre del Municipio o Demarcación Territorial:",
        r"Nombre de la Entidad Federativa:",
        r"Entre Calle:",
        r"Y Calle:",
        r"CURP:",
        r"idCIF:",
    ]
    for label in chained_labels:
        formatted = re.sub(rf"(?<!\n)\s*({label})", rf"\n\1", formatted, flags=re.IGNORECASE)

    # Corrige textos donde una etiqueta queda pegada al cierre de otra oración.
    formatted = re.sub(
        r"CATALOGO\s*Nombre del Municipio o Demarcación Territorial:",
        "CATALOGO\nNombre del Municipio o Demarcación Territorial:",
        formatted,
        flags=re.IGNORECASE,
    )

    headers = [
        r"CÉDULA DE IDENTIFICACIÓN FISCAL",
        r"CONSTANCIA DE SITUACIÓN FISCAL",
        r"Datos de Identificación del Contribuyente:?",
        r"Datos del domicilio registrado",
        r"Actividades Económicas:?",
        r"Regímenes:?",
        r"Obligaciones:?",
    ]
    for header in headers:
        formatted = re.sub(rf"\s*({header})\s*", rf"\n\n\1\n", formatted, flags=re.IGNORECASE)

    inline_labels = [
        r"RFC:",
        r"Denominación/Razón Social:",
        r"Nombre, denominación o razón social",
        r"Régimen Capital:",
        r"NombreComercial:",
        r"Nombre Comercial:",
        r"Fechainiciodeoperaciones:",
        r"Fecha de inicio de operaciones:",
        r"Estatus en el padrón:",
        r"Fecha de último cambio de estado:",
        r"Código Postal:",
        r"CódigoPostal:",
        r"Tipo de Vialidad:",
        r"Nombre de Vialidad:",
        r"Número Exterior:",
        r"Número Interior:",
        r"Nombre de la Colonia:",
        r"Nombre de la Localidad:",
        r"Nombre del Municipio o Demarcación Territorial:",
        r"Nombre de la Entidad Federativa:",
        r"Entre Calle:",
        r"Y Calle:",
        r"CURP:",
        r"idCIF:",
        r"QR:",
    ]
    for label in inline_labels:
        formatted = re.sub(rf"\s*({label})\s*", rf"\n\1 ", formatted, flags=re.IGNORECASE)

    formatted = re.sub(r"(?<!\n)(https?://)", r"\n\1", formatted)
    formatted = re.sub(r"\n{3,}", "\n\n", formatted)

    cleaned_lines: list[str] = []
    for raw_line in formatted.split("\n"):
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        cleaned_lines.append(line)

    formatted = "\n".join(cleaned_lines).strip()

    # Si todavía quedan muchas etiquetas pegadas o señales de OCR/PDF mal cortado,
    # usar Groq para reestructurar el texto completo sin resumirlo.
    needs_llm_cleanup = any(
        marker in original_text
        for marker in (
            "Fechainiciode",
            "Estatusenel",
            "CódigoPostal:",
            "CodigoPostal:",
            "TipodeVialidad:",
            "NombredeVialidad:",
            "Nombredela",
            "CATALOGONombre",
            "Nombre delMunicipio",
        )
    )
    if needs_llm_cleanup and settings.GROQ_PARSER_ENABLED:
        groq_formatted = _format_csf_text_via_groq(formatted)
        if groq_formatted:
            return groq_formatted

    return formatted


def _parse_csf_fields(text: str) -> Dict[str, Any]:
    """
    Extrae campos clave de la CSF.

    Estrategia híbrida:
    1. Fast path: regex segmentado por secciones del documento SAT.
    2. Fallback:  Groq LLM con structured output cuando faltan RFC o razón social.
    """
    flat_text = _normalize_whitespace(text)
    upper_text = flat_text.upper()

    if "CONSTANCIA DE SITUACIÓN FISCAL" not in upper_text and "CONSTANCIA DE SITUACION FISCAL" not in upper_text:
        raise ValueError("El PDF no parece ser una constancia de situación fiscal del SAT.")

    sections = _split_sections(flat_text)
    colon_pairs = _extract_colon_pairs(text)
    contrib_text = sections.get("contribuyente", flat_text)
    domicilio_text = sections.get("domicilio", flat_text)
    regimenes_text = sections.get("regimenes", flat_text)
    parser_source = "regex"

    # --- RFC ---
    rfc = _extract_first(
        contrib_text,
        [
            r"\bRFC[:\s]*([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b",
            r"Registro Federal de Contribuyentes\s*([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b",
        ],
        re.IGNORECASE,
    )
    if not rfc:
        rfc = _extract_first(flat_text, [r"\b([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b"], re.IGNORECASE)
    if not rfc:
        rfc = _pick_from_pairs(colon_pairs, ["RFC"])

    # --- Razón social ---
    razon_social = _extract_first(
        contrib_text,
        [
            r"Nombre,\s*denominación o razón social\s*([A-Z0-9Ñ&.,() \-]+?)(?=\s{2,}|\n|CURP|RFC)",
            r"Denominación/Razón Social:\s*([A-Z0-9Ñ&.,() \-]+?)(?=\s{2,}|\n|Régimen|Nombre Comercial)",
            r"Registro Federal de Contribuyentes\s*[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\s+([A-Z0-9Ñ&.,() \-]+?)\s+Nombre,",
        ],
        re.IGNORECASE | re.DOTALL,
    )
    if not razon_social:
        razon_social = _pick_from_pairs(colon_pairs, ["Denominación/Razón Social", "Nombre, denominación o razón social"])

    # --- Régimen fiscal (en sección de regímenes) ---
    regimen = _extract_first(
        regimenes_text,
        [
            r"(?:Régimen|Regimen)[:\s]*([A-ZÁÉÍÓÚÑa-záéíóúñ0-9 ,]+?)\s+\d{2}/\d{2}/\d{4}",
            r"Régimen:\s*([A-ZÁÉÍÓÚÑa-záéíóúñ0-9 ,]+?)(?:\s{2,}|\n|Fecha)",
        ],
        re.IGNORECASE,
    )
    if not regimen:
        regimen = _extract_first(
            flat_text,
            [r"Régimen Simplificado de Confianza|Régimen de Sueldos y Salarios|Régimen de Actividades Empresariales"],
            re.IGNORECASE,
        )
    if not regimen:
        regimen = _pick_from_pairs(colon_pairs, ["Régimen Capital", "Régimen"])
    if regimen:
        regimen = re.sub(r"^(?:Fecha\s+Inicio\s+Fecha\s+Fin\s*)+", "", regimen, flags=re.IGNORECASE).strip()

    # --- Código Postal (en sección domicilio) ---
    cp = _extract_first(
        domicilio_text,
        [r"Código\s*Postal[:\s]*(\d{5})", r"CódigoPostal[:\s]*(\d{5})", r"\b(\d{5})\b"],
        re.IGNORECASE,
    )
    if not cp:
        cp_value = _pick_from_pairs(colon_pairs, ["Código Postal", "CódigoPostal"])
        cp_match = re.search(r"\b(\d{5})\b", cp_value or "")
        if cp_match:
            cp = cp_match.group(1)

    # --- CURP ---
    curp = _extract_first(
        contrib_text,
        [r"CURP[:\s]*([A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]{2})"],
        re.IGNORECASE,
    )
    if not curp:
        curp = _extract_first(flat_text, [r"\b([A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]{2})\b"])
    if not curp:
        curp = _pick_from_pairs(colon_pairs, ["CURP"])

    # --- idCIF ---
    id_cif = _extract_first(flat_text, [r"idCIF[:\s]*(\d+)"], re.IGNORECASE)
    if not id_cif:
        id_cif = _pick_from_pairs(colon_pairs, ["idCIF"])

    # --- QR en texto ---
    qr_text = _extract_first(flat_text, [r"(https?://[^\s]+(?:verificacion|validador|qr)[^\s]*)"], re.IGNORECASE)
    if not qr_text:
        qr_text = _pick_from_pairs(colon_pairs, ["QR"])

    # ---------------------------------------------------------------------------
    # Fallback Groq: si faltan campos críticos y el parser está habilitado
    # ---------------------------------------------------------------------------
    missing_critical = not rfc or rfc == "DESCONOCIDO" or not razon_social
    if missing_critical and settings.GROQ_PARSER_ENABLED:
        logger.info("Parser regex incompleto (rfc=%s, razon_social=%s). Usando Groq fallback.", rfc, razon_social)
        groq_result = _parse_csf_via_groq(text)
        if groq_result:
            parser_source = "hybrid_groq"
            rfc = rfc or groq_result.rfc
            razon_social = razon_social or groq_result.razon_social
            regimen = regimen or groq_result.regimen
            cp = cp or groq_result.cp
            curp = curp or groq_result.curp
            id_cif = id_cif or groq_result.id_cif
            qr_text = qr_text or groq_result.qr_text

    return {
        "rfc": rfc or "DESCONOCIDO",
        "razon_social": razon_social or "SIN NOMBRE",
        "regimen": regimen,
        "cp": cp,
        "curp": curp,
        "id_cif": id_cif,
        "qr_text": qr_text,
        "parser_source": parser_source,
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
    colon_pairs = _extract_colon_pairs(text)
    crm_autofill = _build_crm_autofill(fields, colon_pairs)
    geolocation = _resolve_geolocation(fields.get("cp"), crm_autofill)
    if geolocation:
        if geolocation.get("latitude") is not None:
            crm_autofill.setdefault("geo_latitude", str(geolocation["latitude"]))
        if geolocation.get("longitude") is not None:
            crm_autofill.setdefault("geo_longitude", str(geolocation["longitude"]))
        if geolocation.get("city"):
            crm_autofill.setdefault("geo_city", geolocation["city"])
        if geolocation.get("state"):
            crm_autofill.setdefault("geo_state", geolocation["state"])
    ai_field_corrections = _suggest_field_corrections(crm_autofill, text)
    corrected_json = _build_corrected_json(crm_autofill, ai_field_corrections)
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
        "parser_source": fields.get("parser_source", "regex"),
        "crm_autofill": crm_autofill,
        "geolocation": geolocation,
        "ai_field_corrections": ai_field_corrections,
        "corrected_json": corrected_json,
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
                data["_pdf_bytes"] = pdf_bytes
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
