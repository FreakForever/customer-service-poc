"""Staff identity for API routes (NFR-SEC-01, LLD-14): short-lived HS256 token issued by the web app (apps/web/lib/staffToken.ts)
after Google sign-in. No token → 401, wrong role → 403."""

import hashlib
from collections.abc import Callable
from typing import Annotated, Literal

import jwt
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.api.chat import TokenDep
from app.config.settings import get_settings

Role = Literal["agent", "admin"]


class Staff(BaseModel):
    email: str
    name: str = ""
    role: Role


def current_staff(token: TokenDep) -> Staff:
    secret = get_settings().nextauth_secret.get_secret_value()
    if not secret or not token:
        raise HTTPException(401, "staff sign-in required")
    try:
        c = jwt.decode(token, hashlib.sha256(f"staff-api:{secret}".encode()).digest(), algorithms=["HS256"], audience="mcs-agent-api",
                       issuer="mcs-web", options={"require": ["exp", "iat", "sub", "aud", "iss"]})
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "invalid or expired staff token") from exc
    if c.get("role") not in ("agent", "admin"):
        raise HTTPException(403, "no staff role")
    return Staff(email=c["sub"], name=str(c.get("name") or ""), role=c["role"])


def require(role: Role) -> Callable[..., Staff]:
    def dep(staff: Annotated[Staff, Depends(current_staff)]) -> Staff:
        if staff.role not in (role, "admin"):
            raise HTTPException(403, f"the {role} role is required")
        return staff
    return dep


AgentDep = Annotated[Staff, Depends(require("agent"))]
AdminDep = Annotated[Staff, Depends(require("admin"))]
