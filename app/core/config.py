from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DB_URL: str = "sqlite:///./local.db"
    USE_SQLITE: bool = False
    API_KEY: str = "change-me-please"
    TENANT_ID: str = "demo-company"
    SURVIVAL_ENABLED: bool = True
    UPLOAD_DIR: str = "./uploads"

    # SAT QR validation
    SAT_ONLINE_VALIDATION: bool = False
    SAT_TIMEOUT: int = 5
    SAT_RETRIES: int = 2
    SAT_CACHE_TTL: int = 86400  # segundos
    SAT_ALLOW_LEGACY_TLS: bool = True

    # Alert thresholds
    ALERT_ERR_RATE: float = 0.05
    ALERT_BURST_EPM: int = 500
    ALERT_P95_LATENCY_MS: int = 5000

    # Notificaciones
    ALERTS_NOTIFY_SLACK_WEBHOOK: Optional[str] = None
    ALERTS_NOTIFY_WEBHOOK: Optional[str] = None
    ALERTS_AUTOPOLL: bool = False
    ALERTS_AUTOPOLL_SECONDS: int = 60

    # Versión
    SERVICE_VERSION: str = "0.1.0"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
