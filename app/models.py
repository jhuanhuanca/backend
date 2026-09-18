from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    store_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    store_tagline: Mapped[str] = mapped_column(String(240), default="")
    pay_qr_path: Mapped[str] = mapped_column(String(500), default="")
    bank_name: Mapped[str] = mapped_column(String(120), default="")
    bank_holder: Mapped[str] = mapped_column(String(160), default="")
    bank_account_type: Mapped[str] = mapped_column(String(80), default="")
    bank_account_number: Mapped[str] = mapped_column(String(80), default="")
    bank_id_doc: Mapped[str] = mapped_column(String(80), default="")
    pay_instructions: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    whatsapp_accounts: Mapped[list["WhatsAppAccount"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class WhatsAppAccount(Base):
    __tablename__ = "whatsapp_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    label: Mapped[str] = mapped_column(String(120), default="Principal")
    business_e164: Mapped[str] = mapped_column(String(32), default="")
    phone_number_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    waba_id: Mapped[str] = mapped_column(String(64), default="")
    token_enc: Mapped[str] = mapped_column(Text, default="")
    app_secret_enc: Mapped[str] = mapped_column(Text, default="")
    verify_token_enc: Mapped[str] = mapped_column(Text, default="")
    graph_version: Mapped[str] = mapped_column(String(16), default="v21.0")
    skip_signature: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    company: Mapped[Company] = relationship(back_populates="whatsapp_accounts")


class User(Base):
    """Login del dashboard: dueño (superadmin) o vendedor de una empresa."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(24), default="vendor", index=True)
    company_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    totp_secret_enc: Mapped[str] = mapped_column(Text, default="")
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    company: Mapped[Optional[Company]] = relationship()


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("company_id", "phone"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    company_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    phone: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    image_url: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(80), default="")
    brand: Mapped[str] = mapped_column(String(80), default="")
    tags: Mapped[str] = mapped_column(String(240), default="")
    specs: Mapped[list] = mapped_column(JSON, default=list)
    gallery: Mapped[list] = mapped_column(JSON, default=list)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    company_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    variants: Mapped[list["ProductVariant"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    sku: Mapped[str] = mapped_column(String(80), unique=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    price_override: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    stock: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped[Product] = relationship(back_populates="variants")


class FarmDevice(Base):
    __tablename__ = "farm_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    serial: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(40), default="unknown")
    model: Mapped[str] = mapped_column(String(120), default="")
    product: Mapped[str] = mapped_column(String(120), default="")
    tiktok_username: Mapped[str] = mapped_column(String(120), default="")
    scrcpy_active: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class LiveSession(Base):
    __tablename__ = "live_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    company_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    public_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    tiktok_username: Mapped[str] = mapped_column(String(120), default="")
    device_serial: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    product_sku: Mapped[str] = mapped_column(String(64), default="")
    capture_url: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(24), default="manual")
    status: Mapped[str] = mapped_column(String(24), default="live")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    orders: Mapped[list["Order"]] = relationship(back_populates="session")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    company_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    public_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    session_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("live_sessions.id"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(24), default="whatsapp")
    status: Mapped[str] = mapped_column(String(32), default="pendiente_pago", index=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    delivery_type: Mapped[str] = mapped_column(String(32), default="to_coordinate")
    delivery_address: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    customer: Mapped[Customer] = relationship(back_populates="orders")
    session: Mapped[Optional[LiveSession]] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    reservations: Mapped[list["InventoryReservation"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    delivery: Mapped[Optional["Delivery"]] = relationship(
        back_populates="order", uselist=False, cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    variant_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("product_variants.id"), nullable=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    product_name: Mapped[str] = mapped_column(String(160), default="")

    order: Mapped[Order] = relationship(back_populates="items")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    method: Mapped[str] = mapped_column(String(32), default="qr_simple")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    qr_payload: Mapped[str] = mapped_column(Text, default="")
    qr_image_path: Mapped[str] = mapped_column(String(500), default="")
    proof_image_path: Mapped[str] = mapped_column(String(500), default="")
    confirmed_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="payments")


class InventoryReservation(Base):
    __tablename__ = "inventory_reservations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    variant_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("product_variants.id"), nullable=True
    )
    order_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("orders.id"), nullable=True, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(24), default="active")

    order: Mapped[Optional[Order]] = relationship(back_populates="reservations")


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(32), default="to_coordinate")
    window_label: Mapped[str] = mapped_column(String(160), default="")
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    address: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    order: Mapped[Order] = relationship(back_populates="delivery")


class Appointment(Base):
    """Reunión de inscripción/presentación o cita ligada a un pedido."""

    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    company_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    phone: Mapped[str] = mapped_column(String(32), index=True)
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    purpose: Mapped[str] = mapped_column(String(32), default="lead", index=True)
    mode: Mapped[str] = mapped_column(String(32), default="meeting")
    meeting_kind: Mapped[str] = mapped_column(String(32), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    window_label: Mapped[str] = mapped_column(String(160), default="")
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    address: Mapped[str] = mapped_column(Text, default="")
    meeting_link: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    order_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="scheduled", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ConversationState(Base):
    __tablename__ = "conversation_states"
    __table_args__ = (UniqueConstraint("company_id", "phone"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    company_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    step: Mapped[str] = mapped_column(String(40), default="idle")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    live_session_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcessedWebhook(Base):
    __tablename__ = "processed_webhooks"
    __table_args__ = (UniqueConstraint("provider", "provider_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    provider: Mapped[str] = mapped_column(String(32))
    provider_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("company_id", "phone"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    company_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    phone: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    last_preview: Mapped[str] = mapped_column(String(240), default="")
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    bot_paused: Mapped[bool] = mapped_column(Boolean, default=False)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    direction: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(16), default="customer")
    msg_type: Mapped[str] = mapped_column(String(24), default="text")
    body: Mapped[str] = mapped_column(Text, default="")
    provider_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    media_path: Mapped[str] = mapped_column(String(500), default="")
    mime_type: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class BotFlow(Base):
    __tablename__ = "bot_flows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    company_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
