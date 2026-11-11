"""Explicit, additive, repeatable fiscal schema install. No PAC or customer writes.

The legacy Alembic history may not be stamped by create_all-based releases.
Apply only the reviewed fiscal migrations; leave its version history alone.
"""
import importlib.util
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from alembic.operations import Operations
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

FILES = ('20261025_01_fiscal_acts.py','20261025_02_payment_cycle.py',
         '20261107_01_cancellations.py','20261107_02_fiscal_revisions.py','20261110_01_sat_tracking.py')


def install(engine):
    with engine.begin() as conn:
        if not inspect(conn).has_table('billing_draft'):
            raise RuntimeError('Existing billing_draft required; no legacy bootstrap is performed')
        if conn.dialect.name == 'postgresql':
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            conn.execute(text('SELECT pg_advisory_xact_lock(87139026)'))
        elif conn.dialect.name != 'sqlite':
            raise RuntimeError('Unsupported fiscal database')
        for filename in FILES:
            path = Path(__file__).resolve().parents[1]/'migrations'/'versions'/filename
            spec = importlib.util.spec_from_file_location('release_'+filename[:-3],path)
            module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            module.op = Operations(MigrationContext.configure(conn));module.upgrade()
    print('Reviewed fiscal schema ready; prior invoices and cash unchanged; no PAC call.')


if __name__ == '__main__':
    from app.db.session import engine
    install(engine)
    from app.core.config import settings
    if settings.LIVE_CANCEL_ENABLED:
        from app.services.production_cancellation import assert_schema
        assert_schema(engine)
