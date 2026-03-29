import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
import app.models.csf  # noqa: F401
import app.models.evento  # noqa: F401
import app.models.sucursal  # noqa: F401
import app.models.user  # noqa: F401
import app.models.usuario  # noqa: F401


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    return value


def _scoped_query(db: Session, model: type[Any], tenant_id: str | None) -> list[Any]:
    query = db.query(model)
    if tenant_id:
        columns = model.__table__.columns.keys()
        if "company_id" in columns:
            query = query.filter(getattr(model, "company_id") == tenant_id)
        elif "tenant_id" in columns:
            query = query.filter(getattr(model, "tenant_id") == tenant_id)
    return query.all()


def export_local_backup(
    db: Session,
    tenant_id: str | None = None,
    requested_by: str | None = None,
) -> dict[str, Any]:
    if not settings.LOCAL_BACKUP_ENABLED:
        raise RuntimeError("El respaldo local está deshabilitado.")

    backup_dir = Path(settings.LOCAL_BACKUP_DIR).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    tenant_slug = (tenant_id or "global").replace("/", "-").replace(" ", "-")
    filename = f"digestor_backup_{tenant_slug}_{timestamp}.json"
    backup_path = backup_dir / filename

    tables: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}

    for mapper in sorted(Base.registry.mappers, key=lambda item: item.class_.__tablename__):
        model = mapper.class_
        if model.__tablename__ == "app_user" and not settings.LOCAL_BACKUP_INCLUDE_USERS:
            continue

        rows = _scoped_query(db, model, tenant_id)
        serialized_rows: list[dict[str, Any]] = []

        for row in rows:
            row_data = {}
            for column in sa_inspect(model).columns:
                row_data[column.key] = _json_safe(getattr(row, column.key))
            serialized_rows.append(row_data)

        tables[model.__tablename__] = serialized_rows
        counts[model.__tablename__] = len(serialized_rows)

    payload = {
        "meta": {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "db_backend": settings.DB_URL.split(":", 1)[0],
            "tenant_id": tenant_id,
            "requested_by": requested_by,
            "service_version": settings.SERVICE_VERSION,
        },
        "counts": counts,
        "tables": tables,
    }

    with backup_path.open("w", encoding="utf-8") as fh:
        json.dump(
            payload,
            fh,
            ensure_ascii=False,
            indent=2 if settings.LOCAL_BACKUP_PRETTY else None,
        )

    return {
        "backup_path": str(backup_path),
        "filename": filename,
        "counts": counts,
        "tenant_id": tenant_id,
        "generated_at": payload["meta"]["generated_at"],
    }