"""Empresa activa de la petición (dashboard o webhook)."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

from sqlalchemy import select
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


async def get_or_create_state(db: AsyncSession, phone: str) -> ConversationState:
    phone = (phone or "").lstrip("+")
    cid = (await resolve_company_id(db)) or ""
    row = await db.scalar(
        select(ConversationState).where(
            ConversationState.phone == phone,
            ConversationState.company_id == cid,
        )
    )
    if not row:
        row = ConversationState(phone=phone, company_id=cid, step="idle", data={})
        db.add(row)
        await db.flush()
    row.updated_at = utcnow()
    return row


async def get_conversation(db: AsyncSession, phone: str) -> Conversation | None:
    phone = (phone or "").lstrip("+")
    cid = await resolve_company_id(db)
    query = select(Conversation).where(Conversation.phone == phone)
    if cid:
        query = query.where(Conversation.company_id == cid)
    return await db.scalar(query)
