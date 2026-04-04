from datetime import datetime, timedelta
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.core.auth import SecurityContext, role_guard
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.services.user_service import (
    authenticate_user,
    create_user,
    get_user_by_username,
)
from app.services.jwt_service import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- schemas ----------

class LoginRequest(BaseModel):
    username: str
    password: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "username": "demo_prueba_01",
                "password": "Demo12345A"
            }
        }
    }


class RegisterRequest(BaseModel):
    username: str
    password: str
    tenant_id: str
    role: str = "viewer"

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        allowed = {"viewer", "operator", "admin", "superadmin"}
        if v not in allowed:
            raise ValueError(f"Rol inválido. Opciones: {allowed}")
        return v

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres.")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    username: str
    role: str
    tenant_id: str


class SandboxSignupRequest(BaseModel):
    username: str | None = None
    password: str | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "username": "demo_prueba_01",
                "password": "Demo12345A"
            }
        }
    }


class SandboxSignupResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    username: str
    role: str
    tenant_id: str
    docs_url: str
    openapi_url: str


class UserOut(BaseModel):
    id: str
    username: str
    tenant_id: str
    role: str
    is_active: bool
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


# ---------- endpoints ----------

@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Usuario desactivado.")

    expire_minutes = settings.JWT_EXPIRE_MINUTES
    token = create_access_token(
        data={"sub": user.username, "tenant": user.tenant_id, "role": user.role},
        expires_delta=timedelta(minutes=expire_minutes),
    )
    return TokenResponse(
        access_token=token,
        expires_in=expire_minutes * 60,
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
    )


@router.post("/register", response_model=UserOut, status_code=201)
def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
    ctx: SecurityContext = Depends(role_guard("admin", "superadmin")),
):
    # admin solo puede crear usuarios en su propio tenant
    if ctx.role != "superadmin" and body.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=403, detail="No puedes crear usuarios en otro tenant.")

    if get_user_by_username(db, body.username):
        raise HTTPException(status_code=409, detail="El nombre de usuario ya existe.")

    user = create_user(
        db,
        username=body.username,
        password=body.password,
        tenant_id=body.tenant_id,
        role=body.role,
    )
    return user


@router.get("/me", response_model=UserOut)
def me(
    db: Session = Depends(get_db),
    ctx: SecurityContext = Depends(role_guard("viewer", "operator", "admin", "superadmin")),
):
    user = get_user_by_username(db, ctx.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return user


@router.post("/sandbox/signup", response_model=SandboxSignupResponse, status_code=201)
def sandbox_signup(body: SandboxSignupRequest, db: Session = Depends(get_db)):
    if not settings.DEVELOPER_SANDBOX_ENABLED:
        raise HTTPException(status_code=404, detail="Sandbox no disponible.")

    username = (body.username or "").strip().lower()
    if not username:
        token_suffix = secrets.token_hex(3)
        username = f"{settings.DEVELOPER_SANDBOX_USERNAME_PREFIX}_{token_suffix}"

    if len(username) < 4:
        raise HTTPException(status_code=422, detail="username debe tener al menos 4 caracteres.")

    # Creamos tenant por usuario para aislar pruebas.
    tenant_id = f"{settings.DEVELOPER_SANDBOX_TENANT_PREFIX}-{username}"

    if get_user_by_username(db, username):
        raise HTTPException(status_code=409, detail="El nombre de usuario ya existe.")

    password = body.password or f"{secrets.token_urlsafe(10)}A1"
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="password debe tener al menos 8 caracteres.")

    role = settings.DEVELOPER_SANDBOX_DEFAULT_ROLE
    if role not in {"viewer", "operator", "admin", "superadmin"}:
        role = "operator"

    user = create_user(
        db,
        username=username,
        password=password,
        tenant_id=tenant_id,
        role=role,
    )

    expire_minutes = settings.DEVELOPER_SANDBOX_TOKEN_EXPIRE_MINUTES
    token = create_access_token(
        data={"sub": user.username, "tenant": user.tenant_id, "role": user.role},
        expires_delta=timedelta(minutes=expire_minutes),
    )

    return SandboxSignupResponse(
        access_token=token,
        expires_in=expire_minutes * 60,
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
