from __future__ import annotations

import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import ChatMessage
from app.services import inbox, simulator, tenancy, whatsapp
from app.web import require_superadmin, require_user, templates

router = APIRouter(tags=["whatsapp-hub"])
settings = get_settings()
log = logging.getLogger("whatsapp.hub")


def _with_rich(messages):
    for msg in messages:
        rich = None
        if msg.msg_type in {"catalog", "product_card", "choices"}:
            try:
                parsed = json.loads(msg.body or "{}")
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                rich = parsed
        msg.rich = rich
    return messages


def _company_id(request: Request) -> str | None:
    return request.session.get("company_id")


async def _hub_context(request: Request, db: AsyncSession, **extra):
    last = await db.scalar(select(ChatMessage).order_by(desc(ChatMessage.created_at)))
    companies = await whatsapp.list_companies(db)
    if request.session.get("role") != "superadmin":
        cid = _company_id(request)
        companies = [c for c in companies if c.id == cid]
    company = await whatsapp.get_company(db, _company_id(request))
    ctx = {
        "app_name": settings.app_name,
        "user": request.session.get("user"),
        "connection": await whatsapp.connection_snapshot(db, company.id if company else None),
        "conversations": await inbox.list_conversations(db),
        "companies": companies,
        "company": company,
        "last_inbound": last.created_at if last else None,
        "flash_ok": request.query_params.get("ok"),
        "flash_error": request.query_params.get("error"),
        "active": None,
        "messages": [],
        "probe": None,
    }
    ctx.update(extra)
    return ctx


@router.get("/whatsapp")
async def whatsapp_hub(request: Request, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    return templates.TemplateResponse(
        request, "whatsapp_inbox.html", await _hub_context(request, db)
    )


@router.post("/whatsapp/empresa")
async def select_company(
    request: Request,
    db: AsyncSession = Depends(get_db),
    company_id: str = Form(""),
):
    redir = require_superadmin(request)
    if redir:
        return redir
    if company_id:
        company = await whatsapp.get_company(db, company_id)
        if company:
            request.session["company_id"] = company.id
            request.session["company_name"] = company.name
    return RedirectResponse("/whatsapp", status_code=303)


@router.post("/whatsapp/empresa/nueva")
async def new_company(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form("Nueva empresa"),
):
    redir = require_superadmin(request)
    if redir:
        return redir
    company = await whatsapp.create_company(db, name)
    await db.commit()
    request.session["company_id"] = company.id
    request.session["company_name"] = company.name
    return RedirectResponse("/whatsapp?ok=empresa", status_code=303)


@router.post("/whatsapp/conexion")
async def save_connection(
    request: Request,
    db: AsyncSession = Depends(get_db),
    company_name: str = Form(""),
    label: str = Form("Principal"),
    business_e164: str = Form(""),
    phone_number_id: str = Form(""),
    waba_id: str = Form(""),
    access_token: str = Form(""),
    app_secret: str = Form(""),
    verify_token: str = Form(""),
    graph_version: str = Form("v21.0"),
    skip_signature: str = Form(""),
):
    redir = require_superadmin(request)
    if redir:
        return redir
    company = await whatsapp.get_company(db, _company_id(request))
    if not company:
        return RedirectResponse("/whatsapp?error=sin-empresa", status_code=303)
    await whatsapp.save_account_credentials(
        db,
        company=company,
        company_name=company_name,
        label=label,
        business_e164=business_e164,
        phone_number_id=phone_number_id,
        waba_id=waba_id,
        access_token=access_token,
        app_secret=app_secret,
        verify_token=verify_token,
        graph_version=graph_version,
        skip_signature=skip_signature in {"on", "1", "true", "yes"},
    )
    await db.commit()
    request.session["company_id"] = company.id
    request.session["company_name"] = company.name
    return RedirectResponse("/whatsapp?ok=guardado", status_code=303)


@router.get("/whatsapp/chat/{phone}")
async def whatsapp_chat(request: Request, phone: str, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    phone = (phone or "").lstrip("+")
    try:
        await inbox.mark_read(db, phone)
        await db.commit()
        messages = _with_rich(await inbox.list_messages(db, phone))
        ctx = await _hub_context(request, db, active=phone, messages=messages)
    except Exception:
        log.exception("No se pudo abrir el chat %s", phone)
        raise HTTPException(500, "No se pudo abrir este chat. Revisá el log de uvicorn.") from None
    return templates.TemplateResponse(request, "whatsapp_inbox.html", ctx)


@router.post("/whatsapp/chat/{phone}")
async def whatsapp_send(
    request: Request,
    phone: str,
    db: AsyncSession = Depends(get_db),
):
    redir = require_user(request)
    if redir:
        return redir
    phone = (phone or "").lstrip("+")
    try:
        form = await request.form()
    except Exception:
        log.exception("No se pudo leer el formulario de envío a %s", phone)
        return RedirectResponse(
            f"/whatsapp/chat/{phone}?error={quote('No se pudo leer el archivo. Probá de nuevo.')}",
            status_code=303,
        )
    text = str(form.get("body") or "").strip()
    voice = str(form.get("voice") or "") in {"1", "on", "true", "yes"}
    media = form.get("media")
    raw = b""
    filename = ""
    mime = ""
    if isinstance(media, UploadFile):
        raw = await media.read()
        filename = (media.filename or "").strip()
        mime = media.content_type or ""
    if not text and not raw:
        return RedirectResponse(f"/whatsapp/chat/{phone}?error=vacio", status_code=303)
    company_id = _company_id(request)
    try:
        if raw:
            mime = inbox.mime_key(mime)
            kind = inbox.kind_from_upload(mime, filename)
            limit = inbox.MAX_BYTES.get(kind, inbox.MAX_BYTES["document"])
            if len(raw) > limit:
                return RedirectResponse(
                    f"/whatsapp/chat/{phone}?error={quote('El archivo pesa demasiado')}",
                    status_code=303,
                )
            dest = inbox.save_chat_file(phone, raw, mime, filename or "archivo")
            await inbox.send_agent_media(
                db,
                phone,
                dest,
                msg_type=kind,
                mime=mime,
                caption=text,
                voice=voice or kind == "audio",
                company_id=company_id,
            )
        else:
            await inbox.send_agent_text(db, phone, text, company_id=company_id)
        await db.commit()
    except whatsapp.CloudError as exc:
        await db.rollback()
        return RedirectResponse(f"/whatsapp/chat/{phone}?error={quote(str(exc))}", status_code=303)
    except Exception:
        log.exception("No se pudo enviar a %s", phone)
        await db.rollback()
        return RedirectResponse(
            f"/whatsapp/chat/{phone}?error={quote('No se pudo enviar. Revisá el token de WhatsApp o el log del servidor.')}",
            status_code=303,
        )
    return RedirectResponse(f"/whatsapp/chat/{phone}", status_code=303)


@router.post("/whatsapp/probar")
async def whatsapp_probe(request: Request, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    probe = await whatsapp.probe_connection(db, _company_id(request))
    return templates.TemplateResponse(
        request,
        "whatsapp_inbox.html",
        await _hub_context(request, db, probe=probe),
    )


class SimPayload(BaseModel):
    phone: str = simulator.DEFAULT_PHONE
    name: str = simulator.DEFAULT_NAME
    text: str = ""
    image: bool = False


def _require_json_user(request: Request) -> None:
    if not request.session.get("user_id") and not request.session.get("user"):
        raise HTTPException(status_code=401, detail="Iniciá sesión")
    tenancy.bind_request(request)


@router.get("/whatsapp/simulador")
async def whatsapp_simulator(
    request: Request, db: AsyncSession = Depends(get_db), phone: str = simulator.DEFAULT_PHONE
):
    redir = require_user(request)
    if redir:
        return redir
    snap = await simulator.snapshot(db, phone)
    return templates.TemplateResponse(
        request,
        "whatsapp_sim.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "default_phone": simulator.DEFAULT_PHONE,
            "default_name": simulator.DEFAULT_NAME,
            "boot": {
                **snap,
                "name": simulator.DEFAULT_NAME,
            },
        },
    )


@router.get("/whatsapp/simulador/estado")
async def simulator_state(
    request: Request, db: AsyncSession = Depends(get_db), phone: str = simulator.DEFAULT_PHONE
):
    _require_json_user(request)
    return await simulator.snapshot(db, phone)


@router.post("/whatsapp/simulador/mensaje")
async def simulator_message(
    request: Request, payload: SimPayload, db: AsyncSession = Depends(get_db)
):
    _require_json_user(request)
    return await simulator.send_as_customer(
        db, phone=payload.phone, name=payload.name, text=payload.text, as_image=payload.image
    )


@router.post("/whatsapp/simulador/media")
async def simulator_media(request: Request, db: AsyncSession = Depends(get_db)):
    _require_json_user(request)
    form = await request.form()
    media = form.get("media")
    if not isinstance(media, UploadFile):
        raise HTTPException(400, "Adjuntá un archivo")
    raw = await media.read()
    if not raw:
        raise HTTPException(400, "Archivo vacío")
    kind = inbox.kind_from_upload(media.content_type or "", media.filename or "")
    if len(raw) > inbox.MAX_BYTES.get(kind, inbox.MAX_BYTES["document"]):
        raise HTTPException(400, "El archivo pesa demasiado")
    try:
        return await simulator.send_as_customer_file(
            db,
            phone=str(form.get("phone") or simulator.DEFAULT_PHONE),
            name=str(form.get("name") or simulator.DEFAULT_NAME),
            caption=str(form.get("text") or ""),
            raw=raw,
            mime=media.content_type or "",
            filename=media.filename or "archivo",
        )
    except Exception:
        log.exception("No se pudo guardar media en el simulador")
        raise HTTPException(400, "No se pudo guardar el archivo en el simulador") from None


@router.post("/whatsapp/simulador/reset")
async def simulator_reset(
    request: Request, payload: SimPayload, db: AsyncSession = Depends(get_db)
):
    _require_json_user(request)
    return await simulator.reset_chat(db, payload.phone)
