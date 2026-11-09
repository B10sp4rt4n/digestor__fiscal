"""
Endpoint de validación fiscal de receptor — sin autenticación requerida.
Valida RFC, CP, régimen, y compatibilidad régimen/tipo-persona/uso CFDI
contra catálogos SAT actualizados a CFDI 4.0.
"""

import re
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(tags=["Receptor Validation"])

# ---------------------------------------------------------------------------
# Catálogos SAT CFDI 4.0
# ---------------------------------------------------------------------------

from app.services.fiscal_catalogs import REGIMENES as _REGIMENES

from app.services.fiscal_catalogs import USOS_CFDI as _USOS_CFDI

# ---------------------------------------------------------------------------
# Helpers de validación
# ---------------------------------------------------------------------------

def _rfc_tipo(rfc: str) -> str | None:
    """Devuelve 'fisica', 'moral' o None si el formato no es válido."""
    rfc = rfc.upper().strip()
    # Moral: 3 letras + 6 dígitos fecha + 3 homoclave = 12 chars
    if re.match(r"^[A-ZÑ&]{3}\d{6}[A-Z0-9]{3}$", rfc):
        return "moral"
    # Física: 4 letras + 6 dígitos fecha + 3 homoclave = 13 chars
    if re.match(r"^[A-ZÑ&]{4}\d{6}[A-Z0-9]{3}$", rfc):
        return "fisica"
    return None


def _rfc_generico(rfc: str) -> bool:
    """RFC genéricos del SAT (XAXX010101000, XEXX010101000)."""
    return rfc.upper() in {"XAXX010101000", "XEXX010101000"}


def _validate_rfc(rfc: str) -> tuple[bool, str, str | None]:
    """Retorna (valid, mensaje, tipo)."""
    rfc = (rfc or "").strip().upper()
    if not rfc:
        return False, "RFC es requerido.", None
    if _rfc_generico(rfc):
        return True, "RFC genérico válido (público en general / extranjero).", "fisica"
    tipo = _rfc_tipo(rfc)
    if tipo is None:
        return False, "Formato de RFC inválido. Debe tener 12 caracteres (moral) o 13 (física) con el patrón correcto.", None
    return True, f"RFC válido — persona {tipo}.", tipo


def _validate_cp(cp: str) -> tuple[bool, str]:
    cp = (cp or "").strip()
    if not cp:
        return False, "Código Postal es requerido."
    if not re.match(r"^\d{5}$", cp):
        return False, "El CP debe tener exactamente 5 dígitos numéricos."
    n = int(cp)
    if n < 1000 or n > 99999:
        return False, "CP fuera del rango válido para México."
    return True, "CP válido."


def _validate_regimen(regimen: str, tipo_persona: str | None) -> tuple[bool, str]:
    regimen = (regimen or "").strip()
    if not regimen:
        return False, "Régimen Fiscal es requerido."
    if regimen not in _REGIMENES:
        codigos = ", ".join(sorted(_REGIMENES.keys()))
        return False, f"Código de régimen '{regimen}' no existe en catálogo SAT. Valores válidos: {codigos}"
    desc, aplica_fisica, aplica_moral = _REGIMENES[regimen]
    if tipo_persona == "fisica" and not aplica_fisica:
        return False, f"El régimen '{regimen} — {desc}' no aplica para personas físicas."
    if tipo_persona == "moral" and not aplica_moral:
        return False, f"El régimen '{regimen} — {desc}' no aplica para personas morales."
    return True, f"Régimen válido: {regimen} — {desc}."


def _validate_uso_cfdi(uso: str, tipo_persona: str | None, regimen: str | None) -> tuple[bool, str]:
    uso = (uso or "").strip()
    if not uso:
        return False, "Uso CFDI es requerido."
    if uso not in _USOS_CFDI:
        return False, f"Código de Uso CFDI '{uso}' no existe en catálogo SAT."
    desc, aplica_fisica, aplica_moral, regimenes_ok = _USOS_CFDI[uso]
    if tipo_persona == "fisica" and not aplica_fisica:
        return False, f"El uso CFDI '{uso} — {desc}' no aplica para personas físicas."
    if tipo_persona == "moral" and not aplica_moral:
        return False, f"El uso CFDI '{uso} — {desc}' no aplica para personas morales."
    if regimen and regimenes_ok is not None and regimen not in regimenes_ok:
        return False, (
            f"El uso CFDI '{uso} — {desc}' no es compatible con el régimen '{regimen}'. "
            f"Regímenes aceptados: {', '.join(regimenes_ok)}"
        )
    return True, f"Uso CFDI válido: {uso} — {desc}."


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class ReceptorValidateRequest(BaseModel):
    rfc: str
    nombre: str | None = None
    cp: str
    regimen: str
    uso_cfdi: str | None = None


class FieldResult(BaseModel):
    valid: bool
    message: str


class PacCheckResult(BaseModel):
    available: bool
    rfc_active: bool | None = None   # None = no se pudo determinar
    error_code: str | None = None
    message: str


class ReceptorValidateResponse(BaseModel):
    valid: bool                        # True solo si TODOS los campos obligatorios pasan
    tipo_persona: str | None = None   # "fisica" | "moral" | None
    fields: dict[str, FieldResult]
    pac: PacCheckResult | None = None  # presente solo si se solicitó pac_check
    summary: str


# ---------------------------------------------------------------------------
# Endpoint — sin autenticación (público)
# ---------------------------------------------------------------------------

@router.post(
    "/v1/receptor/validate",
    response_model=ReceptorValidateResponse,
    summary="Validar datos fiscales de receptor (sin login)",
    description=(
        "Valida RFC, CP, régimen fiscal y uso CFDI contra catálogos SAT CFDI 4.0. "
        "Con pac_check=true envía un CFDI de prueba al sandbox del PAC para verificar "
        "si el RFC existe y está activo en el padrón del SAT. "
        "No requiere autenticación. No persiste ningún dato."
    ),
)
def validate_receptor(
    req: ReceptorValidateRequest,
    pac_check: bool = Query(default=False, description="Verificar RFC contra padrón SAT via PAC sandbox"),
) -> ReceptorValidateResponse:
    results: dict[str, FieldResult] = {}

    # 1. RFC
    rfc_ok, rfc_msg, tipo_persona = _validate_rfc(req.rfc)
    results["rfc"] = FieldResult(valid=rfc_ok, message=rfc_msg)

    # 2. Nombre (solo presencia)
    if req.nombre is not None:
        nombre_ok = bool((req.nombre or "").strip())
        results["nombre"] = FieldResult(
            valid=nombre_ok,
            message="Nombre / Razón Social presente." if nombre_ok else "El nombre no puede estar vacío.",
        )

    # 3. CP
    cp_ok, cp_msg = _validate_cp(req.cp)
    results["cp"] = FieldResult(valid=cp_ok, message=cp_msg)

    # 4. Régimen
    reg_ok, reg_msg = _validate_regimen(req.regimen, tipo_persona if rfc_ok else None)
    results["regimen"] = FieldResult(valid=reg_ok, message=reg_msg)

    # 5. Uso CFDI (opcional — si se envía, se valida)
    if req.uso_cfdi is not None:
        uso_ok, uso_msg = _validate_uso_cfdi(
            req.uso_cfdi,
            tipo_persona if rfc_ok else None,
            req.regimen if reg_ok else None,
        )
        results["uso_cfdi"] = FieldResult(valid=uso_ok, message=uso_msg)

    # 6. Verificación PAC sandbox (nivel 2 — opcional)
    pac_result: PacCheckResult | None = None
    if pac_check and rfc_ok and cp_ok and reg_ok:
        from app.services.timbracfdi_client import validate_rfc_via_pac
        raw = validate_rfc_via_pac(
            rfc=req.rfc,
            nombre=req.nombre or req.rfc,
            cp=req.cp,
            regimen=req.regimen,
        )
        pac_result = PacCheckResult(
            available=raw["pac_available"],
            rfc_active=raw["rfc_valid"],
            error_code=raw["pac_error_code"],
            message=raw["pac_message"],
        )
        # Si el PAC confirma que el RFC no está activo, marcarlo en fields
        if raw["rfc_valid"] is False:
            results["rfc"] = FieldResult(
                valid=False,
                message=f"RFC no activo en padrón SAT ({raw['pac_error_code']}): {raw['pac_message']}",
            )

    # Resultado global
    all_valid = all(r.valid for r in results.values())
    if all_valid:
        pac_suffix = ""
        if pac_result and pac_result.rfc_active is True:
            pac_suffix = " RFC verificado activo en padron SAT."
        elif pac_result and pac_result.rfc_active is None and pac_check:
            pac_suffix = " (verificacion PAC no concluyente)"
        summary = (
            f"Datos fiscales validos para CFDI 4.0. "
            f"Persona {'fisica' if tipo_persona == 'fisica' else 'moral'}, "
            f"regimen {req.regimen}.{pac_suffix}"
        )
    else:
        failed = [k for k, v in results.items() if not v.valid]
        summary = f"Validacion fallida en: {', '.join(failed)}. Revisa los campos marcados."

    return ReceptorValidateResponse(
        valid=all_valid,
        tipo_persona=tipo_persona,
        fields=results,
        pac=pac_result,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Endpoint — catálogos de referencia (público)
# ---------------------------------------------------------------------------

@router.get(
    "/v1/receptor/catalogos",
    summary="Catálogos SAT vigentes (c_RegimenFiscal + c_UsoCFDI)",
    description="Devuelve los catálogos SAT usados en la validación. Sin autenticación.",
)
def get_catalogos():
    return {
        "regimenes": {
            code: {"descripcion": desc, "aplica_fisica": f, "aplica_moral": m}
            for code, (desc, f, m) in _REGIMENES.items()
        },
        "usos_cfdi": {
            code: {
                "descripcion": desc,
                "aplica_fisica": f,
                "aplica_moral": m,
                "regimenes_compatibles": regs,
            }
            for code, (desc, f, m, regs) in _USOS_CFDI.items()
        },
    }
