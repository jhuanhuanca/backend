from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import InventoryReservation, Product, ProductVariant, utcnow

settings = get_settings()


class StockError(ValueError):
    pass


async def expire_reservations(db: AsyncSession) -> int:
    now = utcnow()
    result = await db.scalars(
        select(InventoryReservation).where(
            InventoryReservation.status == "active",
            InventoryReservation.expires_at <= now,
        )
    )
    rows = list(result)
    for row in rows:
        row.status = "released"
    if rows:
        await db.flush()
    return len(rows)


async def available_stock(db: AsyncSession, product_id: str, variant_id: str | None = None) -> int:
    await expire_reservations(db)
    if variant_id:
        variant = await db.get(ProductVariant, variant_id)
        if not variant:
            raise StockError("Variante no encontrada")
        physical = variant.stock
    else:
        product = await db.get(Product, product_id)
        if not product:
            raise StockError("Producto no encontrado")
        physical = product.stock

    stmt = select(InventoryReservation).where(
        InventoryReservation.product_id == product_id,
        InventoryReservation.status == "active",
    )
    if variant_id:
        stmt = stmt.where(InventoryReservation.variant_id == variant_id)
    else:
        stmt = stmt.where(InventoryReservation.variant_id.is_(None))
    reserved = sum(r.quantity for r in (await db.scalars(stmt)).all())
    return max(0, physical - reserved)


async def list_catalog(db: AsyncSession) -> list[Product]:
    await expire_reservations(db)
    result = await db.scalars(
        select(Product)
        .where(Product.active.is_(True))
        .options(selectinload(Product.variants))
        .order_by(Product.name)
    )
    return list(result)


async def reserve(
    db: AsyncSession,
    *,
    product_id: str,
    quantity: int,
    order_id: str | None,
    variant_id: str | None = None,
) -> InventoryReservation:
    if quantity < 1:
        raise StockError("La cantidad debe ser al menos 1")
    free = await available_stock(db, product_id, variant_id)
    if free < quantity:
        raise StockError(f"Stock insuficiente (disponible: {free})")
    row = InventoryReservation(
        product_id=product_id,
        variant_id=variant_id,
        order_id=order_id,
        quantity=quantity,
        expires_at=utcnow() + timedelta(minutes=settings.reservation_minutes),
        status="active",
    )
    db.add(row)
    await db.flush()
    return row


async def consume_reservations(db: AsyncSession, order_id: str) -> None:
    result = await db.scalars(
        select(InventoryReservation).where(
            InventoryReservation.order_id == order_id,
            InventoryReservation.status == "active",
        )
    )
    for row in result:
        if row.variant_id:
            variant = await db.get(ProductVariant, row.variant_id)
            if variant:
                variant.stock = max(0, variant.stock - row.quantity)
        else:
            product = await db.get(Product, row.product_id)
            if product:
                product.stock = max(0, product.stock - row.quantity)
        row.status = "consumed"


async def release_reservations(db: AsyncSession, order_id: str) -> None:
    result = await db.scalars(
        select(InventoryReservation).where(
            InventoryReservation.order_id == order_id,
            InventoryReservation.status == "active",
        )
    )
    for row in result:
        row.status = "released"
