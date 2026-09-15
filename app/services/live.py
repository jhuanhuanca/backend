from __future__ import annotations

from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FarmDevice, LiveSession, utcnow
from app.services.codes import next_live_code
from app.services import whatsapp as wa_svc


async def build_capture_url(db: AsyncSession, public_code: str) -> str:
    number = await wa_svc.business_e164(db)
    text = (
        f"Hola, vengo del live {public_code}. "
        f"Quiero ver el catálogo."
    )
    return f"https://wa.me/{number}?text={quote(text)}"


async def sync_devices(db: AsyncSession, devices: list[dict]) -> list[FarmDevice]:
    stored: list[FarmDevice] = []
    seen: set[str] = set()
    for raw in devices:
        serial = (raw.get("serial") or "").strip()
        if not serial:
            continue
        seen.add(serial)
        row = await db.scalar(select(FarmDevice).where(FarmDevice.serial == serial))
        if not row:
            row = FarmDevice(serial=serial)
            db.add(row)
        row.state = raw.get("state") or "unknown"
        row.model = raw.get("model") or ""
        row.product = raw.get("product") or ""
        if raw.get("tiktok_username"):
            row.tiktok_username = raw["tiktok_username"]
        row.scrcpy_active = bool(raw.get("scrcpy_active"))
        row.last_seen_at = utcnow()
        extra = dict(raw.get("extra") or {})
        extra["transport_id"] = raw.get("transport_id") or extra.get("transport_id") or ""
        row.extra = extra
        stored.append(row)
    if seen:
        others = await db.scalars(select(FarmDevice))
        for row in others:
            if row.serial not in seen:
                row.state = "offline"
                row.scrcpy_active = False
    await db.flush()
    return stored


async def active_lives_by_serial(db: AsyncSession) -> dict[str, LiveSession]:
    result = await db.scalars(
        select(LiveSession).where(
            LiveSession.status == "live",
            LiveSession.device_serial.is_not(None),
        )
    )
    return {s.device_serial: s for s in result if s.device_serial}


async def start_live(
    db: AsyncSession,
    *,
    serial: str,
    tiktok_username: str = "",
    product_sku: str = "",
    source: str = "farm",
) -> LiveSession:
    existing = await db.scalar(
        select(LiveSession).where(
            LiveSession.device_serial == serial,
            LiveSession.status == "live",
        )
    )
    if existing:
        if tiktok_username:
            existing.tiktok_username = tiktok_username
        if product_sku:
            existing.product_sku = product_sku
        return existing

    device = await db.scalar(select(FarmDevice).where(FarmDevice.serial == serial))
    username = tiktok_username or (device.tiktok_username if device else "")
    if device and tiktok_username:
        device.tiktok_username = tiktok_username

    code = await next_live_code(db)
    session = LiveSession(
        public_code=code,
        tiktok_username=username,
        device_serial=serial,
        product_sku=product_sku,
        capture_url=await build_capture_url(db, code),
        source=source,
        status="live",
    )
    db.add(session)
    await db.flush()
    return session


async def stop_live(db: AsyncSession, serial: str) -> LiveSession | None:
    session = await db.scalar(
        select(LiveSession).where(
            LiveSession.device_serial == serial,
            LiveSession.status == "live",
        )
    )
    if not session:
        return None
    session.status = "ended"
    session.ended_at = utcnow()
    await db.flush()
    return session


async def get_by_code(db: AsyncSession, code: str) -> LiveSession | None:
    cleaned = code.strip().upper()
    return await db.scalar(select(LiveSession).where(LiveSession.public_code == cleaned))
