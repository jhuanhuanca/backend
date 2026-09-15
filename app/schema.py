"""Parches SQLite para columnas nuevas (create_all no altera tablas existentes)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection


def apply_schema_patches(conn: Connection) -> None:
    if conn.dialect.name != "sqlite":
        return
    apply_sqlite_patches(conn)


def apply_sqlite_patches(conn: Connection) -> None:
    _add_column(conn, "companies", "store_enabled", "BOOLEAN DEFAULT 1")
    _add_column(conn, "companies", "store_tagline", "VARCHAR(240) DEFAULT ''")
    _add_column(conn, "companies", "pay_qr_path", "VARCHAR(500) DEFAULT ''")
    _add_column(conn, "companies", "bank_name", "VARCHAR(120) DEFAULT ''")
    _add_column(conn, "companies", "bank_holder", "VARCHAR(160) DEFAULT ''")
    _add_column(conn, "companies", "bank_account_type", "VARCHAR(80) DEFAULT ''")
    _add_column(conn, "companies", "bank_account_number", "VARCHAR(80) DEFAULT ''")
    _add_column(conn, "companies", "bank_id_doc", "VARCHAR(80) DEFAULT ''")
    _add_column(conn, "companies", "pay_instructions", "TEXT DEFAULT ''")
    _add_column(conn, "products", "company_id", "VARCHAR(36)")
    _add_column(conn, "products", "image_url", "VARCHAR(500) DEFAULT ''")
    _add_column(conn, "products", "category", "VARCHAR(80) DEFAULT ''")
    _add_column(conn, "products", "brand", "VARCHAR(80) DEFAULT ''")
    _add_column(conn, "products", "tags", "VARCHAR(240) DEFAULT ''")
    _add_column(conn, "products", "specs", "TEXT DEFAULT '[]'")
    _add_column(conn, "products", "gallery", "TEXT DEFAULT '[]'")
    _add_column(conn, "products", "options", "TEXT DEFAULT '{}'")
    default_id = conn.execute(
        text("SELECT id FROM companies WHERE is_default = 1 LIMIT 1")
    ).scalar()
    if not default_id:
        default_id = conn.execute(text("SELECT id FROM companies LIMIT 1")).scalar()
    if default_id:
        conn.execute(
            text(
                "UPDATE products SET company_id = :cid WHERE company_id IS NULL OR company_id = ''"
            ),
            {"cid": default_id},
        )
    conn.execute(text("UPDATE companies SET store_enabled = 1 WHERE store_enabled IS NULL"))


def _add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    names = {row[1] for row in rows}
    if column in names or not names:
        return
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
