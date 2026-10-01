"""JWT authentication and role-based access control helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

security = HTTPBearer(auto_error=False)


ROLE_PRIVILEGES: dict[str, list[str]] = {
    "User": ["chat:use", "jurisdictions:read"],
    "Privs": [
        "chat:use",
        "jurisdictions:read",
        "documents:read",
        "documents:upload",
        "documents:reprocess",
        "documents:delete",
        "ingest:run",
    ],
    "Admin": [
        "chat:use",
        "jurisdictions:read",
        "documents:read",
        "documents:upload",
        "documents:reprocess",
        "documents:delete",
        "ingest:run",
        "rbac:read",
    ],
}


@dataclass(frozen=True)
class AuthUser:
    username: str
    role: str
    privileges: list[str]


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(message: str) -> str:
    settings = get_settings()
    digest = hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        message.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(digest)


def create_access_token(username: str, role: str) -> str:
    settings = get_settings()
    now = int(time.time())
    header = {"alg": settings.jwt_algorithm, "typ": "JWT"}
    payload = {
        "sub": username,
        "role": role,
        "privileges": ROLE_PRIVILEGES[role],
        "iat": now,
        "exp": now + settings.jwt_expires_minutes * 60,
    }
    signing_input = ".".join(
        [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    return f"{signing_input}.{_sign(signing_input)}"


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        header_part, payload_part, signature = token.split(".")
        signing_input = f"{header_part}.{payload_part}"
        if not hmac.compare_digest(signature, _sign(signing_input)):
            raise ValueError("Invalid signature")
        header = json.loads(_b64url_decode(header_part))
        if header.get("alg") != get_settings().jwt_algorithm:
            raise ValueError("Unsupported algorithm")
        payload = json.loads(_b64url_decode(payload_part))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        ) from exc

    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired",
        )
    return payload


def get_demo_users() -> dict[str, dict[str, str]]:
    settings = get_settings()
    return {
        "admin": {"password": settings.admin_password, "role": "Admin"},
        "privs": {"password": settings.privs_password, "role": "Privs"},
        "user": {"password": settings.user_password, "role": "User"},
    }


def authenticate_user(username: str, password: str) -> AuthUser | None:
    record = get_demo_users().get(username.strip().lower())
    if not record or not hmac.compare_digest(password, record["password"]):
        return None
    role = record["role"]
    return AuthUser(username=username.strip().lower(), role=role, privileges=ROLE_PRIVILEGES[role])


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> AuthUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    payload = decode_access_token(credentials.credentials)
    username = str(payload.get("sub") or "")
    role = str(payload.get("role") or "")
    if role not in ROLE_PRIVILEGES or not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    return AuthUser(username=username, role=role, privileges=ROLE_PRIVILEGES[role])


def require_privilege(privilege: str):
    def dependency(user: AuthUser = Depends(current_user)) -> AuthUser:
        if privilege not in user.privileges:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires privilege: {privilege}",
            )
        return user

    return dependency
