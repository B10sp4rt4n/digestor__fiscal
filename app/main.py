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
import app.models.csf       # noqa: F401
import app.models.sucursal  # noqa: F401
import app.models.usuario   # noqa: F401
import app.models.evento    # noqa: F401

from app.api.routers import csf, health, upload, sync, telemetry as telemetry_router
from app.services import telemetry


async def _autopoll_loop():
    """Revisa alertas activas cada N segundos y notifica si las hay."""
    while True:
        await asyncio.sleep(settings.ALERTS_AUTOPOLL_SECONDS)
        active = telemetry.get_active_alerts()
        if active:
            telemetry.notify_alerts(active)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Crear tablas en modo dev (SQLite). En producción usar Alembic.
    Base.metadata.create_all(bind=engine)

    task = None
    if settings.ALERTS_AUTOPOLL:
        task = asyncio.create_task(_autopoll_loop())

    yield

    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Digestor Fiscal",
    description="API de ingestión, sync y telemetría de CSFs del SAT.",
    version=settings.SERVICE_VERSION,
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(csf.router)
app.include_router(upload.router)
app.include_router(sync.router)
app.include_router(telemetry_router.router)
