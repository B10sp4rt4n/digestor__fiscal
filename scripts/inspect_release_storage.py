"""Read configuration/file metadata only. Never connects to DB or imports app.main.

Execute inside the running Digestor environment. Output contains no credentials,
rows, XML or provider tokens. The operator may paste this source into python stdin.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from app.core.config import settings


def inspect_storage():
    url = settings.effective_db_url
    parsed = urlsplit(url)
    backend = parsed.scheme.split("+", 1)[0]
    database = {"backend": backend, "connection_attempted": False}
    if backend == "sqlite":
        raw_path = url.split("sqlite:///", 1)[-1].split("?", 1)[0]
        in_memory = raw_path in ("", ":memory:")
        database["in_memory"] = in_memory
        if not in_memory:
            path = Path(raw_path).resolve()
            database["file"] = str(path)
            database["file_exists"] = path.is_file()
            database["file_bytes"] = path.stat().st_size if path.is_file() else None
    else:
        database["host"] = parsed.hostname
        database["database"] = parsed.path.rsplit("/", 1)[-1]
    root = Path(__file__).resolve().parents[1] if "__file__" in globals() and __file__ != "<stdin>" else Path.cwd()
    hashes = {}
    for name in ("app/main.py", "app/api/routers/commercial_v1.py", "app/services/timbracfdi_client.py"):
        path = root / name
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return {
        "read_only": True,
        "database": database,
        "background_config": {
            "outbound_autopoll": settings.SYNC_OUTBOUND_AUTOPOLL,
            "outbound_webhook_configured": bool(settings.SYNC_OUTBOUND_WEBHOOK_URL),
            "outbound_noop_allowed": settings.SYNC_OUTBOUND_ALLOW_NOOP_TARGET,
            "document_workers": settings.DOCUMENT_QUEUE_WORKERS,
            "alerts_autopoll": settings.ALERTS_AUTOPOLL,
            "local_backup_enabled": settings.LOCAL_BACKUP_ENABLED,
        },
        "runtime_source_sha256": hashes,
        "limits": "Configuration and file metadata only; no DB query, backup, provider call, key creation or state change. Does not prove durable storage or queue drain.",
    }


if __name__ == "__main__":
    print(json.dumps(inspect_storage(), indent=2))
