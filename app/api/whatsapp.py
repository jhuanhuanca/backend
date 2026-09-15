from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ProcessedWebhook
from app.services import conversation, inbox, whatsapp

router = APIRouter(tags=["whatsapp"])


class SimulateIn(BaseModel):
    phone: str
    text: str = ""
    name: str = ""
    msg_type: str = "text"


@router.get("/api/whatsapp/webhook")
async def verify_webhook(
    db: AsyncSession = Depends(get_db),
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and await whatsapp.verify_token_ok(db, hub_verify_token):
        return PlainTextResponse(hub_challenge or "")
    raise HTTPException(status_code=403, detail="Verificación WhatsApp fallida")


@router.post("/api/whatsapp/webhook")
async def receive_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
):
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc
    phone_id = _phone_number_id(payload)
    creds = await whatsapp.creds_for_phone_id(db, phone_id)
    if not whatsapp.verify_signature(raw, x_hub_signature_256, creds):
        raise HTTPException(status_code=403, detail="Firma inválida")
    await _dispatch(db, payload, creds)
    return {"ok": True}


@router.post("/api/dev/whatsapp")
async def simulate_whatsapp(payload: SimulateIn, db: AsyncSession = Depends(get_db)):
    from app.config import get_settings as _settings

    if _settings().is_production:
        raise HTTPException(status_code=404, detail="No disponible")
    parsed = {
        "msg_type": payload.msg_type or "text",
        "body": payload.text,
        "media_id": None,
        "mime_type": "",
        "provider_id": f"sim-{payload.phone}",
    }
    await inbox.ingest_inbound(
        db, phone=payload.phone, name=payload.name or payload.phone, parsed=parsed
    )
    replies = await conversation.run_conversation(
        db, phone=payload.phone, name=payload.name or payload.phone, text=payload.text
    )
    for reply in replies:
        sent = await whatsapp.send_text(payload.phone, reply, db=db)
        await inbox.record_bot_text(db, payload.phone, reply, sent)
    await db.commit()
    return {"ok": True, "replies": replies}


def _phone_number_id(payload: dict) -> str:
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            meta = (change.get("value") or {}).get("metadata") or {}
            pid = meta.get("phone_number_id") or ""
            if pid:
                return str(pid)
    return ""


async def _dispatch(db: AsyncSession, payload: dict, creds) -> None:
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            contacts = {c.get("wa_id"): c for c in value.get("contacts") or []}
            for message in value.get("messages") or []:
                msg_id = message.get("id")
                if msg_id:
                    db.add(ProcessedWebhook(provider="whatsapp", provider_id=msg_id))
                    try:
                        await db.flush()
                    except Exception:
                        await db.rollback()
                        continue
                wa_id = message.get("from") or ""
                profile = (contacts.get(wa_id) or {}).get("profile") or {}
                name = profile.get("name") or wa_id
                parsed = inbox.parse_inbound_payload(message)
                await inbox.ingest_inbound(db, phone=wa_id, name=name, parsed=parsed, creds=creds)
                media_id = parsed.get("media_id") if parsed.get("msg_type") == "image" else None
                replies = await conversation.run_conversation(
                    db,
                    phone=wa_id,
                    name=name,
                    text=parsed.get("body") or None,
                    image_media_id=media_id,
                )
                for reply in replies:
                    sent = await whatsapp.send_text(wa_id, reply, db=db, creds=creds)
                    await inbox.record_bot_text(db, wa_id, reply, sent)
                await db.commit()

