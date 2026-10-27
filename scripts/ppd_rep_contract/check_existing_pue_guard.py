"""Read the existing Digestor PUE rules without importing application/config.

python3 -I scripts/ppd_rep_contract/check_existing_pue_guard.py /path/to/digestor
"""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as S

root = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("existing_pue_rules", root / "app/services/fiscal_rules.py")
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)
item = S(sat_product_code="80101500", unit_code="E48", tax_object="02", tax_rate="0.16",
         quantity="1", unit_price="100", line_subtotal="100.00", line_taxes="16.00")
draft = S(currency="MXN", payment_method="PUE", payment_form="03", customer_rfc="AAA010101AAA",
          emitter_rfc="BBB010101BBB", customer_zip="01000", place_of_issue="01000",
          customer_regimen="601", emitter_regimen="601", customer_use_cfdi="G03", items=[item],
          subtotal="100.00", taxes="16.00", total="116.00")
assert rules.scenario_issues(draft) == []
draft.payment_method, draft.payment_form = "PPD", "99"
assert "unsupported_payment_method" in rules.scenario_issues(draft)
assert "unsupported_payment_form" in rules.scenario_issues(draft)
print("Existing Digestor guard: synthetic PUE accepted; PPD99 still blocked (3 assertions).")
