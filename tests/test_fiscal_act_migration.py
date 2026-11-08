import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/20261025_01_fiscal_acts.py"
    spec = importlib.util.spec_from_file_location("fiscal_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_additive_migration_is_rerunnable_and_preserves_existing_draft():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(text("CREATE TABLE billing_draft (id TEXT PRIMARY KEY, company_id TEXT NOT NULL)"))
        conn.execute(text("INSERT INTO billing_draft VALUES ('old-draft','tenant-a')"))
        module = migration()
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        module.upgrade()
        assert inspect(conn).has_table("billing_fiscal_acts")
        assert conn.execute(text("SELECT company_id FROM billing_draft WHERE id='old-draft'")).scalar() == "tenant-a"
        assert conn.execute(text("SELECT count(*) FROM billing_fiscal_acts")).scalar() == 0
        with pytest.raises(RuntimeError): module.downgrade()
        assert inspect(conn).has_table("billing_fiscal_acts")
    engine.dispose()
