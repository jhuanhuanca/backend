from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CATALOG_DIR, FLOW_DIR, get_settings
from app.database import get_db
from app.models import BotFlow, Product, utcnow
from app.services import flow_engine, inventory, motor_client, tenancy
from app.services.flow_definition import (
    PALETTE,
    TRIGGER_TYPES,
    default_sale_definition,
    empty_definition,
    leads_catalog_definition,
    sale_catalog_definition,
    validate_definition,
)
from app.services.whatsapp import skip_cloud_send
from app.web import require_user, templates

router = APIRouter(tags=["flow-studio"])
settings = get_settings()
log = logging.getLogger("flows")


def _company_id(request: Request) -> str | None:
    return request.session.get("company_id") or None


async def _owned_flow(db: AsyncSession, request: Request, flow_id: str) -> BotFlow | None:
    row = await db.get(BotFlow, flow_id)
    cid = _company_id(request)
    if not row:
        return None
    if cid and row.company_id and row.company_id != cid:
        return None
    return row


def _flows_query(request: Request):
    query = select(BotFlow)
    cid = _company_id(request)
    if cid:
        query = query.where(BotFlow.company_id == cid)
    return query


class FlowSaveIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    description: str = ""
    definition: dict = Field(default_factory=dict)


class SimulateIn(BaseModel):
    phone: str = "59170001111"
    text: str = "hola"
    name: str = "Simulador"


def _flow_json(row: BotFlow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "status": row.status,
        "is_default": row.is_default,
        "definition": row.definition or {"nodes": [], "edges": []},
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/flujos")
async def list_flows(request: Request, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    rows = (await db.scalars(_flows_query(request).order_by(BotFlow.updated_at.desc()))).all()
    motor = await motor_client.health()
    active = next((f for f in rows if f.status == "published"), None)
    return templates.TemplateResponse(
        request,
        "flujos.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "flows": rows,
            "active": active,
            "motor": motor,
            "motor_url": settings.motor_ia_url,
            "ok": request.query_params.get("ok"),
        },
    )


@router.post("/flujos")
async def create_flow(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form("Venta WhatsApp"),
    template: str = Form("venta"),
):
    redir = require_user(request)
    if redir:
        return redir
    if template == "venta_catalogo":
        definition = sale_catalog_definition()
    elif template == "leads":
        definition = leads_catalog_definition()
    elif template == "venta":
        definition = default_sale_definition()
    else:
        definition = empty_definition()
    row = BotFlow(
        name=name.strip() or "Flujo",
        description="Borrador. Publicar para reemplazar el bot fijo.",
        status="draft",
        is_default=False,
        company_id=_company_id(request),
        definition=definition,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return RedirectResponse(f"/flujos/{row.id}/editar", status_code=303)


@router.get("/flujos/{flow_id}/editar")
async def edit_flow(request: Request, flow_id: str, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    row = await _owned_flow(db, request, flow_id)
    if not row:
        return RedirectResponse("/flujos", status_code=303)
    motor = await motor_client.health()
    return templates.TemplateResponse(
        request,
        "flow_studio.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "flow": row,
            "palette": PALETTE,
            "triggers": TRIGGER_TYPES,
            "motor": motor,
        },
    )


@router.get("/api/flujos")
async def api_list_flows(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user"):
        return JSONResponse({"detail": "login"}, status_code=401)
    rows = (await db.scalars(_flows_query(request).order_by(BotFlow.updated_at.desc()))).all()
    active = next((f for f in rows if f.status == "published"), None)
    return {
        "flows": [_flow_json(r) for r in rows],
        "active_id": active.id if active else None,
    }


@router.get("/api/flujos/meta")
async def flow_meta(request: Request):
    if not request.session.get("user"):
        return JSONResponse({"detail": "login"}, status_code=401)
    return {"palette": PALETTE, "triggers": TRIGGER_TYPES}


_ALLOWED_IMG = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_ALLOWED_AUDIO = {".ogg", ".opus", ".mp3", ".m4a", ".aac", ".amr", ".wav"}
_ALLOWED_VIDEO = {".mp4", ".3gp"}
_ALLOWED_BY_KIND = {
    "image": _ALLOWED_IMG,
    "audio": _ALLOWED_AUDIO,
    "video": _ALLOWED_VIDEO,
}
_MAX_BYTES = {
    "image": 6 * 1024 * 1024,
    "audio": 16 * 1024 * 1024,
    "video": 16 * 1024 * 1024,
}


async def _store_catalog_image(file: UploadFile) -> tuple[str | None, str | None]:
    suffix = Path(file.filename or "foto.jpg").suffix.lower()
    if suffix not in _ALLOWED_IMG:
        return None, "Usá JPG, PNG, WEBP o GIF"
    raw = await file.read()
    if len(raw) > 6 * 1024 * 1024:
        return None, "La imagen pesa más de 6 MB"
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    name = uuid4().hex + suffix
    dest = CATALOG_DIR / name
    dest.write_bytes(raw)
    return f"/uploads/catalog/{name}", None


def _detect_media_kind(suffix: str) -> str:
    for kind, exts in _ALLOWED_BY_KIND.items():
        if suffix in exts:
            return kind
    return ""


async def _store_flow_media(file: UploadFile, kind: str = "") -> tuple[str | None, str | None]:
    suffix = Path(file.filename or "").suffix.lower()
    guessed = _detect_media_kind(suffix)
    kind = kind if kind in _ALLOWED_BY_KIND else guessed
    if not kind or suffix not in _ALLOWED_BY_KIND[kind]:
        return None, "Usá foto JPG/PNG/WEBP, audio OGG/MP3/M4A o video MP4"
    raw = await file.read()
    limit = _MAX_BYTES[kind]
    if len(raw) > limit:
        return None, f"El archivo pesa más de {limit // (1024 * 1024)} MB"
    FLOW_DIR.mkdir(parents=True, exist_ok=True)
    name = uuid4().hex + suffix
    dest = FLOW_DIR / name
    dest.write_bytes(raw)
    return f"/uploads/flow/{name}", None


@router.get("/api/flujos/catalogo")
async def catalog_products(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user"):
        return JSONResponse({"detail": "login"}, status_code=401)
    tenancy.bind_request(request)
    products = await inventory.list_catalog(db)
    return {
        "products": [
            {
                "id": p.id,
                "sku": p.sku,
                "name": p.name,
                "price": f"{p.price:.2f}",
                "image_url": p.image_url or "",
                "category": p.category or "",
            }
            for p in products
        ]
    }


@router.post("/api/flujos/upload")
async def upload_catalog_file(
    request: Request, file: UploadFile = File(...), kind: str = ""
):
    if not request.session.get("user"):
        return JSONResponse({"detail": "login"}, status_code=401)
    url, err = await _store_flow_media(file, (kind or "").strip().lower())
    if err:
        return JSONResponse({"detail": err}, status_code=400)
    return {"ok": True, "url": url, "kind": _detect_media_kind(Path(url or "").suffix.lower())}


@router.post("/api/flujos/productos/{product_id}/foto")
async def upload_product_photo(
    request: Request, product_id: str, db: AsyncSession = Depends(get_db), file: UploadFile = File(...)
):
    if not request.session.get("user"):
        return JSONResponse({"detail": "login"}, status_code=401)
    tenancy.bind_request(request)
    product = await db.get(Product, product_id)
    if not product:
        return JSONResponse({"detail": "producto no encontrado"}, status_code=404)
    url, err = await _store_catalog_image(file)
    if err:
        return JSONResponse({"detail": err}, status_code=400)
    product.image_url = url or ""
    await db.commit()
    try:
        from app.services import visual_catalog

        await visual_catalog.reindex_current(db)
    except Exception:
        pass
    return {"ok": True, "url": url, "product_id": product.id}


@router.get("/api/flujos/{flow_id}")
async def get_flow(request: Request, flow_id: str, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user"):
        return JSONResponse({"detail": "login"}, status_code=401)
    tenancy.bind_request(request)
    row = await _owned_flow(db, request, flow_id)
    if not row:
        return JSONResponse({"detail": "no encontrado"}, status_code=404)
    return _flow_json(row)


@router.put("/api/flujos/{flow_id}")
async def save_flow(
    request: Request, flow_id: str, payload: FlowSaveIn, db: AsyncSession = Depends(get_db)
):
    if not request.session.get("user"):
        return JSONResponse({"detail": "login"}, status_code=401)
    tenancy.bind_request(request)
    row = await _owned_flow(db, request, flow_id)
    if not row:
        return JSONResponse({"detail": "no encontrado"}, status_code=404)
    errors = validate_definition(payload.definition)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)
    row.name = payload.name.strip() or row.name
    row.description = payload.description or ""
    row.definition = payload.definition
    row.updated_at = utcnow()
    await db.commit()
    return {"ok": True, "flow": _flow_json(row)}


async def _publish(db: AsyncSession, request: Request, flow_id: str) -> tuple[BotFlow | None, list[str]]:
    row = await _owned_flow(db, request, flow_id)
    if not row:
        return None, ["no encontrado"]
    errors = validate_definition(row.definition or {})
    if errors:
        return row, errors
    query = select(BotFlow).where(BotFlow.id != flow_id)
    if row.company_id:
        query = query.where(BotFlow.company_id == row.company_id)
    others = (await db.scalars(query)).all()
    for other in others:
        other.status = "draft"
        other.is_default = False
    row.status = "published"
    row.is_default = True
    row.updated_at = utcnow()
    await db.commit()
    return row, []


@router.post("/flujos/{flow_id}/publicar")
async def publish_flow_form(request: Request, flow_id: str, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    row, errors = await _publish(db, request, flow_id)
    if not row:
        return RedirectResponse("/flujos", status_code=303)
    if errors:
        return RedirectResponse(f"/flujos/{flow_id}/editar?err=1", status_code=303)
    return RedirectResponse("/flujos", status_code=303)


@router.post("/flujos/{flow_id}/desactivar")
async def unpublish_flow_form(request: Request, flow_id: str, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    row = await _owned_flow(db, request, flow_id)
    if row:
        row.status = "draft"
        row.is_default = False
        row.updated_at = utcnow()
        await db.commit()
    return RedirectResponse("/flujos", status_code=303)


@router.post("/flujos/{flow_id}/eliminar")
async def delete_flow_form(request: Request, flow_id: str, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    row = await _owned_flow(db, request, flow_id)
    if row:
        await db.delete(row)
        await db.commit()
    return RedirectResponse("/flujos?ok=eliminado", status_code=303)


@router.delete("/api/flujos/{flow_id}")
async def delete_flow_api(request: Request, flow_id: str, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user"):
        return JSONResponse({"detail": "login"}, status_code=401)
    tenancy.bind_request(request)
    row = await _owned_flow(db, request, flow_id)
    if not row:
        return JSONResponse({"detail": "no encontrado"}, status_code=404)
    await db.delete(row)
    await db.commit()
    return {"ok": True}


@router.post("/api/flujos/{flow_id}/publish")
async def publish_flow(request: Request, flow_id: str, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user"):
        return JSONResponse({"detail": "login"}, status_code=401)
    row, errors = await _publish(db, request, flow_id)
    if not row:
        return JSONResponse({"detail": "no encontrado"}, status_code=404)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)
    return {"ok": True, "flow": _flow_json(row)}


@router.post("/api/flujos/{flow_id}/unpublish")
async def unpublish_flow(request: Request, flow_id: str, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user"):
        return JSONResponse({"detail": "login"}, status_code=401)
    tenancy.bind_request(request)
    row = await _owned_flow(db, request, flow_id)
    if not row:
        return JSONResponse({"detail": "no encontrado"}, status_code=404)
    row.status = "draft"
    row.is_default = False
    row.updated_at = utcnow()
    await db.commit()
    return {"ok": True, "flow": _flow_json(row)}


@router.post("/api/flujos/{flow_id}/simular")
async def simulate_flow(
    request: Request, flow_id: str, payload: SimulateIn, db: AsyncSession = Depends(get_db)
):
    if not request.session.get("user"):
        return JSONResponse({"detail": "login"}, status_code=401)
    tenancy.bind_request(request)
    row = await _owned_flow(db, request, flow_id)
    if not row:
        return JSONResponse({"detail": "no encontrado"}, status_code=404)
    try:
        with skip_cloud_send():
            replies = await flow_engine.handle_incoming(
                db,
                phone=payload.phone,
                name=payload.name or "Simulador",
                text=payload.text,
                flow=row,
            )
    except Exception as exc:  # noqa: BLE001
        log.exception("No se pudo simular el flujo %s", flow_id)
        return {"ok": False, "replies": [f"El bot no pudo responder: {exc}"], "phone": payload.phone}
    return {"ok": True, "replies": replies, "phone": payload.phone}
