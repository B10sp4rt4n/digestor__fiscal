"""Optional JSON Schema tests. Install requirements-test.txt in an isolated env."""
import copy
import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2] / "docs/contracts/ppd-rep-v1"


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / "wire-contract.json").read_text())
        cls.result_schema = json.loads((ROOT / "result-contract.json").read_text())
        Draft202012Validator.check_schema(cls.schema)
        Draft202012Validator.check_schema(cls.result_schema)
        cls.request_validator = Draft202012Validator(cls.schema, format_checker=FormatChecker())
        cls.result_validator = Draft202012Validator(cls.result_schema, format_checker=FormatChecker())
        cls.example = json.loads((ROOT / "fixtures.json").read_text())["valid_intent"]

    def invalid(self, mutate):
        row = copy.deepcopy(self.example)
        mutate(row)
        self.assertTrue(list(self.request_validator.iter_errors(row)))

    def test_valid_request(self):
        self.request_validator.validate(self.example)

    def test_float_amount_rejected(self):
        self.invalid(lambda r: r["receipt"].update(amount=4640.0))

    def test_payment_form_99_rejected(self):
        self.invalid(lambda r: r["receipt"].update(payment_form="99"))

    def test_usd_rejected(self):
        self.invalid(lambda r: r["receipt"].update(currency="USD"))

    def test_browser_tenant_injection_rejected(self):
        self.invalid(lambda r: r.update(company_id="other"))

    def test_partiality_injection_rejected(self):
        self.invalid(lambda r: r["applications"][0].update(partiality=99))

    def test_balance_injection_rejected(self):
        self.invalid(lambda r: r["applications"][0].update(previous_balance="1.00"))

    def test_evidence_required(self):
        self.invalid(lambda r: r["receipt"].pop("reconciliation_evidence_sha256"))

    def test_timezone_required(self):
        self.invalid(lambda r: r["receipt"].update(received_at="2026-10-26T10:00:00"))

    def result(self):
        return {"contract_version": "rep-result/1.0", "intent_id": self.example["intent_id"],
                "revision": 1, "status": "unknown", "attempt": 1, "retryable": False,
                "next_action": "review", "request_hash": "0" * 64,
                "last_event_id": "synthetic-event-1", "confirmed_document": None}

    def test_unknown_result(self):
        self.result_validator.validate(self.result())

    def test_unknown_not_retryable(self):
        row = self.result()
        row["retryable"] = True
        self.assertTrue(list(self.result_validator.iter_errors(row)))

    def test_stamped_requires_document(self):
        row = self.result()
        row.update(status="stamped", next_action="none")
        self.assertTrue(list(self.result_validator.iter_errors(row)))

    def test_stamped_result(self):
        row = self.result()
        row.update(status="stamped", next_action="none", confirmed_document={
            "uuid": self.example["intent_id"], "xml_sha256": "1" * 64,
            "protected_xml_ref": "synthetic-local-document", "confirmed_applications": [{
                "income_uuid": self.example["applications"][0]["income_uuid"],
                "cash_application_id": "synthetic-application-1", "partiality": 1,
                "previous_balance": "11600.00", "paid": "4640.00",
                "remaining_balance": "6960.00", "fiscal_revision": 1}]})
        self.result_validator.validate(row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
