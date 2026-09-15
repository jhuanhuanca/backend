from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import qrcode

from app.config import QR_DIR, get_settings
from app.models import Company

settings = get_settings()


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


def company_has_pay_setup(company: Company | None) -> bool:
    if not company:
        return False
    qr = Path(company.pay_qr_path) if company.pay_qr_path else None
    return bool((qr and qr.exists()) or company.bank_name or company.bank_account_number)


def pay_instructions_text(order_code: str, amount: Decimal, company: Company | None) -> str:
    lines = [
        f"Monto a pagar: {amount:.2f} {settings.currency}",
        f"Referencia / pedido: {order_code}",
    ]
    if company and (company.bank_name or company.bank_account_number or company.bank_holder):
        lines.append("Transferencia bancaria:")
        if company.bank_name:
            lines.append(f"Banco: {company.bank_name}")
        if company.bank_holder:
            lines.append(f"Titular: {company.bank_holder}")
        if company.bank_account_type:
            lines.append(f"Tipo de cuenta: {company.bank_account_type}")
        if company.bank_account_number:
            lines.append(f"N° de cuenta: {company.bank_account_number}")
        if company.bank_id_doc:
            lines.append(f"CI / NIT: {company.bank_id_doc}")
    if company and (company.pay_instructions or "").strip():
        lines.append(company.pay_instructions.strip())
    qr = Path(company.pay_qr_path) if company and company.pay_qr_path else None
    if qr and qr.exists() and (company.bank_name or company.bank_account_number):
        lines.append("Podés pagar con el QR de la imagen o por transferencia.")
    elif qr and qr.exists():
        lines.append("Pagá con el QR de la imagen (cargá el monto de arriba).")
    elif company and (company.bank_name or company.bank_account_number):
        lines.append("Hacé la transferencia con esos datos.")
    else:
        lines.append("Pagá el QR y enviá el comprobante por este chat.")
    lines.append("Después enviá la foto del comprobante por este chat.")
    return "\n".join(lines)


def prepare_payment_assets(
    order_code: str, amount: Decimal, company: Company | None = None
) -> tuple[str, Path | None, str]:
    """Devuelve texto para el cliente, imagen del QR (si hay) y método."""
    text = pay_instructions_text(order_code, amount, company)
    dest = QR_DIR / f"{order_code}.png"
    src = Path(company.pay_qr_path) if company and company.pay_qr_path else None
    if src and src.exists():
        shutil.copyfile(src, dest)
        return text, dest, "qr_banco"
    if company_has_pay_setup(company):
        return text, None, "transferencia"
    payload, path = generate_qr_png(order_code, amount)
    return f"{text}\n{payload}", path, "qr_simple"
