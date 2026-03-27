"""
Servicio de telemetría y alertas.
Calcula métricas y dispara notificaciones a Slack / webhook.
"""
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Optional

import requests

from app.core.config import settings

# Ventana deslizante de eventos (últimos 5 min)
_event_window: Deque[Dict[str, Any]] = deque()
_WINDOW_SECONDS = 300  # 5 minutos


def record_event(success: bool, latency_ms: float) -> None:
    now = time.time()
    _event_window.append({"ts": now, "success": success, "latency_ms": latency_ms})
    # Purgar eventos fuera de ventana
    while _event_window and _event_window[0]["ts"] < now - _WINDOW_SECONDS:
        _event_window.popleft()


def _current_metrics() -> Dict[str, Any]:
    now = time.time()
    window = [e for e in _event_window if e["ts"] >= now - _WINDOW_SECONDS]
    total = len(window)
    if total == 0:
        return {"total": 0, "err_rate": 0.0, "epm": 0.0, "p95_latency_ms": 0.0}

    errors = sum(1 for e in window if not e["success"])
    err_rate = errors / total
    epm = total / (_WINDOW_SECONDS / 60)

    latencies = sorted(e["latency_ms"] for e in window)
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    p95 = latencies[p95_idx]

    return {"total": total, "err_rate": err_rate, "epm": epm, "p95_latency_ms": p95}


def get_active_alerts(minutes: int = 60) -> List[Dict[str, Any]]:
    metrics = _current_metrics()
    alerts = []

    if metrics["err_rate"] > settings.ALERT_ERR_RATE:
        alerts.append({
            "type": "HIGH_ERROR_RATE",
            "value": round(metrics["err_rate"] * 100, 2),
            "threshold": settings.ALERT_ERR_RATE * 100,
            "unit": "%",
        })
    if metrics["epm"] > settings.ALERT_BURST_EPM:
        alerts.append({
            "type": "BURST_EPM",
            "value": round(metrics["epm"], 1),
            "threshold": settings.ALERT_BURST_EPM,
            "unit": "events/min",
        })
    if metrics["p95_latency_ms"] > settings.ALERT_P95_LATENCY_MS:
        alerts.append({
            "type": "HIGH_P95_LATENCY",
            "value": round(metrics["p95_latency_ms"], 1),
            "threshold": settings.ALERT_P95_LATENCY_MS,
            "unit": "ms",
        })
    return alerts


def notify_alerts(alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not alerts:
        return {"sent": False, "reason": "no_alerts"}

    text = f"[Digestor Fiscal] {len(alerts)} alerta(s) activa(s):\n"
    for a in alerts:
        text += f"  • {a['type']}: {a['value']} {a['unit']} (umbral {a['threshold']})\n"

    sent_slack = _send_slack(text)
    sent_webhook = _send_webhook(alerts)

    return {"sent": True, "slack": sent_slack, "webhook": sent_webhook, "count": len(alerts)}


def _send_slack(text: str) -> bool:
    url = settings.ALERTS_NOTIFY_SLACK_WEBHOOK
    if not url:
        return False
    try:
        r = requests.post(url, json={"text": text}, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _send_webhook(alerts: List[Dict[str, Any]]) -> bool:
    url = settings.ALERTS_NOTIFY_WEBHOOK
    if not url:
        return False
    try:
        r = requests.post(url, json={"alerts": alerts, "ts": datetime.utcnow().isoformat()}, timeout=5)
        return r.status_code < 400
    except Exception:
        return False
