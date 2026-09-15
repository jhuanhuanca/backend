from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import __version__
from app.config import DATA_DIR, UPLOADS_DIR, get_settings
from app.database import Base, SessionLocal, engine
from app.schema import apply_schema_patches
from app.seed import seed_if_empty

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_schema_patches)
    async with SessionLocal() as db:
        await seed_if_empty(db)
    yield


docs = None if settings.is_production else "/docs"
app = FastAPI(
    title=settings.app_name,
    version=__version__,
    lifespan=lifespan,
    description="Ventas en vivo: WhatsApp Hub, Flow Studio, pedidos, inventario, granja.",
    docs_url=docs,
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="vl_session",
    https_only=settings.is_production,
    same_site="lax",
)
if settings.host_list != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.host_list)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

from app.api.farm import router as farm_router  # noqa: E402
from app.api.whatsapp import router as whatsapp_router  # noqa: E402
from app.web import router as web_router  # noqa: E402
from app.web_whatsapp import router as whatsapp_hub_router  # noqa: E402
from app.web_flows import router as flows_router  # noqa: E402
from app.web_shop import router as shop_router  # noqa: E402

app.include_router(farm_router)
app.include_router(whatsapp_router)
app.include_router(web_router)
app.include_router(whatsapp_hub_router)
app.include_router(flows_router)
app.include_router(shop_router)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")


@app.get("/health")
async def health():
    return {"ok": True, "version": __version__, "app": settings.app_name}
