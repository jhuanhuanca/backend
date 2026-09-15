from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import bot, flow_engine


async def run_conversation(
    db: AsyncSession,
    *,
    phone: str,
    name: str,
    text: str | None = None,
    image_media_id: str | None = None,
) -> list[str]:
    published = await flow_engine.published_flow(db)
    if published:
        return await flow_engine.handle_incoming(
            db,
            phone=phone,
            name=name,
            text=text,
            image_media_id=image_media_id,
            flow=published,
        )
    return await bot.handle_incoming(
        db, phone=phone, name=name, text=text, image_media_id=image_media_id
    )


async def engine_label(db: AsyncSession) -> str:
    published = await flow_engine.published_flow(db)
    if published:
        return f"Flujo publicado: {published.name}"
    return "Bot de código (catálogo / pedido)"
