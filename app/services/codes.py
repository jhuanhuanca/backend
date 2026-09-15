from __future__ import annotations

import secrets
import string

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LiveSession, Order


async def next_order_code(db: AsyncSession) -> str:
    result = await db.scalar(select(func.count(Order.id)))
    n = int(result or 0) + 1001
    return f"PED-{n}"


async def next_live_code(db: AsyncSession) -> str:
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = "LV-" + "".join(secrets.choice(alphabet) for _ in range(4))
        exists = await db.scalar(select(LiveSession.id).where(LiveSession.public_code == code))
        if not exists:
            return code
    return f"LV-{secrets.token_hex(3).upper()}"
