"""Deterministic preparation rules. No PAC/catalog lookup or fiscal certification."""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import re
from app.core.config import settings
from app.core.fiscal_sandbox import ppd_transport_is_sandbox

RULE_ID = "digestor.national-income-mxn-pue-ppd-sandbox/2"
CENT = Decimal("0.01")


def decimal_value(value, *, scale=6, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid fiscal decimal") from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError("Fiscal decimal must be finite and nonnegative")
    # Keep six-place units within a range that legacy Float storage round-trips
    # through str without losing the supported decimal places.
    if result > Decimal("99999999.999999") or result != result.quantize(Decimal(1).scaleb(-scale)):
        raise ValueError("Unsupported fiscal precision or amount")
    return result


def money(value):
    result = Decimal(str(value))
    if not result.is_finite() or abs(result) > Decimal("999999999999.99"):
        raise ValueError("Unsupported monetary amount")
    return result.quantize(CENT, rounding=ROUND_HALF_UP)


def unit_text(value):
    # Formatting also serves legacy PDF reads; validation belongs to the gate.
    return format(Decimal(str(value)), "f")


def scenario_issues(draft):
    """Fail closed outside the first supported scenario; preserve stored drafts.
    Syntax checks are NOT validation against SAT catalogs or taxpayer status.
    """
    issues = []
    if draft.currency != "MXN": issues.append("unsupported_currency")
    ppd = draft.payment_method == "PPD" and settings.FISCAL_PPD_ENABLED and ppd_transport_is_sandbox()
    if draft.payment_method != "PUE" and not ppd: issues.append("unsupported_payment_method")
    if ppd:
        if draft.payment_form != "99": issues.append("ppd_requires_payment_form_99")
    elif draft.payment_form not in {"01", "02", "03", "04", "28"}: issues.append("unsupported_payment_form")
    for prefix in ("customer", "emitter"):
        rfc = str(getattr(draft, prefix + "_rfc", "") or "").strip().upper()
        if not re.fullmatch(r"[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}", rfc): issues.append(prefix + "_rfc_syntax")
        if rfc in {"XAXX010101000", "XEXX010101000"}: issues.append("unsupported_generic_rfc")
    if not re.fullmatch(r"\d{5}", str(draft.customer_zip or "")): issues.append("customer_zip_syntax")
    if not re.fullmatch(r"\d{5}", str(draft.place_of_issue or "")): issues.append("place_of_issue_syntax")
    for field in ("customer_regimen", "emitter_regimen"):
        if not re.fullmatch(r"\d{3}", str(getattr(draft, field, "") or "")): issues.append(field + "_syntax")
    if not re.fullmatch(r"[GIDS]\d{2}", str(draft.customer_use_cfdi or "")):
        issues.append("unsupported_cfdi_use")
    for index, item in enumerate(draft.items):
        if not re.fullmatch(r"\d{8}", str(item.sat_product_code or "")): issues.append(f"items.{index}.sat_product_code")
        if not re.fullmatch(r"[A-Z0-9]{2,3}", str(item.unit_code or "")): issues.append(f"items.{index}.unit_code")
        if item.tax_object != "02" or Decimal(str(item.tax_rate or 0)) != Decimal("0.16"):
            issues.append(f"items.{index}.unsupported_tax_profile")
        try:
            quantity = decimal_value(item.quantity, positive=True)
            price = decimal_value(item.unit_price)
            expected = money(quantity * price)
            tax = money(expected * Decimal("0.16"))
            if Decimal(str(item.line_subtotal)) != expected or Decimal(str(item.line_taxes)) != tax:
                issues.append(f"items.{index}.amount_mismatch")
        except (ValueError, InvalidOperation):
            issues.append(f"items.{index}.precision")
    if draft.items and not any("amount_mismatch" in issue or "precision" in issue for issue in issues):
        subtotal = sum((Decimal(str(item.line_subtotal)) for item in draft.items), Decimal(0))
        taxes = sum((Decimal(str(item.line_taxes)) for item in draft.items), Decimal(0))
        if any(Decimal(str(actual)) != expected for actual, expected in
               ((draft.subtotal, subtotal), (draft.taxes, taxes), (draft.total, subtotal + taxes))):
            issues.append("draft_amount_mismatch")
    return list(dict.fromkeys(issues))
