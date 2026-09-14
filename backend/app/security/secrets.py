"""Encrypted-at-rest secret storage and log redaction.

* Secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using USI_MASTER_KEY.
* Plaintext never leaves the server: APIs return only `configured` and a short hint.
* All registered secret values, plus common credential patterns, are scrubbed from logs.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.models import EncryptedSecret

log = logging.getLogger(__name__)

_known_values: set[str] = set()
_lock = threading.Lock()

_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|apikey|access[_-]?token|token|password|passwd|secret)(\s*[=:]\s*|\"\s*:\s*\")([^\s&\"',;]+)"),
    re.compile(r"(?i)(x-api-key|authorization)(:\s*)(\S+)"),
    re.compile(r"(?i)([?&]key=)([^&\s]+)"),
]


class SecretConfigError(RuntimeError):
    pass


def _dev_key_path() -> Path:
    return get_settings().data_dir / ".master_key"


def _key_from_setting(value: str) -> bytes:
    """Accept a Fernet key, or derive one from a random passphrase of at least 32 characters (e.g. a platform-generated value)."""
    raw = value.strip().encode()
    try:
        Fernet(raw)
        return raw
    except (ValueError, TypeError):
        pass
    if len(value.strip()) >= 32:
        return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    raise SecretConfigError("USI_MASTER_KEY must be a Fernet key or a random passphrase of at least 32 characters")


def load_master_key() -> bytes:
    settings = get_settings()
    if settings.master_key:
        key = _key_from_setting(settings.master_key)
    elif settings.is_production:
        raise SecretConfigError("USI_MASTER_KEY must be set in production")
    else:
        path = _dev_key_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(Fernet.generate_key())
            log.warning("Generated development master key at %s (set USI_MASTER_KEY in production)", path)
        key = path.read_bytes().strip()
    try:
        Fernet(key)
    except (ValueError, TypeError) as exc:
        raise SecretConfigError("USI_MASTER_KEY is not a valid Fernet key") from exc
    return key


def _fernet() -> Fernet:
    return Fernet(load_master_key())


def register_secret_value(value: str | None) -> None:
    if value and len(value) >= 4:
        with _lock:
            _known_values.add(value)


def redact_text(text: str) -> str:
    if not text:
        return text
    with _lock:
        values = sorted(_known_values, key=len, reverse=True)
    for value in values:
        text = text.replace(value, "***REDACTED***")
    for pattern in _PATTERNS:
        if pattern.groups == 3:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***", text)
        else:
            text = pattern.sub(lambda m: f"{m.group(1)}***REDACTED***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        redacted = redact_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_log_redaction() -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for f in root.filters):
        root.addFilter(RedactingFilter())
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


def _hint(value: str) -> str:
    return f"…{value[-4:]}" if len(value) >= 8 else "…"


def set_secret(session: Session, name: str, value: str, actor: str) -> EncryptedSecret:
    if not value:
        raise ValueError("Secret value must not be empty")
    token = _fernet().encrypt(value.encode()).decode()
    row = session.scalar(select(EncryptedSecret).where(EncryptedSecret.name == name))
    if row is None:
        row = EncryptedSecret(name=name, ciphertext=token, hint=_hint(value), updated_by=actor)
        session.add(row)
    else:
        row.ciphertext = token
        row.hint = _hint(value)
        row.updated_by = actor
        row.updated_at = utcnow()
    register_secret_value(value)
    return row


def env_secret_var(name: str) -> str:
    """Environment-variable name a secret can be supplied through, e.g. provider.attom.api_key -> USI_SECRET_PROVIDER_ATTOM_API_KEY."""
    return "USI_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def get_secret(session: Session, name: str) -> str | None:
    row = session.scalar(select(EncryptedSecret).where(EncryptedSecret.name == name))
    if row is None:
        # Fallback: allow supplying secrets by environment variable (handy for headless deployments and CI).
        env_value = os.environ.get(env_secret_var(name))
        if env_value:
            register_secret_value(env_value)
            return env_value
        return None
    try:
        value = _fernet().decrypt(row.ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise SecretConfigError(f"Secret {name!r} cannot be decrypted with the current master key") from exc
    register_secret_value(value)
    return value


def delete_secret(session: Session, name: str) -> bool:
    row = session.scalar(select(EncryptedSecret).where(EncryptedSecret.name == name))
    if row is None:
        return False
    session.delete(row)
    return True


def secret_status(session: Session, name: str) -> dict[str, object]:
    row = session.scalar(select(EncryptedSecret).where(EncryptedSecret.name == name))
    if row is None:
        return {"name": name, "configured": False}
    return {"name": name, "configured": True, "hint": row.hint, "updated_at": row.updated_at, "updated_by": row.updated_by}
