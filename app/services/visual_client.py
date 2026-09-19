from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings

settings = get_settings()


def _headers() -> dict[str, str]:
    return {"X-API-Key": settings.motor_visual_api_key}


async def health() -> dict[str, Any] | None:
    url = settings.motor_visual_url.rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except Exception:
        return None


async def index_catalog(tenant_id: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    url = settings.motor_visual_url.rstrip("/") + "/v1/index"
    try:
        async with httpx.AsyncClient(timeout=max(30.0, settings.motor_visual_timeout)) as client:
            response = await client.post(
                url,
                json={"tenant_id": tenant_id, "items": items},
                headers=_headers(),
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        return None


async def match_image(
    tenant_id: str,
    path: str,
    *,
    top_k: int = 3,
    threshold: float | None = None,
    unsure: float | None = None,
) -> dict[str, Any] | None:
    url = settings.motor_visual_url.rstrip("/") + "/v1/match"
    payload = {
        "tenant_id": tenant_id,
        "path": path,
        "top_k": top_k,
        "threshold": threshold,
        "unsure": unsure,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.motor_visual_timeout) as client:
            response = await client.post(url, json=payload, headers=_headers())
            response.raise_for_status()
            return response.json()
    except Exception:
        return None
