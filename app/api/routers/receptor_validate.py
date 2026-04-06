"""
Endpoint de validación fiscal de receptor — sin autenticación requerida.
Valida RFC, CP, régimen, y compatibilidad régimen/tipo-persona/uso CFDI
contra catálogos SAT actualizados a CFDI 4.0.
"""

import re
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["Receptor Validation"])

# ---------------------------------------------------------------------------
# Catálogos SAT CFDI 4.0
# ---------------------------------------------------------------------------

# c_RegimenFiscal — código → (descripción, aplica_física, aplica_moral)
_REGIMENES: dict[str, tuple[str, bool, bool]] = {
    "601": ("General de Ley Personas Morales", False, True),
    "603": ("Personas Morales con Fines no Lucrativos", False, True),
    "605": ("Sueldos y Salarios e Ingresos Asimilados a Salarios", True, False),
    "606": ("Arrendamiento", True, False),
    "607": ("Régimen de Enajenación o Adquisición de Bienes", True, False),
    "608": ("Demás ingresos", True, False),
    "610": ("Residentes en el Extranjero sin Establecimiento Permanente en México", True, True),
    "611": ("Ingresos por Dividendos (socios y accionistas)", True, False),
    "612": ("Personas Físicas con Actividades Empresariales y Profesionales", True, False),
    "614": ("Ingresos por intereses", True, False),
    "615": ("Régimen de los ingresos por obtención de premios", True, False),
    "616": ("Sin obligaciones fiscales", True, False),
    "620": ("Sociedades Cooperativas de Producción que optan por diferir sus ingresos", False, True),
    "621": ("Incorporación Fiscal", True, False),
    "622": ("Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras", True, True),
    "623": ("Opcional para Grupos de Sociedades", False, True),
    "624": ("Coordinados", False, True),
    "625": ("Régimen de las Actividades Empresariales con ingresos a través de Plataformas Tecnológicas", True, False),
    "626": ("Régimen Simplificado de Confianza", True, True),
}

# c_UsoCFDI — código → (descripción, aplica_física, aplica_moral, regimenes_compatibles o None=todos)
_USOS_CFDI: dict[str, tuple[str, bool, bool, list[str] | None]] = {
    "G01": ("Adquisición de mercancias", True, True, None),
    "G02": ("Devoluciones, descuentos o bonificaciones", True, True, None),
    "G03": ("Gastos en general", True, True, None),
    "I01": ("Construcciones", True, True, None),
    "I02": ("Mobilario y equipo de oficina por inversiones", True, True, None),
    "I03": ("Equipo de transporte", True, True, None),
    "I04": ("Equipo de computo y accesorios", True, True, None),
    "I05": ("Dados, troqueles, moldes, matrices y herramental", True, True, None),
    "I06": ("Comunicaciones telefónicas", True, True, None),
    "I07": ("Comunicaciones satelitales", True, True, None),
    "I08": ("Otra maquinaria y equipo", True, True, None),
    "D01": ("Honorarios médicos, dentales y gastos hospitalarios", True, False, ["605", "606", "608", "611", "612", "614", "616", "621", "625", "626"]),
    "D02": ("Gastos médicos por incapacidad o discapacidad", True, False, ["605", "606", "608", "611", "612", "614", "616", "621", "625", "626"]),
    "D03": ("Gastos funerales", True, False, ["605", "606", "608", "611", "612", "614", "616", "621", "625", "626"]),
    "D04": ("Donativos", True, False, ["605", "606", "608", "611", "612", "614", "616", "621", "625", "626"]),
    "D05": ("Intereses reales efectivamente pagados por créditos hipotecarios (casa habitación)", True, False, ["605", "606", "608", "611", "612", "614", "616", "621", "625", "626"]),
    "D06": ("Aportaciones voluntarias al SAR", True, False, ["605", "606", "608", "611", "612", "614", "616", "621", "625", "626"]),
    "D07": ("Primas por seguros de gastos médicos", True, False, ["605", "606", "608", "611", "612", "614", "616", "621", "625", "626"]),
    "D08": ("Gastos de transportación escolar obligatoria", True, False, ["605", "606", "608", "611", "612", "614", "616", "621", "625", "626"]),
    "D09": ("Depósitos en cuentas para el ahorro, primas que tengan como base planes de pensiones", True, False, ["605", "606", "608", "611", "612", "614", "616", "621", "625", "626"]),
    "D10": ("Pagos por servicios educativos (colegiaturas)", True, False, ["605", "606", "608", "611", "612", "614", "616", "621", "625", "626"]),
    "S01": ("Sin efectos fiscales", True, True, None),
    "CP01": ("Pagos", True, True, None),
    "CN01": ("Nómina", True, False, ["605"]),
}

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


class ReceptorValidateResponse(BaseModel):
    valid: bool                        # True solo si TODOS los campos obligatorios pasan
    tipo_persona: str | None = None   # "fisica" | "moral" | None
    fields: dict[str, FieldResult]
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
        "No requiere autenticación. No persiste ningún dato."
    ),
)
def validate_receptor(req: ReceptorValidateRequest) -> ReceptorValidateResponse:
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

    # Resultado global
    all_valid = all(r.valid for r in results.values())
    if all_valid:
        summary = (
            f"Datos fiscales validos para CFDI 4.0. "
            f"Persona {'fisica' if tipo_persona == 'fisica' else 'moral'}, "
            f"regimen {req.regimen}."
        )
    else:
        failed = [k for k, v in results.items() if not v.valid]
        summary = f"Validacion fallida en: {', '.join(failed)}. Revisa los campos marcados."

    return ReceptorValidateResponse(
        valid=all_valid,
        tipo_persona=tipo_persona,
        fields=results,
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
