from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MEDIA_DIR
from app.models import ChatMessage, Conversation, utcnow
from app.services import tenancy, whatsapp

EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "audio/ogg": ".ogg",
    "audio/opus": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-wav": ".wav",
    "video/mp4": ".mp4",
    "video/3gpp": ".3gp",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "application/pdf": ".pdf",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}

MAX_BYTES = {
    "image": 8 * 1024 * 1024,
    "video": 16 * 1024 * 1024,
    "audio": 16 * 1024 * 1024,
    "document": 20 * 1024 * 1024,
}


def mime_key(mime: str) -> str:
    return (mime or "").split(";")[0].strip().lower()


def kind_from_upload(mime: str, filename: str = "") -> str:
    mime = mime_key(mime)
    name = (filename or "").lower()
    if mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return "image"
    if mime.startswith("video/") or name.endswith((".mp4", ".3gp", ".mov", ".mkv")):
        return "video"
    if mime.startswith("audio/") or name.endswith((".ogg", ".opus", ".mp3", ".m4a", ".aac", ".amr", ".wav", ".webm")):
        return "audio"
    if name.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".txt")):
        return "document"
    if name.endswith(".webm"):
        return "video" if mime.startswith("video/") else "audio"
    return "document"


def save_chat_file(phone: str, raw: bytes, mime: str, filename: str = "") -> Path:
    folder = MEDIA_DIR / phone.lstrip("+")
    folder.mkdir(parents=True, exist_ok=True)
    ext = EXT.get(mime_key(mime), Path(filename or "").suffix.lower() or ".bin")
    dest = folder / f"{uuid4().hex}{ext}"
    dest.write_bytes(raw)
    return dest


def prepare_outgoing_audio(path: Path, mime: str, voice: bool = False) -> tuple[Path, str, bool]:
    mime = mime_key(mime) or whatsapp.mime_for_path(path)
    if path.suffix.lower() in {".ogg", ".opus"}:
        return path, "audio/ogg", True
    if mime in {"audio/aac", "audio/mp4", "audio/mpeg", "audio/amr", "audio/ogg"}:
        return path, mime, voice
    dest = path.with_name(path.stem + "-wa.ogg")
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-vn",
                "-c:a",
                "libopus",
                "-b:a",
                "32k",
                str(dest),
            ],
            capture_output=True,
            timeout=40,
            check=False,
        )
        if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return dest, "audio/ogg", True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return path, mime or "audio/webm", False


async def get_or_create_conversation(
    db: AsyncSession, phone: str, name: str = ""
) -> Conversation:
    phone = phone.lstrip("+")
    cid = await tenancy.resolve_company_id(db)
    row = await tenancy.find_by_phone(db, Conversation, phone, cid)
    if row:
        if name and name != phone and (not row.name or row.name == row.phone):
            row.name = name
        if cid and not row.company_id:
            row.company_id = cid
        return row
    row = Conversation(phone=phone, name=name or phone, company_id=cid)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        row = await tenancy.find_by_phone(db, Conversation, phone, cid)
        if not row:
            row = await db.scalar(select(Conversation).where(Conversation.phone == phone))
        if not row:
            raise
        if cid and not row.company_id:
            row.company_id = cid
        if name and name != phone and (not row.name or row.name == row.phone):
            row.name = name
    return row


async def record_message(
    db: AsyncSession,
    *,
    phone: str,
    name: str = "",
    direction: str,
    source: str,
    msg_type: str,
    body: str = "",
    provider_id: str = "",
    media_path: str = "",
    mime_type: str = "",
    increment_unread: bool = False,
    preview: str | None = None,
) -> ChatMessage:
    conv = await get_or_create_conversation(db, phone, name)
    msg = ChatMessage(
        conversation_id=conv.id,
        direction=direction,
        source=source,
        msg_type=msg_type,
        body=body or "",
        provider_id=provider_id or "",
        media_path=media_path or "",
        mime_type=mime_type or "",
    )
    db.add(msg)
    shown = preview if preview is not None else (body.strip() if body else f"[{msg_type}]")
    conv.last_preview = shown[:240]
    conv.last_message_at = utcnow()
    if increment_unread:
        conv.unread_count = (conv.unread_count or 0) + 1
    await db.flush()
    return msg


async def mark_read(db: AsyncSession, phone: str) -> None:
    conv = await tenancy.get_conversation(db, phone)
    if conv:
        conv.unread_count = 0


async def list_conversations(db: AsyncSession) -> list[Conversation]:
    query = select(Conversation).order_by(desc(Conversation.last_message_at))
    cid = tenancy.current_company_id()
    if cid:
        query = query.where(Conversation.company_id == cid)
    result = await db.scalars(query)
    return list(result)


async def list_messages(db: AsyncSession, phone: str, limit: int = 200) -> list[ChatMessage]:
    conv = await tenancy.get_conversation(db, phone)
    if not conv:
        return []
    rows = list(
        await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conv.id)
            .order_by(desc(ChatMessage.created_at))
            .limit(limit)
        )
    )
    rows.reverse()
    return rows


def parse_inbound_payload(message: dict) -> dict:
    msg_type = message.get("type") or "text"
    body = ""
    media_id = None
    mime = ""
    if msg_type == "text":
        body = (message.get("text") or {}).get("body") or ""
    elif msg_type in {"image", "video", "audio", "document", "sticker"}:
        blob = message.get(msg_type) or {}
        media_id = blob.get("id")
        mime = blob.get("mime_type") or ""
        body = blob.get("caption") or blob.get("filename") or ""
        if msg_type == "sticker":
            msg_type = "image"
    elif msg_type == "button":
        body = (message.get("button") or {}).get("text") or ""
        msg_type = "text"
    elif msg_type == "interactive":
        interactive = message.get("interactive") or {}
        reply = interactive.get("list_reply") or interactive.get("button_reply") or {}
        if isinstance(reply, dict):
            body = str(reply.get("id") or reply.get("title") or "").strip()
        else:
            body = str(reply)
        msg_type = "text"
    else:
        body = f"(tipo no soportado: {msg_type})"
        msg_type = "text"
    return {
        "msg_type": msg_type,
        "body": body,
        "media_id": media_id,
        "mime_type": mime,
        "provider_id": message.get("id") or "",
    }


async def ingest_inbound(
    db: AsyncSession,
    *,
    phone: str,
    name: str,
    parsed: dict,
    creds=None,
) -> ChatMessage:
    media_path = ""
    mime = parsed.get("mime_type") or ""
    media_id = parsed.get("media_id")
    media_path = str(parsed.get("media_path") or "")
    ready = creds.ready if creds is not None else await whatsapp.is_configured(db)
    if media_id and ready and not media_path:
        folder = MEDIA_DIR / phone.lstrip("+")
        folder.mkdir(parents=True, exist_ok=True)
        ext = EXT.get(mime_key(mime), "")
        dest = folder / f"{parsed.get('provider_id') or media_id}{ext}"
        try:
            await whatsapp.download_media(media_id, dest, db=db, creds=creds)
            if dest.stat().st_size == 0:
                guessed = dest.with_suffix(_sniff_ext(dest))
                if guessed != dest:
                    dest.rename(guessed)
                    dest = guessed
            elif not dest.suffix:
                guessed = dest.with_suffix(_sniff_ext(dest))
                if guessed != dest:
                    dest.rename(guessed)
                    dest = guessed
            media_path = str(dest)
        except Exception:
            media_path = ""
    return await record_message(
        db,
        phone=phone,
        name=name,
        direction="inbound",
        source="customer",
        msg_type=parsed.get("msg_type") or "text",
        body=parsed.get("body") or "",
        provider_id=parsed.get("provider_id") or "",
        media_path=media_path,
        mime_type=mime,
        increment_unread=True,
    )


async def send_agent_text(
    db: AsyncSession, phone: str, body: str, company_id: str | None = None
) -> ChatMessage:
    creds = await whatsapp.load_creds(db, company_id)
    result = await whatsapp.send_text(phone, body, db=db, creds=creds)
    wamid = ""
    if result and not result.get("skipped"):
        wamid = ((result.get("messages") or [{}])[0] or {}).get("id") or ""
    return await record_message(
        db,
        phone=phone,
        direction="outbound",
        source="agent",
        msg_type="text",
        body=body,
        provider_id=wamid,
    )


async def send_agent_media(
    db: AsyncSession,
    phone: str,
    path: Path,
    *,
    msg_type: str,
    mime: str = "",
    caption: str = "",
    voice: bool = False,
    company_id: str | None = None,
) -> ChatMessage:
    mime = mime_key(mime) or whatsapp.mime_for_path(path)
    out_path = path
    if msg_type == "audio":
        out_path, mime, voice = prepare_outgoing_audio(path, mime, voice)
    creds = await whatsapp.load_creds(db, company_id)
    try:
        result = await whatsapp.send_media(
            phone,
            out_path,
            kind=msg_type,
            caption=caption,
            mime=mime,
            voice=voice,
            db=db,
            creds=creds,
        )
    except whatsapp.CloudError:
        if msg_type == "document":
            raise
        result = await whatsapp.send_media(
            phone,
            out_path,
            kind="document",
            caption=caption,
            mime=mime or "application/octet-stream",
            db=db,
            creds=creds,
        )
        msg_type = "document"
    wamid = ""
    if result and not result.get("skipped"):
        wamid = ((result.get("messages") or [{}])[0] or {}).get("id") or ""
    labels = {"image": "Foto", "video": "Video", "audio": "Audio", "document": "Archivo"}
    preview = (caption or "").strip() or labels.get(msg_type, "[media]")
    return await record_message(
        db,
        phone=phone,
        direction="outbound",
        source="agent",
        msg_type=msg_type,
        body=caption,
        provider_id=wamid,
        media_path=str(out_path),
        mime_type=mime,
        preview=preview,
    )


async def record_bot_text(db: AsyncSession, phone: str, body: str, send_result: dict | None) -> None:
    wamid = ""
    if send_result and not send_result.get("skipped"):
        wamid = ((send_result.get("messages") or [{}])[0] or {}).get("id") or ""
    await record_message(
        db,
        phone=phone,
        direction="outbound",
        source="bot",
        msg_type="text",
        body=body,
        provider_id=wamid,
    )


async def record_bot_image(
    db: AsyncSession, phone: str, image_path: Path, caption: str, send_result: dict | None
) -> None:
    await record_bot_media(
        db,
        phone,
        msg_type="image",
        caption=caption,
        path=image_path,
        mime="image/png",
        send_result=send_result,
    )


async def record_bot_media(
    db: AsyncSession,
    phone: str,
    *,
    msg_type: str,
    caption: str = "",
    path: Path | str | None = None,
    mime: str = "",
    send_result: dict | None = None,
) -> None:
    wamid = ""
    if send_result and not send_result.get("skipped"):
        wamid = ((send_result.get("messages") or [{}])[0] or {}).get("id") or ""
    media_path = str(path) if path else ""
    mime_type = mime or (whatsapp.mime_for_path(Path(media_path)) if media_path and not str(media_path).startswith("http") else "")
    labels = {"image": "Foto", "video": "Video", "audio": "Audio", "document": "Archivo"}
    await record_message(
        db,
        phone=phone,
        direction="outbound",
        source="bot",
        msg_type=msg_type,
        body=caption or "",
        provider_id=wamid,
        media_path=media_path,
        mime_type=mime_type,
        preview=((caption or "").strip() or labels.get(msg_type, "[media]")),
    )


async def record_bot_rich(
    db: AsyncSession,
    phone: str,
    msg_type: str,
    body: str,
    preview: str = "",
) -> None:
    await record_message(
        db,
        phone=phone,
        direction="outbound",
        source="bot",
        msg_type=msg_type,
        body=body,
        preview=preview or None,
    )


def _sniff_ext(path: Path) -> str:
    head = path.read_bytes()[:12]
    if head.startswith(b"\xff\xd8"):
        return ".jpg"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head[4:8] == b"ftyp":
        return ".mp4"
    if head.startswith(b"OggS"):
        return ".ogg"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return ".webm"
    if head.startswith(b"ID3") or head[:2] == b"\xff\xfb":
        return ".mp3"
    return path.suffix or ".bin"
