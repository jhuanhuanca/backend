from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Company, Product
from app.services import inventory, whatsapp
from app.services.product_specs import normalize_gallery, normalize_options, normalize_specs

router = APIRouter(tags=["tienda-publica"])
settings = get_settings()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def money(value) -> str:
    amount = Decimal(str(value or 0))
    if amount == amount.to_integral_value():
        return f"{int(amount)}"
    return f"{amount:.2f}"


def pretty_phone(raw: str) -> str:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if digits.startswith("591") and len(digits) >= 11:
        return f"+591 {digits[3:6]} {digits[6:]}"
    if digits:
        return "+" + digits
    return ""


def tag_list(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


templates.env.filters["money"] = money

CATEGORY_TONES = ("sky", "sand", "lime", "lilac", "rose", "mint")


async def load_store(db: AsyncSession, slug: str) -> dict:
    company = await db.scalar(select(Company).where(Company.slug == slug))
    if not company or not company.store_enabled:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")
    products = list(
        await db.scalars(
            select(Product)
            .where(Product.active.is_(True), Product.company_id == company.id)
            .order_by(Product.name)
        )
    )
    if not products and company.is_default:
        products = await inventory.list_catalog(db, company.id)
    stocks = {p.id: await inventory.available_stock(db, p.id) for p in products}
    categories: list[str] = []
    seen: set[str] = set()
    for product in products:
        label = (product.category or "").strip() or "Catálogo"
        if label not in seen:
            seen.add(label)
            categories.append(label)
    creds = await whatsapp.load_creds(db, company.id)
    wa = (creds.business_e164 or settings.business_whatsapp_e164).lstrip("+")
    return {
        "company": company,
        "products": products,
        "stocks": stocks,
        "categories": categories,
        "category_tones": CATEGORY_TONES,
        "currency": settings.currency,
        "wa_e164": wa,
        "wa_display": pretty_phone(wa),
        "public_url": settings.public_base_url.rstrip("/") + f"/t/{company.slug}",
    }


def fallback_specs(product: Product, stock: int) -> list[dict[str, str]]:
    rows = [
        {"label": "SKU", "value": product.sku},
        {"label": "Categoría", "value": (product.category or "Catálogo").strip() or "Catálogo"},
    ]
    if product.brand:
        rows.insert(0, {"label": "Marca", "value": product.brand})
    rows.append({"label": "Stock", "value": str(stock) if stock > 0 else "Consultar"})
    return rows


@router.get("/t/{slug}", response_class=HTMLResponse)
async def public_store(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    ctx = await load_store(db, slug)
    return templates.TemplateResponse(request, "shop.html", ctx)


@router.get("/t/{slug}/p/{sku}", response_class=HTMLResponse)
async def public_product(
    request: Request, slug: str, sku: str, db: AsyncSession = Depends(get_db)
):
    ctx = await load_store(db, slug)
    wanted = sku.strip().upper()
    product = next((p for p in ctx["products"] if p.sku.upper() == wanted), None)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    catalog = ctx["products"]
    idx = next(i for i, p in enumerate(catalog) if p.id == product.id)
    prev_p = catalog[idx - 1] if idx > 0 else None
    next_p = catalog[idx + 1] if idx + 1 < len(catalog) else None
    stock = ctx["stocks"].get(product.id, product.stock)
    specs = normalize_specs(product.specs) or fallback_specs(product, stock)
    gallery = normalize_gallery(product.gallery, product.image_url)
    options = normalize_options(product.options)
    ctx.update(
        {
            "product": product,
            "stock": stock,
            "specs": specs,
            "gallery": gallery,
            "options": options,
            "tags": tag_list(product.tags),
            "prev_p": prev_p,
            "next_p": next_p,
            "cat_label": (product.category or "").strip() or "Catálogo",
        }
    )
    return templates.TemplateResponse(request, "shop_product.html", ctx)
