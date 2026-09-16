from __future__ import annotations

import contextvars
import hashlib
import hmac
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import SessionLocal
from app.models import Company, WhatsAppAccount, utcnow
from app.services.secrets import decrypt_secret, encrypt_secret, mask_secret

settings = get_settings()
_E164 = re.compile(r"[^\d]")
_skip_cloud = contextvars.ContextVar("wa_skip_cloud", default=False)


def cloud_skipped() -> bool:
    return bool(_skip_cloud.get())


@contextmanager
def skip_cloud_send():
    """No manda a Graph API: sirve para el simulador del dashboard."""
    token = _skip_cloud.set(True)
    try:
        yield
    finally:
        _skip_cloud.reset(token)


@dataclass
class Creds:
    token: str = ""
    phone_number_id: str = ""
    waba_id: str = ""
    app_secret: str = ""
    verify_token: str = ""
    business_e164: str = ""
    graph_version: str = "v21.0"
    skip_signature: bool = True
    account_id: str | None = None
    company_id: str | None = None
    company_name: str = ""
    label: str = ""
    source: str = "env"

    @property
    def ready(self) -> bool:
        return bool(self.token and self.phone_number_id)


def graph_url(path: str, version: str | None = None) -> str:
    ver = version or settings.whatsapp_graph_version
    return f"https://graph.facebook.com/{ver}/{path.lstrip('/')}"


def _from_env() -> Creds:
    return Creds(
        token=settings.whatsapp_token or "",
        phone_number_id=settings.whatsapp_phone_number_id or "",
        app_secret=settings.whatsapp_app_secret or "",
        verify_token=settings.whatsapp_verify_token or "",
        business_e164=settings.business_whatsapp_e164 or "",
        graph_version=settings.whatsapp_graph_version,
        skip_signature=settings.whatsapp_skip_signature,
        source="env",
    )


def _from_account(account: WhatsAppAccount) -> Creds:
    company = account.company
    return Creds(
        token=decrypt_secret(account.token_enc),
        phone_number_id=account.phone_number_id or "",
        waba_id=account.waba_id or "",
        app_secret=decrypt_secret(account.app_secret_enc),
        verify_token=decrypt_secret(account.verify_token_enc),
        business_e164=account.business_e164 or "",
        graph_version=account.graph_version or "v21.0",
        skip_signature=bool(account.skip_signature),
        account_id=account.id,
        company_id=account.company_id,
        company_name=company.name if company else "",
        label=account.label or "",
        source="db",
    )


def snapshot_from_creds(creds: Creds) -> dict:
    return {
        "configured": creds.ready,
        "phone_number_id": creds.phone_number_id or "(vacío)",
        "waba_id": creds.waba_id or "(vacío)",
        "token_preview": mask_secret(creds.token) or "(vacío)",
        "app_secret_preview": mask_secret(creds.app_secret) or "(vacío)",
        "verify_token_preview": mask_secret(creds.verify_token) or "(vacío)",
        "verify_token_set": bool(creds.verify_token),
        "skip_signature": creds.skip_signature,
        "business_e164": creds.business_e164 or settings.business_whatsapp_e164,
        "webhook_url": settings.public_base_url.rstrip("/") + "/api/whatsapp/webhook",
        "graph_version": creds.graph_version,
        "company_name": creds.company_name,
        "label": creds.label,
        "source": creds.source,
        "has_token": bool(creds.token),
        "has_app_secret": bool(creds.app_secret),
        "has_verify_token": bool(creds.verify_token),
    }


async def get_company(db: AsyncSession, company_id: str | None = None) -> Company | None:
    if company_id:
        row = await db.get(Company, company_id)
        if row:
            return row
    row = await db.scalar(select(Company).where(Company.is_default.is_(True)))
    if row:
        return row
    return await db.scalar(select(Company).order_by(Company.created_at))


async def list_companies(db: AsyncSession) -> list[Company]:
    return list(await db.scalars(select(Company).order_by(Company.name)))


async def get_account_for_company(db: AsyncSession, company: Company) -> WhatsAppAccount | None:
    return await db.scalar(
        select(WhatsAppAccount)
        .where(
            WhatsAppAccount.company_id == company.id,
            WhatsAppAccount.is_active.is_(True),
        )
        .options(selectinload(WhatsAppAccount.company))
        .order_by(WhatsAppAccount.updated_at.desc())
    )


async def load_creds(db: AsyncSession | None = None, company_id: str | None = None) -> Creds:
    close = False
    session = db
    if session is None:
        session = SessionLocal()
        close = True
    try:
        company = await get_company(session, company_id)
        if company:
            account = await get_account_for_company(session, company)
            if account:
                creds = _from_account(account)
                if creds.ready or creds.phone_number_id or creds.business_e164:
                    return creds
        return _from_env()
    finally:
        if close:
            await session.close()


async def creds_for_phone_id(db: AsyncSession, phone_number_id: str) -> Creds:
    pid = (phone_number_id or "").strip()
    if pid:
        account = await db.scalar(
            select(WhatsAppAccount)
            .where(WhatsAppAccount.phone_number_id == pid)
            .options(selectinload(WhatsAppAccount.company))
        )
        if account:
            return _from_account(account)
    return await load_creds(db)


async def verify_token_ok(db: AsyncSession, incoming: str | None) -> bool:
    token = (incoming or "").strip()
    if not token:
        return False
    if settings.whatsapp_verify_token and token == settings.whatsapp_verify_token:
        return True
    accounts = list(await db.scalars(select(WhatsAppAccount)))
    for account in accounts:
        stored = decrypt_secret(account.verify_token_enc)
        if stored and hmac.compare_digest(stored, token):
            return True
    return False


async def is_configured(db: AsyncSession | None = None) -> bool:
    creds = await load_creds(db)
    return creds.ready


async def connection_snapshot(db: AsyncSession, company_id: str | None = None) -> dict:
    creds = await load_creds(db, company_id)
    return snapshot_from_creds(creds)


async def business_e164(db: AsyncSession | None = None) -> str:
    creds = await load_creds(db)
    return (creds.business_e164 or settings.business_whatsapp_e164).lstrip("+")


async def probe_connection(db: AsyncSession | None = None, company_id: str | None = None) -> dict:
    creds = await load_creds(db, company_id)
    if not creds.ready:
        return {
            "ok": False,
            "reason": "Falta Access Token o Phone Number ID. Guardalos en este formulario.",
        }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                graph_url(creds.phone_number_id, creds.graph_version),
                params={"fields": "display_phone_number,verified_name,quality_rating,id"},
                headers={"Authorization": f"Bearer {creds.token}"},
            )
            data = response.json()
            if response.is_error:
                return {
                    "ok": False,
                    "reason": _graph_error_es(data, creds.phone_number_id),
                    "status": response.status_code,
                }
            return {"ok": True, "graph": data}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)}


class CloudError(Exception):
    """Fallo de Graph API al enviar o subir media."""


def _graph_error_es(data: dict, object_id: str) -> str:
    err = data.get("error") or {}
    code = err.get("code")
    sub = err.get("error_subcode")
    msg = str(err.get("error_user_msg") or err.get("message") or data or "")
    if code == 131047:
        return (
            "Pasaron más de 24 horas desde el último mensaje de esa persona. "
            "WhatsApp solo deja responder con una plantilla de la cuenta."
        )
    if code == 131026:
        return "Ese número no tiene WhatsApp o no puede recibir mensajes de esta cuenta."
    if code in {131048, 130429}:
        return "WhatsApp limitó los envíos. Esperá unos minutos e intentá de nuevo."
    if code in {100, 803} or "does not exist" in msg.lower() or "missing permissions" in msg.lower():
        return (
            f"Meta no reconoce el ID {object_id} como Phone Number ID con este token. "
            "En WhatsApp → API Setup copiá el campo Phone number ID (no el WABA ID, "
            "no el App ID y no el número 76364961). El token tiene que ser de la misma app "
            "y de un usuario del sistema con permiso sobre ese número. Si la app está en "
            "modo Desarrollo, tu cuenta de Meta debe ser tester o admin."
        )
    if code == 190 or "session" in msg.lower() or "expired" in msg.lower():
        return "El Access Token está vencido o es inválido. Generá uno permanente (usuario del sistema) y volvé a guardarlo."
    if sub:
        return msg or f"Error de WhatsApp ({code}/{sub})"
    return msg or "Error de Graph API"


def _parse_graph_response(response: httpx.Response, object_id: str = "") -> dict:
    try:
        payload = response.json()
    except Exception:
        payload = {"error": {"message": (response.text or "respuesta vacía")[:400]}}
    if not isinstance(payload, dict):
        payload = {"error": {"message": str(payload)[:400]}}
    if response.is_error or payload.get("error"):
        raise CloudError(_graph_error_es(payload, object_id))
    return payload


def verify_signature(raw_body: bytes, header: str | None, creds: Creds | None = None) -> bool:
    skip = creds.skip_signature if creds else settings.whatsapp_skip_signature
    secret = (creds.app_secret if creds else settings.whatsapp_app_secret) or ""
    if skip or not secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[7:], expected)


async def send_text(
    to: str, body: str, db: AsyncSession | None = None, creds: Creds | None = None
) -> dict | None:
    if _skip_cloud.get():
        return {"skipped": True, "to": to, "body": body}
    creds = creds or await load_creds(db)
    if not creds.ready:
        return {"skipped": True, "to": to, "body": body}
    payload = {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+"),
        "type": "text",
        "text": {"body": body},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            graph_url(f"{creds.phone_number_id}/messages", creds.graph_version),
            headers={"Authorization": f"Bearer {creds.token}"},
            json=payload,
        )
        return _parse_graph_response(response, creds.phone_number_id)


MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".amr": "audio/amr",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".mp4": "video/mp4",
    ".3gp": "video/3gpp",
    ".pdf": "application/pdf",
}


def mime_for_path(path: Path, fallback: str = "") -> str:
    return MIME_BY_SUFFIX.get(path.suffix.lower(), fallback or "application/octet-stream")


def cloud_media_kind(kind: str, mime: str) -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    if kind == "image" and mime in {"image/jpeg", "image/png", "image/webp"}:
        return "image"
    if kind == "video" and mime in {"video/mp4", "video/3gpp"}:
        return "video"
    if kind == "audio" and mime in {"audio/aac", "audio/mp4", "audio/mpeg", "audio/amr", "audio/ogg"}:
        return "audio"
    if kind in {"image", "video", "audio"}:
        return "document"
    return kind or "document"


async def send_image(
    to: str,
    image_path: Path,
    caption: str = "",
    db: AsyncSession | None = None,
    creds: Creds | None = None,
) -> dict | None:
    return await send_media(
        to, image_path, kind="image", caption=caption, db=db, creds=creds
    )


async def send_media(
    to: str,
    path: Path,
    *,
    kind: str,
    caption: str = "",
    mime: str = "",
    voice: bool = False,
    db: AsyncSession | None = None,
    creds: Creds | None = None,
) -> dict | None:
    mime = (mime or mime_for_path(path)).split(";")[0].strip()
    send_kind = cloud_media_kind(kind, mime)
    if _skip_cloud.get():
        return {"skipped": True, "to": to, "kind": send_kind, "path": str(path)}
    creds = creds or await load_creds(db)
    if not creds.ready:
        return {"skipped": True, "to": to, "kind": send_kind, "path": str(path)}
    media_id = await _upload_media(path, creds, mime)
    blob: dict = {"id": media_id}
    if send_kind in {"image", "video", "document"} and caption:
        blob["caption"] = caption[:1024]
        if send_kind == "document":
            blob["filename"] = path.name[:240]
    if send_kind == "audio" and voice:
        blob["voice"] = True
    return await _post_cloud(to, {"type": send_kind, send_kind: blob}, db=db, creds=creds)


async def _post_cloud(
    to: str,
    payload: dict,
    db: AsyncSession | None = None,
    creds: Creds | None = None,
) -> dict:
    if _skip_cloud.get():
        return {"skipped": True, "to": to}
    creds = creds or await load_creds(db)
    if not creds or not creds.ready:
        return {"skipped": True, "to": to}
    body = {"messaging_product": "whatsapp", "to": to.lstrip("+"), **payload}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            graph_url(f"{creds.phone_number_id}/messages", creds.graph_version),
            headers={"Authorization": f"Bearer {creds.token}"},
            json=body,
        )
        return _parse_graph_response(response, creds.phone_number_id)


async def send_image_link(
    to: str,
    url: str,
    caption: str = "",
    db: AsyncSession | None = None,
    creds: Creds | None = None,
) -> dict | None:
    image: dict = {"link": url}
    if caption:
        image["caption"] = caption[:1024]
    return await _post_cloud(to, {"type": "image", "image": image}, db=db, creds=creds)


async def send_interactive_list(
    to: str,
    *,
    body: str,
    button: str,
    rows: list[dict],
    header: str = "",
    db: AsyncSession | None = None,
    creds: Creds | None = None,
) -> dict | None:
    interactive: dict = {
        "type": "list",
        "body": {"text": (body or "Catálogo")[:1024]},
        "action": {
            "button": (button or "Ver")[:20],
            "sections": [{"title": "Productos", "rows": rows[:10]}],
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    return await _post_cloud(
        to, {"type": "interactive", "interactive": interactive}, db=db, creds=creds
    )


async def send_reply_buttons(
    to: str,
    body: str,
    buttons: list[dict],
    db: AsyncSession | None = None,
    creds: Creds | None = None,
) -> dict | None:
    interactive = {
        "type": "button",
        "body": {"text": (body or "")[:1024]},
        "action": {
            "buttons": [
                {
                    "type": "reply",
                    "reply": {
                        "id": str(btn.get("id") or "")[:256],
                        "title": str(btn.get("title") or "")[:20],
                    },
                }
                for btn in buttons[:3]
                if btn.get("id") and btn.get("title")
            ]
        },
    }
    return await _post_cloud(
        to, {"type": "interactive", "interactive": interactive}, db=db, creds=creds
    )


async def _upload_media(image_path: Path, creds: Creds, mime: str = "image/png") -> str:
    mime = (mime or "application/octet-stream").split(";")[0].strip()
    async with httpx.AsyncClient(timeout=90) as client:
        with image_path.open("rb") as handle:
            response = await client.post(
                graph_url(f"{creds.phone_number_id}/media", creds.graph_version),
                headers={"Authorization": f"Bearer {creds.token}"},
                files={"file": (image_path.name, handle, mime)},
                data={"messaging_product": "whatsapp", "type": mime},
            )
        data = _parse_graph_response(response, creds.phone_number_id)
        media_id = data.get("id")
        if not media_id:
            raise CloudError("WhatsApp no devolvió el ID del archivo.")
        return media_id


async def download_media(
    media_id: str,
    dest: Path,
    db: AsyncSession | None = None,
    creds: Creds | None = None,
) -> Path:
    creds = creds or await load_creds(db)
    if not creds.ready:
        raise RuntimeError("WhatsApp no configurado")
    headers = {"Authorization": f"Bearer {creds.token}"}
    async with httpx.AsyncClient(timeout=60) as client:
        meta = await client.get(graph_url(media_id, creds.graph_version), headers=headers)
        meta.raise_for_status()
        url = meta.json()["url"]
        binary = await client.get(url, headers=headers)
        binary.raise_for_status()
        dest.write_bytes(binary.content)
    return dest


def normalize_e164(value: str) -> str:
    return _E164.sub("", value or "")


async def save_account_credentials(
    db: AsyncSession,
    *,
    company: Company,
    company_name: str,
    label: str,
    business_e164: str,
    phone_number_id: str,
    waba_id: str,
    access_token: str,
    app_secret: str,
    verify_token: str,
    graph_version: str,
    skip_signature: bool,
) -> WhatsAppAccount:
    name = (company_name or "").strip() or company.name
    company.name = name
    account = await get_account_for_company(db, company)
    if not account:
        account = WhatsAppAccount(company_id=company.id, is_active=True)
        db.add(account)
    account.label = (label or "").strip() or "Principal"
    account.business_e164 = normalize_e164(business_e164)
    account.phone_number_id = (phone_number_id or "").strip()
    account.waba_id = (waba_id or "").strip()
    account.graph_version = (graph_version or "v21.0").strip() or "v21.0"
    account.skip_signature = skip_signature
    if access_token.strip():
        account.token_enc = encrypt_secret(access_token)
    if app_secret.strip():
        account.app_secret_enc = encrypt_secret(app_secret)
    if verify_token.strip():
        account.verify_token_enc = encrypt_secret(verify_token)
    account.is_active = True
    account.updated_at = utcnow()
    await db.flush()
    return account


async def create_company(db: AsyncSession, name: str) -> Company:
    cleaned = (name or "").strip() or "Nueva empresa"
    slug_base = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-") or "empresa"
    slug = slug_base
    n = 2
    while await db.scalar(select(Company.id).where(Company.slug == slug)):
        slug = f"{slug_base}-{n}"
        n += 1
    company = Company(name=cleaned, slug=slug, is_default=False)
    db.add(company)
    await db.flush()
    db.add(WhatsAppAccount(company_id=company.id, label="Principal", is_active=True))
    await db.flush()
    return company
