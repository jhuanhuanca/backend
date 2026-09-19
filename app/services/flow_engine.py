from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROOFS_DIR, get_settings
from app.models import BotFlow, Conversation, ConversationState, Product, utcnow
from app.services import calls, deliveries, inbox, inventory, live, motor_client, orders, tenancy, visual_catalog, visual_client, wa_catalog, whatsapp
from app.services.flow_definition import WAIT_TYPES
from app.services.whatsapp import CloudError

settings = get_settings()
LIVE_RE = re.compile(r"\bLV-[A-Z0-9]{4}\b", re.IGNORECASE)
MAX_STEPS = 24
CLOUD_FIRST = {
    "buttons",
    "catalog",
    "send_image",
    "send_audio",
    "send_video",
    "schedule_fulfillment",
    "schedule_call",
    "create_order",
}
MAX_BUTTONS = 10


async def published_flow(db: AsyncSession) -> BotFlow | None:
    cid = tenancy.current_company_id()
    if not cid:
        return None
    query = (
        select(BotFlow)
        .where(BotFlow.status == "published", BotFlow.company_id == cid)
        .order_by(BotFlow.is_default.desc(), BotFlow.updated_at.desc())
    )
    return await db.scalar(query)


async def handle_incoming(
    db: AsyncSession,
    *,
    phone: str,
    name: str,
    text: str | None,
    image_media_id: str | None = None,
    image_path: str | None = None,
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
                if state.live_session_id != session.id:
                    replies.append(
                        f"Quedaste vinculado al live {session.public_code}"
                        + (f" (@{session.tiktok_username})" if session.tiktok_username else "")
                        + "."
                    )
                state.live_session_id = session.id

    vars_ = dict(state.data or {}).get("vars") or {}
    node_id = dict(state.data or {}).get("node_id")
    vars_["name"] = name

    nodes = {n["id"]: n for n in (graph.definition or {}).get("nodes") or [] if n.get("id")}
    edges = (graph.definition or {}).get("edges") or []
    if not nodes:
        await db.commit()
        return replies

    parked_now = nodes.get(node_id) if node_id else None
    data_now = dict(state.data or {})
    in_schedule = bool(
        (state.step or "") in deliveries.SCHEDULE_STEPS or data_now.get("preorder")
    )
    in_call = bool(
        parked_now
        and parked_now.get("type") == "schedule_call"
        and not data_now.get("call_done")
    )
    skip_schedule_intercept = bool(
        parked_now and parked_now.get("type") == "buttons" and not in_schedule and not in_call
    )
    if text and not image_media_id and in_call and not in_schedule:
        booked = await calls.handle_incoming(db, phone=phone, name=name, text=text)
        if booked is not None:
            replies.extend(booked)
            state = await tenancy.get_or_create_state(db, phone)
            if not (state.data or {}).get("call_done"):
                await db.commit()
                return replies
            inbound_text = ""
            schedule_advance = True
        else:
            inbound_text = (text or "").strip()
            schedule_advance = False
    elif text and not image_media_id and not skip_schedule_intercept:
        scheduled = await deliveries.handle_incoming(db, phone=phone, name=name, text=text)
        if scheduled is not None:
            replies.extend(scheduled)
            state = await tenancy.get_or_create_state(db, phone)
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

    current = nodes.get(node_id) if node_id else None
    inbound = {
        "text": inbound_text,
        "image_media_id": None if schedule_advance else image_media_id,
        "is_image": bool(image_media_id) and not schedule_advance,
        "schedule_advance": schedule_advance,
        "image_path": str(image_path or ""),
    }
    vars_["last_text"] = inbound["text"]
    vars_["currency"] = settings.currency
    if inbound["is_image"] and not inbound["image_path"]:
        photo = await inbox.latest_inbound_image(db, phone)
        inbound["image_path"] = str(photo) if photo else ""
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
        await _flush_texts(ctx, replies)
        current, extra = await _auto_until_wait(current, ctx, already_parked=was_waiting)
        replies.extend(extra)
        has_inbound = bool(inbound["text"] or inbound["is_image"])
        parked = current is not None and current.get("type") in WAIT_TYPES
        if parked and has_inbound:
            chosen = None
            if current.get("type") == "buttons":
                chosen = _resolve_button(current.get("config") or {}, inbound.get("text") or "")
                if chosen:
                    inbound["text"] = chosen["title"]
                    vars_["choice"] = chosen["title"]
                    vars_["choice_id"] = chosen["id"]
                    stored = str((current.get("config") or {}).get("var") or "").strip()
                    if stored:
                        vars_[stored] = chosen["title"]
            nxt = _pick_edge(edges, current["id"], inbound, {}, mode="wait")
            if not nxt and current.get("type") == "buttons":
                dest = str((chosen or {}).get("to") or "")
                if dest in nodes:
                    nxt = {"to": dest, "trigger_type": "keyword"}
            # Primer turno (salimos de Inicio y caímos en Esperar): el texto del wa.me
            # tiene que disparar "catálogo"/"hola". No uses default: eso interpretaría
            # "vengo del live" como producto.
            if not was_waiting and nxt and (nxt.get("trigger_type") or "always") in {
                "always",
                "default",
            }:
                nxt = None
            if (
                current.get("type") not in {"wait_payment", "buttons"}
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


async def _flush_texts(ctx: dict[str, Any], extra: list[str]) -> None:
    pending = [str(text).strip() for text in extra if str(text).strip()]
    extra.clear()
    if not pending:
        return
    db = ctx["db"]
    phone = ctx["phone"]
    for text in pending:
        try:
            sent = await whatsapp.send_text(phone, text, db=db)
        except CloudError:
            extra.append(text)
            continue
        await inbox.record_bot_text(db, phone, text, sent)


async def _auto_until_wait(
    current: dict[str, Any] | None,
    ctx: dict[str, Any],
    *,
    already_parked: bool = False,
) -> tuple[dict[str, Any] | None, list[str]]:
    extra: list[str] = []
    nodes: dict[str, dict[str, Any]] = ctx["nodes"]
    edges: list[dict[str, Any]] = ctx["edges"]
    steps = 0
    while current and steps < MAX_STEPS:
        steps += 1
        ntype = current.get("type") or ""
        last_result: dict[str, Any] = {}
        if ntype in WAIT_TYPES:
            if ntype in {"schedule_fulfillment", "schedule_call"}:
                pass
            elif ntype == "buttons" and not already_parked:
                pass
            else:
                break
        if ntype in CLOUD_FIRST:
            await _flush_texts(ctx, extra)
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
        if ntype in {"schedule_fulfillment", "schedule_call", "buttons"} and last_result.get("waiting", True):
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

    if ntype in {"send_image", "send_audio", "send_video"}:
        kind = {"send_image": "image", "send_audio": "audio", "send_video": "video"}[ntype]
        extra = await _present_media(db, phone, config, vars_, kind)
        out.extend(extra)
        return out, result

    if ntype == "catalog":
        extra = await wa_catalog.present_catalog(db, phone, config)
        out.extend(extra)
        return out, result

    if ntype == "buttons":
        items = _button_items(config)
        question = _render(str(config.get("text") or "Elegí una opción:"), vars_)
        extra = await wa_catalog.present_choices(
            db,
            phone,
            text=question,
            items=items,
            button=str(config.get("button") or "Ver opciones")[:20],
            preview="Opciones",
        )
        out.extend(extra)
        result["waiting"] = True
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
        state = await tenancy.get_or_create_state(db, phone)
        data = dict((state.data or {}) if state else {})
        if state:
            vars_.update(dict(data.get("vars") or {}))
            data["schedule_node_id"] = node.get("id")
            state.data = data
        waiting = bool(data.get("preorder")) and not data.get("schedule_done")
        result["waiting"] = waiting
        return out, result

    if ntype == "schedule_call":
        extra = await calls.begin_call(db, phone, config, vars_, node_id=str(node.get("id") or ""))
        out.extend(extra)
        state = await tenancy.get_or_create_state(db, phone)
        data = dict((state.data or {}) if state else {})
        if state:
            vars_.update(dict(data.get("vars") or {}))
            data["schedule_node_id"] = node.get("id")
            state.data = data
        result["waiting"] = bool(data.get("call_booking")) and not data.get("call_done")
        return out, result

    if ntype == "match_product":
        product = await _match_listed_or_text(db, inbound.get("text") or "", vars_)
        if product:
            await _bind_product(db, vars_, product)
            result["match"] = "found"
        else:
            result["match"] = "not_found"
        return out, result

    if ntype == "match_image":
        sent, match = await _run_visual_match(db, phone, inbound, vars_, config)
        out.extend(sent)
        result["match"] = match
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
        state = await tenancy.get_or_create_state(db, phone)
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
                pay_override=config,
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


async def _bind_product(db: AsyncSession, vars_: dict[str, Any], product: Product) -> None:
    free = await inventory.available_stock(db, product.id)
    vars_["product_id"] = product.id
    vars_["product_name"] = product.name
    vars_["product_price"] = f"{product.price:.2f}"
    vars_["stock"] = str(free)
    vars_.pop("visual_choices", None)


async def _match_listed_or_text(
    db: AsyncSession, body: str, vars_: dict[str, Any]
) -> Product | None:
    text = wa_catalog.choice_value(body)
    choices = vars_.get("visual_choices") or []
    if text.isdigit() and isinstance(choices, list) and choices:
        idx = int(text) - 1
        if 0 <= idx < len(choices):
            product = await db.get(Product, str(choices[idx]))
            if product:
                return product
    return await wa_catalog.match_product(db, body)


async def _run_visual_match(
    db: AsyncSession,
    phone: str,
    inbound: dict[str, Any],
    vars_: dict[str, Any],
    config: dict[str, Any],
) -> tuple[list[str], str]:
    path = Path(str(inbound.get("image_path") or ""))
    if not path.is_file():
        photo = await inbox.latest_inbound_image(db, phone)
        path = photo or Path()
    if not path.is_file():
        return ["Mandame una foto o captura del producto (del live o del catálogo)."], "not_found"
    cid = await tenancy.resolve_company_id(db)
    if not cid:
        return ["No hay empresa activa para comparar el catálogo."], "not_found"
    threshold = float(config.get("threshold") or settings.motor_visual_threshold)
    unsure_cut = float(config.get("unsure") or settings.motor_visual_unsure)

    async def _query():
        return await visual_client.match_image(
            cid,
            str(path),
            top_k=int(config.get("top_k") or 3),
            threshold=threshold,
            unsure=unsure_cut,
        )

    hit = await _query()
    if hit is None or (hit.get("estado") in {"no_encontrado", "error"} and not hit.get("alternativas")):
        await visual_catalog.reindex_current(db)
        hit = await _query()
    if not hit or hit.get("estado") in {None, "error"}:
        return [
            "No pude comparar la foto ahora. Escribí el nombre o el número del catálogo."
        ], "not_found"
    estado = hit.get("estado")
    if estado == "match":
        raw = hit.get("producto") or {}
        product = await db.get(Product, str(raw.get("product_id") or ""))
        if not product:
            return ["Encontré algo parecido, pero ya no está en el inventario."], "not_found"
        await _bind_product(db, vars_, product)
        score = raw.get("score")
        tmpl = str(
            config.get("confirm_text")
            or "Encontré: {{product_name}} — {{product_price}} {{currency}}.\n¿Es este? Si sí, decime la cantidad."
        )
        text = _render(tmpl, vars_)
        if score is not None:
            text = f"{text}\n(coincidencia {float(score) * 100:.0f}%)"
        extra: list[str] = []
        if (product.image_url or "").strip():
            await wa_catalog._send_catalog_image(db, phone, product.image_url, text)
        else:
            extra.append(text)
        return extra, "found"
    if estado == "dudoso":
        alts = hit.get("alternativas") or []
        products: list[Product] = []
        seen: set[str] = set()
        for item in alts:
            pid = str(item.get("product_id") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            row = await db.get(Product, pid)
            if row:
                products.append(row)
        if not products:
            return ["No lo ubiqué. Escribí el nombre o tocá el catálogo."], "not_found"
        vars_["visual_choices"] = [p.id for p in products]
        lines = ["¿Cuál de estos es? Escribí el número:"]
        items = []
        for idx, product in enumerate(products, start=1):
            lines.append(f"{idx}. {product.name} — {product.price:.2f} {settings.currency}")
            items.append(
                {
                    "id": f"prod:{product.id}",
                    "title": f"{idx}. {product.name}"[:24],
                    "description": f"{product.price:.2f} {settings.currency}",
                }
            )
        extra = await wa_catalog.present_choices(
            db,
            phone,
            text="\n".join(lines),
            items=items[:10],
            button="Ver opciones",
            preview="¿Cuál es?",
        )
        return extra, "unsure"
    return ["No lo ubiqué. Mandame otra foto o escribí el nombre."], "not_found"


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
    if last_result.get("match") == "unsure":
        return _first(outgoing, "unsure") or _first(outgoing, "not_found") or _first(outgoing, "default")
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


def _button_items(config: dict[str, Any]) -> list[dict[str, Any]]:
    titles: list[str] = [""] * MAX_BUTTONS
    dests: list[str] = [""] * MAX_BUTTONS
    raw = config.get("buttons")
    if isinstance(raw, list):
        for idx, item in enumerate(raw[:MAX_BUTTONS]):
            if isinstance(item, dict):
                titles[idx] = str(item.get("title") or "").strip()
                dests[idx] = str(item.get("to") or "").strip()
            else:
                titles[idx] = str(item or "").strip()
    for idx in range(MAX_BUTTONS):
        extra = str(config.get(f"button{idx + 1}") or "").strip()
        if extra:
            titles[idx] = extra
    filled = [title for title in titles if title]
    limit = 20 if len(filled) <= 3 else 24
    items: list[dict[str, Any]] = []
    for idx, title in enumerate(titles, start=1):
        if not title:
            continue
        desc = ""
        if isinstance(raw, list) and idx - 1 < len(raw) and isinstance(raw[idx - 1], dict):
            desc = str(raw[idx - 1].get("description") or "")[:72]
        items.append(
            {
                "id": f"opt{idx}",
                "title": title[:limit],
                "description": desc,
                "to": dests[idx - 1],
            }
        )
    return items


def _resolve_button(config: dict[str, Any], text: str) -> dict[str, Any] | None:
    items = _button_items(config)
    if not items:
        return None
    folded = _fold(text)
    kind, rest = wa_catalog.choice_kind(text)
    token = _fold(rest if kind else text)
    if folded.isdigit():
        pos = int(folded) - 1
        if 0 <= pos < len(items):
            return items[pos]
    for item in items:
        title = _fold(item["title"])
        ident = _fold(item["id"])
        if folded in {title, ident} or token in {title, ident}:
            return item
        if title and title in folded:
            return item
    return None


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


async def _present_media(
    db: AsyncSession,
    phone: str,
    config: dict[str, Any],
    vars_: dict[str, Any],
    kind: str,
) -> list[str]:
    url = str(config.get("url") or "").strip()
    caption = _render(str(config.get("caption") or ""), vars_)
    voice = bool(config.get("voice"))
    local = wa_catalog.local_upload_path(url)
    public = wa_catalog.public_media_url(url)
    labels = {"image": "foto", "audio": "audio", "video": "video"}
    label = labels.get(kind, "archivo")
    try:
        if local:
            mime = whatsapp.mime_for_path(local)
            send_path = local
            if kind == "audio":
                send_path, mime, voice = inbox.prepare_outgoing_audio(local, mime, voice)
            sent = await whatsapp.send_media(
                phone,
                send_path,
                kind=kind,
                caption=caption,
                mime=mime,
                voice=voice,
                db=db,
            )
            await inbox.record_bot_media(
                db,
                phone,
                msg_type=kind,
                caption=caption,
                path=send_path,
                mime=mime,
                send_result=sent,
            )
            return []
        if public:
            sent = await whatsapp.send_media_link(
                phone, public, kind=kind, caption=caption, db=db
            )
            await inbox.record_bot_media(
                db,
                phone,
                msg_type=kind,
                caption=caption,
                path=public,
                send_result=sent,
            )
            return []
    except CloudError:
        pass
    if caption:
        return [caption]
    if url:
        return [f"[{label}]"]
    return [f"Falta el archivo de {label} en este paso."]


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
    return await tenancy.get_or_create_state(db, phone)


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
