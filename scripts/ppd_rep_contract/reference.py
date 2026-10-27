"""Offline contract model, NOT an application service, fiscal builder or PAC client.

Only synthetic invoice/document identities are accepted. Sequential execution
models the result of locks; it does not prove database concurrency or durability.
"""
import copy
import hashlib
import json
import re
from datetime import datetime, date
from decimal import Decimal


class Conflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9][0-9]{0,11})\.[0-9]{2}", value):
        raise Conflict("DECIMAL_STRING_REQUIRED")
    return Decimal(value)


def due_date(received_at):
    timestamp = datetime.fromisoformat(received_at)
    if timestamp.utcoffset() is None:
        raise Conflict("TIMEZONE_REQUIRED")
    # This reference requires Mexico City's explicit offset, not arbitrary UTC.
    if timestamp.utcoffset().total_seconds() != -21600:
        raise Conflict("FISCAL_LOCAL_DATE_REQUIRED")
    return date(timestamp.year + (timestamp.month == 12), timestamp.month % 12 + 1, 5).isoformat()


class Model:
    def __init__(self, tenant="synthetic-tenant"):
        self.tenant = tenant
        self.invoices, self.receipts, self.intents = {}, {}, {}
        self.keys, self.bank_ids, self.events = {}, {}, []

    def event(self, kind, before, after, evidence):
        row = {"recordia_version": "1.0", "contract_version": "ppd-rep-model/1",
               "tenant_id": self.tenant, "sequence": len(self.events) + 1,
               "event_id": f"synthetic-event-{len(self.events) + 1}",
               "action_or_state": kind, "epistemic_class": "observed_fact",
               "support_status": "supported", "support_scope": "synthetic_model_only",
               "state_before": before, "state_after": after,
               "evidence_refs": evidence, "policy_or_rule_refs": ["ppd-rep-design/1"],
               "previous_hash": self.events[-1]["content_hash"] if self.events else None}
        self.events.append({**row, "content_hash": digest(row)})

    def verify(self):
        previous = None
        for n, entry in enumerate(self.events, 1):
            row = {k: v for k, v in entry.items() if k != "content_hash"}
            if (row["tenant_id"] != self.tenant or row["sequence"] != n or
                    row["previous_hash"] != previous or digest(row) != entry["content_hash"]):
                raise Conflict("HISTORY_MISMATCH")
            previous = entry["content_hash"]
        return previous

    def invoice(self, invoice_id, total="11600.00", *, method="PPD", form="99", currency="MXN", tenant=None, receiver="synthetic-customer"):
        if tenant is not None and tenant != self.tenant:
            raise Conflict("TENANT_SCOPE")
        if not invoice_id.startswith("synthetic-") or method != "PPD" or form != "99" or currency != "MXN":
            raise Conflict("UNSUPPORTED_PROFILE")
        if invoice_id in self.invoices or amount(total) <= 0:
            raise Conflict("DUPLICATE_INVOICE_OR_AMOUNT")
        # Represents an already confirmed synthetic income, never a draft.
        self.invoices[invoice_id] = {"total": total, "cash": total, "fiscal": total,
                                     "part": 1, "revision": 0, "receiver": receiver, "last_date": None}

    def receipt(self, key, receipt_id, value, *, bank_id=None, received_at="2026-10-26T10:00:00-06:00"):
        payload = {"id": receipt_id, "amount": value, "bank_id": bank_id, "received_at": received_at}
        fingerprint = digest(payload)
        if key in self.keys:
            if self.keys[key] != fingerprint:
                raise Conflict("DUPLICATE_KEY_CONFLICT")
            return receipt_id
        if receipt_id in self.receipts or (bank_id and bank_id in self.bank_ids):
            raise Conflict("DUPLICATE_PAYMENT")
        if not receipt_id.startswith("synthetic-") or amount(value) <= 0:
            raise Conflict("INVALID_RECEIPT")
        deadline = due_date(received_at)
        self.receipts[receipt_id] = {**payload, "reconciled": False, "applications": [], "due": deadline}
        self.keys[key] = fingerprint
        if bank_id:
            self.bank_ids[bank_id] = receipt_id
        self.event("receipt.recorded", None, "recorded", {"source": receipt_id})
        return receipt_id

    def reconcile(self, receipt_id, evidence):
        if not evidence:
            raise Conflict("EVIDENCE_REQUIRED")
        row = self.receipts[receipt_id]
        if not row["reconciled"]:
            row["reconciled"] = True
            self.event("receipt.reconciled", "recorded", "reconciled", {"source": evidence})

    def apply(self, receipt_id, invoice_id, value, revision):
        receipt, invoice = self.receipts[receipt_id], self.invoices[invoice_id]
        paid = amount(value)
        if not receipt["reconciled"]:
            raise Conflict("NOT_RECONCILED")
        if any(x["receipt_id"] == receipt_id for x in self.intents.values()):
            raise Conflict("APPLICATION_FROZEN")
        if invoice["revision"] != revision:
            raise Conflict("STALE_SNAPSHOT")
        if paid <= 0 or paid > amount(invoice["cash"]) or sum((amount(a["paid"]) for a in receipt["applications"]), Decimal(0)) + paid > amount(receipt["amount"]):
            raise Conflict("OVERAPPLICATION")
        if any(a["invoice_id"] == invoice_id for a in receipt["applications"]):
            raise Conflict("DUPLICATE_APPLICATION")
        if receipt["applications"] and self.invoices[receipt["applications"][0]["invoice_id"]]["receiver"] != invoice["receiver"]:
            raise Conflict("RECEIVER_MISMATCH")
        before = invoice["cash"]
        after = format(amount(before) - paid, ".2f")
        receipt["applications"].append({"invoice_id": invoice_id, "paid": value, "before": before, "after": after})
        invoice["cash"], invoice["revision"] = after, revision + 1
        self.event("cash.applied", before, after, {"source": receipt_id, "invoice": invoice_id})

    def reserve(self, intent_id, receipt_id):
        if intent_id in self.intents:
            if self.intents[intent_id]["receipt_id"] != receipt_id:
                raise Conflict("DUPLICATE_KEY_CONFLICT")
            return copy.deepcopy(self.intents[intent_id])  # replay does NOT submit again
        receipt = self.receipts[receipt_id]
        if any(x["receipt_id"] == receipt_id for x in self.intents.values()):
            raise Conflict("DUPLICATE_REP")
        apps = receipt["applications"]
        if not apps or sum((amount(a["paid"]) for a in apps), Decimal(0)) != amount(receipt["amount"]):
            raise Conflict("UNALLOCATED_CASH")
        documents = []
        for app in sorted(apps, key=lambda a: a["invoice_id"]):
            invoice_id = app["invoice_id"]
            inv = self.invoices[invoice_id]
            if any(x["status"] in {"processing", "unknown"} and any(a["invoice_id"] == invoice_id for a in x["documents"]) for x in self.intents.values()):
                raise Conflict("PREVIOUS_REP_PENDING")
            if inv["fiscal"] != app["before"]:
                raise Conflict("PREVIOUS_REP_PENDING")
            if inv["last_date"] and receipt["received_at"] < inv["last_date"]:
                raise Conflict("BACKDATED_PAYMENT_REVIEW")
            documents.append({**app, "part": inv["part"]})
        row = {"receipt_id": receipt_id, "documents": documents, "status": "processing", "uuid": None, "attempt": 1, "past_attempts": []}
        row["request_hash"] = digest({"tenant": self.tenant, "intent": intent_id, "receipt": receipt, "documents": documents})
        self.intents[intent_id] = row
        self.event("rep.reserved", "prepared", "processing", {"request_hash": row["request_hash"]})
        return copy.deepcopy(row)

    def retry(self, intent_id, expected_request_hash, authorization_ref):
        """Explicit retry of unchanged synthetic payload after proven nonissuance.

        Correcting fiscal data requires the versioned contract workflow; this
        reduced model does not implement correction or cancellation.
        """
        row = self.intents[intent_id]
        if row["status"] not in {"rejected", "failed_local"}:
            raise Conflict("RETRY_FORBIDDEN")
        if not authorization_ref or expected_request_hash != row["request_hash"]:
            raise Conflict("RETRY_AUTHORIZATION_REQUIRED")
        for app in row["documents"]:
            inv = self.invoices[app["invoice_id"]]
            if inv["fiscal"] != app["before"] or inv["part"] != app["part"]:
                raise Conflict("STALE_FISCAL_STATE")
        before = row["status"]
        row["past_attempts"].append({"attempt": row["attempt"], "request_hash": row["request_hash"], "status": before})
        row["attempt"] += 1
        row["status"] = "processing"
        self.event("rep.retry_reserved", before, "processing", {"authorization": authorization_ref, "attempt": row["attempt"]})
        return copy.deepcopy(row)

    def outcome(self, intent_id, result, *, document=None, evidence=None, fenced=False):
        row = self.intents[intent_id]
        if result not in {"unknown", "rejected", "not_called", "confirmed"}:
            raise Conflict("INVALID_OUTCOME")
        if row["status"] == "stamped":
            if result == "confirmed" and document == row.get("document"):
                return  # inbox/recovery replay
            raise Conflict("DOCUMENT_MISMATCH")
        if row["status"] not in {"processing", "unknown"}:
            raise Conflict("TERMINAL_ATTEMPT")
        if row["status"] == "unknown" and result != "unknown" and not (evidence and fenced):
            raise Conflict("RECONCILIATION_REQUIRED")
        before = row["status"]
        if result == "confirmed":
            # Synthetic adapter attestation only. Real implementation must validate
            # XML, signatures, identity, taxes and exact request binding itself.
            expected = {"uuid": "synthetic-rep-" + intent_id, "request_hash": row["request_hash"], "documents": row["documents"]}
            if document != expected:
                raise Conflict("DOCUMENT_MISMATCH")
            for app in row["documents"]:
                inv = self.invoices[app["invoice_id"]]
                if inv["fiscal"] != app["before"] or inv["part"] != app["part"]:
                    raise Conflict("STALE_FISCAL_STATE")
            for app in row["documents"]:
                inv = self.invoices[app["invoice_id"]]
                inv["fiscal"], inv["part"] = app["after"], inv["part"] + 1
                inv["last_date"] = self.receipts[row["receipt_id"]]["received_at"]
            row["status"], row["uuid"], row["document"] = "stamped", document["uuid"], copy.deepcopy(document)
        elif result == "unknown":
            row["status"] = "unknown"
        else:
            if not evidence:
                raise Conflict("NONISSUANCE_EVIDENCE_REQUIRED")
            row["status"] = "rejected" if result == "rejected" else "failed_local"
        self.event("rep." + result, before, row["status"], {"source": evidence, "request_hash": row["request_hash"]})

    def synthetic_document(self, intent_id):
        row = self.intents[intent_id]
        return {"uuid": "synthetic-rep-" + intent_id, "request_hash": row["request_hash"], "documents": copy.deepcopy(row["documents"])}

    def balances(self, invoice_id):
        row = self.invoices[invoice_id]
        return {"receivable": row["cash"], "fiscal_pending": row["fiscal"],
                "awaiting_rep": format(amount(row["fiscal"]) - amount(row["cash"]), ".2f"),
                "complete": row["cash"] == row["fiscal"] == "0.00"}
