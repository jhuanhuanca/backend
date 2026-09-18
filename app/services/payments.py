from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import qrcode

from app.config import QR_DIR, UPLOADS_DIR, get_settings
from app.models import Company

settings = get_settings()

PAY_FIELDS = (
    "bank_name",
    "bank_holder",
    "bank_account_type",
    "bank_account_number",
    "bank_id_doc",
    "pay_instructions",
    "pay_qr_path",
)


class PayInfo:
    """Datos de cobro: módulo Cobros, con campos del nodo si están llenos."""

    def __init__(self, **fields: str):
        for name in PAY_FIELDS:
            setattr(self, name, str(fields.get(name) or "").strip())


def _pick(override: dict | None, key: str, company: Company | None) -> str:
    raw = str((override or {}).get(key) or "").strip()
    if raw:
        return raw
    return str(getattr(company, key, "") or "").strip() if company else ""


def merge_pay_info(company: Company | None, override: dict | None = None) -> PayInfo:
    ov = override or {}
    qr = str(ov.get("pay_qr_path") or ov.get("qr_path") or ov.get("qr_url") or "").strip()
    if not qr and company:
        qr = str(company.pay_qr_path or "").strip()
    return PayInfo(
        bank_name=_pick(ov, "bank_name", company),
        bank_holder=_pick(ov, "bank_holder", company),
        bank_account_type=_pick(ov, "bank_account_type", company),
        bank_account_number=_pick(ov, "bank_account_number", company),
        bank_id_doc=_pick(ov, "bank_id_doc", company),
        pay_instructions=_pick(ov, "pay_instructions", company),
        pay_qr_path=qr,
    )


def resolve_qr_file(path: str | None) -> Path | None:
    raw = (path or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.exists():
        return candidate
    if raw.startswith("/uploads/"):
        rel = raw[len("/uploads/") :].lstrip("/")
        mapped = UPLOADS_DIR / rel
        if mapped.exists():
            return mapped
    return None


def build_qr_payload(order_code: str, amount: Decimal) -> str:
    return (
        f"PAGO {order_code}\n"
        f"Monto: {amount:.2f} {settings.currency}\n"
        f"Negocio WhatsApp: +{settings.business_whatsapp_e164.lstrip('+')}"
    )


def generate_qr_png(order_code: str, amount: Decimal) -> tuple[str, Path]:
    payload = build_qr_payload(order_code, amount)
    path = QR_DIR / f"{order_code}.png"
    img = qrcode.make(payload)
    img.save(path)
    return payload, path


def company_has_pay_setup(
    company: Company | PayInfo | None, override: dict | None = None
) -> bool:
    info = company if isinstance(company, PayInfo) else merge_pay_info(company, override)
    qr = resolve_qr_file(info.pay_qr_path)
    return bool(qr or info.bank_name or info.bank_account_number)


def pay_instructions_text(
    order_code: str,
    amount: Decimal,
    company: Company | PayInfo | None,
    override: dict | None = None,
) -> str:
    info = company if isinstance(company, PayInfo) else merge_pay_info(company, override)
    lines = [
        f"Monto a pagar: {amount:.2f} {settings.currency}",
        f"Referencia / pedido: {order_code}",
    ]
    if info.bank_name or info.bank_account_number or info.bank_holder:
        lines.append("Transferencia bancaria:")
        if info.bank_name:
            lines.append(f"Banco: {info.bank_name}")
        if info.bank_holder:
            lines.append(f"Titular: {info.bank_holder}")
        if info.bank_account_type:
            lines.append(f"Tipo de cuenta: {info.bank_account_type}")
        if info.bank_account_number:
            lines.append(f"N° de cuenta: {info.bank_account_number}")
        if info.bank_id_doc:
            lines.append(f"CI / NIT: {info.bank_id_doc}")
    if info.pay_instructions:
        lines.append(info.pay_instructions)
    qr = resolve_qr_file(info.pay_qr_path)
    if qr and (info.bank_name or info.bank_account_number):
        lines.append("Podés pagar con el QR de la imagen o por transferencia.")
    elif qr:
        lines.append("Pagá con el QR de la imagen (cargá el monto de arriba).")
    elif info.bank_name or info.bank_account_number:
        lines.append("Hacé la transferencia con esos datos.")
    else:
        lines.append("Pagá el QR y enviá el comprobante por este chat.")
    lines.append("Después enviá la foto del comprobante por este chat.")
    return "\n".join(lines)


def prepare_payment_assets(
    order_code: str,
    amount: Decimal,
    company: Company | None = None,
    override: dict | None = None,
) -> tuple[str, Path | None, str]:
    """Devuelve texto para el cliente, imagen del QR (si hay) y método."""
    info = merge_pay_info(company, override)
    text = pay_instructions_text(order_code, amount, info)
    dest = QR_DIR / f"{order_code}.png"
    src = resolve_qr_file(info.pay_qr_path)
    if src:
        QR_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return text, dest, "qr_banco"
    if company_has_pay_setup(info):
        return text, None, "transferencia"
    payload, path = generate_qr_png(order_code, amount)
    return f"{text}\n{payload}", path, "qr_simple"
