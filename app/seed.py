from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotFlow, Company, Product, WhatsAppAccount
from app.services.flow_definition import (
    default_sale_definition,
    leads_catalog_definition,
    sale_catalog_definition,
)
from app.config import get_settings
from app.services.product_specs import normalize_specs
from app.services.secrets import encrypt_secret


SEED_PRODUCTS = [
    {
        "sku": "POL-NEG-M",
        "name": "Polera negra M",
        "description": "Polera básica de algodón para el live. Corte clásico, cómoda para el día a día y fácil de combinar.",
        "price": Decimal("80.00"),
        "stock": 20,
        "category": "Ropa",
        "brand": "Live",
        "tags": "algodón, live, básica",
        "specs": [
            {"label": "Talla", "value": "M"},
            {"label": "Color", "value": "Negro"},
            {"label": "Material", "value": "Algodón"},
            {"label": "Uso", "value": "Live / diario"},
            {"label": "Año", "value": "2026"},
        ],
        "options": {"Color": ["Negro", "Blanco"], "Talla": ["S", "M", "L"]},
    },
    {
        "sku": "GOR-UNI",
        "name": "Gorra unisex",
        "description": "Gorra ajustable unisex, visera curva. Ideal para salida y para mostrar en cámara.",
        "price": Decimal("50.00"),
        "stock": 15,
        "category": "Accesorios",
        "brand": "Live",
        "tags": "gorra, unisex, ajustable",
        "specs": [
            {"label": "Talla", "value": "Única"},
            {"label": "Color", "value": "Negro"},
            {"label": "Ajuste", "value": "Hebilla"},
            {"label": "Material", "value": "Algodón / poliéster"},
            {"label": "Año", "value": "2026"},
        ],
        "options": {"Color": ["Negro", "Beige"]},
    },
    {
        "sku": "RIÑ-NEG",
        "name": "Riñonera negra",
        "description": "Riñonera para delivery o salida. Bolsillo frontal y correa ajustable.",
        "price": Decimal("90.00"),
        "stock": 10,
        "category": "Accesorios",
        "brand": "Live",
        "tags": "riñonera, delivery, salida",
        "specs": [
            {"label": "Color", "value": "Negro"},
            {"label": "Material", "value": "Nylon"},
            {"label": "Cierre", "value": "Cierre metálico"},
            {"label": "Uso", "value": "Delivery / salida"},
            {"label": "Año", "value": "2026"},
        ],
        "options": {"Color": ["Negro", "Gris"]},
    },
]


async def seed_if_empty(db: AsyncSession) -> None:
    exists = await db.scalar(select(Product.id).limit(1))
    if not exists:
        for item in SEED_PRODUCTS:
            db.add(Product(**item))
        await db.commit()
    has_flow = await db.scalar(select(BotFlow.id).limit(1))
    if not has_flow:
        db.add(
            BotFlow(
                name="Venta WhatsApp",
                description="Plantilla. Publicá desde Flow Studio para reemplazar el bot de código.",
                status="draft",
                is_default=False,
                definition=default_sale_definition(),
            )
        )
        await db.commit()
    await ensure_sample_flows(db)
    has_company = await db.scalar(select(Company.id).limit(1))
    if not has_company:
        settings = get_settings()
        company = Company(
            name="Mi tienda",
            slug="mi-tienda",
            is_default=True,
            store_enabled=True,
            store_tagline="Ropa del live · envíos en Bolivia",
        )
        db.add(company)
        await db.flush()
        db.add(
            WhatsAppAccount(
                company_id=company.id,
                label="Principal",
                business_e164=settings.business_whatsapp_e164 or "",
                phone_number_id=settings.whatsapp_phone_number_id or "",
                token_enc=encrypt_secret(settings.whatsapp_token or ""),
                app_secret_enc=encrypt_secret(settings.whatsapp_app_secret or ""),
                verify_token_enc=encrypt_secret(settings.whatsapp_verify_token or ""),
                graph_version=settings.whatsapp_graph_version or "v21.0",
                skip_signature=settings.whatsapp_skip_signature,
                is_active=True,
            )
        )
        await db.commit()
    company = await db.scalar(select(Company).where(Company.is_default.is_(True)))
    if not company:
        company = await db.scalar(select(Company).limit(1))
    if company:
        orphans = list(await db.scalars(select(Product).where(Product.company_id.is_(None))))
        for product in orphans:
            product.company_id = company.id
        if orphans:
            await db.commit()
    sku_cats = {"POL-NEG-M": "Ropa", "GOR-UNI": "Accesorios", "RIÑ-NEG": "Accesorios"}
    seed_by_sku = {item["sku"]: item for item in SEED_PRODUCTS}
    catalog = list(await db.scalars(select(Product)))
    tagged = False
    for product in catalog:
        extra = seed_by_sku.get(product.sku)
        if not (product.category or "").strip() and product.sku in sku_cats:
            product.category = sku_cats[product.sku]
            tagged = True
        if extra and not normalize_specs(product.specs):
            product.specs = extra.get("specs") or []
            product.options = extra.get("options") or {}
            product.brand = extra.get("brand") or product.brand
            product.tags = extra.get("tags") or product.tags
            if extra.get("description"):
                product.description = extra["description"]
            tagged = True
    if tagged:
        await db.commit()


SAMPLE_FLOWS = (
    {
        "name": "Muestra · Venta catálogo",
        "description": "[muestra] Catálogo completo, envío local u otro departamento, reunión, adelanto 50% + envío y confirmación de fecha/hora.",
        "builder": sale_catalog_definition,
    },
    {
        "name": "Muestra · Leads + catálogo",
        "description": "[muestra] Menú: comprar del catálogo (mismo flujo de venta) o inscribirse / más info con reunión presencial o virtual.",
        "builder": leads_catalog_definition,
    },
)


async def ensure_sample_flows(db: AsyncSession) -> None:
    changed = False
    for item in SAMPLE_FLOWS:
        row = await db.scalar(select(BotFlow).where(BotFlow.name == item["name"]))
        definition = item["builder"]()
        if row:
            row.definition = definition
            row.description = item["description"]
        else:
            db.add(
                BotFlow(
                    name=item["name"],
                    description=item["description"],
                    status="draft",
                    is_default=False,
                    definition=definition,
                )
            )
        changed = True
    if changed:
        await db.commit()
