from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Company, User, WhatsAppAccount
from app.services import auth, whatsapp
from app.web import require_superadmin, templates

router = APIRouter(tags=["admin"])
settings = get_settings()


@router.get("/login/2fa")
async def login_2fa_form(request: Request):
    if not request.session.get("pending_2fa"):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "login_2fa.html",
        {"app_name": settings.app_name, "error": None},
    )


@router.post("/login/2fa")
async def login_2fa_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    code: str = Form(""),
):
    user_id = request.session.get("pending_2fa")
    nxt = request.session.get("login_next") or "/"
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = await auth.get_user(db, str(user_id))
    secret = auth.user_totp_secret(user) if user else ""
    if not user or not user.is_active or not auth.verify_totp(secret, code):
        return templates.TemplateResponse(
            request,
            "login_2fa.html",
            {"app_name": settings.app_name, "error": "Código 2FA incorrecto"},
            status_code=401,
        )
    await auth.establish_session(request, db, user)
    return RedirectResponse(nxt if isinstance(nxt, str) else "/", status_code=303)


@router.get("/seguridad/2fa")
async def setup_2fa_form(request: Request, db: AsyncSession = Depends(get_db)):
    redir = require_superadmin(request)
    if redir:
        return redir
    user = await auth.get_user(db, request.session.get("user_id") or "")
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.totp_enabled:
        return templates.TemplateResponse(
            request,
            "setup_2fa.html",
            {
                "app_name": settings.app_name,
                "user": user.username,
                "already": True,
                "qr": None,
                "error": None,
                "ok": request.query_params.get("ok"),
            },
        )
    secret = auth.user_totp_secret(user)
    if not secret:
        secret = auth.new_totp_secret()
        auth.store_totp_secret(user, secret)
        await db.commit()
    uri = auth.totp_uri(secret, user.username, settings.app_name)
    return templates.TemplateResponse(
        request,
        "setup_2fa.html",
        {
            "app_name": settings.app_name,
            "user": user.username,
            "already": False,
            "qr": auth.totp_qr_data_uri(uri),
            "error": None,
            "ok": None,
        },
    )


@router.post("/seguridad/2fa")
async def setup_2fa_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    code: str = Form(""),
):
    redir = require_superadmin(request)
    if redir:
        return redir
    user = await auth.get_user(db, request.session.get("user_id") or "")
    if not user:
        return RedirectResponse("/login", status_code=303)
    secret = auth.user_totp_secret(user)
    if not auth.verify_totp(secret, code):
        uri = auth.totp_uri(secret, user.username, settings.app_name) if secret else ""
        return templates.TemplateResponse(
            request,
            "setup_2fa.html",
            {
                "app_name": settings.app_name,
                "user": user.username,
                "already": False,
                "qr": auth.totp_qr_data_uri(uri) if uri else None,
                "error": "Ese código no coincide. Escaneá de nuevo e intentá.",
                "ok": None,
            },
            status_code=400,
        )
    user.totp_enabled = True
    await db.commit()
    request.session["totp_enabled"] = True
    return RedirectResponse("/clientes?ok=2fa", status_code=303)


@router.get("/clientes")
async def clients_page(request: Request, db: AsyncSession = Depends(get_db)):
    redir = require_superadmin(request)
    if redir:
        return redir
    companies = list(
        await db.scalars(select(Company).order_by(Company.name))
    )
    users = list(await db.scalars(select(User).order_by(User.username)))
    by_company: dict[str, list[User]] = {}
    for row in users:
        if row.company_id:
            by_company.setdefault(row.company_id, []).append(row)
    accounts = list(await db.scalars(select(WhatsAppAccount)))
    wa_ready = {acc.company_id: bool(acc.phone_number_id and acc.token_enc) for acc in accounts}
    return templates.TemplateResponse(
        request,
        "clientes.html",
        {
            "app_name": settings.app_name,
            "user": request.session.get("user"),
            "companies": companies,
            "by_company": by_company,
            "wa_ready": wa_ready,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
            "active_company": request.session.get("company_id"),
        },
    )


@router.post("/clientes")
async def create_client(
    request: Request,
    db: AsyncSession = Depends(get_db),
    company_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    totp: str = Form(""),
    business_e164: str = Form(""),
    phone_number_id: str = Form(""),
    access_token: str = Form(""),
    verify_token: str = Form(""),
):
    redir = require_superadmin(request)
    if redir:
        return redir
    owner = await auth.get_user(db, request.session.get("user_id") or "")
    if not owner or not owner.totp_enabled or not auth.verify_totp(auth.user_totp_secret(owner), totp):
        return RedirectResponse("/clientes?error=2fa", status_code=303)
    name = company_name.strip()
    uname = username.strip()
    pwd = password.strip()
    if not name or not uname or len(pwd) < 6:
        return RedirectResponse("/clientes?error=datos", status_code=303)
    if await auth.find_user(db, uname):
        return RedirectResponse("/clientes?error=usuario", status_code=303)
    company = await whatsapp.create_company(db, name)
    db.add(
        User(
            username=uname,
            password_hash=auth.hash_password(pwd),
            role=auth.ROLE_VENDOR,
            company_id=company.id,
            is_active=True,
            totp_enabled=False,
        )
    )
    if phone_number_id.strip() or access_token.strip() or business_e164.strip():
        await whatsapp.save_account_credentials(
            db,
            company=company,
            company_name=name,
            label="Principal",
            business_e164=business_e164,
            phone_number_id=phone_number_id,
            waba_id="",
            access_token=access_token,
            app_secret="",
            verify_token=verify_token,
            graph_version="v21.0",
            skip_signature=False,
        )
    await db.commit()
    return RedirectResponse("/clientes?ok=creado", status_code=303)


@router.post("/clientes/{company_id}/entrar")
async def enter_client(
    request: Request, company_id: str, db: AsyncSession = Depends(get_db)
):
    redir = require_superadmin(request)
    if redir:
        return redir
    company = await db.get(Company, company_id)
    if not company:
        return RedirectResponse("/clientes?error=no", status_code=303)
    request.session["company_id"] = company.id
    request.session["company_name"] = company.name
    return RedirectResponse("/", status_code=303)


@router.post("/clientes/{user_id}/estado")
async def toggle_user(
    request: Request,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    totp: str = Form(""),
):
    redir = require_superadmin(request)
    if redir:
        return redir
    owner = await auth.get_user(db, request.session.get("user_id") or "")
    if not owner or not owner.totp_enabled or not auth.verify_totp(auth.user_totp_secret(owner), totp):
        return RedirectResponse("/clientes?error=2fa", status_code=303)
    user = await db.get(User, user_id)
    if not user or user.role == auth.ROLE_SUPERADMIN:
        return RedirectResponse("/clientes?error=no", status_code=303)
    user.is_active = not user.is_active
    await db.commit()
    return RedirectResponse("/clientes?ok=estado", status_code=303)
