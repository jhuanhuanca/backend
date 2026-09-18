from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from pathlib import Path

from app.config import UPLOADS_DIR, get_settings
from app.models import Product
from app.services import inbox, inventory, whatsapp
from app.services.whatsapp import CloudError

settings = get_settings()


def clip(text: str, limit: int) -> str:
    value = " ".join((text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)] + "…"


def choice_kind(body: str) -> tuple[str, str]:
    text = (body or "").strip()
    if ":" in text:
        kind, rest = text.split(":", 1)
        if kind.lower() in {"sku", "prod", "mode", "day", "slot", "kind"}:
            return kind.lower(), rest.strip()
    return "", text


def choice_value(body: str) -> str:
    kind, rest = choice_kind(body)
    if kind in {"sku", "prod"}:
        return rest
    return (body or "").strip()


async def match_product(db: AsyncSession, body: str) -> Product | None:
    products = await inventory.list_catalog(db)
    text = choice_value(body)
    if not text:
        return None
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(products):
            return products[idx]
    lowered = text.lower()
    for product in products:
        if lowered == product.sku.lower() or lowered == product.name.lower():
            return product
        if lowered in product.name.lower():
            return product
    return None


async def build_items(db: AsyncSession) -> list[dict]:
    products = await inventory.list_catalog(db)
    items: list[dict] = []
    for idx, product in enumerate(products, start=1):
        stock = await inventory.available_stock(db, product.id)
        items.append(
            {
                "n": idx,
                "sku": product.sku,
                "name": product.name,
                "price": f"{product.price:.2f}",
                "currency": settings.currency,
                "stock": stock,
                "image": (product.image_url or "").strip(),
                "category": (product.category or "").strip(),
            }
        )
    return items


def public_media_url(url: str) -> str:
    raw = (url or "").strip()
    if raw.startswith("https://"):
        return raw
    base = (settings.public_base_url or "").rstrip("/")
    if raw.startswith("/uploads/") and base.startswith("https://"):
        return f"{base}{raw}"
    return ""


def _public_image_url(url: str) -> str:
    return public_media_url(url)


def local_upload_path(url: str) -> Path | None:
    raw = (url or "").strip()
    if not raw.startswith("/uploads/"):
        return None
    path = (UPLOADS_DIR / raw[len("/uploads/") :]).resolve()
    try:
        path.relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def _local_upload_path(url: str) -> Path | None:
    return local_upload_path(url)


async def _send_catalog_image(db: AsyncSession, phone: str, url: str, caption: str) -> None:
    link = (url or "").strip()
    try:
        local = _local_upload_path(link)
        if local:
            sent = await whatsapp.send_image(phone, local, caption, db=db)
            await inbox.record_bot_image(db, phone, local, caption, sent)
            return
        public = _public_image_url(link)
        if public:
            await whatsapp.send_image_link(phone, public, caption, db=db)
    except CloudError:
        return


def _catalog_text(header: str, items: list[dict]) -> str:
    lines = [header.strip() or "Este es el catálogo."]
    for item in items:
        lines.append(
            f"{item['n']}. {item['name']} — {item['price']} {item['currency']} (stock {item['stock']})"
        )
    lines.append("Escribí el número o el nombre del producto.")
    return "\n".join(lines)


def _truthy(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


async def present_catalog(
    db: AsyncSession, phone: str, config: dict | None = None
) -> list[str]:
    cfg = config or {}
    text = str(cfg.get("text") or "Este es el catálogo. Tocá un producto o escribí el número.")
    button = str(cfg.get("button") or "Ver productos")[:20]
    skus = [s.strip().lower() for s in str(cfg.get("skus") or "").split(",") if s.strip()]
    category = str(cfg.get("category") or "").strip()
    try:
        limit = max(1, min(50, int(cfg.get("limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    try:
        image_count = max(0, min(10, int(cfg.get("image_count") or 4)))
    except (TypeError, ValueError):
        image_count = 4
    send_list = _truthy(cfg.get("send_list"), True)
    cover = str(cfg.get("cover_url") or "").strip()

    items = await build_items(db)
    if skus:
        wanted = set(skus)
        items = [item for item in items if item["sku"].lower() in wanted]
    if category:
        items = [item for item in items if (item["category"] or "").lower() == category.lower()]
    items = items[:limit]

    if cover:
        await _send_catalog_image(db, phone, cover, text)

    if not send_list:
        if not cover:
            return ["Subí una foto de catálogo o activá la lista de productos."]
        return []

    if not items:
        return ["Todavía no hay productos cargados. Cargalos en Inventario o subí una foto de portada."]

    payload = {
        "text": text,
        "button": button,
        "items": items,
        "cover": cover,
    }
    await inbox.record_bot_rich(
        db, phone, "catalog", json.dumps(payload, ensure_ascii=False), preview="Catálogo"
    )
    rows = [
        {
            "id": f"sku:{item['sku']}"[:200],
            "title": clip(item["name"], 24),
            "description": clip(
                f"{item['price']} {item['currency']} · stock {item['stock']}", 72
            ),
        }
        for item in items[:10]
    ]
    catalog_text = _catalog_text(payload["text"], items)
    try:
        await whatsapp.send_interactive_list(
            phone,
            body=payload["text"],
            button=button,
            rows=rows,
            header="Catálogo",
            db=db,
        )
    except CloudError:
        pass
    shown = 0
    if not whatsapp.cloud_skipped():
        for item in items:
            if shown >= image_count:
                break
            image = item["image"]
            if not (_local_upload_path(image) or _public_image_url(image)):
                continue
            caption = f"{item['n']}. {item['name']} — {item['price']} {item['currency']}"
            await _send_catalog_image(db, phone, image, caption)
            shown += 1
    return [catalog_text]


async def present_product(db: AsyncSession, phone: str, product: Product, stock: int) -> list[str]:
    text = (
        f"{product.name} — {product.price:.2f} {settings.currency}. "
        f"Stock: {stock}. ¿Cuántas unidades querés?"
    )
    payload = {
        "text": text,
        "image": (product.image_url or "").strip(),
        "sku": product.sku,
        "buttons": [
            {"id": "1", "title": "1 unidad"},
            {"id": "2", "title": "2 unidades"},
            {"id": "3", "title": "3 unidades"},
        ],
    }
    await inbox.record_bot_rich(
        db,
        phone,
        "product_card",
        json.dumps(payload, ensure_ascii=False),
        preview=product.name,
    )
    image = payload["image"]
    if _local_upload_path(image) or _public_image_url(image):
        await _send_catalog_image(db, phone, image, text)
    else:
        try:
            await whatsapp.send_text(phone, text, db=db)
        except CloudError:
            return [text]
    try:
        await whatsapp.send_reply_buttons(
            phone, "Elegí cantidad o escribí el número.", payload["buttons"], db=db
        )
        return []
    except CloudError:
        return [text]


async def present_choices(
    db: AsyncSession,
    phone: str,
    *,
    text: str,
    items: list[dict],
    button: str = "Ver opciones",
    preview: str = "Opciones",
) -> list[str]:
    payload = {"text": text, "button": button, "items": items}
    await inbox.record_bot_rich(
        db, phone, "choices", json.dumps(payload, ensure_ascii=False), preview=preview
    )
    short = [
        {"id": str(item.get("id") or ""), "title": clip(str(item.get("title") or ""), 20)}
        for item in items
        if item.get("id") and item.get("title")
    ]
    fallback = text
    if items:
        extra = []
        for item in items[:10]:
            title = str(item.get("title") or "").strip()
            if title:
                extra.append(f"• {title}")
        if extra:
            fallback = text + "\n" + "\n".join(extra)
    try:
        if 0 < len(short) <= 3:
            await whatsapp.send_reply_buttons(phone, text, short, db=db)
        else:
            rows = [
                {
                    "id": str(item.get("id") or "")[:200],
                    "title": clip(str(item.get("title") or ""), 24),
                    "description": clip(str(item.get("description") or ""), 72),
                }
                for item in items[:10]
                if item.get("id") and item.get("title")
            ]
            await whatsapp.send_interactive_list(
                phone, body=text, button=button[:20], rows=rows, section="Opciones", db=db
            )
        return []
    except CloudError:
        return [fallback]
