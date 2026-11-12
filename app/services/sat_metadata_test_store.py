"""Disposable SQLite staging proof. Not imported by app startup or production APIs."""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3

from app.services.sat_metadata_staging import StagingOperations

APPLICATION_ID = 1396790356
DDL = (
    'CREATE TABLE IF NOT EXISTS packages (package_key TEXT PRIMARY KEY, scope TEXT NOT NULL, '
    'raw BLOB NOT NULL, raw_hash TEXT NOT NULL, manifest TEXT NOT NULL, manifest_hash TEXT NOT NULL, '
    'result TEXT NOT NULL, result_hash TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS documents (identity TEXT PRIMARY KEY, scope_owner TEXT NOT NULL, '
    'facts_hash TEXT NOT NULL, observed_at TEXT NOT NULL, state_hash TEXT NOT NULL, '
    'payload TEXT NOT NULL, payload_hash TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS observations (idempotency_key TEXT PRIMARY KEY, '
    'identity TEXT NOT NULL REFERENCES documents(identity), package_key TEXT NOT NULL '
    'REFERENCES packages(package_key), payload TEXT NOT NULL, payload_hash TEXT NOT NULL)',
)


class DisposableMetadataStore(StagingOperations):
    def __init__(self, path: Path, *, disposable_root: Path):
        self.path = Path(path)
        root = Path(disposable_root).resolve(strict=True)
        if (not self.path.is_absolute() or not self.path.name.endswith('.sat-metadata-test.sqlite3')
                or self.path.is_symlink() or not self.path.parent.resolve(strict=True).is_relative_to(root)):
            raise ValueError('An explicit disposable local database path is required')
        created = False
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            created = True
        except FileExistsError:
            pass
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            app_id = db.execute('PRAGMA application_id').fetchone()[0]
            if app_id != APPLICATION_ID and not (created and app_id == 0):
                raise ValueError('Refusing a database not owned by this test store')
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            for statement in DDL:
                db.execute(statement)
            db.commit()

    @contextmanager
    def _connection(self):
        if self.path.is_symlink():
            raise ValueError('Symlink database rejected')
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield SQLiteSession(db)
        finally:
            db.close()  # Uncommitted work rolls back, including an interrupted import.



class SQLiteSession:
    def __init__(self, db):
        self.db = db

    def execute(self, query, parameters=()):
        return self.db.execute(query.replace('%s', '?'), parameters)

    def begin_serialized(self):
        self.db.execute('BEGIN IMMEDIATE')

    def commit(self):
        self.db.commit()
