"""Login, contraseñas y 2FA (TOTP)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import re
import struct
import time
import unicodedata
from urllib.parse import quote

import qrcode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.models import Company, User
from app.services.secrets import decrypt_secret, encrypt_secret

ROLE_SUPERADMIN = "superadmin"
ROLE_VENDOR = "vendor"
_PBKDF2_ROUNDS = 180_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    raw = (stored or "").strip()
    parts = raw.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2":
        return False
    try:
        rounds = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(digest, expected)


def slugify(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return (slug or "empresa")[:80]


async def unique_slug(db: AsyncSession, name: str) -> str:
    base = slugify(name)
    slug = base
    idx = 2
    while await db.scalar(select(Company.id).where(Company.slug == slug)):
        slug = f"{base}-{idx}"[:80]
        idx += 1
    return slug


async def find_user(db: AsyncSession, username: str) -> User | None:
    name = (username or "").strip()
    if not name:
        return None
    return await db.scalar(select(User).where(User.username == name))


async def get_user(db: AsyncSession, user_id: str) -> User | None:
    if not user_id:
        return None
    return await db.get(User, user_id)


def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _b32_key(secret: str) -> bytes:
    raw = re.sub(r"[^A-Z2-7]", "", (secret or "").upper())
    pad = (-len(raw)) % 8
    return base64.b32decode(raw + ("=" * pad))


def _totp_at(secret: str, for_time: float, interval: int = 30, digits: int = 6) -> str:
    counter = int(for_time // interval)
    digest = hmac.new(_b32_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{number % (10 ** digits):0{digits}d}"


def totp_uri(secret: str, username: str, issuer: str) -> str:
    label = quote(f"{issuer}:{username}")
    iss = quote(issuer)
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={iss}"
        "&algorithm=SHA1&digits=6&period=30"
    )


def totp_qr_data_uri(uri: str) -> str:
    image = qrcode.make(uri)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def verify_totp(secret: str, code: str) -> bool:
    token = re.sub(r"\D", "", code or "")
    if len(token) != 6 or not secret:
        return False
    now = time.time()
    for skew in (-1, 0, 1):
        if hmac.compare_digest(_totp_at(secret, now + skew * 30), token):
            return True
    return False


def user_totp_secret(user: User) -> str:
    return decrypt_secret(user.totp_secret_enc or "")


def store_totp_secret(user: User, secret: str) -> None:
    user.totp_secret_enc = encrypt_secret(secret)


async def establish_session(request: Request, db: AsyncSession, user: User) -> None:
    from app.services.whatsapp import get_company

    if user.role == ROLE_VENDOR:
        company = await db.get(Company, user.company_id) if user.company_id else None
    else:
        company = await get_company(db, user.company_id)
    request.session.clear()
    request.session["user"] = user.username
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    request.session["totp_enabled"] = bool(user.totp_enabled)
    request.session["company_id"] = company.id if company else (user.company_id or "")
    request.session["company_name"] = company.name if company else ""


def is_superadmin(request: Request) -> bool:
    return request.session.get("role") == ROLE_SUPERADMIN
