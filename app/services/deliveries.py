from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    LA_PAZ = ZoneInfo("America/La_Paz")
except Exception:  # Windows sin tzdata
    LA_PAZ = timezone(timedelta(hours=-4))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Appointment, Conversation, ConversationState, Customer, Delivery, Order, utcnow
from app.services import inbox, tenancy, wa_catalog, whatsapp

SLOT_HOURS = (10, 14, 18)
SLOT_LABEL = {
    10: "Mañana 10:00–12:00",
    14: "Tarde 14:00–16:00",
    18: "Noche 18:00–20:00",
}
DAY_NAMES = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)
DELIVERY_STEPS = {
    "await_delivery_mode",
    "await_delivery_day",
    "await_delivery_window",
    "await_delivery_address",
    "await_delivery_when",
}
PREORDER_STEPS = {
    "await_fulfillment_mode",
    "await_delivery_day",
    "await_delivery_window",
    "await_delivery_address",
    "await_meeting_kind",
    "await_lead_city",
}
SCHEDULE_STEPS = DELIVERY_STEPS | PREORDER_STEPS
PAID_STATUSES = {"pagado", "listo_entrega", "en_transito"}
SCHEDULABLE_STATUSES = {"pendiente_pago", "pagado", "listo_entrega", "en_transito"}
OPEN_DELIVERY = {"pending", "confirmed", "in_transit"}
SCHEDULE_WORDS = (
    "entrega",
    "programada",
    "coordinar",
    "reparto",
    "envío",
    "envio",
    "envios",
    "envíos",
    "reunión",
    "reunion",
    "cita",
    "encuentro",
    "horario",
    "días",
    "dias",
)

MODE_LABEL = {
    "delivery": "Envío a domicilio",
    "shipping": "Envío a otra ciudad",
    "meeting": "Reunión / encuentro",
    "scheduled": "Programada",
    "to_coordinate": "A coordinar",
}
STATUS_LABEL = {
    "pendiente_pago": "Pendiente de pago",
    "pagado": "Pagado",
    "listo_entrega": "Listo para entrega",
    "en_transito": "En camino",
    "entregado": "Entregado",
    "cancelado": "Cancelado",
}
PURPOSE_LABEL = {
    "lead": "Inscripción / presentación",
    "order": "Reunión de venta",
    "call": "Llamada",
}
KIND_LABEL = {
    "presencial": "Presencial",
    "virtual": "Virtual",
    "llamada": "Llamada",
}
APPT_STATUS_LABEL = {
    "scheduled": "Programada",
    "done": "Hecha",
    "cancelled": "Cancelada",
}


@dataclass
class Window:
    key: str
    label: str
    start: datetime


def upcoming_windows(now: datetime | None = None, days: int = 7) -> list[Window]:
    local = (now or datetime.now(LA_PAZ)).astimezone(LA_PAZ)
    cutoff = local + timedelta(minutes=30)
    out: list[Window] = []
    idx = 1
    for day in range(days):
        day_date = (local + timedelta(days=day)).date()
        day_name = _day_label(local, day, day_date)
        for hour in SLOT_HOURS:
            start = datetime(day_date.year, day_date.month, day_date.day, hour, 0, tzinfo=LA_PAZ)
            if start < cutoff:
                continue
            label = f"{day_name} · {SLOT_LABEL[hour]}"
            out.append(Window(key=str(idx), label=label, start=start.replace(tzinfo=None)))
            idx += 1
    return out


def upcoming_days(now: datetime | None = None, days: int = 7) -> list[dict]:
    local = (now or datetime.now(LA_PAZ)).astimezone(LA_PAZ)
    items: list[dict] = []
    for offset in range(days):
        day_date = (local + timedelta(days=offset)).date()
        items.append(
            {
                "id": f"day:{offset}",
                "key": str(offset),
                "title": _day_label(local, offset, day_date),
                "description": day_date.strftime("%d/%m"),
                "date": day_date.isoformat(),
            }
        )
    return items


def slots_for_date(date_iso: str, now: datetime | None = None) -> list[dict]:
    local = (now or datetime.now(LA_PAZ)).astimezone(LA_PAZ)
    parts = (date_iso or "").split("-")
    if len(parts) < 3:
        return []
    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    cutoff = local + timedelta(minutes=30)
    items: list[dict] = []
    for hour in SLOT_HOURS:
        start = datetime(year, month, day, hour, 0, tzinfo=LA_PAZ)
        if start < cutoff:
            continue
        items.append(
            {
                "id": f"slot:{hour}",
                "title": SLOT_LABEL[hour],
                "description": f"{hour:02d}:00",
                "hour": hour,
                "start": start.replace(tzinfo=None).isoformat(),
            }
        )
    return items


def _day_label(local: datetime, offset: int, day_date) -> str:
    name = DAY_NAMES[day_date.weekday()]
    if offset == 0:
        prefix = "Hoy"
    elif offset == 1:
        prefix = "Mañana"
    else:
        prefix = name.capitalize()
    return f"{prefix} {day_date.strftime('%d/%m')}"


def windows_text(windows: list[Window]) -> str:
    lines = ["Horarios disponibles:"]
    for window in windows:
        lines.append(f"{window.key}. {window.label}")
    lines.append("Respondé con el número del horario.")
    return "\n".join(lines)


def mode_prompt(order_code: str) -> str:
    return (
        f"Pedido {order_code}. ¿Cómo lo recibís?\n"
        "1. Envío a domicilio\n"
        "2. Envío a otra ciudad\n"
        "3. Reunión / punto de encuentro\n"
        "4. Coordinar después"
    )


def mode_items() -> list[dict]:
    return [
        {"id": "mode:delivery", "title": "Envío a domicilio", "description": "Te lo llevamos en La Paz / El Alto"},
        {"id": "mode:shipping", "title": "Envío a otra ciudad", "description": "Por flota o encomienda"},
        {"id": "mode:meeting", "title": "Reunión / encuentro", "description": "Nos vemos en un lugar"},
        {"id": "mode:coordinate", "title": "Coordinar después", "description": "Lo vemos más adelante"},
    ]


def address_prompt(mode: str) -> str:
    if mode == "shipping":
        return "¿A qué ciudad y dirección lo enviamos? (ej: Cochabamba, Av. Heroínas 123)"
    if mode == "meeting":
        return "¿Dónde nos vemos? Decime zona, plaza o tienda."
    return "¿A qué dirección o zona te lo llevamos?"


async def get_or_create_state(db: AsyncSession, phone: str) -> ConversationState:
    return await tenancy.get_or_create_state(db, phone)


async def get_or_create_delivery(db: AsyncSession, order: Order) -> Delivery:
    if order.delivery:
        return order.delivery
    row = await db.scalar(select(Delivery).where(Delivery.order_id == order.id))
    if row:
        return row
    row = Delivery(order_id=order.id, mode=order.delivery_type or "to_coordinate")
    db.add(row)
    await db.flush()
    order.delivery = row
    return row


async def order_awaiting_delivery(db: AsyncSession, customer_id: str) -> Order | None:
    return await db.scalar(
        select(Order)
        .where(Order.customer_id == customer_id, Order.status.in_(PAID_STATUSES))
        .options(selectinload(Order.delivery), selectinload(Order.customer))
        .order_by(Order.created_at.desc())
    )


async def order_for_schedule(db: AsyncSession, customer_id: str) -> Order | None:
    return await db.scalar(
        select(Order)
        .where(Order.customer_id == customer_id, Order.status.in_(SCHEDULABLE_STATUSES))
        .options(selectinload(Order.delivery), selectinload(Order.customer))
        .order_by(Order.created_at.desc())
    )


def delivery_incomplete(order: Order) -> bool:
    if order.status not in SCHEDULABLE_STATUSES:
        return False
    delivery = order.delivery
    if not delivery:
        return True
    if delivery.status == "delivered":
        return False
    if delivery.mode in {"delivery", "scheduled", "meeting"}:
        return not (delivery.window_label and delivery.address)
    if delivery.mode == "shipping":
        return not (delivery.window_label and delivery.address)
    return not (delivery.address or delivery.notes or delivery.window_label)


async def begin_schedule(db: AsyncSession, order: Order) -> list[str]:
    customer = order.customer
    if not customer:
        return []
    await get_or_create_delivery(db, order)
    state = await get_or_create_state(db, customer.phone)
    data = dict(state.data or {})
    data["delivery_order_id"] = order.id
    state.data = data
    state.step = "await_delivery_mode"
    extra = await wa_catalog.present_choices(
        db,
        customer.phone,
        text=mode_prompt(order.public_code),
        items=mode_items(),
        button="Elegir",
        preview="Agendar entrega",
    )
    return extra


async def start_scheduling(db: AsyncSession, order: Order) -> list[str]:
    if not delivery_incomplete(order):
        return []
    return await begin_schedule(db, order)


async def handle_incoming(
    db: AsyncSession, *, phone: str, name: str, text: str | None
) -> list[str] | None:
    body = (text or "").strip()
    if not body:
        return None
    state = await get_or_create_state(db, phone)
    from app.services import orders as order_svc

    customer = await order_svc.get_or_create_customer(db, phone, name)
    in_flow = state.step in DELIVERY_STEPS
    data = dict(state.data or {})
    kind, _rest = wa_catalog.choice_kind(body)
    scheduling = bool(data.get("preorder") or state.step in PREORDER_STEPS)
    if kind in {"day", "slot"} and (data.get("days") or data.get("slots")):
        scheduling = True
    if scheduling and (kind in {"day", "slot", "mode", "kind"} or state.step in PREORDER_STEPS):
        if kind == "slot":
            state.step = "await_delivery_window"
        elif kind == "day":
            state.step = "await_delivery_day"
        elif kind == "mode" and state.step not in PREORDER_STEPS:
            state.step = "await_fulfillment_mode"
        elif kind == "kind" and state.step not in PREORDER_STEPS:
            state.step = "await_meeting_kind"
        data["preorder"] = True
        state.data = data
        return await _handle_preorder(db, state, phone, body)
    if data.get("preorder") and state.step in PREORDER_STEPS:
        return await _handle_preorder(db, state, phone, body)
    lowered = body.lower()
    wants = any(k in lowered for k in SCHEDULE_WORDS)
    if not in_flow and not wants:
        return None
    order = None
    order_id = (state.data or {}).get("delivery_order_id")
    if order_id:
        order = await order_svc.load_order(db, order_id)
    if not order or order.status not in SCHEDULABLE_STATUSES:
        order = await order_for_schedule(db, customer.id)
    if not order:
        if in_flow:
            state.step = "idle"
            return ["No hay un pedido abierto para agendar. Armá uno con CATALOGO."]
        return None
    if not delivery_incomplete(order):
        if wants:
            return await begin_schedule(db, order)
        if in_flow:
            state.step = "idle"
        return None

    if not in_flow:
        return await begin_schedule(db, order)

    if state.step == "await_delivery_mode":
        return await _pick_mode(db, state, order, body)
    if state.step == "await_delivery_day":
        return await _pick_day(db, state, order, body)
    if state.step == "await_delivery_window":
        return await _pick_window(db, state, order, body)
    if state.step == "await_delivery_address":
        return await _save_address(db, state, order, body)
    if state.step == "await_delivery_when":
        return await _save_when(db, state, order, body)
    return None


async def _pick_mode(
    db: AsyncSession, state: ConversationState, order: Order, body: str
) -> list[str]:
    kind, rest = wa_catalog.choice_kind(body)
    token = (rest if kind == "mode" else body).lower().strip()
    delivery = await get_or_create_delivery(db, order)
    mode = ""
    if token in {"1", "delivery", "domicilio", "local", "envio local", "envío local", "programada", "programado"}:
        mode = "delivery"
    elif token in {"2", "shipping", "ciudad", "interior", "encomienda", "flota", "envio", "envío", "envios", "envíos"}:
        mode = "shipping"
    elif token in {"3", "meeting", "reunion", "reunión", "encuentro", "cita", "punto"}:
        mode = "meeting"
    elif token in {"4", "coordinate", "coordinar", "despues", "después", "luego"}:
        mode = "to_coordinate"
    if not mode:
        extra = await wa_catalog.present_choices(
            db,
            order.customer.phone,
            text="Elegí una opción o respondé 1, 2, 3 o 4.",
            items=mode_items(),
            button="Elegir",
            preview="Agendar entrega",
        )
        extra.append("1 domicilio · 2 envío a otra ciudad · 3 reunión · 4 coordinar después")
        return extra
    delivery.mode = mode
    order.delivery_type = mode
    data = dict(state.data or {})
    data["delivery_mode"] = mode
    state.data = data
    if mode == "to_coordinate":
        state.step = "await_delivery_address"
        return [address_prompt(mode)]
    days = upcoming_days()
    data["days"] = days
    state.data = data
    state.step = "await_delivery_day"
    day_text = (
        "¿Qué día lo enviamos?"
        if mode == "shipping"
        else ("¿Qué día nos vemos?" if mode == "meeting" else "¿Qué día te lo llevamos?")
    )
    return await wa_catalog.present_choices(
        db,
        order.customer.phone,
        text=day_text,
        items=days,
        button="Ver días",
        preview="Elegir día",
    )


async def _pick_day(
    db: AsyncSession, state: ConversationState, order: Order, body: str
) -> list[str]:
    days = (state.data or {}).get("days") or upcoming_days()
    kind, rest = wa_catalog.choice_kind(body)
    key = rest if kind == "day" else body.strip()
    chosen = next((d for d in days if str(d.get("key")) == key or str(d.get("id")) == f"day:{key}" or str(d.get("id")) == body.strip()), None)
    if not chosen and body.strip().isdigit():
        idx = int(body.strip()) - 1
        if 0 <= idx < len(days):
            chosen = days[idx]
    if not chosen:
        lowered = body.lower()
        chosen = next((d for d in days if lowered in (d.get("title") or "").lower()), None)
    if not chosen:
        return await wa_catalog.present_choices(
            db,
            order.customer.phone,
            text="No reconocí el día. Elegí uno de la lista.",
            items=upcoming_days(),
            button="Ver días",
            preview="Elegir día",
        )
    data = dict(state.data or {})
    data["day"] = chosen
    state.data = data
    mode = data.get("delivery_mode") or "delivery"
    if mode == "shipping":
        delivery = await get_or_create_delivery(db, order)
        delivery.window_label = chosen.get("title") or ""
        state.step = "await_delivery_address"
        return [f"Día de envío: {delivery.window_label}.\n" + address_prompt(mode)]
    slots = slots_for_date(chosen.get("date") or "")
    if not slots:
        state.step = "await_delivery_when"
        return ["Ese día ya no tiene horarios. Escribí la hora que preferís."]
    data["slots"] = slots
    state.data = data
    state.step = "await_delivery_window"
    return await wa_catalog.present_choices(
        db,
        order.customer.phone,
        text=f"{chosen.get('title')}. ¿En qué horario?",
        items=slots,
        button="Horarios",
        preview="Elegir hora",
    )


async def _pick_window(
    db: AsyncSession, state: ConversationState, order: Order, body: str
) -> list[str]:
    slots = (state.data or {}).get("slots") or []
    windows = (state.data or {}).get("windows") or []
    kind, rest = wa_catalog.choice_kind(body)
    key = rest if kind == "slot" else body.strip()
    chosen = next((s for s in slots if str(s.get("hour")) == key or str(s.get("id")) == f"slot:{key}" or str(s.get("id")) == body.strip()), None)
    if not chosen and windows:
        chosen = next((w for w in windows if str(w.get("key")) == key), None)
        if chosen:
            chosen = {
                "title": chosen.get("label"),
                "start": chosen.get("start"),
            }
    if not chosen and body.strip().isdigit():
        idx = int(body.strip()) - 1
        if 0 <= idx < len(slots):
            chosen = slots[idx]
    if not chosen:
        day = (state.data or {}).get("day") or {}
        rebuilt = slots_for_date(day.get("date") or "")
        if rebuilt:
            return await wa_catalog.present_choices(
                db,
                order.customer.phone,
                text="No reconocí el horario. Elegí 1, 2 o 3.",
                items=rebuilt,
                button="Horarios",
                preview="Elegir hora",
            )
        rebuilt_w = upcoming_windows()
        return ["No reconocí el horario.\n" + windows_text(rebuilt_w)]
    data = dict(state.data or {})
    day = data.get("day") or {}
    label = f"{day.get('title') or ''} · {chosen.get('title') or ''}".strip(" ·")
    data["slot"] = chosen
    data["window_label"] = label
    data["scheduled_at"] = chosen.get("start")
    state.data = data
    mode = data.get("delivery_mode") or "delivery"
    state.step = "await_delivery_address"
    return [f"Anoté {label}.\n" + address_prompt(mode)]


async def _save_address(
    db: AsyncSession, state: ConversationState, order: Order, body: str
) -> list[str]:
    delivery = await get_or_create_delivery(db, order)
    delivery.address = body.strip()
    order.delivery_address = body.strip()
    mode = (state.data or {}).get("delivery_mode") or delivery.mode or "to_coordinate"
    if mode == "to_coordinate":
        state.step = "await_delivery_when"
        return ["Anotado. ¿Qué día y hora te viene bien? (ej: mañana 18 hs, sábado a la tarde)"]
    data = state.data or {}
    label = data.get("window_label") or delivery.window_label
    if label:
        delivery.window_label = label
    start = data.get("scheduled_at") or (data.get("slot") or {}).get("start")
    if start:
        try:
            delivery.scheduled_at = datetime.fromisoformat(start)
        except (TypeError, ValueError):
            delivery.scheduled_at = None
    delivery.mode = mode
    delivery.status = "confirmed"
    delivery.updated_at = utcnow()
    order.delivery_type = mode
    if order.status == "pagado":
        order.status = "listo_entrega"
    order.updated_at = utcnow()
    state.step = "idle"
    kind = MODE_LABEL.get(mode, mode)
    return [
        f"{kind} para {order.public_code}.\n"
        f"Cuando: {delivery.window_label or 'a confirmar'}\n"
        f"Lugar: {delivery.address}\n"
        "Si querés cambiarlo, escribí ENTREGA."
    ]


async def _save_when(
    db: AsyncSession, state: ConversationState, order: Order, body: str
) -> list[str]:
    delivery = await get_or_create_delivery(db, order)
    delivery.window_label = body.strip()
    delivery.notes = body.strip()
    delivery.mode = delivery.mode or "to_coordinate"
    delivery.status = "confirmed"
    delivery.updated_at = utcnow()
    order.delivery_type = delivery.mode
    if order.status == "pagado":
        order.status = "listo_entrega"
    order.updated_at = utcnow()
    state.step = "idle"
    where = delivery.address or "a coordinar"
    return [
        f"Entrega a coordinar para {order.public_code}.\n"
        f"Lugar: {where}\n"
        f"Cuando: {delivery.window_label}\n"
        "El vendedor te confirma. Si cambia, escribí ENTREGA."
    ]


async def apply_from_dashboard(
    db: AsyncSession,
    order: Order,
    *,
    mode: str,
    address: str,
    notes: str,
    window_key: str = "",
    when_text: str = "",
) -> Delivery:
    delivery = await get_or_create_delivery(db, order)
    delivery.mode = mode if mode in MODE_LABEL else ("scheduled" if mode == "scheduled" else "to_coordinate")
    order.delivery_type = mode
    delivery.address = address.strip()
    order.delivery_address = address.strip()
    delivery.notes = notes.strip()
    if mode == "scheduled" and window_key:
        windows = upcoming_windows()
        chosen = next((w for w in windows if w.key == window_key), None)
        if chosen:
            delivery.window_label = chosen.label
            delivery.scheduled_at = chosen.start
    elif when_text.strip():
        delivery.window_label = when_text.strip()
        if not delivery.notes:
            delivery.notes = when_text.strip()
    delivery.status = "confirmed"
    delivery.updated_at = utcnow()
    if order.status == "pagado":
        order.status = "listo_entrega"
    order.updated_at = utcnow()
    return delivery


async def apply_preorder_to_order(db: AsyncSession, order: Order, vars_: dict) -> Delivery:
    mode = str(vars_.get("fulfillment_mode") or vars_.get("delivery_mode") or "to_coordinate")
    address = str(vars_.get("address") or "")
    label = str(vars_.get("window_label") or "")
    delivery = await get_or_create_delivery(db, order)
    delivery.mode = mode if mode in MODE_LABEL else "to_coordinate"
    delivery.address = address
    delivery.window_label = label
    start = vars_.get("scheduled_at")
    if start:
        try:
            delivery.scheduled_at = datetime.fromisoformat(str(start))
        except (TypeError, ValueError):
            delivery.scheduled_at = None
    delivery.status = "confirmed"
    delivery.updated_at = utcnow()
    order.delivery_type = delivery.mode
    order.delivery_address = address
    order.updated_at = utcnow()
    phone = order.customer.phone if order.customer else ""
    if phone and delivery.mode == "meeting":
        payload = {
            "fulfillment_mode": delivery.mode,
            "delivery_mode": delivery.mode,
            "address": address,
            "window_label": label,
            "scheduled_at": vars_.get("scheduled_at"),
            "city": vars_.get("city"),
            "meeting_kind": vars_.get("meeting_kind"),
            "meeting_link": vars_.get("meeting_link"),
            "office_address": vars_.get("office_address"),
            "appointment_id": vars_.get("appointment_id"),
            "vars": vars_,
        }
        await persist_meeting_appointment(db, phone, payload, order_id=order.id)
    return delivery


async def set_order_status(db: AsyncSession, order: Order, status: str) -> Order:
    allowed = {
        "pagado": {"listo_entrega", "en_transito", "entregado"},
        "listo_entrega": {"en_transito", "entregado", "pagado"},
        "en_transito": {"entregado", "listo_entrega"},
        "entregado": set(),
        "pendiente_pago": {"pagado", "cancelado"},
        "cancelado": set(),
    }
    current = order.status
    if status == current:
        return order
    if status not in allowed.get(current, set()):
        raise ValueError(f"No se puede pasar de {current} a {status}")
    delivery = await get_or_create_delivery(db, order) if status in {
        "listo_entrega",
        "en_transito",
        "entregado",
    } else order.delivery
    if status == "en_transito" and delivery:
        delivery.status = "in_transit"
        delivery.updated_at = utcnow()
    if status == "entregado":
        if delivery:
            delivery.status = "delivered"
            delivery.delivered_at = utcnow()
            delivery.updated_at = utcnow()
        order.status = "entregado"
        order.updated_at = utcnow()
        await db.flush()
        return order
    if status == "listo_entrega" and delivery and delivery.status == "pending":
        delivery.status = "confirmed"
        delivery.updated_at = utcnow()
    order.status = status
    order.updated_at = utcnow()
    await db.flush()
    return order


async def notify_customer(db: AsyncSession, order: Order, text: str) -> None:
    if not order.customer or not text:
        return
    sent = await whatsapp.send_text(order.customer.phone, text, db=db)
    await inbox.record_bot_text(db, order.customer.phone, text, sent)


def summary(order: Order) -> str:
    delivery = order.delivery
    if not delivery:
        if order.delivery_type == "scheduled":
            return "Programada (sin horario)"
        if order.delivery_type == "to_coordinate":
            return "A coordinar"
        return "—"
    mode = MODE_LABEL.get(delivery.mode, delivery.mode)
    extra = delivery.window_label or delivery.address
    if extra:
        return f"{mode}: {extra}"
    return mode


def confirm_detail_from_data(data: dict) -> str:
    mode = data.get("fulfillment_mode") or data.get("delivery_mode") or ""
    when = data.get("window_label") or "a confirmar"
    place = data.get("address") or ""
    kind = data.get("meeting_kind") or ""
    city = data.get("city") or ""
    if mode == "shipping":
        dest = f" Destino: {place}." if place else ""
        return f"Envío a otro departamento. Fecha de llegada: {when}.{dest}"
    if mode == "meeting":
        if kind == "virtual":
            loc = f"\nLink: {place}" if place else ""
            return f"Reunión virtual{f' ({city})' if city else ''}. Horario: {when}.{loc}"
        loc = f"\nDirección: {place}" if place else ""
        return f"Reunión presencial{f' ({city})' if city else ''}. Horario: {when}.{loc}"
    addr = f" Dirección: {place}." if place else ""
    return f"Envío local. Hora del envío: {when}.{addr}"


def _parse_scheduled_at(raw) -> datetime | None:
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None) if raw.tzinfo else raw
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except (TypeError, ValueError):
        return None


async def persist_meeting_appointment(
    db: AsyncSession,
    phone: str,
    data: dict,
    *,
    order_id: str | None = None,
) -> Appointment | None:
    vars_ = dict(data.get("vars") or {})
    mode = str(data.get("fulfillment_mode") or data.get("delivery_mode") or vars_.get("fulfillment_mode") or "")
    kind = str(data.get("meeting_kind") or vars_.get("meeting_kind") or "")
    if mode != "meeting":
        return None
    customer = await db.scalar(
        select(Customer).where(Customer.phone == phone, Customer.company_id == tenancy.current_company_id())
    ) if tenancy.current_company_id() else await db.scalar(select(Customer).where(Customer.phone == phone))
    conv = await tenancy.get_conversation(db, phone)
    name = (
        (customer.name if customer else "")
        or (conv.name if conv else "")
        or str(vars_.get("name") or "")
    )
    has_product = bool(vars_.get("product_id") or vars_.get("order_code") or order_id)
    address = str(data.get("address") or vars_.get("address") or "")
    link = str(data.get("meeting_link") or vars_.get("meeting_link") or "")
    if kind == "virtual" and not link and address.startswith("http"):
        link = address
    existing_id = data.get("appointment_id") or vars_.get("appointment_id")
    appt = await db.get(Appointment, str(existing_id)) if existing_id else None
    if not appt:
        appt = Appointment(phone=phone, status="scheduled", company_id=tenancy.current_company_id())
        db.add(appt)
    appt.customer_name = name
    appt.purpose = "order" if has_product else "lead"
    appt.mode = "meeting"
    appt.meeting_kind = kind
    appt.city = str(data.get("city") or vars_.get("city") or "")
    appt.window_label = str(data.get("window_label") or vars_.get("window_label") or "")
    appt.scheduled_at = _parse_scheduled_at(data.get("scheduled_at") or vars_.get("scheduled_at"))
    if kind == "virtual":
        appt.meeting_link = link or (address if address.startswith("http") else "")
        appt.address = "" if appt.meeting_link else address
    else:
        appt.address = address or str(data.get("office_address") or vars_.get("office_address") or "")
        appt.meeting_link = link if link.startswith("http") else ""
    appt.notes = confirm_detail_from_data({**vars_, **data})
    if order_id:
        appt.order_id = order_id
    appt.updated_at = utcnow()
    await db.flush()
    return appt


def confirm_after_proof(order: Order) -> str:
    delivery = order.delivery
    if not delivery:
        return "El vendedor confirmará la entrega o la reunión."
    data = {
        "fulfillment_mode": delivery.mode,
        "window_label": delivery.window_label,
        "address": delivery.address,
        "meeting_kind": "virtual" if (delivery.address or "").startswith("http") else "presencial",
    }
    return confirm_detail_from_data(data)


def _parse_modes(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(m).strip() for m in raw if str(m).strip()]
    if isinstance(raw, str) and raw.strip():
        return [m.strip() for m in raw.split(",") if m.strip()]
    return ["delivery", "shipping", "meeting"]


def fulfillment_items(modes: list[str]) -> list[dict]:
    catalog = {
        "delivery": {
            "id": "mode:delivery",
            "title": "Envío local",
            "description": "La Paz / El Alto · coordinamos la hora",
        },
        "shipping": {
            "id": "mode:shipping",
            "title": "Otro departamento",
            "description": "Flota o encomienda · fecha de llegada",
        },
        "meeting": {
            "id": "mode:meeting",
            "title": "Reunión / recojo",
            "description": "Presencial o punto de encuentro",
        },
    }
    return [catalog[m] for m in modes if m in catalog]


async def begin_preorder(db: AsyncSession, phone: str, config: dict | None = None, vars_: dict | None = None) -> list[str]:
    cfg = config or {}
    state = await get_or_create_state(db, phone)
    data = dict(state.data or {})
    in_progress = bool(data.get("preorder")) and not data.get("schedule_done")
    merged_vars = dict(data.get("vars") or {})
    merged_vars.update(vars_ or {})
    if not in_progress:
        for key in (
            "window_label",
            "fulfillment_mode",
            "delivery_mode",
            "slot",
            "day",
            "slots",
            "days",
            "scheduled_at",
            "appointment_id",
            "address",
            "schedule_done",
        ):
            data.pop(key, None)
            merged_vars.pop(key, None)
    modes = _parse_modes(cfg.get("modes"))
    data["preorder"] = True
    data["schedule_done"] = False
    data["fulfill_modes"] = modes
    data["office_address"] = str(cfg.get("office_address") or "")
    data["meeting_link"] = str(cfg.get("meeting_link") or "")
    data["use_meeting_kind"] = bool(cfg.get("use_meeting_kind"))
    if merged_vars.get("meeting_kind"):
        data["meeting_kind"] = merged_vars["meeting_kind"]
    if merged_vars.get("city"):
        data["city"] = merged_vars["city"]
    data["vars"] = merged_vars
    state.data = data

    if data.get("window_label") and data.get("fulfillment_mode"):
        return await save_preorder(db, state, phone, already=True)

    if len(modes) == 1 and modes[0] == "meeting":
        kind = data.get("meeting_kind") or merged_vars.get("meeting_kind")
        if data.get("use_meeting_kind") and not kind:
            state.step = "await_meeting_kind"
            return await wa_catalog.present_choices(
                db,
                phone,
                text="¿La reunión es presencial o virtual?",
                items=[
                    {"id": "kind:presencial", "title": "Presencial", "description": "En la oficina"},
                    {"id": "kind:virtual", "title": "Virtual", "description": "Te mando el link"},
                ],
                button="Elegir",
                preview="Tipo de reunión",
            )
        data["fulfillment_mode"] = "meeting"
        data["delivery_mode"] = "meeting"
        state.data = data
        return await _preorder_ask_day(db, state, phone, "meeting")

    state.step = "await_fulfillment_mode"
    extra = await wa_catalog.present_choices(
        db,
        phone,
        text="¿Cómo lo recibís?",
        items=fulfillment_items(modes),
        button="Elegir",
        preview="Tipo de envío",
    )
    return extra


async def save_preorder(
    db: AsyncSession, state: ConversationState, phone: str, already: bool = False
) -> list[str]:
    data = dict(state.data or {})
    vars_ = dict(data.get("vars") or {})
    for key in ("fulfillment_mode", "delivery_mode", "window_label", "address", "city", "meeting_kind", "scheduled_at"):
        if data.get(key):
            vars_[key] = data[key]
    vars_["confirm_detail"] = confirm_detail_from_data(data)
    data["vars"] = vars_
    data["schedule_done"] = True
    data["preorder"] = False
    data["days"] = []
    data["slots"] = []
    if not (already and (data.get("appointment_id") or vars_.get("appointment_id"))):
        appt = await persist_meeting_appointment(db, phone, data)
        if appt:
            data["appointment_id"] = appt.id
            vars_["appointment_id"] = appt.id
            data["vars"] = vars_
    state.data = data
    state.step = "flow"
    return []


async def _handle_preorder(db: AsyncSession, state: ConversationState, phone: str, body: str) -> list[str]:
    if state.step == "await_lead_city":
        data = dict(state.data or {})
        data["city"] = body.strip()
        vars_ = dict(data.get("vars") or {})
        vars_["city"] = body.strip()
        data["vars"] = vars_
        state.data = data
        state.step = "await_meeting_kind"
        return await wa_catalog.present_choices(
            db,
            phone,
            text="¿La reunión es presencial o virtual?",
            items=[
                {"id": "kind:presencial", "title": "Presencial", "description": "En la oficina"},
                {"id": "kind:virtual", "title": "Virtual", "description": "Te mando el link"},
            ],
            button="Elegir",
            preview="Tipo de reunión",
        )
    if state.step == "await_meeting_kind":
        return await _preorder_pick_kind(db, state, phone, body)
    if state.step == "await_fulfillment_mode":
        return await _preorder_pick_mode(db, state, phone, body)
    if state.step == "await_delivery_day":
        return await _preorder_pick_day(db, state, phone, body)
    if state.step == "await_delivery_window":
        return await _preorder_pick_window(db, state, phone, body)
    if state.step == "await_delivery_address":
        return await _preorder_save_address(db, state, phone, body)
    return ["Elegí una opción de la lista para seguir con el envío o la reunión."]


def _kind_from_token(body: str) -> str:
    kind, rest = wa_catalog.choice_kind(body)
    token = (rest if kind in {"kind", "mode"} else body).lower().strip()
    if token in {"1", "presencial", "oficina", "local", "en persona", "presencial"} or "presencial" in token:
        return "presencial"
    if token in {"2", "virtual", "online", "meet", "zoom", "link", "videollamada"} or "virtual" in token:
        return "virtual"
    return ""


async def _preorder_pick_kind(db: AsyncSession, state: ConversationState, phone: str, body: str) -> list[str]:
    kind = _kind_from_token(body)
    if not kind:
        return await wa_catalog.present_choices(
            db,
            phone,
            text="Elegí 1 presencial o 2 virtual.",
            items=[
                {"id": "kind:presencial", "title": "Presencial", "description": "En la oficina"},
                {"id": "kind:virtual", "title": "Virtual", "description": "Te mando el link"},
            ],
            button="Elegir",
            preview="Tipo de reunión",
        )
    data = dict(state.data or {})
    data["meeting_kind"] = kind
    data["fulfillment_mode"] = "meeting"
    data["delivery_mode"] = "meeting"
    vars_ = dict(data.get("vars") or {})
    vars_["meeting_kind"] = kind
    data["vars"] = vars_
    state.data = data
    return await _preorder_ask_day(db, state, phone, "meeting")


async def _preorder_pick_mode(db: AsyncSession, state: ConversationState, phone: str, body: str) -> list[str]:
    kind, rest = wa_catalog.choice_kind(body)
    token = (rest if kind == "mode" else body).lower().strip()
    mode = ""
    if token in {"1", "delivery", "domicilio", "local", "envio local", "envío local", "la paz", "el alto"}:
        mode = "delivery"
    elif token in {
        "2",
        "shipping",
        "ciudad",
        "interior",
        "encomienda",
        "flota",
        "departamento",
        "otro departamento",
        "envío",
        "envio",
    }:
        mode = "shipping"
    elif token in {"3", "meeting", "reunion", "reunión", "encuentro", "cita", "recojo"}:
        mode = "meeting"
    if not mode:
        extra = await wa_catalog.present_choices(
            db,
            phone,
            text="Elegí 1 envío local, 2 otro departamento o 3 reunión.",
            items=fulfillment_items(_parse_modes((state.data or {}).get("fulfill_modes"))),
            button="Elegir",
            preview="Tipo de envío",
        )
        extra.append("1 local · 2 otro departamento · 3 reunión")
        return extra
    data = dict(state.data or {})
    data["fulfillment_mode"] = mode
    data["delivery_mode"] = mode
    state.data = data
    return await _preorder_ask_day(db, state, phone, mode)


async def _preorder_ask_day(db: AsyncSession, state: ConversationState, phone: str, mode: str) -> list[str]:
    days = upcoming_days()
    data = dict(state.data or {})
    data["days"] = days
    state.data = data
    state.step = "await_delivery_day"
    if mode == "shipping":
        day_text = "¿Qué día estimamos la llegada a tu departamento?"
    elif mode == "meeting":
        day_text = "¿Qué día te viene bien la reunión?"
    else:
        day_text = "¿Qué día te hacemos el envío local?"
    return await wa_catalog.present_choices(
        db, phone, text=day_text, items=days, button="Ver días", preview="Elegir día"
    )


async def _preorder_pick_day(db: AsyncSession, state: ConversationState, phone: str, body: str) -> list[str]:
    days = (state.data or {}).get("days") or upcoming_days()
    kind, rest = wa_catalog.choice_kind(body)
    key = rest if kind == "day" else body.strip()
    chosen = next(
        (
            d
            for d in days
            if str(d.get("key")) == key or str(d.get("id")) == f"day:{key}" or str(d.get("id")) == body.strip()
        ),
        None,
    )
    if not chosen and body.strip().isdigit():
        idx = int(body.strip()) - 1
        if 0 <= idx < len(days):
            chosen = days[idx]
    if not chosen:
        lowered = body.lower()
        chosen = next((d for d in days if lowered in (d.get("title") or "").lower()), None)
    if not chosen:
        return await wa_catalog.present_choices(
            db,
            phone,
            text="No reconocí el día. Elegí uno de la lista.",
            items=upcoming_days(),
            button="Ver días",
            preview="Elegir día",
        )
    data = dict(state.data or {})
    data["day"] = chosen
    state.data = data
    mode = data.get("fulfillment_mode") or data.get("delivery_mode") or "delivery"
    if mode == "shipping":
        data["window_label"] = chosen.get("title") or ""
        state.data = data
        state.step = "await_delivery_address"
        return [f"Fecha de llegada: {data['window_label']}.\n¿A qué ciudad y dirección lo enviamos?"]
    slots = slots_for_date(chosen.get("date") or "")
    if not slots:
        state.step = "await_delivery_address"
        return ["Ese día ya no tiene horarios. Escribí la hora que preferís y el lugar."]
    data["slots"] = slots
    state.data = data
    state.step = "await_delivery_window"
    return await wa_catalog.present_choices(
        db,
        phone,
        text=f"{chosen.get('title')}. ¿En qué horario?",
        items=slots,
        button="Horarios",
        preview="Elegir hora",
    )


async def _preorder_pick_window(db: AsyncSession, state: ConversationState, phone: str, body: str) -> list[str]:
    slots = (state.data or {}).get("slots") or []
    kind, rest = wa_catalog.choice_kind(body)
    key = rest if kind == "slot" else body.strip()
    chosen = next(
        (
            s
            for s in slots
            if str(s.get("hour")) == key or str(s.get("id")) == f"slot:{key}" or str(s.get("id")) == body.strip()
        ),
        None,
    )
    if not chosen and body.strip().isdigit():
        idx = int(body.strip()) - 1
        if 0 <= idx < len(slots):
            chosen = slots[idx]
    if not chosen:
        day = (state.data or {}).get("day") or {}
        rebuilt = slots_for_date(day.get("date") or "")
        if rebuilt:
            return await wa_catalog.present_choices(
                db,
                phone,
                text="No reconocí el horario. Elegí 1, 2 o 3.",
                items=rebuilt,
                button="Horarios",
                preview="Elegir hora",
            )
        return ["No reconocí el horario. Escribí mañana, tarde o noche."]
    data = dict(state.data or {})
    day = data.get("day") or {}
    label = f"{day.get('title') or ''} · {chosen.get('title') or ''}".strip(" ·")
    data["slot"] = chosen
    data["window_label"] = label
    data["scheduled_at"] = chosen.get("start")
    state.data = data
    mode = data.get("fulfillment_mode") or "delivery"
    kind = data.get("meeting_kind") or ""
    if mode == "meeting" and kind == "virtual":
        data["address"] = data.get("meeting_link") or ""
        state.data = data
        return await save_preorder(db, state, phone)
    if mode == "meeting" and kind == "presencial":
        data["address"] = data.get("office_address") or ""
        state.data = data
        return await save_preorder(db, state, phone)
    if mode == "meeting":
        state.step = "await_delivery_address"
        return [f"Anoté {label}.\n¿Dónde nos vemos? Zona, plaza u oficina."]
    state.step = "await_delivery_address"
    prompt = (
        "¿A qué dirección o zona te lo llevamos?"
        if mode == "delivery"
        else "¿A qué ciudad y dirección lo enviamos?"
    )
    return [f"Anoté {label}.\n{prompt}"]


async def _preorder_save_address(
    db: AsyncSession, state: ConversationState, phone: str, body: str
) -> list[str]:
    data = dict(state.data or {})
    data["address"] = body.strip()
    if not data.get("window_label"):
        data["window_label"] = body.strip()
    state.data = data
    return await save_preorder(db, state, phone)
