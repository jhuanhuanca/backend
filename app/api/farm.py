from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import FarmDevice, LiveSession
from app.services import inventory, live

router = APIRouter(prefix="/api/farm", tags=["farm"])
settings = get_settings()


class DeviceIn(BaseModel):
    serial: str
    state: str = "unknown"
    model: str = ""
    product: str = ""
    tiktok_username: str = ""
    scrcpy_active: bool = False
    transport_id: str = ""


class SyncIn(BaseModel):
    devices: list[DeviceIn] = Field(default_factory=list)


class LiveStartIn(BaseModel):
    serial: str
    tiktok_username: str = ""
    product_sku: str = ""


class LiveStopIn(BaseModel):
    serial: str


async def require_farm_key(x_farm_key: str = Header(..., alias="X-Farm-Key")) -> None:
    if not secrets.compare_digest(x_farm_key, settings.farm_api_key):
        raise HTTPException(status_code=401, detail="Farm API key inválida")


def session_payload(session: LiveSession) -> dict:
    return {
        "id": session.id,
        "public_code": session.public_code,
        "capture_url": session.capture_url,
        "tiktok_username": session.tiktok_username,
        "device_serial": session.device_serial,
        "product_sku": session.product_sku,
        "status": session.status,
        "source": session.source,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
    }


def device_payload(device: FarmDevice, session: LiveSession | None = None) -> dict:
    return {
        "serial": device.serial,
        "state": device.state,
        "model": device.model,
        "product": device.product,
        "tiktok_username": device.tiktok_username,
        "scrcpy_active": device.scrcpy_active,
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "live": session_payload(session) if session else None,
    }


@router.post("/devices/sync")
async def sync_devices(
    payload: SyncIn,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_farm_key),
):
    await live.sync_devices(db, [item.model_dump() for item in payload.devices])
    lives = await live.active_lives_by_serial(db)
    devices = list(await db.scalars(select(FarmDevice).order_by(FarmDevice.serial)))
    await db.commit()
    return {
        "ok": True,
        "devices": [device_payload(d, lives.get(d.serial)) for d in devices],
        "lives": {serial: session_payload(s) for serial, s in lives.items()},
    }


@router.get("/snapshot")
async def snapshot(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_farm_key),
):
    lives = await live.active_lives_by_serial(db)
    devices = list(await db.scalars(select(FarmDevice).order_by(FarmDevice.serial)))
    products = await inventory.list_catalog(db)
    return {
        "devices": [device_payload(d, lives.get(d.serial)) for d in devices],
        "lives": {serial: session_payload(s) for serial, s in lives.items()},
        "products": [
            {
                "sku": p.sku,
                "name": p.name,
                "price": str(p.price),
                "stock": await inventory.available_stock(db, p.id),
            }
            for p in products
        ],
    }


@router.post("/live/start")
async def start_live(
    payload: LiveStartIn,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_farm_key),
):
    session = await live.start_live(
        db,
        serial=payload.serial.strip(),
        tiktok_username=payload.tiktok_username.strip(),
        product_sku=payload.product_sku.strip(),
        source="farm",
    )
    await db.commit()
    await db.refresh(session)
    return {"ok": True, "live": session_payload(session)}


@router.post("/live/stop")
async def stop_live(
    payload: LiveStopIn,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_farm_key),
):
    session = await live.stop_live(db, payload.serial.strip())
    await db.commit()
    if not session:
        return {"ok": True, "live": None}
    return {"ok": True, "live": session_payload(session)}
