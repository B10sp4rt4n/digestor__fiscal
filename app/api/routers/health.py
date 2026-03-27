"""
Router de salud — /health y /version
"""
from fastapi import APIRouter
from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/")
def root():
    return {"app": "Digestor Fiscal", "docs": "/docs", "status": "ok"}


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/version")
def version():
    return {"version": settings.SERVICE_VERSION}
