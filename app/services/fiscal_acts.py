import hashlib
import json
import uuid
from datetime import datetime, timezone

from app.models.fiscal_act import FiscalAct
from app.services.fiscal_rules import RULE_ID

TRANSITIONS = {
    "draft_created": ({"absent"}, {"not_sent"}),
    "draft_edited": ({"not_sent", "rejected"}, {"not_sent", "rejected"}),
    "stamp_reserved": ({"not_sent", "rejected"}, {"processing"}),
    "request_prepared": ({"processing"}, {"processing"}),
    "local_validation_failed": ({"processing"}, {"not_sent"}),
    "provider_not_called": ({"processing"}, {"not_sent"}),
    "provider_result_unknown": ({"processing"}, {"unknown"}),
    "stamp_confirmed": ({"processing"}, {"stamped"}),
    "stamp_rejected": ({"processing"}, {"rejected"}),
}


def state(draft):
    return {"status": draft.status, "stamp_status": draft.stamp_status,
            "has_xml": draft.stamped_xml_base64 is not None, "has_stamp_time": draft.stamped_at is not None}


def draft_digest(draft):
    fields = ["customer_name", "customer_rfc", "customer_zip", "customer_regimen", "customer_use_cfdi",
              "emitter_rfc", "emitter_name", "emitter_regimen", "place_of_issue", "currency",
              "payment_method", "payment_form", "series", "folio", "subtotal", "taxes", "total"]
    details = {field: str(getattr(draft, field, None)) for field in fields}
    details["items"] = [{field: str(getattr(item, field, None)) for field in
        ("description", "quantity", "unit_price", "sat_product_code", "unit_code", "tax_object", "tax_rate")}
        for item in draft.items]
    return digest(canonical(details))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _verify(rows):
    previous_hash, previous_id, previous_state = None, None, None
    for sequence, row in enumerate(rows, 1):
        payload = json.loads(row.payload)
        if (row.sequence != sequence or row.previous_hash != previous_hash or digest(row.payload) != row.content_hash
                or canonical(payload) != row.payload or payload["event_id"] != row.id
                or payload["tenant_id"] != row.company_id or payload["subject"]["entity_id"] != row.draft_id
                or payload["sequence"] != sequence or payload["previous_hash"] != previous_hash
                or payload["previous_event_id"] != previous_id
                or (previous_state is not None and payload["state_before"] != previous_state)):
            raise ValueError("Fiscal history integrity mismatch")
        previous_hash, previous_id, previous_state = row.content_hash, row.id, payload["state_after"]
    return previous_hash, previous_id, previous_state


def verify(rows):
    try:
        return _verify(rows)
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Fiscal history integrity mismatch") from exc


def append(db, draft, ctx, action, before, evidence=None):
    """Caller owns the draft row lock/reservation. Flush only, never commit.
    Existing generic audit_service commits internally and is deliberately NOT
    reused: state and fiscal act must succeed/rollback together.
    """
    after = state(draft)
    old, new = TRANSITIONS[action]
    if before["stamp_status"] not in old or after["stamp_status"] not in new:
        raise ValueError("Forbidden fiscal transition")
    if before["has_xml"] or before["has_stamp_time"]:
        raise ValueError("Cannot transition a draft with existing fiscal evidence")
    rows = db.query(FiscalAct).filter_by(company_id=draft.company_id, draft_id=draft.id).order_by(FiscalAct.sequence).all()
    previous_hash, previous_id, previous_state = verify(rows)
    if previous_state is not None and previous_state != before:
        raise ValueError("Fiscal transition does not extend the observed state")
    now = datetime.now(timezone.utc)
    event_id = str(uuid.uuid4())
    payload = {
        "recordia_version": "1.0", "contract_version": "fiscal-act/1.0", "event_id": event_id,
        "sequence": len(rows) + 1, "tenant_id": draft.company_id,
        "case_id": "digestor:" + draft.company_id + ":draft:" + draft.id,
        "subject": {"entity_type": "billing_draft", "entity_id": draft.id},
        "actors": [{"actor_id": ctx.user_id, "role": ctx.role, "identity_status": "declared"}],
        "action_or_state": "fiscal." + action, "epistemic_class": "observed_fact",
        "support_status": "supported", "support_scope": "application_transition_only",
        "occurred_at_utc": now.isoformat(), "recorded_at_utc": now.isoformat(),
        "state_before": before, "state_after": after, "policy_or_rule_refs": [RULE_ID, "digestor.stamp-reservation/1"],
        "source_refs": ["billing_draft:" + draft.id], "evidence_refs": evidence or {},
        "relations": [{"predicate": "follows", "target_id": previous_id}] if previous_id else [],
        "previous_event_id": previous_id, "previous_hash": previous_hash,
        "history_basis": "observed_from_first_recorded_action",
        "memory": {"status": "local_only", "external_recordia_ref": None, "custody_status": "not_requested"},
    }
    encoded = canonical(payload)
    row = FiscalAct(id=event_id, company_id=draft.company_id, draft_id=draft.id, sequence=len(rows) + 1,
                    payload=encoded, content_hash=digest(encoded), previous_hash=previous_hash,
                    recorded_at=now.replace(tzinfo=None))
    db.add(row)
    db.flush()
    return row
