from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings

settings = get_settings()


async def decide(
    *,
    conversation_id: str,
    user_message: str,
    knowledge: list[dict[str, Any]],
    transitions: list[str],
    node: dict[str, Any],
    lead_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    url = settings.motor_ia_url.rstrip("/") + "/v1/decide"
    payload = {
        "tenant_id": "shop",
        "conversation_id": conversation_id,
        "user_message": user_message,
        "allowed_knowledge": knowledge,
        "available_transitions": transitions,
        "current_node": node,
        "lead_context": lead_context or {},
    }
    try:
        async with httpx.AsyncClient(timeout=settings.motor_ia_timeout) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"X-API-Key": settings.motor_ia_api_key},
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        return None


async def classify(user_message: str, labels: list[str] | None = None) -> dict[str, Any] | None:
    url = settings.motor_ia_url.rstrip("/") + "/v1/classify"
    try:
        async with httpx.AsyncClient(timeout=settings.motor_ia_timeout) as client:
            response = await client.post(
                url,
                json={
                    "tenant_id": "shop",
                    "user_message": user_message,
                    "labels": labels or [],
                },
                headers={"X-API-Key": settings.motor_ia_api_key},
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        return None


async def health() -> dict[str, Any] | None:
    url = settings.motor_ia_url.rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except Exception:
        return None
