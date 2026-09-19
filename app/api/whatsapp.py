from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ProcessedWebhook
from app.services import conversation, inbox, tenancy, whatsapp
from app.services.whatsapp import CloudError, use_creds

router = APIRouter(tags=["whatsapp"])
log = logging.getLogger("whatsapp.webhook")


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
        log.info("webhook verificado por Meta")
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
        log.warning(
            "firma inválida phone_id=%s company=%s skip=%s secret=%s",
            phone_id,
            creds.company_id,
            creds.skip_signature,
            bool(creds.app_secret),
        )
        raise HTTPException(status_code=403, detail="Firma inválida")
    handled = await _dispatch(db, payload, creds)
    log.info(
        "webhook ok phone_id=%s company=%s inbound=%s",
        phone_id,
        creds.company_id,
        handled,
    )
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


async def _seen(db: AsyncSession, msg_id: str) -> bool:
    if not msg_id:
        return False
    db.add(ProcessedWebhook(provider="whatsapp", provider_id=msg_id))
    try:
        await db.flush()
        return False
    except IntegrityError:
        await db.rollback()
        return True
    except Exception:
        log.exception("no se pudo marcar webhook %s", msg_id)
        await db.rollback()
        return True


async def _dispatch(db: AsyncSession, payload: dict, creds) -> int:
    handled = 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            messages = value.get("messages") or []
            statuses = value.get("statuses") or []
            meta = value.get("metadata") or {}
            log.info(
                "webhook field=%s messages=%s statuses=%s display=%s phone_id=%s",
                change.get("field"),
                len(messages),
                len(statuses),
                meta.get("display_phone_number"),
                meta.get("phone_number_id"),
            )
            contacts = {c.get("wa_id"): c for c in value.get("contacts") or []}
            for message in messages:
                msg_id = message.get("id") or ""
                if await _seen(db, msg_id):
                    continue
                wa_id = message.get("from") or ""
                profile = (contacts.get(wa_id) or {}).get("profile") or {}
                name = profile.get("name") or wa_id
                parsed = inbox.parse_inbound_payload(message)
                try:
                    with use_creds(creds), tenancy.use_company(creds.company_id):
                        await inbox.ingest_inbound(
                            db, phone=wa_id, name=name, parsed=parsed, creds=creds
                        )
                    await db.commit()
                    handled += 1
                except Exception:
                    log.exception("no se guardó el mensaje de %s", wa_id)
                    await db.rollback()
                    continue
                try:
                    with use_creds(creds), tenancy.use_company(creds.company_id):
                        media_id = parsed.get("media_id") if parsed.get("msg_type") == "image" else None
                        replies = await conversation.run_conversation(
                            db,
                            phone=wa_id,
                            name=name,
                            text=parsed.get("body") or None,
                            image_media_id=media_id,
                        )
                        for reply in replies:
                            try:
                                sent = await whatsapp.send_text(wa_id, reply, db=db, creds=creds)
                            except CloudError:
                                log.exception("Graph no envió respuesta a %s", wa_id)
                                sent = None
                            await inbox.record_bot_text(db, wa_id, reply, sent)
                    await db.commit()
                except Exception:
                    log.exception("el bot falló para %s; el mensaje sí quedó en la bandeja", wa_id)
                    await db.rollback()
    return handled
