"""
Punto de entrada principal — Digestor Fiscal API
"""
import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.db.base import Base
from app.db.session import engine

# Importar modelos para que Alembic/Base los registre
import app.models.audit_log         # noqa: F401
import app.models.csf              # noqa: F401
import app.models.document_job     # noqa: F401
import app.models.document_sync_event  # noqa: F401
import app.models.sucursal         # noqa: F401
import app.models.usuario          # noqa: F401
import app.models.evento           # noqa: F401
import app.models.user             # noqa: F401

from app.api.routers import audit_v1, backups, csf, documents_v1, health, metrics_v1, upload, sync, sync_v1, telemetry as telemetry_router
from app.api.routers import auth_router
from app.services import telemetry
from app.services.document_queue import document_queue
from app.services.outbound_delivery_service import outbound_delivery_worker


async def _autopoll_loop():
    """Revisa alertas activas cada N segundos y notifica si las hay."""
    while True:
        await asyncio.sleep(settings.ALERTS_AUTOPOLL_SECONDS)
        active = telemetry.get_active_alerts()
        if active:
            telemetry.notify_alerts(active)


def _seed_initial_admin() -> None:
    """Crea el primer usuario admin si no existe ningún usuario en BD."""
    from app.db.session import SessionLocal
    from app.services.user_service import create_user, get_user_by_username
    from app.models.user import User

    db = SessionLocal()
    try:
        total = db.query(User).count()
        if total == 0:
            create_user(
                db,
                username=settings.SEED_ADMIN_USERNAME,
                password=settings.SEED_ADMIN_PASSWORD,
                tenant_id=settings.SEED_ADMIN_TENANT,
                role="superadmin",
            )
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Crear tablas en modo dev (SQLite). En producción usar Alembic.
    Base.metadata.create_all(bind=engine)

    # Seed del primer usuario admin si no existe ninguno
    _seed_initial_admin()

    await document_queue.start(settings.DOCUMENT_QUEUE_WORKERS)
    if settings.SYNC_OUTBOUND_AUTOPOLL:
        await outbound_delivery_worker.start()

    task = None
    if settings.ALERTS_AUTOPOLL:
        task = asyncio.create_task(_autopoll_loop())

    yield

    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    await document_queue.stop()
    if settings.SYNC_OUTBOUND_AUTOPOLL:
        await outbound_delivery_worker.stop()


app = FastAPI(
    title="Digestor Fiscal",
    description="API de ingestión, sync y telemetría de CSFs del SAT.",
    version=settings.SERVICE_VERSION,
    lifespan=lifespan,
)

app.include_router(auth_router.router)
app.include_router(health.router)
app.include_router(audit_v1.router)
app.include_router(backups.router)
app.include_router(csf.router)
app.include_router(documents_v1.router)
app.include_router(metrics_v1.router)
app.include_router(upload.router)
app.include_router(sync.router)
app.include_router(sync_v1.router)
app.include_router(telemetry_router.router)
