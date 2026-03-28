from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DB_URL: str = "sqlite:///./local.db"
    USE_SQLITE: bool = False
    API_KEY: str = "change-me-please"
    TENANT_ID: str = "demo-company"
    DEFAULT_USER_ID: str = "local-user"
    DEFAULT_USER_ROLE: str = "admin"
    SURVIVAL_ENABLED: bool = True
    UPLOAD_DIR: str = "./uploads"

    # Auth / tenancy
    AUTH_CONNECTOR: str = "jwt"  # none|header|jwt
    AUTH_ALLOW_ANONYMOUS: bool = False
    AUTH_HEADER_TENANT: str = "X-Tenant-Id"
    AUTH_HEADER_USER: str = "X-User-Id"
    AUTH_HEADER_ROLE: str = "X-User-Role"

    # JWT
    JWT_SECRET_KEY: str = "cambia-esto-en-produccion-con-secreto-largo"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 480  # 8 horas

    # Seed admin inicial (solo si no existe ningún usuario en BD)
    SEED_ADMIN_USERNAME: str = "admin"
    SEED_ADMIN_PASSWORD: str = "admin1234"
    SEED_ADMIN_TENANT: str = "demo-company"

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
