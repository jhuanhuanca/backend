"""Empresa activa de la petición (dashboard o webhook)."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.models import Conversation, ConversationState, utcnow

_company_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tenant_company_id", default=None
)


def current_company_id() -> str | None:
    value = _company_id.get()
    return value or None


def bind_request(request: Request) -> None:
    cid = request.session.get("company_id") or None
    _company_id.set(cid)


@contextmanager
def use_company(company_id: str | None):
    token = _company_id.set(company_id or None)
    try:
        yield
    finally:
        _company_id.reset(token)


async def resolve_company_id(db: AsyncSession, company_id: str | None = None) -> str | None:
    cid = company_id or current_company_id()
    if cid:
        return cid
    from app.services.whatsapp import get_company

    company = await get_company(db)
    return company.id if company else None


async def find_by_phone(db: AsyncSession, model, phone: str, cid: str | None):
    """Busca por empresa; si no hay, reclama la fila vieja sin company_id."""
    phone = (phone or "").lstrip("+")
    if cid:
        row = await db.scalar(
            select(model).where(model.phone == phone, model.company_id == cid)
        )
        if row:
            return row
        row = await db.scalar(
            select(model).where(
                model.phone == phone,
                or_(model.company_id.is_(None), model.company_id == ""),
            )
        )
        if row:
            row.company_id = cid
            return row
        return None
    return await db.scalar(select(model).where(model.phone == phone))


async def get_or_create_state(db: AsyncSession, phone: str) -> ConversationState:
    phone = (phone or "").lstrip("+")
    cid = (await resolve_company_id(db)) or ""
    row = await find_by_phone(db, ConversationState, phone, cid or None)
    if not row:
        row = ConversationState(phone=phone, company_id=cid, step="idle", data={})
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            row = await find_by_phone(db, ConversationState, phone, cid or None)
            if not row:
                row = await db.scalar(
                    select(ConversationState).where(ConversationState.phone == phone)
                )
            if not row:
                raise
            if cid and not row.company_id:
                row.company_id = cid
    row.updated_at = utcnow()
    return row


async def get_conversation(db: AsyncSession, phone: str) -> Conversation | None:
    phone = (phone or "").lstrip("+")
    cid = await resolve_company_id(db)
    return await find_by_phone(db, Conversation, phone, cid)
