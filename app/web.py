from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import PAY_DIR, UPLOADS_DIR, get_settings
from app.database import get_db
from app.models import Appointment, Company, FarmDevice, LiveSession, Order, Product, utcnow
from app.services import auth, deliveries, inventory, live, orders as order_svc, tenancy
from app.services.deliveries import (
    APPT_STATUS_LABEL,
    KIND_LABEL,
    MODE_LABEL,
    PURPOSE_LABEL,
    STATUS_LABEL,
    upcoming_windows,
)
from app.services.product_specs import (
    format_gallery,
    format_options,
    format_spec_lines,
    parse_gallery,
    parse_options,
    parse_spec_lines,
)

router = APIRouter(tags=["dashboard"])
settings = get_settings()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def upload_url(path: str | None) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://", "/uploads/")):
        return raw
    try:
        rel = Path(raw).expanduser().resolve().relative_to(UPLOADS_DIR.resolve())
        return f"/uploads/{rel.as_posix()}"
    except (ValueError, OSError, RuntimeError):
        return ""


templates.env.globals["upload_url"] = upload_url
templates.env.globals["order_status_label"] = lambda s: STATUS_LABEL.get(s, s)
templates.env.globals["delivery_mode_label"] = lambda s: MODE_LABEL.get(s, s)
templates.env.globals["delivery_summary"] = deliveries.summary
templates.env.globals["purpose_label"] = lambda s: PURPOSE_LABEL.get(s, s)
templates.env.globals["kind_label"] = lambda s: KIND_LABEL.get(s, s or "presencial")
templates.env.globals["appt_status_label"] = lambda s: APPT_STATUS_LABEL.get(s, s)


def require_user(request: Request):
    if request.session.get("pending_2fa"):
        if request.url.path not in {"/login/2fa", "/logout"}:
            return RedirectResponse("/login/2fa", status_code=303)
    if not request.session.get("user_id"):
        nxt = request.url.path
        if request.url.query:
            nxt = f"{nxt}?{request.url.query}"
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        return RedirectResponse(f"/login?next={quote(nxt, safe='')}", status_code=303)
    tenancy.bind_request(request)
    if (
        request.session.get("role") == auth.ROLE_SUPERADMIN
        and not request.session.get("totp_enabled")
        and not request.url.path.startswith("/seguridad")
        and request.url.path != "/logout"
    ):
        return RedirectResponse("/seguridad/2fa", status_code=303)
    return None


def require_superadmin(request: Request):
    redir = require_user(request)
    if redir:
        return redir
    if request.session.get("role") != auth.ROLE_SUPERADMIN:
        return RedirectResponse("/", status_code=303)
    return None


def _tenant_id(request: Request) -> str | None:
    return request.session.get("company_id") or None


def _belongs(request: Request, company_id: str | None) -> bool:
    cid = _tenant_id(request)
    if not cid or not company_id:
        return True
    return company_id == cid


async def _owned_order(db: AsyncSession, request: Request, order_id: str) -> Order | None:
    order = await db.get(Order, order_id)
    if not order or not _belongs(request, order.company_id):
        return None
    return order


def _safe_next(value: str) -> str:
    path = (value or "").strip()
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


@router.get("/login")
async def login_form(request: Request, next: str = ""):
    if request.session.get("pending_2fa"):
        return RedirectResponse("/login/2fa", status_code=303)
    if request.session.get("user_id"):
        return RedirectResponse(_safe_next(next) if next else "/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": None,
            "app_name": settings.app_name,
            "next": _safe_next(next) if next else "/",
        },
    )


@router.post("/login")
async def login_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
):
    user = await auth.find_user(db, username)
    if (
        not user
        or not user.is_active
        or not auth.verify_password(password, user.password_hash)
    ):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Usuario o contraseña incorrectos",
                "app_name": settings.app_name,
                "next": _safe_next(next),
            },
            status_code=401,
        )
    if user.totp_enabled:
        request.session.clear()
        request.session["pending_2fa"] = user.id
        request.session["login_next"] = _safe_next(next)
        return RedirectResponse("/login/2fa", status_code=303)
    await auth.establish_session(request, db, user)
    if user.role == auth.ROLE_SUPERADMIN and not user.totp_enabled:
        return RedirectResponse("/seguridad/2fa", status_code=303)
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/")
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    cid = _tenant_id(request)
    await inventory.expire_reservations(db)
    order_q = select(func.count(Order.id))
    pending_q = select(func.count(Order.id)).where(Order.status == "pendiente_pago")
    paid_q = select(func.count(Order.id)).where(Order.status == "pagado")
    delivering_q = select(func.count(Order.id)).where(
        Order.status.in_(["listo_entrega", "en_transito"])
    )
    appt_q = select(func.count(Appointment.id)).where(Appointment.status == "scheduled")
    if cid:
        order_q = order_q.where(Order.company_id == cid)
        pending_q = pending_q.where(Order.company_id == cid)
        paid_q = paid_q.where(Order.company_id == cid)
        delivering_q = delivering_q.where(Order.company_id == cid)
        appt_q = appt_q.where(Appointment.company_id == cid)
    order_count = await db.scalar(order_q)
    pending = await db.scalar(pending_q)
    paid = await db.scalar(paid_q)
    delivering = await db.scalar(delivering_q)
    live_count = await db.scalar(
        select(func.count(LiveSession.id)).where(LiveSession.status == "live")
    )
    device_count = await db.scalar(select(func.count(FarmDevice.id)))
    appointment_count = await db.scalar(appt_q)
    recent_q = (
        select(Order)
        .options(selectinload(Order.customer), selectinload(Order.items))
        .order_by(desc(Order.created_at))
        .limit(8)
    )
    upcoming_q = (
        select(Appointment)
        .where(Appointment.status == "scheduled")
        .order_by(Appointment.scheduled_at.asc().nulls_last(), desc(Appointment.created_at))
        .limit(6)
    )
    if cid:
        recent_q = recent_q.where(Order.company_id == cid)
        upcoming_q = upcoming_q.where(Appointment.company_id == cid)
    recent = list(await db.scalars(recent_q))
    upcoming = list(await db.scalars(upcoming_q))
    devices = list(await db.scalars(select(FarmDevice).order_by(FarmDevice.serial)))
    lives = await live.active_lives_by_serial(db)
    sessions = list(
        await db.scalars(
            select(LiveSession).order_by(desc(LiveSession.started_at)).limit(8)
        )
    )
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "stats": {
                "orders": order_count or 0,
                "pending": pending or 0,
                "paid": paid or 0,
                "delivering": delivering or 0,
                "appointments": appointment_count or 0,
                "lives": live_count or 0,
                "devices": device_count or 0,
            },
            "recent": recent,
            "upcoming": upcoming,
            "devices": devices,
            "lives": lives,
            "sessions": sessions,
        },
    )


@router.get("/pedidos")
async def orders_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    estado: str = "",
):
    redir = require_user(request)
    if redir:
        return redir
    query = select(Order).options(
        selectinload(Order.customer),
        selectinload(Order.items),
        selectinload(Order.session),
        selectinload(Order.delivery),
    )
    cid = _tenant_id(request)
    if cid:
        query = query.where(Order.company_id == cid)
    if estado:
        query = query.where(Order.status == estado)
    rows = list(await db.scalars(query.order_by(desc(Order.created_at))))
    return templates.TemplateResponse(
        request,
        "orders.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "orders": rows,
            "estado": estado,
            "ok": request.query_params.get("ok"),
        },
    )


@router.get("/agenda")
async def agenda_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    estado: str = "",
    tipo: str = "",
):
    redir = require_user(request)
    if redir:
        return redir
    query = select(Appointment)
    cid = _tenant_id(request)
    if cid:
        query = query.where(Appointment.company_id == cid)
    if estado:
        query = query.where(Appointment.status == estado)
    if tipo:
        query = query.where(Appointment.purpose == tipo)
    rows = list(
        await db.scalars(
            query.order_by(Appointment.scheduled_at.asc().nulls_last(), desc(Appointment.created_at))
        )
    )
    return templates.TemplateResponse(
        request,
        "agenda.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "appointments": rows,
            "estado": estado,
            "tipo": tipo,
            "ok": request.query_params.get("ok"),
        },
    )


@router.post("/agenda/{appt_id}/estado")
async def agenda_status(
    request: Request,
    appt_id: str,
    db: AsyncSession = Depends(get_db),
    status: str = Form(...),
):
    redir = require_user(request)
    if redir:
        return redir
    if status not in APPT_STATUS_LABEL:
        raise HTTPException(400, "Estado inválido")
    appt = await db.get(Appointment, appt_id)
    if not appt or not _belongs(request, appt.company_id):
        raise HTTPException(404, "Reunión no encontrada")
    appt.status = status
    appt.updated_at = utcnow()
    await db.commit()
    return RedirectResponse("/agenda", status_code=303)


@router.post("/agenda/{appt_id}/eliminar")
async def delete_appointment(
    request: Request,
    appt_id: str,
    db: AsyncSession = Depends(get_db),
):
    redir = require_user(request)
    if redir:
        return redir
    appt = await db.get(Appointment, appt_id)
    if not appt or not _belongs(request, appt.company_id):
        raise HTTPException(404, "Reunión no encontrada")
    await db.delete(appt)
    await db.commit()
    return RedirectResponse("/agenda?ok=eliminado", status_code=303)


@router.get("/pedidos/{order_id}")
async def order_detail(request: Request, order_id: str, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    order = await _owned_order(db, request, order_id)
    if not order:
        raise HTTPException(404, "Pedido no encontrado")
    return templates.TemplateResponse(
        request,
        "order_detail.html",
        {"app_name": settings.app_name, "user": request.session.get("user"), "order": order, "windows": upcoming_windows()},
    )


@router.post("/pedidos/{order_id}/confirmar-pago")
async def confirm_pay(request: Request, order_id: str, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    order = await _owned_order(db, request, order_id)
    if not order:
        raise HTTPException(404)
    await order_svc.confirm_payment(db, order)
    await db.flush()
    loaded = await order_svc.load_order(db, order_id)
    if loaded:
        await deliveries.start_scheduling(db, loaded)
    await db.commit()
    return RedirectResponse(f"/pedidos/{order_id}", status_code=303)


@router.post("/pedidos/{order_id}/cancelar")
async def cancel(request: Request, order_id: str, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    order = await _owned_order(db, request, order_id)
    if not order:
        raise HTTPException(404)
    await order_svc.cancel_order(db, order)
    await db.commit()
    return RedirectResponse(f"/pedidos/{order_id}", status_code=303)


@router.post("/pedidos/{order_id}/eliminar")
async def delete_order(
    request: Request,
    order_id: str,
    db: AsyncSession = Depends(get_db),
):
    redir = require_user(request)
    if redir:
        return redir
    order = await _owned_order(db, request, order_id)
    if not order:
        raise HTTPException(404, "Pedido no encontrado")
    await order_svc.delete_order(db, order)
    await db.commit()
    return RedirectResponse("/pedidos?ok=eliminado", status_code=303)


@router.post("/pedidos/{order_id}/entrega")
async def save_delivery(
    request: Request,
    order_id: str,
    db: AsyncSession = Depends(get_db),
    mode: str = Form("to_coordinate"),
    address: str = Form(""),
    notes: str = Form(""),
    window_key: str = Form(""),
    when_text: str = Form(""),
):
    redir = require_user(request)
    if redir:
        return redir
    order = await _owned_order(db, request, order_id)
    if not order:
        raise HTTPException(404)
    await deliveries.apply_from_dashboard(
        db,
        order,
        mode=mode,
        address=address,
        notes=notes,
        window_key=window_key,
        when_text=when_text,
    )
    await db.commit()
    return RedirectResponse(f"/pedidos/{order_id}", status_code=303)


@router.post("/pedidos/{order_id}/estado")
async def change_status(
    request: Request,
    order_id: str,
    db: AsyncSession = Depends(get_db),
    status: str = Form(...),
):
    redir = require_user(request)
    if redir:
        return redir
    order = await _owned_order(db, request, order_id)
    if not order:
        raise HTTPException(404)
    try:
        await deliveries.set_order_status(db, order, status)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if status == "en_transito":
        await deliveries.notify_customer(
            db, order, f"Tu pedido {order.public_code} ya va en camino."
        )
    elif status == "entregado":
        await deliveries.notify_customer(
            db, order, f"Tu pedido {order.public_code} fue entregado. ¡Gracias por tu compra!"
        )
    await db.commit()
    return RedirectResponse(f"/pedidos/{order_id}", status_code=303)


@router.get("/inventario")
async def inventory_page(request: Request, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    from app.services import whatsapp as wa

    company = await wa.get_company(db, request.session.get("company_id"))
    query = select(Product).where(Product.active.is_(True)).order_by(Product.name)
    if company:
        query = query.where(Product.company_id == company.id)
    products = list(await db.scalars(query))
    stocks = {p.id: await inventory.available_stock(db, p.id) for p in products}
    store_url = (
        settings.public_base_url.rstrip("/") + f"/t/{company.slug}" if company else ""
    )
    return templates.TemplateResponse(
        request,
        "products.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "products": products,
            "stocks": stocks,
            "store_url": store_url,
            "ok": request.query_params.get("ok"),
        },
    )


def _fill_product(
    product: Product,
    *,
    sku: str,
    name: str,
    price: str,
    stock: int,
    description: str,
    image_url: str,
    category: str,
    brand: str,
    tags: str,
    specs: str,
    options: str,
    gallery: str,
    company_id: str | None,
) -> None:
    product.sku = sku.strip().upper()
    product.name = name.strip()
    product.description = description.strip()
    product.price = Decimal(price.replace(",", "."))
    product.stock = stock
    product.image_url = image_url.strip()
    product.category = category.strip()[:80]
    product.brand = brand.strip()[:80]
    product.tags = tags.strip()[:240]
    product.specs = parse_spec_lines(specs)
    product.options = parse_options(options)
    product.gallery = parse_gallery(gallery)
    if company_id:
        product.company_id = company_id


@router.post("/inventario")
async def create_product(
    request: Request,
    db: AsyncSession = Depends(get_db),
    sku: str = Form(...),
    name: str = Form(...),
    price: str = Form(...),
    stock: int = Form(0),
    description: str = Form(""),
    image_url: str = Form(""),
    category: str = Form(""),
    brand: str = Form(""),
    tags: str = Form(""),
    specs: str = Form(""),
    options: str = Form(""),
    gallery: str = Form(""),
):
    redir = require_user(request)
    if redir:
        return redir
    from app.services import whatsapp as wa

    company = await wa.get_company(db, request.session.get("company_id"))
    product = Product()
    _fill_product(
        product,
        sku=sku,
        name=name,
        price=price,
        stock=stock,
        description=description,
        image_url=image_url,
        category=category,
        brand=brand,
        tags=tags,
        specs=specs,
        options=options,
        gallery=gallery,
        company_id=company.id if company else None,
    )
    db.add(product)
    await db.commit()
    return RedirectResponse("/inventario", status_code=303)


@router.get("/inventario/{product_id}")
async def edit_product_page(
    request: Request, product_id: str, db: AsyncSession = Depends(get_db)
):
    redir = require_user(request)
    if redir:
        return redir
    product = await db.get(Product, product_id)
    if not product or not _belongs(request, product.company_id):
        raise HTTPException(404, "Producto no encontrado")
    return templates.TemplateResponse(
        request,
        "product_edit.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "product": product,
            "specs_text": format_spec_lines(product.specs),
            "options_text": format_options(product.options),
            "gallery_text": format_gallery(product.gallery),
        },
    )


@router.post("/inventario/{product_id}")
async def update_product(
    request: Request,
    product_id: str,
    db: AsyncSession = Depends(get_db),
    sku: str = Form(...),
    name: str = Form(...),
    price: str = Form(...),
    stock: int = Form(0),
    description: str = Form(""),
    image_url: str = Form(""),
    category: str = Form(""),
    brand: str = Form(""),
    tags: str = Form(""),
    specs: str = Form(""),
    options: str = Form(""),
    gallery: str = Form(""),
):
    redir = require_user(request)
    if redir:
        return redir
    product = await db.get(Product, product_id)
    if not product or not _belongs(request, product.company_id):
        raise HTTPException(404, "Producto no encontrado")
    _fill_product(
        product,
        sku=sku,
        name=name,
        price=price,
        stock=stock,
        description=description,
        image_url=image_url,
        category=category,
        brand=brand,
        tags=tags,
        specs=specs,
        options=options,
        gallery=gallery,
        company_id=product.company_id,
    )
    await db.commit()
    return RedirectResponse("/inventario", status_code=303)


@router.post("/inventario/{product_id}/stock")
async def adjust_stock(
    request: Request,
    product_id: str,
    db: AsyncSession = Depends(get_db),
    stock: int = Form(...),
):
    redir = require_user(request)
    if redir:
        return redir
    product = await db.get(Product, product_id)
    if not product or not _belongs(request, product.company_id):
        raise HTTPException(404, "Producto no encontrado")
    product.stock = stock
    await db.commit()
    return RedirectResponse("/inventario", status_code=303)


@router.post("/inventario/{product_id}/eliminar")
async def delete_product(
    request: Request,
    product_id: str,
    db: AsyncSession = Depends(get_db),
):
    redir = require_user(request)
    if redir:
        return redir
    product = await db.get(Product, product_id)
    if not product or not _belongs(request, product.company_id):
        raise HTTPException(404, "Producto no encontrado")
    try:
        result = await inventory.delete_product(db, product_id)
        await db.commit()
    except inventory.StockError:
        raise HTTPException(404, "Producto no encontrado")
    return RedirectResponse(f"/inventario?ok={result}", status_code=303)


@router.get("/tienda")
async def store_settings(request: Request, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    from app.services import whatsapp as wa

    company = await wa.get_company(db, request.session.get("company_id"))
    store_url = ""
    if company:
        store_url = settings.public_base_url.rstrip("/") + f"/t/{company.slug}"
    return templates.TemplateResponse(
        request,
        "store.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "company": company,
            "store_url": store_url,
        },
    )


@router.post("/tienda")
async def save_store_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    store_tagline: str = Form(""),
    store_enabled: str = Form(""),
):
    redir = require_user(request)
    if redir:
        return redir
    from app.services import whatsapp as wa

    company = await wa.get_company(db, request.session.get("company_id"))
    if company:
        company.store_tagline = store_tagline.strip()[:240]
        company.store_enabled = store_enabled in {"on", "1", "true", "yes"}
        await db.commit()
    return RedirectResponse("/tienda", status_code=303)


_PAY_IMG = {".jpg", ".jpeg", ".png", ".webp"}
BANK_ACCOUNT_TYPES = ("Caja de ahorro", "Cuenta corriente", "Cuenta fiscal")


async def _store_pay_qr(file: UploadFile) -> tuple[str | None, str | None]:
    suffix = Path(file.filename or "qr.png").suffix.lower()
    if suffix not in _PAY_IMG:
        return None, "Usá JPG, PNG o WEBP"
    raw = await file.read()
    if not raw:
        return None, "El archivo está vacío"
    if len(raw) > 6 * 1024 * 1024:
        return None, "La imagen pesa más de 6 MB"
    PAY_DIR.mkdir(parents=True, exist_ok=True)
    dest = PAY_DIR / f"{uuid4().hex}{suffix}"
    dest.write_bytes(raw)
    return str(dest), None


@router.get("/cobros")
async def payments_settings(request: Request, db: AsyncSession = Depends(get_db), ok: str = "", error: str = ""):
    redir = require_user(request)
    if redir:
        return redir
    from app.services import whatsapp as wa

    company = await wa.get_company(db, request.session.get("company_id"))
    return templates.TemplateResponse(
        request,
        "cobros.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "company": company,
            "account_types": BANK_ACCOUNT_TYPES,
            "ok": ok,
            "error": error,
        },
    )


@router.post("/cobros")
async def save_payments_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    bank_name: str = Form(""),
    bank_holder: str = Form(""),
    bank_account_type: str = Form(""),
    bank_account_number: str = Form(""),
    bank_id_doc: str = Form(""),
    pay_instructions: str = Form(""),
):
    redir = require_user(request)
    if redir:
        return redir
    from app.services import whatsapp as wa

    company = await wa.get_company(db, request.session.get("company_id"))
    if not company:
        return RedirectResponse("/cobros?error=empresa", status_code=303)
    request.session["company_id"] = company.id
    await db.execute(
        update(Company)
        .where(Company.id == company.id)
        .values(
            bank_name=bank_name.strip()[:120],
            bank_holder=bank_holder.strip()[:160],
            bank_account_type=bank_account_type.strip()[:80],
            bank_account_number=bank_account_number.strip()[:80],
            bank_id_doc=bank_id_doc.strip()[:80],
            pay_instructions=pay_instructions.strip()[:2000],
        )
    )
    await db.commit()
    return RedirectResponse("/cobros?ok=cuenta", status_code=303)


@router.post("/cobros/qr")
async def save_pay_qr(
    request: Request,
    db: AsyncSession = Depends(get_db),
    qr_file: UploadFile = File(...),
):
    redir = require_user(request)
    if redir:
        return redir
    from app.services import whatsapp as wa

    company = await wa.get_company(db, request.session.get("company_id"))
    if not company:
        return RedirectResponse("/cobros?error=empresa", status_code=303)
    path, err = await _store_pay_qr(qr_file)
    if err or not path:
        return RedirectResponse(f"/cobros?error={quote(err or 'No se pudo guardar el QR')}", status_code=303)
    request.session["company_id"] = company.id
    await db.execute(update(Company).where(Company.id == company.id).values(pay_qr_path=path))
    await db.commit()
    return RedirectResponse("/cobros?ok=qr", status_code=303)


@router.post("/cobros/qr/borrar")
async def delete_pay_qr(request: Request, db: AsyncSession = Depends(get_db)):
    redir = require_user(request)
    if redir:
        return redir
    from app.services import whatsapp as wa

    company = await wa.get_company(db, request.session.get("company_id"))
    if company:
        old = Path(company.pay_qr_path) if company.pay_qr_path else None
        await db.execute(update(Company).where(Company.id == company.id).values(pay_qr_path=""))
        await db.commit()
        if old and old.exists() and PAY_DIR.resolve() in old.resolve().parents:
            old.unlink(missing_ok=True)
    return RedirectResponse("/cobros?ok=qr", status_code=303)


@router.get("/dispositivos")
async def devices_page(request: Request, db: AsyncSession = Depends(get_db)):
    redir = require_superadmin(request)
    if redir:
        return redir
    devices = list(await db.scalars(select(FarmDevice).order_by(FarmDevice.serial)))
    lives = await live.active_lives_by_serial(db)
    sessions = list(
        await db.scalars(select(LiveSession).order_by(desc(LiveSession.started_at)).limit(30))
    )
    return templates.TemplateResponse(
        request,
        "devices.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "devices": devices,
            "lives": lives,
            "sessions": sessions,
        },
    )
