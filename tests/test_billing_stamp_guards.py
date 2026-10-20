"""Pruebas de protección fiscal sin DB real ni llamadas al PAC."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.api.routers import commercial_v1
from app.core.auth import SecurityContext


@pytest.fixture
def ctx():
    return SecurityContext(tenant_id="t1", user_id="u1", role="operator")


def fake_draft(status="ready_to_stamp", stamp_status="not_sent", stamped_at=None):
    return SimpleNamespace(
        id="d1",
        status=status,
        stamp_status=stamp_status,
        stamped_at=stamped_at,
    )


@pytest.mark.parametrize(
    "status,stamp_status",
    [("stamped", "stamped"), ("ready_to_stamp", "processing"), ("ready_to_stamp", "unknown")],
)
def test_no_pac_call_when_already_stamped_or_uncertain(monkeypatch, ctx, status, stamp_status):
    pac = Mock()
    monkeypatch.setattr(commercial_v1, "_get_draft_or_404", lambda db, cid, did: fake_draft(status, stamp_status))
    monkeypatch.setattr(commercial_v1.timbracfdi_client, "timbra_cfdi", pac)

    with pytest.raises(HTTPException) as error:
        commercial_v1.stamp_billing_draft("d1", commercial_v1.BillingDraftStampRequest(), None, ctx, Mock())

    assert error.value.status_code == 409
    pac.assert_not_called()


def test_lost_atomic_reservation_never_calls_pac(monkeypatch, ctx):
    query = Mock()
    query.filter.return_value = query
    query.update.return_value = 0
    db = Mock()
    db.query.return_value = query
    pac = Mock()
    monkeypatch.setattr(commercial_v1, "_get_draft_or_404", lambda db, cid, did: fake_draft())
    monkeypatch.setattr(commercial_v1, "_missing_fields", lambda draft: [])
    monkeypatch.setattr(commercial_v1.timbracfdi_client, "timbra_cfdi", pac)

    with pytest.raises(HTTPException) as error:
        commercial_v1.stamp_billing_draft("d1", commercial_v1.BillingDraftStampRequest(), None, ctx, db)

    assert error.value.status_code == 409
    db.rollback.assert_called_once()
    pac.assert_not_called()
