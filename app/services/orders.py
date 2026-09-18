from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Appointment,
    ConversationState,
    Customer,
    InventoryReservation,
    Order,
    OrderItem,
    Payment,
    Product,
    utcnow,
)
from app.services import inventory as inventory
from app.services import tenancy
from app.services.codes import next_order_code
from app.services.payments import prepare_payment_assets


class OrderError(ValueError):
    pass


async def get_or_create_customer(
    db: AsyncSession, phone: str, name: str = ""
) -> Customer:
    phone = phone.lstrip("+")
    cid = await tenancy.resolve_company_id(db)
    row = await tenancy.find_by_phone(db, Customer, phone, cid)
    if row:
        if name and not row.name:
            row.name = name
        if cid and not row.company_id:
            row.company_id = cid
        return row
    row = Customer(phone=phone, name=name or phone, company_id=cid)
    db.add(row)
    await db.flush()
    return row


async def create_whatsapp_order(
    db: AsyncSession,
    *,
    phone: str,
    name: str,
    product: Product,
    quantity: int,
    session_id: str | None,
    pay_amount: Decimal | None = None,
    shipping_fee: Decimal = Decimal("0"),
    notes: str = "",
    delivery_type: str = "to_coordinate",
    delivery_address: str = "",
    pay_override: dict | None = None,
) -> Order:
    customer = await get_or_create_customer(db, phone, name)
    unit = Decimal(product.price)
    product_total = unit * quantity
    shipping_fee = Decimal(str(shipping_fee or 0))
    total = product_total + shipping_fee
    charge = pay_amount if pay_amount is not None else total
    charge = Decimal(str(charge)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    free = await inventory.available_stock(db, product.id)
    if free < quantity:
        raise inventory.StockError(f"Stock insuficiente (disponible: {free})")
    order = Order(
        public_code=await next_order_code(db),
        company_id=await tenancy.resolve_company_id(db),
        customer_id=customer.id,
        session_id=session_id,
        channel="whatsapp",
        status="pendiente_pago",
        total_amount=total,
        delivery_type=delivery_type or "to_coordinate",
        delivery_address=delivery_address or "",
        notes=notes or "",
    )
    db.add(order)
    await db.flush()
    order.customer = customer
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            quantity=quantity,
            unit_price=unit,
            product_name=product.name,
        )
    )
    await inventory.reserve(
        db, product_id=product.id, quantity=quantity, order_id=order.id
    )
    from app.services import whatsapp as wa_svc

    company = await wa_svc.get_company(db, await tenancy.resolve_company_id(db))
    payload, qr_path, method = prepare_payment_assets(
        order.public_code, charge, company, override=pay_override
    )
    payment = Payment(
        order_id=order.id,
        amount=charge,
        method=method,
        status="pending",
        qr_payload=payload,
        qr_image_path=str(qr_path) if qr_path else "",
    )
    db.add(payment)
    await db.flush()
    loaded = await load_order(db, order.id)
    assert loaded is not None
    return loaded


async def load_order(db: AsyncSession, order_id: str) -> Order | None:
    return await db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.items),
            selectinload(Order.payments),
            selectinload(Order.customer),
            selectinload(Order.session),
            selectinload(Order.delivery),
        )
    )


async def attach_proof(db: AsyncSession, order: Order, proof_path: Path) -> Payment:
    payment = order.payments[-1] if order.payments else None
    if not payment:
        payment = Payment(order_id=order.id, amount=order.total_amount, status="proof_received")
        db.add(payment)
    payment.proof_image_path = str(proof_path)
    payment.status = "proof_received"
    order.status = "pendiente_pago"
    order.updated_at = utcnow()
    await db.flush()
    return payment


async def confirm_payment(db: AsyncSession, order: Order, confirmed_by: str = "seller") -> Order:
    if order.status == "cancelado":
        raise OrderError("El pedido está cancelado")
    await inventory.consume_reservations(db, order.id)
    for payment in order.payments:
        payment.status = "confirmed"
        payment.confirmed_by = confirmed_by
    order.status = "pagado"
    order.updated_at = utcnow()
    await db.flush()
    return order


async def cancel_order(db: AsyncSession, order: Order) -> Order:
    if order.status in {"pagado", "entregado"}:
        raise OrderError("No se puede cancelar un pedido ya pagado o entregado")
    await inventory.release_reservations(db, order.id)
    order.status = "cancelado"
    order.updated_at = utcnow()
    for payment in order.payments:
        if payment.status != "confirmed":
            payment.status = "rejected"
    await db.flush()
    return order


async def delete_order(db: AsyncSession, order: Order) -> None:
    """Borra el pedido, pagos, entrega, reservas y reuniones ligadas. Devuelve stock."""
    await inventory.unwind_order_stock(db, order.id)
    holds = list(
        await db.scalars(
            select(InventoryReservation).where(InventoryReservation.order_id == order.id)
        )
    )
    for hold in holds:
        await db.delete(hold)
    meetings = list(
        await db.scalars(select(Appointment).where(Appointment.order_id == order.id))
    )
    for meeting in meetings:
        await db.delete(meeting)
    await _detach_order_from_chats(db, order)
    await db.flush()
    await db.delete(order)
    await db.flush()


async def _detach_order_from_chats(db: AsyncSession, order: Order) -> None:
    rows = list(await db.scalars(select(ConversationState)))
    for state in rows:
        data = dict(state.data or {})
        changed = False
        if data.get("delivery_order_id") == order.id:
            data.pop("delivery_order_id", None)
            changed = True
            if (state.step or "").startswith("deliv") or state.step in {
                "ask_mode",
                "ask_window",
                "ask_address",
            }:
                state.step = "idle"
        vars_ = data.get("vars")
        if isinstance(vars_, dict) and (
            vars_.get("order_id") == order.id or vars_.get("order_code") == order.public_code
        ):
            vars_ = dict(vars_)
            vars_.pop("order_id", None)
            vars_.pop("order_code", None)
            data["vars"] = vars_
            changed = True
        if changed:
            state.data = data


async def latest_open_order(db: AsyncSession, customer_id: str) -> Order | None:
    return await db.scalar(
        select(Order)
        .where(
            Order.customer_id == customer_id,
            Order.status == "pendiente_pago",
        )
        .options(selectinload(Order.payments), selectinload(Order.items))
        .order_by(Order.created_at.desc())
    )
