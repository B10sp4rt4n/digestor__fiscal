"""Run: python3 -I scripts/ppd_rep_contract/test_reference.py. No dependencies."""
import copy
import importlib.util
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("offline_ppd_reference", Path(__file__).with_name("reference.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Model, Conflict, due_date, amount = module.Model, module.Conflict, module.due_date, module.amount


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.m = Model()
        self.m.invoice("synthetic-invoice")

    def payment(self, suffix="1", value="4640.00", invoice="synthetic-invoice", revision=0):
        rid = "synthetic-receipt-" + suffix
        self.m.receipt("key-" + suffix, rid, value, bank_id="synthetic-bank-" + suffix)
        self.m.reconcile(rid, "synthetic-statement-line-" + suffix)
        self.m.apply(rid, invoice, value, revision)
        return rid

    def reserve(self):
        return self.m.reserve("intent1", self.payment())

    def test_two_partial_payments(self):
        row = self.reserve()
        self.assertEqual(row["documents"][0], {"invoice_id": "synthetic-invoice", "paid": "4640.00", "before": "11600.00", "after": "6960.00", "part": 1})
        self.m.outcome("intent1", "confirmed", document=self.m.synthetic_document("intent1"))
        self.assertEqual(self.m.balances("synthetic-invoice"), {"receivable": "6960.00", "fiscal_pending": "6960.00", "awaiting_rep": "0.00", "complete": False})
        self.m.reserve("intent2", self.payment("2", "6960.00", revision=1))
        self.assertEqual(self.m.intents["intent2"]["documents"][0]["part"], 2)
        self.m.outcome("intent2", "confirmed", document=self.m.synthetic_document("intent2"))
        self.assertTrue(self.m.balances("synthetic-invoice")["complete"])

    def test_deferred_full_payment_is_partiality_one(self):
        row = self.m.reserve("intent1", self.payment(value="11600.00"))
        self.assertEqual(row["documents"][0]["part"], 1)
        self.assertEqual(row["documents"][0]["after"], "0.00")

    def test_cash_is_not_fiscal_confirmation(self):
        self.payment(value="11600.00")
        self.assertEqual(self.m.balances("synthetic-invoice"), {"receivable": "0.00", "fiscal_pending": "11600.00", "awaiting_rep": "11600.00", "complete": False})

    def test_recording_does_not_change_balance(self):
        self.m.receipt("key", "synthetic-r", "100.00")
        self.assertEqual(self.m.balances("synthetic-invoice")["receivable"], "11600.00")

    def test_idempotency_replay_has_no_extra_act(self):
        args = ("key", "synthetic-r", "100.00")
        self.m.receipt(*args)
        count = len(self.m.events)
        self.m.receipt(*args)
        self.assertEqual(len(self.m.events), count)

    def test_idempotency_content_conflict(self):
        self.m.receipt("key", "synthetic-r", "100.00")
        with self.assertRaisesRegex(Conflict, "DUPLICATE_KEY_CONFLICT"):
            self.m.receipt("key", "synthetic-r", "200.00")

    def test_external_transaction_duplicate(self):
        self.m.receipt("key", "synthetic-r", "100.00", bank_id="synthetic-bank")
        with self.assertRaisesRegex(Conflict, "DUPLICATE_PAYMENT"):
            self.m.receipt("key2", "synthetic-r2", "100.00", bank_id="synthetic-bank")

    def test_same_amount_not_identity(self):
        self.m.receipt("key", "synthetic-r", "100.00", bank_id="synthetic-bank")
        self.m.receipt("key2", "synthetic-r2", "100.00", bank_id="synthetic-bank2")
        self.assertEqual(len(self.m.receipts), 2)

    def test_unreconciled_payment_cannot_apply(self):
        self.m.receipt("key", "synthetic-r", "100.00")
        with self.assertRaisesRegex(Conflict, "NOT_RECONCILED"):
            self.m.apply("synthetic-r", "synthetic-invoice", "100.00", 0)

    def test_reconcile_requires_evidence(self):
        self.m.receipt("key", "synthetic-r", "100.00")
        with self.assertRaisesRegex(Conflict, "EVIDENCE_REQUIRED"):
            self.m.reconcile("synthetic-r", None)

    def test_stale_revision_rejects_second_writer(self):
        self.payment()
        with self.assertRaisesRegex(Conflict, "STALE_SNAPSHOT"):
            self.payment("2", "100.00", revision=0)
        self.assertEqual(self.m.balances("synthetic-invoice")["receivable"], "6960.00")

    def test_overapplication_preserves_balance(self):
        with self.assertRaisesRegex(Conflict, "OVERAPPLICATION"):
            self.payment(value="12000.00")
        self.assertEqual(self.m.balances("synthetic-invoice")["receivable"], "11600.00")

    def test_excess_receipt_preserved_unallocated(self):
        self.m.receipt("key", "synthetic-r", "12000.00")
        self.m.reconcile("synthetic-r", "synthetic-evidence")
        self.m.apply("synthetic-r", "synthetic-invoice", "11600.00", 0)
        with self.assertRaisesRegex(Conflict, "UNALLOCATED_CASH"):
            self.m.reserve("intent", "synthetic-r")
        self.assertEqual(self.m.receipts["synthetic-r"]["amount"], "12000.00")

    def test_unknown_retains_reservation_and_cash(self):
        self.reserve()
        self.m.outcome("intent1", "unknown")
        self.assertEqual(self.m.balances("synthetic-invoice")["receivable"], "6960.00")
        self.assertEqual(self.m.balances("synthetic-invoice")["fiscal_pending"], "11600.00")
        receipt2 = self.payment("2", "100.00", revision=1)
        with self.assertRaisesRegex(Conflict, "PREVIOUS_REP_PENDING"):
            self.m.reserve("intent2", receipt2)

    def test_unknown_recovery_requires_fencing_and_evidence(self):
        self.reserve()
        self.m.outcome("intent1", "unknown")
        with self.assertRaisesRegex(Conflict, "RECONCILIATION_REQUIRED"):
            self.m.outcome("intent1", "confirmed", document=self.m.synthetic_document("intent1"))
        self.m.outcome("intent1", "confirmed", document=self.m.synthetic_document("intent1"), evidence="synthetic-provider-record", fenced=True)
        self.assertEqual(self.m.balances("synthetic-invoice")["fiscal_pending"], "6960.00")

    def test_processing_crash_blocks_other_intent(self):
        self.reserve()  # simulate response lost before local result commit
        receipt2 = self.payment("2", "100.00", revision=1)
        with self.assertRaisesRegex(Conflict, "PREVIOUS_REP_PENDING"):
            self.m.reserve("intent2", receipt2)

    def test_reserve_replay_returns_same_request(self):
        before = self.reserve()
        count = len(self.m.events)
        self.assertEqual(self.m.reserve("intent1", "synthetic-receipt-1"), before)
        self.assertEqual(len(self.m.events), count)

    def test_new_intent_does_not_duplicate_same_receipt(self):
        self.reserve()
        with self.assertRaisesRegex(Conflict, "DUPLICATE_REP"):
            self.m.reserve("intent2", "synthetic-receipt-1")

    def test_confirmation_replay_does_not_advance_twice(self):
        self.reserve()
        doc = self.m.synthetic_document("intent1")
        self.m.outcome("intent1", "confirmed", document=doc)
        count = len(self.m.events)
        self.m.outcome("intent1", "confirmed", document=doc)
        self.assertEqual(len(self.m.events), count)
        self.assertEqual(self.m.invoices["synthetic-invoice"]["part"], 2)

    def test_mismatched_confirmation_is_quarantined_without_mutation(self):
        self.reserve()
        doc = self.m.synthetic_document("intent1")
        doc["documents"][0]["paid"] = "999.00"
        with self.assertRaisesRegex(Conflict, "DOCUMENT_MISMATCH"):
            self.m.outcome("intent1", "confirmed", document=doc)
        self.assertEqual(self.m.intents["intent1"]["status"], "processing")

    def test_explicit_rejection_preserves_payment_and_partiality(self):
        self.reserve()
        self.m.outcome("intent1", "rejected", evidence="synthetic-terminal-rejection")
        self.assertEqual(self.m.invoices["synthetic-invoice"]["part"], 1)
        self.assertEqual(self.m.balances("synthetic-invoice")["receivable"], "6960.00")

    def test_local_failure_preserves_payment(self):
        self.reserve()
        self.m.outcome("intent1", "not_called", evidence="synthetic-pre-send-failure")
        self.assertEqual(self.m.intents["intent1"]["status"], "failed_local")
        self.assertEqual(self.m.balances("synthetic-invoice")["fiscal_pending"], "11600.00")

    def test_unknown_is_not_rejection(self):
        self.reserve()
        self.m.outcome("intent1", "unknown")
        with self.assertRaisesRegex(Conflict, "RECONCILIATION_REQUIRED"):
            self.m.outcome("intent1", "rejected", evidence="empty-local-GET")

    def test_multi_invoice_same_receiver(self):
        self.m.invoice("synthetic-invoice2", "116.00")
        self.m.receipt("key", "synthetic-r", "232.00")
        self.m.reconcile("synthetic-r", "synthetic-evidence")
        for invoice in ["synthetic-invoice", "synthetic-invoice2"]:
            self.m.apply("synthetic-r", invoice, "116.00", 0)
        row = self.m.reserve("intent1", "synthetic-r")
        self.assertEqual(len(row["documents"]), 2)

    def test_multi_invoice_receiver_mismatch(self):
        self.m.invoice("synthetic-invoice2", "116.00", receiver="other-customer")
        self.m.receipt("key", "synthetic-r", "232.00")
        self.m.reconcile("synthetic-r", "synthetic-evidence")
        self.m.apply("synthetic-r", "synthetic-invoice", "116.00", 0)
        with self.assertRaisesRegex(Conflict, "RECEIVER_MISMATCH"):
            self.m.apply("synthetic-r", "synthetic-invoice2", "116.00", 0)

    def test_cross_tenant_invoice_rejected(self):
        with self.assertRaisesRegex(Conflict, "TENANT_SCOPE"):
            self.m.invoice("synthetic-other", tenant="other")

    def test_pue_and_invalid_ppd_do_not_enter_new_profile(self):
        for changes in ({"method": "PUE", "form": "03"}, {"method": "PPD", "form": "03"}, {"currency": "USD"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(Conflict, "UNSUPPORTED_PROFILE"):
                self.m.invoice("synthetic-other", **changes)

    def test_invalid_money_rejected(self):
        for value in [1.00, "NaN", "Infinity", "-1.00", "0.001", "1e2", " 1.00"]:
            with self.subTest(value=value), self.assertRaises(Conflict):
                amount(value)

    def test_due_date_uses_month_of_receipt(self):
        self.assertEqual(due_date("2026-10-31T23:59:59-06:00"), "2026-11-05")
        self.assertEqual(due_date("2026-12-31T23:59:59-06:00"), "2027-01-05")
        with self.assertRaisesRegex(Conflict, "TIMEZONE_REQUIRED"):
            due_date("2026-10-26T10:00:00")

    def test_history_tamper_detected(self):
        self.reserve()
        self.m.verify()
        self.m.events[0]["evidence_refs"]["source"] = "tampered"
        with self.assertRaisesRegex(Conflict, "HISTORY_MISMATCH"):
            self.m.verify()

    def test_history_reorder_detected(self):
        self.reserve()
        self.m.events.reverse()
        with self.assertRaisesRegex(Conflict, "HISTORY_MISMATCH"):
            self.m.verify()

    def test_reference_fixtures(self):
        fixture = json.loads((ROOT / "docs/contracts/ppd-rep-v1/fixtures.json").read_text())
        self.assertEqual(fixture["scenario"]["total"], "11600.00")
        self.assertEqual(sum(amount(p["amount"]) for p in fixture["scenario"]["payments"]), amount("11600.00"))

    def test_rejected_attempt_explicit_retry_preserves_history(self):
        row = self.reserve()
        self.m.outcome("intent1", "rejected", evidence="synthetic-terminal-rejection")
        retry = self.m.retry("intent1", row["request_hash"], "synthetic-new-authorization")
        self.assertEqual(retry["attempt"], 2)
        self.assertEqual(retry["past_attempts"][0]["status"], "rejected")
        self.m.outcome("intent1", "confirmed", document=self.m.synthetic_document("intent1"))
        self.assertEqual(self.m.invoices["synthetic-invoice"]["part"], 2)

    def test_unknown_cannot_be_retried_with_new_authorization(self):
        row = self.reserve()
        self.m.outcome("intent1", "unknown")
        with self.assertRaisesRegex(Conflict, "RETRY_FORBIDDEN"):
            self.m.retry("intent1", row["request_hash"], "synthetic-new-authorization")

    def test_rejected_retry_requires_matching_authorization(self):
        row = self.reserve()
        self.m.outcome("intent1", "rejected", evidence="synthetic-terminal-rejection")
        with self.assertRaisesRegex(Conflict, "RETRY_AUTHORIZATION_REQUIRED"):
            self.m.retry("intent1", row["request_hash"], None)

    def test_out_of_order_rep_is_blocked_even_before_first_reservation(self):
        self.payment()
        second = self.payment("2", "100.00", revision=1)
        with self.assertRaisesRegex(Conflict, "PREVIOUS_REP_PENDING"):
            self.m.reserve("intent2", second)

    def test_correction_is_blocked_after_reservation(self):
        self.reserve()
        with self.assertRaisesRegex(Conflict, "APPLICATION_FROZEN"):
            self.m.apply("synthetic-receipt-1", "synthetic-invoice", "1.00", 1)


if __name__ == "__main__":
    # Fail closed if this suite accidentally gains a network operation.
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
