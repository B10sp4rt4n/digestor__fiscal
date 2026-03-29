from typing import Any

from pydantic import BaseModel, Field


class TenantAlert(BaseModel):
    severity: str  # critical, warning, info
    type: str
    message: str


class JobMetrics(BaseModel):
    total: int
    queued: int
    processing: int
    done: int
    failed: int
    success_rate_percent: float


class DocumentMetrics(BaseModel):
    total_csf: int
    csf_recent: int


class PerformanceMetrics(BaseModel):
    avg_processing_time_ms: float


class TenantDashboard(BaseModel):
    tenant_id: str
    timestamp: str
    period_hours: int
    jobs: JobMetrics
    documents: DocumentMetrics
    performance: PerformanceMetrics
    alerts: list[TenantAlert] = Field(default_factory=list)
