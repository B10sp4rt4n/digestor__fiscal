"""
Router de telemetría — /telemetry/metrics y /telemetry/alerts/notify
"""
from fastapi import APIRouter, Query
from app.services import telemetry

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/metrics")
def metrics():
    return telemetry._current_metrics()


@router.get("/alerts")
def alerts(minutes: int = Query(default=60)):
    return {"alerts": telemetry.get_active_alerts(minutes=minutes)}


@router.post("/alerts/notify")
def notify(minutes: int = Query(default=60)):
    active = telemetry.get_active_alerts(minutes=minutes)
    return telemetry.notify_alerts(active)
