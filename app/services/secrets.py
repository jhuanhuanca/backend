"""Cifrado de secretos de tenant (token Cloud API) con la SECRET_KEY del backend."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    text = (plain or "").strip()
    if not text:
        return ""
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt_secret(blob: str) -> str:
    raw = (blob or "").strip()
    if not raw:
        return ""
    try:
        return _fernet().decrypt(raw.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""


def mask_secret(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    if len(token) > 12:
        return token[:6] + "…" + token[-4:]
    return "••••"
