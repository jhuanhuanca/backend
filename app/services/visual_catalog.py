"""Arma el índice visual del inventario de la empresa activa."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CATALOG_DIR, get_settings
from app.models import Product
from app.services import inventory, tenancy, visual_client, wa_catalog
from app.services.product_specs import normalize_gallery

log = logging.getLogger(__name__)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_IMAGE_SUFFIX = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _image_urls(product: Product) -> list[str]:
    return normalize_gallery(product.gallery, product.image_url)[:6]


def _looks_like_image(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


def _suffix_from(url: str, content_type: str = "") -> str:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    by_mime = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    if mime in by_mime:
        return by_mime[mime]
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in _IMAGE_SUFFIX:
        return suffix
    return ".jpg"


def _direct_image_url(url: str) -> str:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query)
    if host in {"drive.google.com", "docs.google.com"}:
        file_id = ""
        parts = [p for p in parsed.path.split("/") if p]
        if "d" in parts:
            idx = parts.index("d")
            if idx + 1 < len(parts):
                file_id = parts[idx + 1]
        file_id = file_id or (query.get("id") or [""])[0]
        if file_id:
            return f"https://drive.google.com/uc?export=download&id={file_id}"
    if "dropbox.com" in host:
        if "dl=0" in raw:
            return raw.replace("dl=0", "dl=1")
        if "raw=1" not in raw and "dl=1" not in raw:
            sep = "&" if parsed.query else "?"
            return raw + sep + "raw=1"
    return raw


async def _download(url: str) -> tuple[bytes, str] | None:
    headers = {"User-Agent": _UA, "Accept": "image/*,*/*;q=0.8"}
    last_error = None
    for verify in (True, False):
        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True, verify=verify, headers=headers
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.content, response.headers.get("content-type") or ""
        except httpx.HTTPError as exc:
            last_error = exc
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            break
    log.warning("No pude bajar la foto %s: %s", url, last_error)
    return None


async def materialize_image(url: str) -> Path | None:
    local = wa_catalog.local_upload_path(url)
    if local:
        return local
    raw = (url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return None
    target = _direct_image_url(raw)
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    dest = CATALOG_DIR / f"idx-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}.bin"
    if dest.is_file() and dest.stat().st_size > 32:
        with dest.open("rb") as handle:
            head = handle.read(32)
        if _looks_like_image(head):
            return dest
    cached = next(
        (
            hit
            for hit in dest.parent.glob(dest.stem + ".*")
            if hit.is_file() and hit.stat().st_size > 32
        ),
        None,
    )
    if cached:
        with cached.open("rb") as handle:
            if _looks_like_image(handle.read(32)):
                return cached
    fetched = await _download(target)
    if not fetched:
        return None
    body, content_type = fetched
    if not _looks_like_image(body):
        log.warning("El link no era una foto (HTML u otro archivo): %s", raw)
        return None
    suffix = _suffix_from(target, content_type)
    dest = dest.with_suffix(suffix)
    dest.write_bytes(body)
    return dest if dest.stat().st_size > 0 else None


async def _materialize(url: str) -> Path | None:
    return await materialize_image(url)


async def catalog_items(db: AsyncSession) -> list[dict]:
    products = await inventory.list_catalog(db)
    items: list[dict] = []
    missing = 0
    for product in products:
        urls = _image_urls(product)
        if not urls:
            continue
        got = 0
        for idx, url in enumerate(urls):
            path = await materialize_image(url)
            if not path:
                missing += 1
                continue
            got += 1
            items.append(
                {
                    "id": f"{product.id}:{idx}",
                    "product_id": product.id,
                    "path": str(path),
                    "sku": product.sku,
                    "name": product.name,
                }
            )
        if urls and not got:
            log.warning("Producto %s sin foto usable: %s", product.sku, urls[0])
    if missing:
        log.warning("Fotos de catálogo no indexadas: %s", missing)
    return items


async def reindex_current(db: AsyncSession) -> dict | None:
    cid = await tenancy.resolve_company_id(db)
    if not cid:
        return None
    items = await catalog_items(db)
    if not items:
        log.warning("motor-visual: el catálogo no tiene fotos locales ni links descargables")
    result = await visual_client.index_catalog(cid, items)
    if result is None:
        log.warning("motor-visual no indexó (¿está apagado? url=%s)", get_settings().motor_visual_url)
    else:
        log.info("motor-visual indexó %s fotos", result.get("indexed"))
    return result
