from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROOFS_DIR, get_settings
from app.models import BotFlow, Conversation, ConversationState, Product, utcnow
from app.services import deliveries, inbox, inventory, live, motor_client, orders, wa_catalog, whatsapp
from app.services.flow_definition import WAIT_TYPES
from app.services.inventory import StockError

settings = get_settings()
LIVE_RE = re.compile(r"\bLV-[A-Z0-9]{4}\b", re.IGNORECASE)
MAX_STEPS = 24


async def published_flow(db: AsyncSession) -> BotFlow | None:
    return await db.scalar(
        select(BotFlow)
        .where(BotFlow.status == "published")
        .order_by(BotFlow.is_default.desc(), BotFlow.updated_at.desc())
    )


async def handle_incoming(
    db: AsyncSession,
    *,
    phone: str,
    name: str,
    text: str | None,
    image_media_id: str | None = None,
    flow: BotFlow | None = None,
) -> list[str]:
    phone = phone.lstrip("+")
    conv = await inbox.get_or_create_conversation(db, phone, name)
    if conv.bot_paused:
        await db.commit()
        return []

    graph = flow or await published_flow(db)
    if not graph:
        await db.commit()
        return []

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
            state = await db.get(ConversationState, phone) or state
            if not (state.data or {}).get("schedule_done"):
                await db.commit()
                return replies
            inbound_text = ""
            schedule_advance = True
        else:
            inbound_text = (text or "").strip()
            schedule_advance = False
    else:
        inbound_text = (text or "").strip()
        schedule_advance = False

    vars_ = dict(state.data or {}).get("vars") or {}
    node_id = dict(state.data or {}).get("node_id")
    vars_["name"] = name

    nodes = {n["id"]: n for n in (graph.definition or {}).get("nodes") or [] if n.get("id")}
    edges = (graph.definition or {}).get("edges") or []
    if not nodes:
        await db.commit()
        return replies

    current = nodes.get(node_id) if node_id else None
    inbound = {
        "text": inbound_text,
        "image_media_id": None if schedule_advance else image_media_id,
        "is_image": bool(image_media_id) and not schedule_advance,
        "schedule_advance": schedule_advance,
    }
    vars_["last_text"] = inbound["text"]
    vars_["currency"] = settings.currency
    if current is None or current.get("type") == "start":
        current = _start_node(nodes)

    ctx = {
        "db": db,
        "phone": phone,
        "name": name,
        "inbound": inbound,
        "vars": vars_,
        "graph": graph,
        "edges": edges,
        "nodes": nodes,
        "conv": conv,
    }

    if inbound.get("schedule_advance"):
        sched_id = (state.data or {}).get("schedule_node_id") or (current or {}).get("id")
        outgoing = [e for e in edges if e.get("from") == sched_id]
        nxt = _first(outgoing, "always") or _first(outgoing, "default") or (outgoing[0] if outgoing else None)
        if nxt and nxt.get("to") in nodes:
            current = nodes[nxt["to"]]
            current, extra = await _auto_until_wait(current, ctx)
            replies.extend(extra)
    else:
        was_waiting = current is not None and current.get("type") in WAIT_TYPES
        current, extra = await _auto_until_wait(current, ctx)
        replies.extend(extra)
        has_inbound = bool(inbound["text"] or inbound["is_image"])
        if was_waiting and current and current.get("type") in WAIT_TYPES and has_inbound:
            nxt = _pick_edge(edges, current["id"], inbound, {}, mode="wait")
            if (
                current.get("type") != "wait_payment"
                and _looks_like_product(inbound.get("text") or "")
            ):
                match_node = next((n for n in nodes.values() if n.get("type") == "match_product"), None)
                if match_node:
                    nxt = {"to": match_node["id"], "trigger_type": "product_pick"}
            if nxt and nxt.get("trigger_type") == "is_digit" and inbound.get("text", "").strip().isdigit():
                vars_["qty"] = int(inbound["text"].strip())
            if nxt and nxt.get("to") in nodes:
                current = nodes[nxt["to"]]
                current, extra = await _auto_until_wait(current, ctx)
                replies.extend(extra)

    node_id = current["id"] if current else None
    await _persist(state, graph.id, node_id, vars_)
    await db.commit()
    return replies


async def _auto_until_wait(
    current: dict[str, Any] | None,
    ctx: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    extra: list[str] = []
    nodes: dict[str, dict[str, Any]] = ctx["nodes"]
    edges: list[dict[str, Any]] = ctx["edges"]
    steps = 0
    while current and steps < MAX_STEPS:
        steps += 1
        ntype = current.get("type") or ""
        last_result: dict[str, Any] = {}
        if ntype in WAIT_TYPES and ntype != "schedule_fulfillment":
            break
        sent, last_result = await _run_node(
            db=ctx["db"],
            node=current,
            phone=ctx["phone"],
            name=ctx["name"],
            inbound=ctx["inbound"],
            vars_=ctx["vars"],
            graph=ctx["graph"],
            edges=edges,
            conv=ctx["conv"],
        )
        extra.extend(sent)
        if ntype == "handoff":
            break
        if ntype == "schedule_fulfillment" and last_result.get("waiting", True):
            break
        nxt = _pick_edge(edges, current["id"], ctx["inbound"], last_result, mode="auto")
        if not nxt or nxt.get("to") not in nodes:
            break
        current = nodes[nxt["to"]]
    return current, extra


async def _run_node(
    db: AsyncSession,
    *,
    node: dict[str, Any],
    phone: str,
    name: str,
    inbound: dict[str, Any],
    vars_: dict[str, Any],
    graph: BotFlow,
    edges: list[dict[str, Any]],
    conv: Conversation,
) -> tuple[list[str], dict[str, Any]]:
    ntype = node.get("type") or ""
    config = node.get("config") or {}
    result: dict[str, Any] = {}
    out: list[str] = []

    if ntype in {"start", "end"}:
        return out, result

    if ntype == "message":
        text = _render(str(config.get("text") or ""), vars_)
        if text:
            out.append(text)
        return out, result

    if ntype == "catalog":
        extra = await wa_catalog.present_catalog(db, phone, config)
        out.extend(extra)
        return out, result

    if ntype == "capture":
        var = str(config.get("var") or "value")
        if config.get("value") not in (None, ""):
            vars_[var] = str(config.get("value"))
        else:
            vars_[var] = inbound.get("text") or vars_.get("last_text") or ""
        return out, result

    if ntype == "schedule_fulfillment":
        extra = await deliveries.begin_preorder(db, phone, config, vars_)
        out.extend(extra)
        state = await db.get(ConversationState, phone)
        data = dict((state.data or {}) if state else {})
        if state:
            vars_.update(dict(data.get("vars") or {}))
            data["schedule_node_id"] = node.get("id")
            state.data = data
        waiting = bool(data.get("preorder")) and not data.get("schedule_done")
        result["waiting"] = waiting
        return out, result

    if ntype == "match_product":
        product = await wa_catalog.match_product(db, inbound.get("text") or "")
        if product:
            free = await inventory.available_stock(db, product.id)
            vars_["product_id"] = product.id
            vars_["product_name"] = product.name
            vars_["product_price"] = f"{product.price:.2f}"
            vars_["stock"] = str(free)
            result["match"] = "found"
        else:
            result["match"] = "not_found"
        return out, result

    if ntype == "create_order":
        qty_raw = wa_catalog.choice_value(inbound.get("text") or str(vars_.get("qty") or ""))
        if qty_raw.isdigit():
            vars_["qty"] = int(qty_raw)
        qty = int(vars_.get("qty") or 0)
        product = await db.get(Product, vars_.get("product_id") or "")
        if not product or qty < 1:
            out.append("Indicá la cantidad con un número (ej: 2) o escribí CATALOGO.")
            result["match"] = "not_found"
            return out, result
        state = await db.get(ConversationState, phone)
        session_id = state.live_session_id if state else None
        mode = str(vars_.get("fulfillment_mode") or vars_.get("delivery_mode") or "delivery")
        shipping = Decimal("0")
        if mode == "shipping":
            shipping = _money(config.get("shipping_interior"))
        elif mode == "delivery":
            shipping = _money(config.get("shipping_local"))
        product_total = Decimal(product.price) * qty
        percent = _money(config.get("deposit_percent") or 100)
        if percent <= 0 or percent > 100:
            percent = Decimal("100")
        deposit = (product_total * percent / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        pay_amount = (deposit + shipping).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        remaining = (product_total - deposit).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        vars_["shipping_fee"] = f"{shipping:.2f}"
        vars_["deposit"] = f"{deposit:.2f}"
        vars_["pay_amount"] = f"{pay_amount:.2f}"
        vars_["remaining"] = f"{remaining:.2f}"
        vars_["product_total"] = f"{product_total:.2f}"
        notes = (
            f"Adelanto {percent:.0f}% + envío. Producto {product_total:.2f} · "
            f"envío {shipping:.2f} · ahora {pay_amount:.2f} · saldo {remaining:.2f}"
        )
        try:
            order = await orders.create_whatsapp_order(
                db,
                phone=phone,
                name=name,
                product=product,
                quantity=qty,
                session_id=session_id,
                pay_amount=pay_amount,
                shipping_fee=shipping,
                notes=notes,
                delivery_type=mode,
                delivery_address=str(vars_.get("address") or ""),
            )
        except StockError as exc:
            out.append(str(exc))
            return out, result
        vars_["order_code"] = order.public_code
        vars_["total"] = f"{order.total_amount:.2f}"
        payment = order.payments[-1] if order.payments else None
        pay_extra = (payment.qr_payload if payment and payment.qr_payload else "Pagá y enviá el comprobante por este chat.")
        caption = (
            f"Pedido {order.public_code}\n"
            f"{qty} x {product.name} = {product_total:.2f} {settings.currency}\n"
            f"Envío ({deliveries.MODE_LABEL.get(mode, mode)}): {shipping:.2f} {settings.currency}\n"
            f"Adelanto {percent:.0f}% + envío a pagar ahora: {pay_amount:.2f} {settings.currency}\n"
            f"Saldo: {remaining:.2f} {settings.currency}\n"
            f"Reserva por {settings.reservation_minutes} min.\n"
            f"{pay_extra}"
        )
        if payment and payment.qr_image_path:
            sent = await whatsapp.send_image(phone, Path(payment.qr_image_path), caption)
            await inbox.record_bot_image(db, phone, Path(payment.qr_image_path), caption, sent)
        else:
            out.append(caption)
        loaded = await orders.load_order(db, order.id)
        if loaded:
            await deliveries.apply_preorder_to_order(db, loaded, vars_)
            skip = str(config.get("skip_schedule") or "").lower() in {"1", "true", "yes"} or bool(
                vars_.get("window_label")
            )
            if not skip:
                out.extend(await deliveries.begin_schedule(db, loaded))
        return out, result

    if ntype == "attach_proof":
        media_id = inbound.get("image_media_id")
        customer = await orders.get_or_create_customer(db, phone, name)
        order = await orders.latest_open_order(db, customer.id)
        if not order:
            out.append("No hay un pedido pendiente de pago. Escribí CATALOGO para armar uno.")
            return out, result
        vars_["order_code"] = order.public_code
        if not media_id:
            out.append(f"Enviá la foto del comprobante para {order.public_code}.")
            return out, result
        dest = PROOFS_DIR / f"{order.public_code}.jpg"
        try:
            if str(media_id).startswith("sim-"):
                from app.services.payments import generate_qr_png

                _, sample = generate_qr_png(f"SIM-{order.public_code}", Decimal("1"))
                dest.write_bytes(Path(sample).read_bytes())
            else:
                await whatsapp.download_media(media_id, dest)
        except Exception:
            dest.write_bytes(b"")
            out.append(
                "Recibí el comprobante, pero no pude descargar la imagen. "
                "El vendedor lo revisará igual si reenviás o lo confirmamos a mano."
            )
            return out, result
        await orders.attach_proof(db, order, dest)
        loaded = await orders.load_order(db, order.id) or order
        vars_["confirm_detail"] = deliveries.confirm_after_proof(loaded)
        if loaded.delivery:
            vars_["window_label"] = loaded.delivery.window_label
            vars_["address"] = loaded.delivery.address
            vars_["fulfillment_mode"] = loaded.delivery.mode
        pay = loaded.payments[-1] if loaded.payments else None
        if pay:
            vars_["pay_amount"] = f"{pay.amount:.2f}"
        return out, result

    if ntype == "order_status":
        customer = await orders.get_or_create_customer(db, phone, name)
        order = await orders.latest_open_order(db, customer.id)
        if not order:
            out.append("No tenés pedidos abiertos.")
        else:
            out.append(
                f"{order.public_code} está {order.status}. "
                f"Total {order.total_amount:.2f} {settings.currency}."
            )
        return out, result

    if ntype == "cancel_order":
        customer = await orders.get_or_create_customer(db, phone, name)
        order = await orders.latest_open_order(db, customer.id)
        if not order:
            out.append("No hay pedido para cancelar.")
        else:
            await orders.cancel_order(db, order)
            out.append(f"Cancelé {order.public_code}.")
        vars_.pop("product_id", None)
        vars_.pop("qty", None)
        return out, result

    if ntype == "handoff":
        conv.bot_paused = True
        text = _render(str(config.get("text") or "Te paso con una persona del equipo."), vars_)
        out.append(text)
        return out, result

    if ntype == "ai_reply":
        knowledge = await _catalog_knowledge(db)
        transitions = _ai_transitions(edges, node["id"])
        decision = await motor_client.decide(
            conversation_id=phone,
            user_message=inbound.get("text") or "hola",
            knowledge=knowledge,
            transitions=transitions,
            node={"type": "ai_reply", "name": node.get("name"), "config": config},
            lead_context={"vars": {k: v for k, v in vars_.items() if k != "last_text"}},
        )
        if decision:
            reply = (decision.get("reply_text") or "").strip()
            if reply:
                out.append(reply)
            chosen = decision.get("chosen_transition") or "default"
            result["transition"] = chosen
            if decision.get("needs_human"):
                result["transition"] = "human" if "human" in transitions else chosen
        else:
            extra = await wa_catalog.present_catalog(db, phone)
            out.extend(extra)
            result["transition"] = "default"
        return out, result

    return out, result


def _money(value, default: str = "0") -> Decimal:
    try:
        raw = default if value in (None, "") else value
        return Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal(default).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pick_edge(
    edges: list[dict[str, Any]],
    from_id: str,
    inbound: dict[str, Any],
    last_result: dict[str, Any],
    mode: str,
) -> dict[str, Any] | None:
    outgoing = [e for e in edges if e.get("from") == from_id]
    if not outgoing:
        return None
    text = inbound.get("text") or ""
    if mode == "wait":
        if inbound.get("is_image"):
            hit = _first(outgoing, "is_image")
            if hit:
                return hit
        if text:
            for edge in outgoing:
                if edge.get("trigger_type") == "keyword" and _keyword_hit(
                    edge.get("trigger_key") or "", text
                ):
                    return edge
            for edge in outgoing:
                if edge.get("trigger_type") == "regex":
                    pattern = edge.get("trigger_key") or ""
                    try:
                        if pattern and re.search(pattern, text, re.IGNORECASE):
                            return edge
                    except re.error:
                        continue
        if text.isdigit():
            hit = _first(outgoing, "is_digit")
            if hit:
                return hit
        return _first(outgoing, "default") or _first(outgoing, "always")
    if last_result.get("match") == "found":
        return _first(outgoing, "found") or _first(outgoing, "always")
    if last_result.get("match") == "not_found":
        return _first(outgoing, "not_found") or _first(outgoing, "default")
    if last_result.get("transition"):
        key = last_result["transition"]
        for edge in outgoing:
            if edge.get("trigger_type") == "transition" and (edge.get("trigger_key") or "") == key:
                return edge
        return _first(outgoing, "default") or _first(outgoing, "always")
    return _first(outgoing, "always") or _first(outgoing, "default") or outgoing[0]


def _first(outgoing: list[dict[str, Any]], trigger: str) -> dict[str, Any] | None:
    for edge in outgoing:
        if (edge.get("trigger_type") or "always") == trigger:
            return edge
    return None


def _looks_like_product(text: str) -> bool:
    kind, _ = wa_catalog.choice_kind(text)
    return kind in {"sku", "prod"}


def _fold(text: str) -> str:
    table = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return (text or "").translate(table).lower().strip()


def _keyword_hit(key: str, text: str) -> bool:
    lowered = _fold(text)
    parts = [_fold(p) for p in key.split(",") if p.strip()]
    for part in parts:
        if not part:
            continue
        if part.isdigit() or len(part) <= 1:
            if lowered == part:
                return True
            continue
        if part == lowered or part in lowered:
            return True
    return False


def _start_node(nodes: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for node in nodes.values():
        if node.get("type") == "start":
            return node
    return next(iter(nodes.values()), None)


def _ai_transitions(edges: list[dict[str, Any]], node_id: str) -> list[str]:
    keys: list[str] = []
    for edge in edges:
        if edge.get("from") != node_id:
            continue
        if edge.get("trigger_type") == "transition":
            key = (edge.get("trigger_key") or "default").strip()
            if key and key not in keys:
                keys.append(key)
    if "default" not in keys:
        keys.append("default")
    return keys


def _render(template: str, vars_: dict[str, Any]) -> str:
    text = template
    for key, value in vars_.items():
        text = text.replace("{{" + str(key) + "}}", str(value))
    return text


async def _catalog_knowledge(db: AsyncSession) -> list[dict[str, Any]]:
    products = await inventory.list_catalog(db)
    items: list[dict[str, Any]] = []
    for product in products:
        free = await inventory.available_stock(db, product.id)
        items.append(
            {
                "title": product.name,
                "content": (
                    f"{product.name} cuesta {product.price:.2f} {settings.currency}. "
                    f"Stock {free} unidades. SKU {product.sku}."
                ),
                "tags": ["catalogo", "precios"],
            }
        )
    return items


async def _get_state(db: AsyncSession, phone: str) -> ConversationState:
    row = await db.get(ConversationState, phone)
    if not row:
        row = ConversationState(phone=phone, step="flow", data={})
        db.add(row)
        await db.flush()
    row.updated_at = utcnow()
    return row


async def _persist(state: ConversationState, flow_id: str, node_id: str | None, vars_: dict[str, Any]) -> None:
    data = dict(state.data or {})
    data["flow_id"] = flow_id
    data["vars"] = vars_
    if state.step in deliveries.SCHEDULE_STEPS and data.get("schedule_node_id"):
        data["node_id"] = data["schedule_node_id"]
    else:
        data["node_id"] = node_id
    if state.step not in deliveries.SCHEDULE_STEPS:
        state.step = "flow"
        data["preorder"] = False
    state.data = data
