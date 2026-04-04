from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DB_URL: str = "sqlite:///./local.db"
    DATABASE_URL: Optional[str] = None
    USE_SQLITE: bool = False
    DB_SSL_REQUIRE: bool = True
    DB_POOL_PRE_PING: bool = True
    DB_POOL_RECYCLE_SECONDS: int = 1800
    API_KEY: str = "change-me-please"
    TENANT_ID: str = "demo-company"
    DEFAULT_USER_ID: str = "local-user"
    DEFAULT_USER_ROLE: str = "admin"
    SURVIVAL_ENABLED: bool = True
    UPLOAD_DIR: str = "./uploads"
    LOCAL_BACKUP_ENABLED: bool = True
    LOCAL_BACKUP_DIR: str = "./backups"
    LOCAL_BACKUP_PRETTY: bool = True
    LOCAL_BACKUP_INCLUDE_USERS: bool = False

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

    # Groq AI (parser fallback)
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "llama-3.1-8b-instant"
    GROQ_PARSER_ENABLED: bool = True
    AI_FIELD_CORRECTION_ENABLED: bool = True
    AI_FIELD_CORRECTION_MIN_CONFIDENCE: float = 0.70
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4.1-mini"
    AI_FIELD_VALIDATION_ENABLED: bool = False
    AI_FIELD_VALIDATION_AUTO_APPLY: bool = False
    AI_FIELD_VALIDATION_MIN_CONFIDENCE: float = 0.85

    # Geolocalización por código postal (MX)
    GEO_CP_ENABLED: bool = True
    GEO_CP_TIMEOUT: int = 6
    GEO_ADDRESS_ENABLED: bool = True
    GEO_ADDRESS_TIMEOUT: int = 12
    GEO_USER_AGENT: str = "digestor-fiscal/0.1"

    # OpenCage Geocoding (freemium)
    GEO_OPENCAGE_ENABLED: bool = True
    GEO_OPENCAGE_TIMEOUT: int = 10
    GEO_OPENCAGE_BASE_URL: str = "https://api.opencagedata.com/geocode/v1/json"
    GEO_OPENCAGE_API_KEY: Optional[str] = None

    # Versión
    SERVICE_VERSION: str = "0.1.0"
    DOCUMENT_QUEUE_WORKERS: int = 1
    SYNC_OUTBOUND_AUTOPOLL: bool = True
    SYNC_OUTBOUND_POLL_SECONDS: int = 5
    SYNC_OUTBOUND_WEBHOOK_URL: Optional[str] = None
    SYNC_OUTBOUND_MAX_ATTEMPTS: int = 6
    SYNC_OUTBOUND_ALLOW_NOOP_TARGET: bool = True

    # Facturación / Timbrado CFDI (sandbox)
    TIMBRACFDI_BASE_URL: str = "https://pruebas.timbracfdi33.mx:1444/api/v2"
    TIMBRACFDI_TOKEN: Optional[str] = None
    TIMBRACFDI_TIMEOUT: int = 45

    # Developer-led GTM
    DEVELOPER_PORTAL_ENABLED: bool = True
    DEVELOPER_SANDBOX_ENABLED: bool = True
    DEVELOPER_SANDBOX_TENANT_PREFIX: str = "sandbox"
    DEVELOPER_SANDBOX_USERNAME_PREFIX: str = "dev"
    DEVELOPER_SANDBOX_DEFAULT_ROLE: str = "operator"
    DEVELOPER_SANDBOX_TOKEN_EXPIRE_MINUTES: int = 1440

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def effective_db_url(self) -> str:
        return (self.DATABASE_URL or self.DB_URL).strip()


settings = Settings()
