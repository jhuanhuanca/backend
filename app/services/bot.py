from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROOFS_DIR, get_settings
from app.models import ConversationState, Product, utcnow
from app.services import deliveries, inbox, inventory, live, orders, wa_catalog, whatsapp
from app.services.inventory import StockError

settings = get_settings()

LIVE_RE = re.compile(r"\bLV-[A-Z0-9]{4}\b", re.IGNORECASE)
CATALOG_WORDS = {"hola", "menu", "menú", "catalogo", "catálogo", "hi", "buenas", "info"}


async def handle_incoming(
    db: AsyncSession,
    *,
    phone: str,
    name: str,
    text: str | None,
    image_media_id: str | None = None,
) -> list[str]:
    replies: list[str] = []
    state = await _get_state(db, phone)
    if text:
        code_match = LIVE_RE.search(text.upper())
        if code_match:
            session = await live.get_by_code(db, code_match.group(0))
            if session:
                state.live_session_id = session.id
                replies.append(
                    f"Quedaste vinculado al live {session.public_code}"
                    + (f" (@{session.tiktok_username})" if session.tiktok_username else "")
                    + "."
                )

    if text and not image_media_id:
        scheduled = await deliveries.handle_incoming(db, phone=phone, name=name, text=text)
        if scheduled is not None:
            replies.extend(scheduled)
            await db.commit()
            return replies

    if image_media_id:
        replies.extend(await _handle_image(db, phone, name, image_media_id))
        await db.commit()
        return replies

    body = (text or "").strip()
    if not body:
        await db.commit()
        return replies

    lowered = body.lower()
    if lowered in CATALOG_WORDS or "catálogo" in lowered or "catalogo" in lowered:
        replies.extend(await wa_catalog.present_catalog(db, phone))
        state.step = "idle"
        await db.commit()
        return replies

    if state.step == "await_qty":
        replies.extend(await _handle_qty(db, state, phone, name, body))
        await db.commit()
        return replies

    product = await wa_catalog.match_product(db, body)
    if product:
        state.step = "await_qty"
        state.data = {"product_id": product.id, "product_name": product.name}
        free = await inventory.available_stock(db, product.id)
        replies.extend(await wa_catalog.present_product(db, phone, product, free))
        await db.commit()
        return replies

    if lowered in {"pedido", "estado"}:
        replies.append(await _order_status(db, phone))
        await db.commit()
        return replies

    if lowered in {"cancelar", "cancel"}:
        replies.append(await _cancel_open(db, phone))
        await db.commit()
        return replies

    replies.extend(await wa_catalog.present_catalog(db, phone))
    replies.append("También podés escribir PEDIDO o CANCELAR.")
    await db.commit()
    return replies


async def _handle_qty(
    db: AsyncSession, state: ConversationState, phone: str, name: str, body: str
) -> list[str]:
    qty_text = wa_catalog.choice_value(body)
    if not qty_text.isdigit():
        return ["Indicá la cantidad con un número (ej: 2)."]
    qty = int(qty_text)
    product = await db.get(Product, state.data.get("product_id"))
    if not product:
        state.step = "idle"
        return ["No encontré el producto. Escribí CATALOGO."]
    try:
        order = await orders.create_whatsapp_order(
            db,
            phone=phone,
            name=name,
            product=product,
            quantity=qty,
            session_id=state.live_session_id,
        )
    except StockError as exc:
        return [str(exc)]
    state.step = "idle"
    state.data = {}
    payment = order.payments[-1] if order.payments else None
    pay_extra = (payment.qr_payload if payment and payment.qr_payload else "Pagá y enviá el comprobante por este chat.")
    caption = (
        f"Pedido {order.public_code}\n"
        f"{qty} x {product.name} = {order.total_amount:.2f} {settings.currency}\n"
        f"Reserva por {settings.reservation_minutes} min.\n"
        f"{pay_extra}"
    )
    replies: list[str] = []
    if payment and payment.qr_image_path:
        sent = await whatsapp.send_image(phone, Path(payment.qr_image_path), caption)
        await inbox.record_bot_image(db, phone, Path(payment.qr_image_path), caption, sent)
    else:
        replies.append(caption)
    loaded = await orders.load_order(db, order.id)
    if loaded:
        replies.extend(await deliveries.begin_schedule(db, loaded))
    return replies


async def _handle_image(db: AsyncSession, phone: str, name: str, media_id: str) -> list[str]:
    customer = await orders.get_or_create_customer(db, phone, name)
    order = await orders.latest_open_order(db, customer.id)
    if not order:
        return ["No hay un pedido pendiente de pago. Escribí CATALOGO para armar uno."]
    dest = PROOFS_DIR / f"{order.public_code}.jpg"
    try:
        await whatsapp.download_media(media_id, dest)
    except Exception:
        dest.write_bytes(b"")
        return [
            "Recibí el comprobante, pero no pude descargar la imagen. "
            "El vendedor lo revisará igual si reenviás o lo confirmamos a mano."
        ]
    await orders.attach_proof(db, order, dest)
    replies = [
        f"Comprobante recibido para {order.public_code}. "
        "El vendedor lo confirmará en el dashboard."
    ]
    if deliveries.delivery_incomplete(order):
        replies.extend(await deliveries.begin_schedule(db, order))
    return replies


async def _order_status(db: AsyncSession, phone: str) -> str:
    customer = await orders.get_or_create_customer(db, phone)
    order = await orders.latest_open_order(db, customer.id)
    if not order:
        return "No tenés pedidos abiertos."
    return f"{order.public_code} está {order.status}. Total {order.total_amount:.2f} {settings.currency}."


async def _cancel_open(db: AsyncSession, phone: str) -> str:
    customer = await orders.get_or_create_customer(db, phone)
    order = await orders.latest_open_order(db, customer.id)
    if not order:
        return "No hay pedido para cancelar."
    await orders.cancel_order(db, order)
    return f"Cancelé {order.public_code}."


async def _get_state(db: AsyncSession, phone: str) -> ConversationState:
    phone = phone.lstrip("+")
    row = await db.get(ConversationState, phone)
    if not row:
        row = ConversationState(phone=phone, step="idle", data={})
        db.add(row)
        await db.flush()
    row.updated_at = utcnow()
    return row
