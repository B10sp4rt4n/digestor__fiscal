"""Deterministic CSF review. Never calls AI/PAC or modifies the source document."""
import hashlib
import json
import re
import unicodedata

from app.services.fiscal_catalogs import REGIMENES


def _norm(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFD", str(value or ""))
                            if unicodedata.category(c) != "Mn").upper().split())


def _regime_code(value):
    text = _norm(value)
    for code, (description, _, _) in REGIMENES.items():
        if text in {code, _norm(description), "REGIMEN " + _norm(description),
                    code + " - " + _norm(description)}:
            return code
    return ""


def build_csf_review(row):
    crm = row.crm_autofill or {}
    # Prefer original extraction over historical AI auto-applied main fields.
    def original(key, fallback):
        return str(crm[key] if key in crm else fallback or "").strip()

    regime = original("tax_regime", row.regimen)
    values = {
        "rfc": original("tax_id", row.rfc).upper(),
        "company": original("legal_name", row.razon_social),
        "fiscalRegime": _regime_code(regime),
        "zipCode": original("postal_code", row.cp),
        "address": ", ".join(str(crm.get(k) or "").strip() for k in
                             ("street_type", "street_name", "ext_number", "int_number",
                              "neighborhood", "municipality", "state") if crm.get(k)),
    }
    issues = {}
    if (not re.fullmatch(r"[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}", values["rfc"])
            or values["rfc"] in {"XAXX010101000", "XEXX010101000"}):
        issues["rfc"] = "RFC ausente, genérico o con formato inválido."
    if _norm(values["company"]) in {"", "SIN NOMBRE", "DESCONOCIDO", "N/A", "PENDIENTE", "NULL"}:
        issues["company"] = "Razón social ausente o provisional."
    if len(values["company"]) > 200:
        issues["company"] = "La razón social supera 200 caracteres."
    if not re.fullmatch(r"\d{5}", values["zipCode"]) or int(values["zipCode"] or "0") < 1000:
        issues["zipCode"] = "Código postal inválido."
    code = values["fiscalRegime"]
    if not code:
        issues["fiscalRegime"] = "Régimen no reconocido; requiere selección manual."
    elif "rfc" not in issues:
        _, physical, legal = REGIMENES[code]
        if not (physical if len(values["rfc"]) == 13 else legal):
            issues["fiscalRegime"] = "Régimen incompatible con el tipo de persona."
    cif = original("cif_id", row.id_cif)
    if not re.fullmatch(r"\d+", cif):
        issues["cifId"] = "idCIF ausente o inválido."
    snapshot = {key: getattr(row, key, None) for key in
                ("csf_hash", "version", "rfc", "razon_social", "regimen", "cp", "id_cif",
                 "crm_autofill", "ai_field_corrections", "corrected_json", "field_validation")}
    revision = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":")).encode()).hexdigest()
    return {"revision": revision, "values": values, "regimeText": regime,
            "issues": issues, "suggestions": row.ai_field_corrections or [],
            "cifId": cif, "validationScope": "local_format_and_catalog",
            "regimeOptions": [{"code": code, "label": spec[0]} for code, spec in REGIMENES.items()]}
