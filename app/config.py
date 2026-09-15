from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
QR_DIR = UPLOADS_DIR / "qr"
PROOFS_DIR = UPLOADS_DIR / "proofs"
MEDIA_DIR = UPLOADS_DIR / "whatsapp"
CATALOG_DIR = UPLOADS_DIR / "catalog"
PAY_DIR = UPLOADS_DIR / "pay"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Vendedor Live"
    secret_key: str = "dev-secret-change-me"
    database_url: str = f"sqlite+aiosqlite:///{(DATA_DIR / 'app.db').as_posix()}"

    farm_api_key: str = "farm-dev-key"

    dashboard_user: str = "admin"
    dashboard_password: str = "admin"

    business_whatsapp_e164: str = "59176364961"

    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = "verify-dev"
    whatsapp_app_secret: str = ""
    whatsapp_skip_signature: bool = True
    whatsapp_graph_version: str = "v21.0"

    reservation_minutes: int = 10
    currency: str = "BOB"
    public_base_url: str = "http://127.0.0.1:8000"

    motor_ia_url: str = "http://127.0.0.1:8010"
    motor_ia_api_key: str = "dev-motor-ia-key-change-me"
    motor_ia_timeout: float = 20.0

    app_env: str = "local"
    allowed_hosts: str = "*"

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    @property
    def host_list(self) -> list[str]:
        raw = (self.allowed_hosts or "*").strip()
        if raw == "*":
            return ["*"]
        return [h.strip() for h in raw.split(",") if h.strip()]


@lru_cache
def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    QR_DIR.mkdir(parents=True, exist_ok=True)
    PROOFS_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    PAY_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()
