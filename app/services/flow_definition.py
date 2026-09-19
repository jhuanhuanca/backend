"""Grafo de flujos (JSON) y plantilla de venta por WhatsApp."""

from __future__ import annotations

from typing import Any

NODE_TYPES = (
    "start",
    "message",
    "catalog",
    "wait_input",
    "wait_payment",
    "match_product",
    "match_image",
    "create_order",
    "attach_proof",
    "order_status",
    "cancel_order",
    "ai_reply",
    "handoff",
    "end",
    "capture",
    "schedule_fulfillment",
    "schedule_call",
    "buttons",
    "send_image",
    "send_audio",
    "send_video",
)

PALETTE = [
    {"type": "start", "label": "Inicio", "hint": "Acá empieza el chat", "group": "Básico"},
    {
        "type": "end",
        "label": "Terminar",
        "hint": "Vuelve al inicio",
        "group": "Básico",
    },
    {"type": "message", "label": "Mensaje", "hint": "El bot escribe un texto", "group": "El bot envía"},
    {
        "type": "send_image",
        "label": "Imagen",
        "hint": "Manda una foto",
        "group": "El bot envía",
    },
    {
        "type": "send_audio",
        "label": "Audio",
        "hint": "Manda un audio o nota de voz",
        "group": "El bot envía",
    },
    {
        "type": "send_video",
        "label": "Video",
        "hint": "Manda un video",
        "group": "El bot envía",
    },
    {
        "type": "buttons",
        "label": "Botones",
        "hint": "Opciones para tocar (hasta 10; más de 3 salen como menú)",
        "group": "El bot envía",
    },
    {"type": "catalog", "label": "Catálogo", "hint": "Muestra productos y fotos", "group": "El bot envía"},
    {
        "type": "wait_input",
        "label": "Esperar respuesta",
        "hint": "Espera lo que escriba, elija o mande el cliente",
        "group": "Espera al cliente",
    },
    {
        "type": "wait_payment",
        "label": "Esperar foto de pago",
        "hint": "Espera el comprobante",
        "group": "Espera al cliente",
    },
    {
        "type": "match_product",
        "label": "Buscar producto",
        "hint": "Entiende el número o el nombre",
        "group": "Venta",
    },
    {
        "type": "match_image",
        "label": "Reconocer foto",
        "hint": "Compara una captura con el catálogo",
        "group": "Venta",
    },
    {
        "type": "schedule_fulfillment",
        "label": "Agendar entrega o reunión",
        "hint": "Día, hora, domicilio o Meet",
        "group": "Venta",
    },
    {
        "type": "schedule_call",
        "label": "Agendar llamada",
        "hint": "Turnos de 30 min, uno por persona",
        "group": "Venta",
    },
    {
        "type": "create_order",
        "label": "Crear pedido y QR",
        "hint": "Arma el pedido y manda a pagar",
        "group": "Venta",
    },
    {
        "type": "attach_proof",
        "label": "Guardar foto de pago",
        "hint": "Guarda el comprobante",
        "group": "Venta",
    },
    {
        "type": "capture",
        "label": "Guardar lo que dijo",
        "hint": "Ej. ciudad o tipo de reunión",
        "group": "Venta",
    },
    {
        "type": "order_status",
        "label": "Estado del pedido",
        "hint": "Responde cómo va el pedido",
        "group": "Venta",
    },
    {
        "type": "cancel_order",
        "label": "Cancelar pedido",
        "hint": "Anula y libera stock",
        "group": "Venta",
    },
    {
        "type": "ai_reply",
        "label": "Responder con IA",
        "hint": "Contesta si no hay opción clara",
        "group": "Otros",
    },
    {
        "type": "handoff",
        "label": "Pasar a una persona",
        "hint": "El bot se calla y atiende un vendedor",
        "group": "Otros",
    },
]

TRIGGER_TYPES = (
    "always",
    "default",
    "keyword",
    "regex",
    "is_digit",
    "is_image",
    "found",
    "not_found",
    "unsure",
    "transition",
)

AUTO_TYPES = {
    "start",
    "message",
    "catalog",
    "send_image",
    "send_audio",
    "send_video",
    "match_product",
    "match_image",
    "create_order",
    "attach_proof",
    "order_status",
    "cancel_order",
    "ai_reply",
    "handoff",
    "end",
    "capture",
}

WAIT_TYPES = {"wait_input", "wait_payment", "schedule_fulfillment", "schedule_call", "buttons"}


def empty_definition() -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": "n_start",
                "type": "start",
                "name": "Inicio",
                "x": 40,
                "y": 180,
                "config": {},
            }
        ],
        "edges": [],
    }


def default_sale_definition() -> dict[str, Any]:
    """Replica el bot actual: catálogo → cantidad → QR → comprobante, con nodo IA de respaldo."""
    return {
        "nodes": [
            {"id": "n_start", "type": "start", "name": "Inicio", "x": 40, "y": 200, "config": {}},
            {"id": "n_wait", "type": "wait_input", "name": "Menú", "x": 260, "y": 200, "config": {}},
            {"id": "n_catalog", "type": "catalog", "name": "Catálogo", "x": 700, "y": 40, "config": {}},
            {"id": "n_match", "type": "match_product", "name": "Producto", "x": 700, "y": 200, "config": {}},
            {
                "id": "n_qty",
                "type": "message",
                "name": "Cantidad",
                "x": 940,
                "y": 120,
                "config": {
                    "text": "{{product_name}} — {{product_price}} {{currency}}. Stock: {{stock}}. ¿Cuántas unidades querés?"
                },
            },
            {"id": "n_wait_qty", "type": "wait_input", "name": "Esperar cantidad", "x": 1160, "y": 120, "config": {}},
            {"id": "n_order", "type": "create_order", "name": "Pedido + QR", "x": 1380, "y": 120, "config": {}},
            {"id": "n_wait_pay", "type": "wait_payment", "name": "Esperar comprobante", "x": 1600, "y": 120, "config": {}},
            {"id": "n_proof", "type": "attach_proof", "name": "Guardar comprobante", "x": 1820, "y": 40, "config": {}},
            {
                "id": "n_thanks",
                "type": "message",
                "name": "Gracias",
                "x": 2040,
                "y": 40,
                "config": {"text": "Comprobante recibido para {{order_code}}. El vendedor lo confirmará."},
            },
            {"id": "n_status", "type": "order_status", "name": "Estado", "x": 700, "y": 360, "config": {}},
            {"id": "n_cancel", "type": "cancel_order", "name": "Cancelar", "x": 940, "y": 360, "config": {}},
            {
                "id": "n_ai",
                "type": "ai_reply",
                "name": "IA",
                "x": 940,
                "y": 280,
                "config": {
                    "system_hint": "Ayudá a elegir producto del catálogo. No inventes precios.",
                    "fallback_transition": "default",
                    "min_confidence": 0.65,
                },
            },
            {
                "id": "n_human",
                "type": "handoff",
                "name": "Humano",
                "x": 1160,
                "y": 280,
                "config": {"text": "Te paso con una persona del equipo. En un momento te escriben."},
            },
        ],
        "edges": [
            {"id": "e1", "from": "n_start", "to": "n_wait", "trigger_type": "always", "trigger_key": ""},
            {
                "id": "e3",
                "from": "n_wait",
                "to": "n_catalog",
                "trigger_type": "keyword",
                "trigger_key": "hola,menu,menú,catalogo,catálogo,hi,buenas,info",
            },
            {"id": "e4", "from": "n_wait", "to": "n_status", "trigger_type": "keyword", "trigger_key": "pedido,estado"},
            {"id": "e5", "from": "n_wait", "to": "n_cancel", "trigger_type": "keyword", "trigger_key": "cancelar,cancel"},
            {"id": "e6", "from": "n_wait", "to": "n_proof", "trigger_type": "is_image", "trigger_key": ""},
            {"id": "e7", "from": "n_wait", "to": "n_match", "trigger_type": "default", "trigger_key": ""},
            {"id": "e8", "from": "n_catalog", "to": "n_wait", "trigger_type": "always", "trigger_key": ""},
            {"id": "e9", "from": "n_match", "to": "n_qty", "trigger_type": "found", "trigger_key": ""},
            {"id": "e10", "from": "n_match", "to": "n_ai", "trigger_type": "not_found", "trigger_key": ""},
            {"id": "e11", "from": "n_qty", "to": "n_wait_qty", "trigger_type": "always", "trigger_key": ""},
            {"id": "e12", "from": "n_wait_qty", "to": "n_order", "trigger_type": "is_digit", "trigger_key": ""},
            {
                "id": "e13",
                "from": "n_wait_qty",
                "to": "n_cancel",
                "trigger_type": "keyword",
                "trigger_key": "cancelar,cancel",
            },
            {"id": "e14", "from": "n_wait_qty", "to": "n_qty", "trigger_type": "default", "trigger_key": ""},
            {"id": "e15", "from": "n_order", "to": "n_wait_pay", "trigger_type": "always", "trigger_key": ""},
            {"id": "e16", "from": "n_wait_pay", "to": "n_proof", "trigger_type": "is_image", "trigger_key": ""},
            {
                "id": "e17",
                "from": "n_wait_pay",
                "to": "n_status",
                "trigger_type": "keyword",
                "trigger_key": "pedido,estado",
            },
            {
                "id": "e18",
                "from": "n_wait_pay",
                "to": "n_cancel",
                "trigger_type": "keyword",
                "trigger_key": "cancelar,cancel",
            },
            {"id": "e19", "from": "n_wait_pay", "to": "n_wait_pay", "trigger_type": "default", "trigger_key": ""},
            {"id": "e20", "from": "n_proof", "to": "n_thanks", "trigger_type": "always", "trigger_key": ""},
            {"id": "e21", "from": "n_thanks", "to": "n_wait", "trigger_type": "always", "trigger_key": ""},
            {"id": "e22", "from": "n_status", "to": "n_wait", "trigger_type": "always", "trigger_key": ""},
            {"id": "e23", "from": "n_cancel", "to": "n_wait", "trigger_type": "always", "trigger_key": ""},
            {"id": "e24", "from": "n_ai", "to": "n_match", "trigger_type": "transition", "trigger_key": "buy"},
            {"id": "e25", "from": "n_ai", "to": "n_human", "trigger_type": "transition", "trigger_key": "human"},
            {"id": "e26", "from": "n_ai", "to": "n_wait", "trigger_type": "transition", "trigger_key": "default"},
            {"id": "e27", "from": "n_human", "to": "n_wait", "trigger_type": "always", "trigger_key": ""},
        ],
    }


def validate_definition(definition: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    nodes = definition.get("nodes") or []
    edges = definition.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return ["definition.nodes y definition.edges deben ser listas"]
    ids = {n.get("id") for n in nodes if isinstance(n, dict) and n.get("id")}
    if not ids:
        errors.append("El flujo no tiene nodos")
    types = {n.get("type") for n in nodes if isinstance(n, dict)}
    if "start" not in types:
        errors.append("Falta un nodo start")
    for node in nodes:
        if not isinstance(node, dict):
            errors.append("Nodo inválido")
            continue
        ntype = node.get("type")
        if ntype not in NODE_TYPES:
            errors.append(f"Tipo de nodo desconocido: {ntype}")
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if edge.get("from") not in ids or edge.get("to") not in ids:
            errors.append(f"Arista huérfana: {edge.get('id')}")
        trig = edge.get("trigger_type") or "always"
        if trig not in TRIGGER_TYPES:
            errors.append(f"Trigger desconocido: {trig}")
    return errors


OFFICE_ADDRESS = "Av. 16 de Julio 1234, El Prado, La Paz · oficina Live, piso 2"
MEETING_LINK = "https://meet.google.com/abc-defg-hij"
SHIPPING_LOCAL = "20"
SHIPPING_INTERIOR = "40"


def _n(nid: str, ntype: str, name: str, x: int, y: int, config: dict | None = None) -> dict[str, Any]:
    return {"id": nid, "type": ntype, "name": name, "x": x, "y": y, "config": config or {}}


def _e(eid: str, frm: str, to: str, trigger: str = "always", key: str = "") -> dict[str, Any]:
    return {"id": eid, "from": frm, "to": to, "trigger_type": trigger, "trigger_key": key}


def _sale_order_config() -> dict[str, Any]:
    return {
        "deposit_percent": 50,
        "shipping_local": SHIPPING_LOCAL,
        "shipping_interior": SHIPPING_INTERIOR,
        "skip_schedule": True,
    }


def _sale_schedule_config() -> dict[str, Any]:
    return {
        "modes": "delivery,shipping,meeting",
        "office_address": OFFICE_ADDRESS,
        "meeting_link": MEETING_LINK,
    }


def _thanks_text() -> str:
    return (
        "Comprobante recibido para {{order_code}}.\n\n"
        "{{confirm_detail}}\n\n"
        "Adelanto: {{pay_amount}} {{currency}} (50% del producto + envío).\n"
        "Saldo: {{remaining}} {{currency}}.\n"
        "Si querés cambiar el horario, escribí ENTREGA."
    )


def _sale_nodes(prefix: str, x: int, y: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Rama catálogo → cantidad → agenda → adelanto 50% + envío → comprobante."""
    p = prefix
    catalog_id = f"{p}catalog"
    wait_id = f"{p}wait"
    nodes = [
        _n(
            catalog_id,
            "catalog",
            "Catálogo",
            x,
            y,
            {
                "text": "Este es el catálogo completo. Tocá un producto o escribí el número / nombre.",
                "button": "Ver productos",
                "limit": 50,
                "image_count": 0,
                "send_list": True,
            },
        ),
        _n(f"{p}wait_pick", "wait_input", "Elegir producto", x + 220, y),
        _n(f"{p}match", "match_product", "Buscar producto", x + 440, y),
        _n(
            f"{p}miss",
            "message",
            "No encontrado",
            x + 440,
            y + 80,
            {
                "text": "No encontré ese producto. Tocá otra tarjeta del catálogo o escribí el nombre. El pedido sigue acá, no hace falta empezar de nuevo."
            },
        ),
        _n(
            f"{p}qty",
            "message",
            "Cantidad",
            x + 660,
            y - 80,
            {
                "text": "{{product_name}} — {{product_price}} {{currency}}. Stock: {{stock}}. ¿Cuántas unidades querés?"
            },
        ),
        _n(f"{p}wait_qty", "wait_input", "Esperar cantidad", x + 880, y - 80),
        _n(
            f"{p}sched",
            "schedule_fulfillment",
            "Local o interior",
            x + 1100,
            y - 80,
            _sale_schedule_config(),
        ),
        _n(f"{p}order", "create_order", "Adelanto + QR", x + 1320, y - 80, _sale_order_config()),
        _n(f"{p}wait_pay", "wait_payment", "Esperar comprobante", x + 1540, y - 80),
        _n(f"{p}proof", "attach_proof", "Guardar comprobante", x + 1760, y - 140),
        _n(f"{p}thanks", "message", "Confirmación", x + 1980, y - 140, {"text": _thanks_text()}),
        _n(f"{p}status", "order_status", "Estado", x + 440, y + 140),
        _n(f"{p}cancel", "cancel_order", "Cancelar", x + 660, y + 140),
    ]
    edges = [
        _e(f"{p}e1", catalog_id, f"{p}wait_pick"),
        _e(f"{p}e2", f"{p}wait_pick", f"{p}match", "default"),
        _e(f"{p}e3", f"{p}wait_pick", f"{p}status", "keyword", "pedido,estado"),
        _e(f"{p}e4", f"{p}wait_pick", f"{p}cancel", "keyword", "cancelar,cancel"),
        _e(f"{p}e5", f"{p}match", f"{p}qty", "found"),
        _e(f"{p}e6", f"{p}match", f"{p}miss", "not_found"),
        _e(f"{p}e6b", f"{p}miss", f"{p}wait_pick"),
        _e(f"{p}e7", f"{p}qty", f"{p}wait_qty"),
        _e(f"{p}e8", f"{p}wait_qty", f"{p}sched", "is_digit"),
        _e(f"{p}e9", f"{p}wait_qty", f"{p}cancel", "keyword", "cancelar,cancel"),
        _e(f"{p}e10", f"{p}wait_qty", f"{p}qty", "default"),
        _e(f"{p}e11", f"{p}sched", f"{p}order"),
        _e(f"{p}e12", f"{p}order", f"{p}wait_pay"),
        _e(f"{p}e13", f"{p}wait_pay", f"{p}proof", "is_image"),
        _e(f"{p}e14", f"{p}wait_pay", f"{p}status", "keyword", "pedido,estado"),
        _e(f"{p}e15", f"{p}wait_pay", f"{p}cancel", "keyword", "cancelar,cancel"),
        _e(f"{p}e16", f"{p}wait_pay", f"{p}wait_pay", "default"),
        _e(f"{p}e17", f"{p}proof", f"{p}thanks"),
        _e(f"{p}e18", f"{p}thanks", wait_id if prefix else f"{p}wait_pick"),
        _e(f"{p}e19", f"{p}status", wait_id if prefix else f"{p}wait_pick"),
        _e(f"{p}e20", f"{p}cancel", wait_id if prefix else f"{p}wait_pick"),
    ]
    return nodes, edges, catalog_id


def sale_catalog_definition() -> dict[str, Any]:
    """Venta: catálogo completo → envío local/interior o reunión → adelanto 50% + envío."""
    sale_nodes, sale_edges, catalog_id = _sale_nodes("", 700, 200)
    # Loop thanks/status/cancel back to menu wait, not wait_pick
    sale_edges = [e for e in sale_edges if e["id"] not in {"e18", "e19", "e20"}]
    nodes = [
        _n("n_start", "start", "Inicio", 40, 200),
        _n(
            "n_hello",
            "message",
            "Bienvenida",
            260,
            200,
            {
                "text": "Hola, soy el asistente de ventas. Te muestro el catálogo y coordinamos envío o reunión."
            },
        ),
        _n("n_wait", "wait_input", "Menú", 480, 200),
        *sale_nodes,
    ]
    edges = [
        _e("e0", "n_start", "n_hello"),
        _e("e0b", "n_hello", catalog_id),
        _e("e_cat", "n_wait", catalog_id, "keyword", "hola,hi,buenas,menu,menú,catalogo,catálogo,info,productos,comprar"),
        _e("e_def", "n_wait", catalog_id, "default"),
        _e("e_st", "n_wait", "status", "keyword", "pedido,estado"),
        _e("e_ca", "n_wait", "cancel", "keyword", "cancelar,cancel"),
        *sale_edges,
        _e("e18", "thanks", "n_wait"),
        _e("e19", "status", "n_wait"),
        _e("e20", "cancel", "n_wait"),
    ]
    return {"nodes": nodes, "edges": edges}


def leads_catalog_definition() -> dict[str, Any]:
    """Menú: catálogo (misma venta) o inscripción / más info con reunión presencial o virtual."""
    sale_nodes, sale_edges, catalog_id = _sale_nodes("s_", 700, 360)
    sale_edges = [e for e in sale_edges if not e["id"].endswith(("e18", "e19", "e20"))]
    nodes = [
        _n("n_start", "start", "Inicio", 40, 200),
        _n(
            "n_welcome",
            "message",
            "Menú inicial",
            260,
            200,
            {
                "text": (
                    "Hola, ¿en qué te ayudo?\n"
                    "1. Ver catálogo y comprar\n"
                    "2. Inscribirme / más información (agendar una reunión)"
                )
            },
        ),
        _n(
            "n_retry",
            "message",
            "Repetí opción",
            500,
            320,
            {"text": "No te entendí. Escribí *1* para ver el catálogo o *2* para inscribirte / agendar una reunión."},
        ),
        _n("n_wait", "wait_input", "Elegir opción", 500, 200),
        _n(
            "n_city_q",
            "message",
            "Ciudad",
            740,
            80,
            {"text": "¿De qué ciudad nos escribís?"},
        ),
        _n("n_wait_city", "wait_input", "Esperar ciudad", 980, 80),
        _n("n_cap_city", "capture", "Guardar ciudad", 1220, 80, {"var": "city"}),
        _n(
            "n_kind_q",
            "message",
            "Tipo de reunión",
            1460,
            80,
            {
                "text": (
                    "¿Cómo querés la reunión?\n"
                    "1. Presencial en oficina\n"
                    "2. Virtual (te mando el link)"
                )
            },
        ),
        _n("n_wait_kind", "wait_input", "Presencial o virtual", 1700, 80),
        _n("n_cap_pres", "capture", "Presencial", 1940, 20, {"var": "meeting_kind", "value": "presencial"}),
        _n("n_cap_virt", "capture", "Virtual", 1940, 140, {"var": "meeting_kind", "value": "virtual"}),
        _n(
            "n_sched_meet",
            "schedule_fulfillment",
            "Agendar reunión",
            2180,
            80,
            {
                "modes": "meeting",
                "use_meeting_kind": True,
                "office_address": OFFICE_ADDRESS,
                "meeting_link": MEETING_LINK,
            },
        ),
        _n(
            "n_meet_ok",
            "message",
            "Datos de la reunión",
            2420,
            80,
            {
                "text": (
                    "Listo, {{name}}.\n"
                    "{{confirm_detail}}\n\n"
                    "Ciudad: {{city}}\n"
                    "Si querés ver productos, escribí CATALOGO."
                )
            },
        ),
        *sale_nodes,
    ]
    edges = [
        _e("e0", "n_start", "n_welcome"),
        _e("e0b", "n_welcome", "n_wait"),
        _e("e_cat1", "n_wait", catalog_id, "keyword", "catalogo,catálogo,comprar,productos"),
        _e("e_cat2", "n_wait", catalog_id, "regex", r"^1$"),
        _e("e_lead1", "n_wait", "n_city_q", "keyword", "inscribir,inscripcion,inscripción,info,informacion,información,reunion,reunión,asesor"),
        _e("e_lead2", "n_wait", "n_city_q", "regex", r"^2$"),
        _e("e_lead_def", "n_wait", "n_retry", "default"),
        _e("e_retry", "n_retry", "n_wait"),
        _e("e_city1", "n_city_q", "n_wait_city"),
        _e("e_city2", "n_wait_city", "n_cap_city"),
        _e("e_city3", "n_cap_city", "n_kind_q"),
        _e("e_kind1", "n_kind_q", "n_wait_kind"),
        _e("e_kind_p", "n_wait_kind", "n_cap_pres", "keyword", "presencial,oficina,en persona"),
        _e("e_kind_v", "n_wait_kind", "n_cap_virt", "keyword", "virtual,online,meet,zoom,link,videollamada"),
        _e("e_kind_p2", "n_wait_kind", "n_cap_pres", "regex", r"^1$"),
        _e("e_kind_v2", "n_wait_kind", "n_cap_virt", "regex", r"^2$"),
        _e("e_kind_d", "n_wait_kind", "n_kind_q", "default"),
        _e("e_p", "n_cap_pres", "n_sched_meet"),
        _e("e_v", "n_cap_virt", "n_sched_meet"),
        _e("e_m1", "n_sched_meet", "n_meet_ok"),
        _e("e_m2", "n_meet_ok", "n_wait"),
        *sale_edges,
        _e("s_e18", "s_thanks", "n_wait"),
        _e("s_e19", "s_status", "n_wait"),
        _e("s_e20", "s_cancel", "n_wait"),
    ]
    return {"nodes": nodes, "edges": edges}
