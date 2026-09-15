from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import UPLOADS_DIR
from app.models import Conversation, ConversationState
from app.services import conversation as conv_svc
from app.services import inbox
from app.services.whatsapp import skip_cloud_send

DEFAULT_PHONE = "59170009999"
DEFAULT_NAME = "Cliente de prueba"


def clean_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits[:32] or DEFAULT_PHONE


def media_url(path: str | None) -> str:
    if not path:
        return ""
    try:
        rel = Path(path).resolve().relative_to(UPLOADS_DIR.resolve())
        return f"/uploads/{rel.as_posix()}"
    except ValueError:
        return ""


def serialize_message(msg) -> dict:
    created = msg.created_at.isoformat(sep=" ", timespec="seconds") if msg.created_at else ""
    rich = None
    if msg.msg_type in {"catalog", "product_card", "choices"}:
        try:
            rich = json.loads(msg.body or "{}")
        except json.JSONDecodeError:
            rich = None
    image = ""
    if isinstance(rich, dict):
        image = str(rich.get("image") or "")
        if not image:
            items = rich.get("items") or []
            if items and items[0].get("image"):
                image = str(items[0]["image"])
    return {
        "id": msg.id,
        "direction": msg.direction,
        "source": msg.source,
        "msg_type": msg.msg_type,
        "body": msg.body or "",
        "media_url": image or media_url(msg.media_path),
        "rich": rich,
        "created_at": created,
    }


async def snapshot(db: AsyncSession, phone: str) -> dict:
    phone = clean_phone(phone)
    state = await db.get(ConversationState, phone)
    messages = await inbox.list_messages(db, phone)
    return {
        "phone": phone,
        "step": state.step if state else "idle",
        "engine": await conv_svc.engine_label(db),
        "messages": [serialize_message(m) for m in messages],
    }


async def send_as_customer(
    db: AsyncSession, *, phone: str, name: str, text: str, as_image: bool = False
) -> dict:
    phone = clean_phone(phone)
    name = (name or DEFAULT_NAME).strip() or DEFAULT_NAME
    body = (text or "").strip()
    if as_image:
        body = body or "comprobante"
    elif not body:
        snap = await snapshot(db, phone)
        snap["replies"] = []
        return snap
    media_id = f"sim-{uuid4().hex[:12]}" if as_image else None
    parsed = {
        "msg_type": "image" if as_image else "text",
        "body": body,
        "media_id": media_id,
        "mime_type": "image/jpeg" if as_image else "",
        "provider_id": f"sim-{phone}-{uuid4().hex[:12]}",
    }
    await inbox.ingest_inbound(db, phone=phone, name=name, parsed=parsed)
    try:
        with skip_cloud_send():
            replies = await conv_svc.run_conversation(
                db,
                phone=phone,
                name=name,
                text=body,
                image_media_id=media_id,
            )
    except Exception as exc:  # noqa: BLE001
        replies = [f"El bot no pudo responder: {exc}"]
    recent = [
        (m.body or "").strip()
        for m in await inbox.list_messages(db, phone)
        if m.direction == "outbound"
    ]
    seen = set(recent[-12:])
    for reply in replies:
        text_reply = (reply or "").strip()
        if not text_reply or text_reply in seen:
            continue
        await inbox.record_bot_text(db, phone, text_reply, {"skipped": True})
        seen.add(text_reply)
    await db.commit()
    snap = await snapshot(db, phone)
    snap["replies"] = replies
    return snap


async def send_as_customer_file(
    db: AsyncSession,
    *,
    phone: str,
    name: str,
    caption: str,
    raw: bytes,
    mime: str,
    filename: str,
) -> dict:
    phone = clean_phone(phone)
    name = (name or DEFAULT_NAME).strip() or DEFAULT_NAME
    kind = inbox.kind_from_upload(mime, filename)
    dest = inbox.save_chat_file(phone, raw, mime, filename)
    media_id = f"sim-{uuid4().hex[:12]}" if kind == "image" else None
    parsed = {
        "msg_type": kind,
        "body": (caption or "").strip(),
        "media_id": media_id,
        "media_path": str(dest),
        "mime_type": inbox.mime_key(mime),
        "provider_id": f"sim-{phone}-{uuid4().hex[:12]}",
    }
    await inbox.ingest_inbound(db, phone=phone, name=name, parsed=parsed)
    try:
        with skip_cloud_send():
            replies = await conv_svc.run_conversation(
                db,
                phone=phone,
                name=name,
                text=caption or None,
                image_media_id=media_id,
            )
    except Exception as exc:  # noqa: BLE001
        replies = [f"El bot no pudo responder: {exc}"]
    recent = [
        (m.body or "").strip()
        for m in await inbox.list_messages(db, phone)
        if m.direction == "outbound"
    ]
    seen = set(recent[-12:])
    for reply in replies:
        text_reply = (reply or "").strip()
        if not text_reply or text_reply in seen:
            continue
        await inbox.record_bot_text(db, phone, text_reply, {"skipped": True})
        seen.add(text_reply)
    await db.commit()
    snap = await snapshot(db, phone)
    snap["replies"] = replies
    return snap


async def reset_chat(db: AsyncSession, phone: str) -> dict:
    phone = clean_phone(phone)
    state = await db.get(ConversationState, phone)
    if state:
        await db.delete(state)
    conv = await db.scalar(
        select(Conversation)
        .where(Conversation.phone == phone)
        .options(selectinload(Conversation.messages))
    )
    if conv:
        conv.messages.clear()
        conv.last_preview = ""
        conv.unread_count = 0
    await db.commit()
    return await snapshot(db, phone)
