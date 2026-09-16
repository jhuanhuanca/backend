from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import InventoryReservation, OrderItem, Product, ProductVariant, utcnow

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


async def unwind_order_stock(db: AsyncSession, order_id: str) -> None:
    """Devuelve al inventario el stock de reservas ya consumidas y libera las activas."""
    rows = list(
        await db.scalars(
            select(InventoryReservation).where(InventoryReservation.order_id == order_id)
        )
    )
    for row in rows:
        if row.status == "consumed":
            if row.variant_id:
                variant = await db.get(ProductVariant, row.variant_id)
                if variant:
                    variant.stock += row.quantity
            else:
                product = await db.get(Product, row.product_id)
                if product:
                    product.stock += row.quantity
        row.status = "released"


async def delete_product(db: AsyncSession, product_id: str) -> str:
    product = await db.scalar(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.variants))
    )
    if not product:
        raise StockError("Producto no encontrado")
    used = await db.scalar(select(OrderItem.id).where(OrderItem.product_id == product_id).limit(1))
    reservations = list(
        await db.scalars(
            select(InventoryReservation).where(InventoryReservation.product_id == product_id)
        )
    )
    for row in reservations:
        await db.delete(row)
    if used:
        product.active = False
        suffix = f"-X{product.id[:8]}"
        base = (product.sku or "SKU")[: max(1, 64 - len(suffix))]
        product.sku = f"{base}{suffix}"
        await db.flush()
        return "oculto"
    for variant in list(product.variants or []):
        await db.delete(variant)
    await db.delete(product)
    await db.flush()
    return "eliminado"
