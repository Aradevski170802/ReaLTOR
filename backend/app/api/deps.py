"""Request dependencies: database session, authenticated actor and role checks."""

from __future__ import annotations

import hashlib
import secrets as pysecrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import User

ROLE_RANK = {"viewer": 0, "analyst": 1, "admin": 2}


@dataclass(frozen=True)
class Actor:
    name: str
    role: str
    user_id: int | None = None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return "usi_" + pysecrets.token_urlsafe(32)


def current_actor(authorization: str | None = Header(default=None), session: Session = Depends(get_session)) -> Actor:
    settings = get_settings()
    if settings.auth_mode == "local":
        return Actor("local-user", "admin")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    user = session.scalar(select(User).where(User.token_hash == hash_token(authorization[7:].strip()), User.is_active.is_(True)))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token", headers={"WWW-Authenticate": "Bearer"})
    return Actor(user.email, user.role, user.id)


def require(role: str):
    def dependency(actor: Actor = Depends(current_actor)) -> Actor:
        if ROLE_RANK.get(actor.role, -1) < ROLE_RANK[role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"This action requires the '{role}' role")
        return actor

    return dependency


viewer = require("viewer")
analyst = require("analyst")
admin = require("admin")


def get_or_404(session: Session, model, object_id: int | str, label: str | None = None):
    obj = session.get(model, object_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label or model.__name__} {object_id} not found")
    return obj
