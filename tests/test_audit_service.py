from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.audit_log import AuditLog
from app.services import audit_service


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AuditLog.__table__])
    return sessionmaker(bind=engine)()


def test_create_audit_log_links_hash_chain_and_sequence():
    db = make_session()

    first = audit_service.create_audit_log(
        db,
        company_id="tenant-a",
        user_id="user-1",
        action="document_upload_start",
        entity_type="document_job",
        entity_id="job-1",
        details={"filename": "a.pdf"},
    )
    second = audit_service.create_audit_log(
        db,
        company_id="tenant-a",
        user_id="user-1",
        action="document_upload_complete",
        entity_type="document_job",
        entity_id="job-1",
        details={"status": "done"},
    )

    assert first.sequence_number == 1
    assert first.previous_hash is None
    assert second.sequence_number == 2
    assert second.previous_hash == first.chain_hash

    is_valid, message = audit_service.verify_audit_chain(db, "tenant-a")
    assert is_valid is True
    assert message is None


def test_verify_audit_chain_detects_tampering():
    db = make_session()

    log = audit_service.create_audit_log(
        db,
        company_id="tenant-a",
        user_id="user-1",
        action="upload",
        entity_type="csf",
        entity_id="csf-1",
        details={"field": "original"},
    )

    log.details = '{"field":"tampered"}'
    db.commit()

    is_valid, message = audit_service.verify_audit_chain(db, "tenant-a")
    assert is_valid is False
    assert "content_hash" in message


def test_get_audit_records_can_filter_by_entity():
    db = make_session()

    audit_service.create_audit_log(
        db,
        company_id="tenant-a",
        user_id="user-1",
        action="upload",
        entity_type="document_job",
        entity_id="job-1",
        details={"step": 1},
    )
    audit_service.create_audit_log(
        db,
        company_id="tenant-a",
        user_id="user-1",
        action="upload",
        entity_type="document_job",
        entity_id="job-2",
        details={"step": 2},
    )

    records = audit_service.get_audit_records(db, "tenant-a", entity_id="job-1")

    assert len(records) == 1
    assert records[0]["entity_id"] == "job-1"
    assert records[0]["sequence"] == 1


def test_details_are_serialized_canonically_before_hashing():
    db = make_session()

    log = audit_service.create_audit_log(
        db,
        company_id="tenant-a",
        user_id="user-1",
        action="upload",
        entity_type="document_job",
        entity_id="job-1",
        details={"z": 1, "a": 2},
    )

    assert log.details == '{"a":2,"z":1}'