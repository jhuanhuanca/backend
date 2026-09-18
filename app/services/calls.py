"""Agenda de llamadas: un turno de 30 minutos por persona, sin solaparse."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, Conversation, ConversationState, Customer, utcnow
from app.services import deliveries, tenancy, wa_catalog
from app.services.deliveries import LA_PAZ

CALL_STEPS = {"await_call_day", "await_call_slot"}
DEFAULT_START = 9
DEFAULT_END = 18
DEFAULT_DAYS = 7
DEFAULT_INTERVAL = 30
SLOT_PAGE = 9


def _cfg(config: dict | None) -> dict:
    raw = config or {}
    try:
        start = int(raw.get("start_hour") or DEFAULT_START)
    except (TypeError, ValueError):
        start = DEFAULT_START
    try:
        end = int(raw.get("end_hour") or DEFAULT_END)
    except (TypeError, ValueError):
        end = DEFAULT_END
    try:
        days = int(raw.get("days") or DEFAULT_DAYS)
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    try:
        interval = int(raw.get("interval_min") or DEFAULT_INTERVAL)
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL
    start = max(0, min(23, start))
    end = max(start + 1, min(24, end))
    days = max(1, min(14, days))
    if interval not in {15, 30, 60}:
        interval = DEFAULT_INTERVAL
    text = str(raw.get("text") or "¿Qué día te llamamos?").strip()
    return {
        "start_hour": start,
        "end_hour": end,
        "days": days,
        "interval_min": interval,
        "text": text or "¿Qué día te llamamos?",
    }


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def _floor_slot(moment: datetime, interval: int) -> datetime:
    moment = _naive(moment)
    minute = (moment.minute // interval) * interval
    return moment.replace(minute=minute, second=0, microsecond=0)


def _stamp(text: str) -> str:
    raw = (text or "").strip().replace(".", ":")
    parts = raw.split(":")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1][:2].isdigit():
        return f"{int(parts[0]):02d}:{int(parts[1][:2]):02d}"
    return raw


def _label_range(start: datetime, interval: int) -> str:
    end = start + timedelta(minutes=interval)
    return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"


async def _occupied(
    db: AsyncSession,
    day_start: datetime,
    day_end: datetime,
    *,
    interval: int,
    ignore_id: str | None = None,
) -> set[datetime]:
    query = select(Appointment).where(
        Appointment.status == "scheduled",
        Appointment.scheduled_at.is_not(None),
        Appointment.scheduled_at >= day_start,
        Appointment.scheduled_at < day_end,
    )
    cid = tenancy.current_company_id()
    if cid:
        query = query.where(Appointment.company_id == cid)
    rows = (await db.scalars(query)).all()
    taken: set[datetime] = set()
    for row in rows:
        if ignore_id and row.id == ignore_id:
            continue
        start = _floor_slot(row.scheduled_at, interval)
        taken.add(start)
    return taken


async def slots_for_date(
    db: AsyncSession,
    date_iso: str,
    config: dict,
    *,
    ignore_id: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    parts = (date_iso or "").split("-")
    if len(parts) < 3:
        return []
    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    interval = int(config.get("interval_min") or DEFAULT_INTERVAL)
    start_h = int(config.get("start_hour") or DEFAULT_START)
    end_h = int(config.get("end_hour") or DEFAULT_END)
    local = (now or datetime.now(LA_PAZ)).astimezone(LA_PAZ)
    cutoff = local + timedelta(minutes=interval)
    day_start = datetime(year, month, day, 0, 0)
    day_end = day_start + timedelta(days=1)
    taken = await _occupied(db, day_start, day_end, interval=interval, ignore_id=ignore_id)
    cursor = datetime(year, month, day, start_h, 0, tzinfo=LA_PAZ)
    limit = datetime(year, month, day, end_h, 0, tzinfo=LA_PAZ)
    items: list[dict] = []
    while cursor < limit:
        naive = cursor.replace(tzinfo=None)
        if cursor >= cutoff and naive not in taken:
            stamp = naive.strftime("%H:%M")
            items.append(
                {
                    "id": f"slot:{stamp}",
                    "title": _label_range(naive, interval),
                    "description": f"{interval} min",
                    "start": naive.isoformat(),
                    "stamp": stamp,
                }
            )
        cursor += timedelta(minutes=interval)
    return items


def _page_slots(slots: list[dict], offset: int) -> list[dict]:
    offset = max(0, int(offset or 0))
    chunk = slots[offset : offset + SLOT_PAGE]
    if offset + SLOT_PAGE < len(slots):
        chunk.append(
            {
                "id": "more:next",
                "title": "Más horarios",
                "description": "Ver los siguientes",
            }
        )
    return chunk


async def begin_call(
    db: AsyncSession,
    phone: str,
    config: dict | None,
    vars_: dict | None,
    *,
    node_id: str = "",
) -> list[str]:
    phone = phone.lstrip("+")
    cfg = _cfg(config)
    state = await deliveries.get_or_create_state(db, phone)
    data = dict(state.data or {})
    merged = dict(data.get("vars") or {})
    merged.update(vars_ or {})
    data["call_booking"] = True
    data["call_done"] = False
    data["call_config"] = cfg
    data["schedule_node_id"] = node_id or data.get("schedule_node_id") or ""
    data["slot_offset"] = 0
    data.pop("call_day", None)
    data.pop("call_slots", None)
    data["vars"] = merged
    state.data = data
    days = deliveries.upcoming_days(days=cfg["days"])
    data["days"] = days
    state.data = data
    state.step = "await_call_day"
    return await wa_catalog.present_choices(
        db,
        phone,
        text=cfg["text"],
        items=days,
        button="Ver días",
        preview="Día de llamada",
    )


def _in_call(state: ConversationState | None) -> bool:
    if not state:
        return False
    data = dict(state.data or {})
    if data.get("call_done"):
        return False
    if data.get("call_booking"):
        return True
    return (state.step or "") in CALL_STEPS


async def handle_incoming(
    db: AsyncSession, *, phone: str, name: str, text: str | None
) -> list[str] | None:
    body = (text or "").strip()
    if not body:
        return None
    state = await deliveries.get_or_create_state(db, phone)
    if not _in_call(state):
        return None
    if state.step == "await_call_slot":
        return await _pick_slot(db, state, phone, name, body)
    return await _pick_day(db, state, phone, body)


async def _pick_day(db: AsyncSession, state: ConversationState, phone: str, body: str) -> list[str]:
    data = dict(state.data or {})
    cfg = _cfg(data.get("call_config"))
    days = data.get("days") or deliveries.upcoming_days(days=cfg["days"])
    kind, rest = wa_catalog.choice_kind(body)
    key = rest if kind == "day" else body.strip()
    chosen = next(
        (
            d
            for d in days
            if str(d.get("key")) == key
            or str(d.get("id")) == f"day:{key}"
            or str(d.get("id")) == body.strip()
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
        data["days"] = deliveries.upcoming_days(days=cfg["days"])
        state.data = data
        state.step = "await_call_day"
        return await wa_catalog.present_choices(
            db,
            phone,
            text="No reconocí el día. Elegí uno de la lista.",
            items=data["days"],
            button="Ver días",
            preview="Día de llamada",
        )
    data["call_day"] = chosen
    data["slot_offset"] = 0
    slots = await slots_for_date(db, chosen.get("date") or "", cfg)
    data["call_slots"] = slots
    state.data = data
    if not slots:
        state.step = "await_call_day"
        return await wa_catalog.present_choices(
            db,
            phone,
            text=f"{chosen.get('title')} ya no tiene turnos libres de {cfg['interval_min']} min. Elegí otro día.",
            items=deliveries.upcoming_days(days=cfg["days"]),
            button="Ver días",
            preview="Día de llamada",
        )
    state.step = "await_call_slot"
    return await wa_catalog.present_choices(
        db,
        phone,
        text=f"{chosen.get('title')}. ¿A qué hora te llamamos? Cada llamada dura {cfg['interval_min']} min.",
        items=_page_slots(slots, 0),
        button="Horarios",
        preview="Hora de llamada",
    )


async def _pick_slot(
    db: AsyncSession, state: ConversationState, phone: str, name: str, body: str
) -> list[str]:
    data = dict(state.data or {})
    cfg = _cfg(data.get("call_config"))
    day = data.get("call_day") or {}
    slots = data.get("call_slots") or []
    if not slots:
        slots = await slots_for_date(db, day.get("date") or "", cfg)
        data["call_slots"] = slots
        state.data = data
    folded = body.strip().lower()
    kind, rest = wa_catalog.choice_kind(body)
    if folded in {"more:next", "más horarios", "mas horarios", "más", "mas"} or rest == "next":
        offset = int(data.get("slot_offset") or 0) + SLOT_PAGE
        if offset >= len(slots):
            offset = 0
        data["slot_offset"] = offset
        state.data = data
        state.step = "await_call_slot"
        return await wa_catalog.present_choices(
            db,
            phone,
            text="Estos son los siguientes horarios libres.",
            items=_page_slots(slots, offset),
            button="Horarios",
            preview="Hora de llamada",
        )
    token = _stamp(rest if kind == "slot" else body)
    chosen = next(
        (
            s
            for s in slots
            if str(s.get("stamp")) == token
            or str(s.get("id")) == body.strip()
            or str(s.get("id")) == f"slot:{token}"
            or _stamp(str(s.get("title") or "").split("–")[0]) == token
        ),
        None,
    )
    if not chosen and body.strip().isdigit():
        offset = int(data.get("slot_offset") or 0)
        page = [s for s in _page_slots(slots, offset) if not str(s.get("id") or "").startswith("more:")]
        idx = int(body.strip()) - 1
        if 0 <= idx < len(page):
            chosen = page[idx]
    if not chosen:
        lowered = body.lower().replace(" ", "")
        chosen = next(
            (s for s in slots if lowered in str(s.get("stamp") or "").replace(":", "") or lowered in str(s.get("title") or "").lower()),
            None,
        )
    if not chosen:
        state.step = "await_call_slot"
        return await wa_catalog.present_choices(
            db,
            phone,
            text="No reconocí la hora. Elegí un horario libre.",
            items=_page_slots(slots, int(data.get("slot_offset") or 0)),
            button="Horarios",
            preview="Hora de llamada",
        )
    start = deliveries._parse_scheduled_at(chosen.get("start"))
    if not start:
        return await _pick_day(db, state, phone, str(day.get("id") or ""))
    ignore_id = str(data.get("appointment_id") or "") or None
    day_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    taken = await _occupied(
        db,
        day_start,
        day_start + timedelta(days=1),
        interval=int(cfg["interval_min"]),
        ignore_id=ignore_id,
    )
    bucket = _floor_slot(start, int(cfg["interval_min"]))
    if bucket in taken:
        slots = await slots_for_date(db, day.get("date") or "", cfg, ignore_id=ignore_id)
        data["call_slots"] = slots
        data["slot_offset"] = 0
        state.data = data
        state.step = "await_call_slot"
        if not slots:
            state.step = "await_call_day"
            return await wa_catalog.present_choices(
                db,
                phone,
                text="Ese horario lo tomó otra persona y el día se llenó. Elegí otro día.",
                items=deliveries.upcoming_days(days=cfg["days"]),
                button="Ver días",
                preview="Día de llamada",
            )
        return await wa_catalog.present_choices(
            db,
            phone,
            text="Ese horario lo acaba de tomar otra persona. Elegí otro.",
            items=_page_slots(slots, 0),
            button="Horarios",
            preview="Hora de llamada",
        )
    window = f"{day.get('title') or ''} · {_label_range(bucket, int(cfg['interval_min']))}".strip(" ·")
    appt = await persist_call(
        db,
        phone,
        name=name,
        data=data,
        scheduled_at=bucket,
        window_label=window,
    )
    vars_ = dict(data.get("vars") or {})
    vars_["call_slot"] = _label_range(bucket, int(cfg["interval_min"]))
    vars_["call_day"] = str(day.get("title") or "")
    vars_["window_label"] = window
    vars_["scheduled_at"] = bucket.isoformat()
    if appt:
        vars_["appointment_id"] = appt.id
        data["appointment_id"] = appt.id
    data["vars"] = vars_
    data["call_done"] = True
    data["call_booking"] = False
    data["window_label"] = window
    data["scheduled_at"] = bucket.isoformat()
    data.pop("call_slots", None)
    state.data = data
    state.step = "flow"
    return [
        f"Agendé tu llamada: {window}.\n"
        f"Te llamamos a este WhatsApp. Cada persona tiene {cfg['interval_min']} minutos."
    ]


async def persist_call(
    db: AsyncSession,
    phone: str,
    *,
    name: str,
    data: dict,
    scheduled_at: datetime,
    window_label: str,
) -> Appointment:
    phone = phone.lstrip("+")
    vars_ = dict(data.get("vars") or {})
    existing_id = data.get("appointment_id") or vars_.get("appointment_id")
    appt = await db.get(Appointment, str(existing_id)) if existing_id else None
    if not appt:
        appt = await db.scalar(
            select(Appointment)
            .where(
                Appointment.phone == phone,
                Appointment.purpose == "call",
                Appointment.status == "scheduled",
                Appointment.company_id == tenancy.current_company_id(),
            )
            .order_by(Appointment.updated_at.desc())
        )
    if not appt:
        appt = Appointment(phone=phone, status="scheduled", company_id=tenancy.current_company_id())
        db.add(appt)
    if tenancy.current_company_id():
        appt.company_id = tenancy.current_company_id()
    customer = await db.scalar(
        select(Customer).where(
            Customer.phone == phone,
            Customer.company_id == tenancy.current_company_id(),
        )
    )
    conv = await tenancy.get_conversation(db, phone)
    appt.customer_name = (
        (customer.name if customer else "")
        or (conv.name if conv else "")
        or (name or "")
        or str(vars_.get("name") or "")
    )
    appt.purpose = "call"
    appt.mode = "call"
    appt.meeting_kind = "llamada"
    appt.city = str(data.get("city") or vars_.get("city") or "")
    appt.window_label = window_label
    appt.scheduled_at = scheduled_at
    appt.address = ""
    appt.meeting_link = ""
    appt.notes = f"Llamada de {int(_cfg(data.get('call_config'))['interval_min'])} min"
    appt.status = "scheduled"
    appt.updated_at = utcnow()
    await db.flush()
    return appt
