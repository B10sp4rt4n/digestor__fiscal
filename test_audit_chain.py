#!/usr/bin/env python3
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.audit_log import AuditLog
from app.core.config import settings
from app.services import audit_service
import json

load_dotenv()

db_url = settings.DB_URL
engine = create_engine(db_url)
SessionLocal = sessionmaker(bind=engine)
session = SessionLocal()

try:
    # Create some test audit logs
    print("Creating test audit records...")
    
    # First record
    log1 = audit_service.create_audit_log(
        session,
        company_id="demo-company",
        user_id="fff68b29-eeb9-4b63-9905-5ba40091a1d2",
        action="test_action_1",
        entity_type="test",
        entity_id="test-1",
        details={"message": "This is the first test record"},
        ip_address="127.0.0.1",
        user_agent="test-client/1.0",
        status="success"
    )
    print(f"Created record 1: {log1.id}")
    print(f"  content_hash: {log1.content_hash}")
    print(f"  chain_hash: {log1.chain_hash}")
    print(f"  previous_hash: {log1.previous_hash}")
    
    # Second record
    log2 = audit_service.create_audit_log(
        session,
        company_id="demo-company",
        user_id="fff68b29-eeb9-4b63-9905-5ba40091a1d2",
        action="test_action_2",
        entity_type="test",
        entity_id="test-2",
        details={"message": "This is the second test record"},
        ip_address="127.0.0.1",
        user_agent="test-client/1.0",
        status="success"
    )
    print(f"\nCreated record 2: {log2.id}")
    print(f"  content_hash: {log2.content_hash}")
    print(f"  chain_hash: {log2.chain_hash}")
    print(f"  previous_hash: {log2.previous_hash}")
    
    # Verify chain
    print("\n\nVerifying chain integrity...")
    is_valid, message = audit_service.verify_audit_chain(session, "demo-company")
    print(f"Chain is valid: {is_valid}")
    print(f"Message: {message}")
    
    # Get all records
    records = audit_service.get_audit_records(session, "demo-company")
    print(f"\nTotal records: {len(records)}")
    for i, rec in enumerate(records[-4:], 1):  # Show last 4 records
        action = rec.get('action') if isinstance(rec, dict) else rec.action
        seq = rec.get('sequence_number') if isinstance(rec, dict) else rec.sequence_number
        print(f"  {i}. {action} (seq: {seq})")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    session.close()
