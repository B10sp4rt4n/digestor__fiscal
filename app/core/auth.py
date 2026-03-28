from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError

from app.core.config import settings

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class SecurityContext:
    tenant_id: str
    user_id: str
    role: str


VALID_ROLES = {"viewer", "operator", "admin", "superadmin"}


def _normalize_role(role: str) -> str:
    role = (role or "").strip().lower()
    return role if role in VALID_ROLES else "viewer"


def _context_from_jwt(token: str) -> SecurityContext:
    from app.services.jwt_service import decode_access_token  # lazy import para evitar circular

    try:
        payload = decode_access_token(token)
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado. Vuelve a iniciar sesión.")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido.")

    username = payload.get("sub")
    tenant = payload.get("tenant")
    role = _normalize_role(payload.get("role") or "viewer")

    if not username or not tenant:
        raise HTTPException(status_code=401, detail="Token malformado.")

    return SecurityContext(tenant_id=tenant, user_id=username, role=role)


def get_security_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> SecurityContext:
    connector = settings.AUTH_CONNECTOR.strip().lower()

    if connector == "none":
        return SecurityContext(
            tenant_id=settings.TENANT_ID,
            user_id=settings.DEFAULT_USER_ID,
            role=_normalize_role(settings.DEFAULT_USER_ROLE),
        )

    if connector == "jwt":
        if credentials and credentials.scheme.lower() == "bearer":
            return _context_from_jwt(credentials.credentials)

        if settings.AUTH_ALLOW_ANONYMOUS:
            return SecurityContext(
                tenant_id=settings.TENANT_ID,
                user_id=settings.DEFAULT_USER_ID,
                role=_normalize_role(settings.DEFAULT_USER_ROLE),
            )

        raise HTTPException(
            status_code=401,
            detail="Se requiere autenticación. Inicia sesión en /auth/login.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # connector == "header" (legado)
    tenant = request.headers.get(settings.AUTH_HEADER_TENANT)
    user_id = request.headers.get(settings.AUTH_HEADER_USER)
    role = _normalize_role(request.headers.get(settings.AUTH_HEADER_ROLE) or settings.DEFAULT_USER_ROLE)

    if tenant and user_id:
        return SecurityContext(tenant_id=tenant, user_id=user_id, role=role)

    if settings.AUTH_ALLOW_ANONYMOUS:
        return SecurityContext(
            tenant_id=tenant or settings.TENANT_ID,
            user_id=user_id or settings.DEFAULT_USER_ID,
            role=role,
        )

    raise HTTPException(status_code=401, detail="Credenciales de acceso requeridas.")


def is_auth_disabled() -> bool:
    return settings.AUTH_CONNECTOR.strip().lower() == "none"


def role_guard(*allowed_roles: str) -> Callable[[SecurityContext], SecurityContext]:
    allowed = {_normalize_role(r) for r in allowed_roles}

    def _dependency(ctx: SecurityContext = Depends(get_security_context)) -> SecurityContext:
        if ctx.role not in allowed:
            raise HTTPException(status_code=403, detail="No tienes permisos para esta operación.")
        return ctx

    return _dependency


def enforce_tenant_scope(ctx: SecurityContext, requested_tenant: str | None) -> str:
    if is_auth_disabled():
        return requested_tenant or ctx.tenant_id

    if ctx.role == "superadmin":
        return requested_tenant or ctx.tenant_id

    if requested_tenant and requested_tenant != ctx.tenant_id:
        raise HTTPException(status_code=403, detail="No puedes acceder a otro tenant.")

    return ctx.tenant_id


def can_access_tenant(ctx: SecurityContext, tenant_id: str) -> bool:
    if is_auth_disabled():
        return True
    if ctx.role == "superadmin":
        return True
    return ctx.tenant_id == tenant_id
