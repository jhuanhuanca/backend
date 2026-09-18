"""Parches para columnas nuevas (create_all no altera tablas existentes)."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

TENANT_TABLES = (
    "products",
    "orders",
    "appointments",
    "bot_flows",
    "live_sessions",
    "customers",
    "conversations",
)


def apply_schema_patches(conn: Connection) -> None:
    dialect = conn.dialect.name
    if dialect == "sqlite":
        apply_sqlite_patches(conn)
    elif dialect == "postgresql":
        apply_postgres_patches(conn)
    elif dialect in {"mysql", "mariadb"}:
        apply_mysql_patches(conn)


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
    _add_column(conn, "orders", "company_id", "VARCHAR(36)")
    _add_column(conn, "appointments", "company_id", "VARCHAR(36)")
    _add_column(conn, "bot_flows", "company_id", "VARCHAR(36)")
    _add_column(conn, "live_sessions", "company_id", "VARCHAR(36)")
    _add_column(conn, "customers", "company_id", "VARCHAR(36)")
    _add_column(conn, "conversations", "company_id", "VARCHAR(36)")
    default_id = _default_company_id(conn)
    if default_id:
        for table in TENANT_TABLES:
            if _has_column(conn, table, "company_id"):
                conn.execute(
                    text(
                        f"UPDATE {table} SET company_id = :cid WHERE company_id IS NULL OR company_id = ''"
                    ),
                    {"cid": default_id},
                )
        conn.execute(text("UPDATE companies SET store_enabled = 1 WHERE store_enabled IS NULL"))
    _rebuild_conversation_states(conn, default_id or "")
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        _rebuild_unique_company_phone(conn, "conversations", default_id or "")
        _rebuild_unique_company_phone(conn, "customers", default_id or "")
    finally:
        conn.execute(text("PRAGMA foreign_keys=ON"))


def _default_company_id(conn: Connection) -> str | None:
    if not _table_exists(conn, "companies"):
        return None
    default_id = conn.execute(
        text("SELECT id FROM companies WHERE is_default = 1 LIMIT 1")
    ).scalar()
    if default_id:
        return str(default_id)
    any_id = conn.execute(text("SELECT id FROM companies LIMIT 1")).scalar()
    return str(any_id) if any_id else None


def _table_exists(conn: Connection, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name = :n"),
        {"n": table},
    ).scalar()
    return bool(row)


def _has_column(conn: Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return column in {row[1] for row in rows}


def _add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    names = {row[1] for row in rows}
    if column in names or not names:
        return
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _rebuild_conversation_states(conn: Connection, default_id: str) -> None:
    if not _table_exists(conn, "conversation_states"):
        return
    cols = {row[1] for row in conn.execute(text("PRAGMA table_info(conversation_states)")).fetchall()}
    if "id" in cols and "company_id" in cols:
        if default_id:
            conn.execute(
                text(
                    "UPDATE conversation_states SET company_id = :cid "
                    "WHERE company_id IS NULL OR company_id = ''"
                ),
                {"cid": default_id},
            )
        return
    conn.execute(
        text(
            """
            CREATE TABLE conversation_states_new (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                company_id VARCHAR(36) NOT NULL DEFAULT '',
                phone VARCHAR(32) NOT NULL,
                step VARCHAR(40) DEFAULT 'idle',
                data TEXT DEFAULT '{}',
                live_session_id VARCHAR(36),
                updated_at DATETIME,
                UNIQUE (company_id, phone)
            )
            """
        )
    )
    rows = conn.execute(
        text(
            "SELECT phone, step, data, live_session_id, updated_at FROM conversation_states"
        )
    ).fetchall()
    for row in rows:
        conn.execute(
            text(
                """
                INSERT INTO conversation_states_new
                    (id, company_id, phone, step, data, live_session_id, updated_at)
                VALUES (:id, :cid, :phone, :step, :data, :live, :updated)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "cid": default_id,
                "phone": row[0],
                "step": row[1],
                "data": row[2],
                "live": row[3],
                "updated": row[4],
            },
        )
    conn.execute(text("DROP TABLE conversation_states"))
    conn.execute(text("ALTER TABLE conversation_states_new RENAME TO conversation_states"))


def _rebuild_unique_company_phone(conn: Connection, table: str, default_id: str) -> None:
    if not _table_exists(conn, table):
        return
    sql = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name = :n"),
        {"n": table},
    ).scalar() or ""
    lowered = sql.lower().replace(" ", "")
    if "unique(company_id,phone)" in lowered or "unique(company_id, phone)" in sql.lower():
        return
    if table == "conversations":
        _rebuild_conversations(conn, default_id)
    elif table == "customers":
        _rebuild_customers(conn, default_id)


def _rebuild_conversations(conn: Connection, default_id: str) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE conversations_new (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                company_id VARCHAR(36),
                phone VARCHAR(32) NOT NULL,
                name VARCHAR(120) DEFAULT '',
                last_preview VARCHAR(240) DEFAULT '',
                last_message_at DATETIME,
                unread_count INTEGER DEFAULT 0,
                bot_paused BOOLEAN DEFAULT 0,
                UNIQUE (company_id, phone)
            )
            """
        )
    )
    rows = conn.execute(text("SELECT * FROM conversations")).fetchall()
    col_names = [
        row[1] for row in conn.execute(text("PRAGMA table_info(conversations)")).fetchall()
    ]
    for row in rows:
        data = dict(zip(col_names, row))
        conn.execute(
            text(
                """
                INSERT INTO conversations_new
                    (id, company_id, phone, name, last_preview, last_message_at, unread_count, bot_paused)
                VALUES (:id, :cid, :phone, :name, :preview, :when, :unread, :paused)
                """
            ),
            {
                "id": data.get("id"),
                "cid": data.get("company_id") or default_id,
                "phone": data.get("phone"),
                "name": data.get("name") or "",
                "preview": data.get("last_preview") or "",
                "when": data.get("last_message_at"),
                "unread": data.get("unread_count") or 0,
                "paused": data.get("bot_paused") or 0,
            },
        )
    conn.execute(text("DROP TABLE conversations"))
    conn.execute(text("ALTER TABLE conversations_new RENAME TO conversations"))


def _rebuild_customers(conn: Connection, default_id: str) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE customers_new (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                company_id VARCHAR(36),
                phone VARCHAR(32) NOT NULL,
                name VARCHAR(120) DEFAULT '',
                address TEXT DEFAULT '',
                city VARCHAR(80) DEFAULT '',
                notes TEXT DEFAULT '',
                created_at DATETIME,
                updated_at DATETIME,
                UNIQUE (company_id, phone)
            )
            """
        )
    )
    col_names = [row[1] for row in conn.execute(text("PRAGMA table_info(customers)")).fetchall()]
    rows = conn.execute(text("SELECT * FROM customers")).fetchall()
    for row in rows:
        data = dict(zip(col_names, row))
        conn.execute(
            text(
                """
                INSERT INTO customers_new
                    (id, company_id, phone, name, address, city, notes, created_at, updated_at)
                VALUES (:id, :cid, :phone, :name, :address, :city, :notes, :created, :updated)
                """
            ),
            {
                "id": data.get("id"),
                "cid": data.get("company_id") or default_id,
                "phone": data.get("phone"),
                "name": data.get("name") or "",
                "address": data.get("address") or "",
                "city": data.get("city") or "",
                "notes": data.get("notes") or "",
                "created": data.get("created_at"),
                "updated": data.get("updated_at"),
            },
        )
    conn.execute(text("DROP TABLE customers"))
    conn.execute(text("ALTER TABLE customers_new RENAME TO customers"))


def apply_postgres_patches(conn: Connection) -> None:
    _pg_add_column(conn, "companies", "store_enabled", "BOOLEAN DEFAULT TRUE")
    _pg_add_column(conn, "companies", "store_tagline", "VARCHAR(240) DEFAULT ''")
    _pg_add_column(conn, "companies", "pay_qr_path", "VARCHAR(500) DEFAULT ''")
    _pg_add_column(conn, "companies", "bank_name", "VARCHAR(120) DEFAULT ''")
    _pg_add_column(conn, "companies", "bank_holder", "VARCHAR(160) DEFAULT ''")
    _pg_add_column(conn, "companies", "bank_account_type", "VARCHAR(80) DEFAULT ''")
    _pg_add_column(conn, "companies", "bank_account_number", "VARCHAR(80) DEFAULT ''")
    _pg_add_column(conn, "companies", "bank_id_doc", "VARCHAR(80) DEFAULT ''")
    _pg_add_column(conn, "companies", "pay_instructions", "TEXT DEFAULT ''")
    for table in TENANT_TABLES:
        _pg_add_column(conn, table, "company_id", "VARCHAR(36)")
    default_id = _sql_default_company_id(conn)
    if default_id:
        for table in TENANT_TABLES:
            if _sql_has_column(conn, table, "company_id"):
                conn.execute(
                    text(
                        f"UPDATE {table} SET company_id = :cid WHERE company_id IS NULL OR company_id = ''"
                    ),
                    {"cid": default_id},
                )
        if _sql_has_column(conn, "companies", "store_enabled"):
            conn.execute(text("UPDATE companies SET store_enabled = TRUE WHERE store_enabled IS NULL"))
    _pg_patch_conversation_states(conn, default_id or "")
    for table in ("conversations", "customers"):
        _pg_drop_unique_on_columns(conn, table, ("phone",))
        _pg_ensure_unique(conn, table, ("company_id", "phone"))


def apply_mysql_patches(conn: Connection) -> None:
    _mysql_add_column(conn, "companies", "store_enabled", "TINYINT(1) DEFAULT 1")
    _mysql_add_column(conn, "companies", "store_tagline", "VARCHAR(240) DEFAULT ''")
    _mysql_add_column(conn, "companies", "pay_qr_path", "VARCHAR(500) DEFAULT ''")
    _mysql_add_column(conn, "companies", "bank_name", "VARCHAR(120) DEFAULT ''")
    _mysql_add_column(conn, "companies", "bank_holder", "VARCHAR(160) DEFAULT ''")
    _mysql_add_column(conn, "companies", "bank_account_type", "VARCHAR(80) DEFAULT ''")
    _mysql_add_column(conn, "companies", "bank_account_number", "VARCHAR(80) DEFAULT ''")
    _mysql_add_column(conn, "companies", "bank_id_doc", "VARCHAR(80) DEFAULT ''")
    _mysql_add_column(conn, "companies", "pay_instructions", "TEXT")
    for table in TENANT_TABLES:
        _mysql_add_column(conn, table, "company_id", "VARCHAR(36) NULL")
    default_id = _sql_default_company_id(conn)
    if default_id:
        for table in TENANT_TABLES:
            if _sql_has_column(conn, table, "company_id"):
                conn.execute(
                    text(
                        f"UPDATE {table} SET company_id = :cid WHERE company_id IS NULL OR company_id = ''"
                    ),
                    {"cid": default_id},
                )
    _mysql_patch_conversation_states(conn, default_id or "")
    for table in ("conversations", "customers"):
        _mysql_drop_unique_on_phone(conn, table)
        _mysql_ensure_unique(conn, table, ("company_id", "phone"))


def _sql_has_table(conn: Connection, table: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :t"
        )
        if conn.dialect.name == "postgresql"
        else text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :t"
        ),
        {"t": table},
    ).scalar()
    return bool(row)


def _sql_has_column(conn: Connection, table: str, column: str) -> bool:
    if conn.dialect.name == "postgresql":
        row = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    else:
        row = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() "
                "AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    return bool(row)


def _sql_default_company_id(conn: Connection) -> str | None:
    if not _sql_has_table(conn, "companies"):
        return None
    default_id = conn.execute(
        text("SELECT id FROM companies WHERE is_default IS TRUE LIMIT 1")
        if conn.dialect.name == "postgresql"
        else text("SELECT id FROM companies WHERE is_default = 1 LIMIT 1")
    ).scalar()
    if default_id:
        return str(default_id)
    any_id = conn.execute(text("SELECT id FROM companies LIMIT 1")).scalar()
    return str(any_id) if any_id else None


def _pg_add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    if not _sql_has_table(conn, table) or _sql_has_column(conn, table, column):
        return
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _mysql_add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    if not _sql_has_table(conn, table) or _sql_has_column(conn, table, column):
        return
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _pg_constraint_columns(conn: Connection, table: str) -> list[tuple[str, str, tuple[str, ...]]]:
    rows = conn.execute(
        text(
            """
            SELECT c.conname, c.contype, array_agg(a.attname ORDER BY u.ord) AS cols
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN unnest(c.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = u.attnum
            WHERE n.nspname = current_schema() AND t.relname = :table
              AND c.contype IN ('u', 'p')
            GROUP BY c.conname, c.contype
            """
        ),
        {"table": table},
    ).fetchall()
    return [(str(r[0]), str(r[1]), tuple(r[2] or ())) for r in rows]


def _pg_drop_unique_on_columns(conn: Connection, table: str, columns: tuple[str, ...]) -> None:
    if not _sql_has_table(conn, table):
        return
    wanted = tuple(columns)
    for name, kind, cols in _pg_constraint_columns(conn, table):
        if kind == "u" and cols == wanted:
            conn.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "{name}"'))
    index_rows = conn.execute(
        text(
            """
            SELECT i.relname, array_agg(a.attname ORDER BY u.ord) AS cols
            FROM pg_index idx
            JOIN pg_class i ON i.oid = idx.indexrelid
            JOIN pg_class t ON t.oid = idx.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN unnest(idx.indkey) WITH ORDINALITY AS u(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = u.attnum
            WHERE n.nspname = current_schema() AND t.relname = :table
              AND idx.indisunique AND NOT idx.indisprimary
            GROUP BY i.relname
            """
        ),
        {"table": table},
    ).fetchall()
    for name, cols in index_rows:
        if tuple(cols or ()) == wanted:
            conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))


def _pg_ensure_unique(conn: Connection, table: str, columns: tuple[str, ...]) -> None:
    if not _sql_has_table(conn, table):
        return
    wanted = tuple(columns)
    for _name, kind, cols in _pg_constraint_columns(conn, table):
        if kind == "u" and cols == wanted:
            return
    index_name = f"uq_{table}_{'_'.join(columns)}"
    cols_sql = ", ".join(columns)
    conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({cols_sql})"))


def _pg_patch_conversation_states(conn: Connection, default_id: str) -> None:
    if not _sql_has_table(conn, "conversation_states"):
        return
    if not _sql_has_column(conn, "conversation_states", "company_id"):
        conn.execute(
            text("ALTER TABLE conversation_states ADD COLUMN company_id VARCHAR(36) DEFAULT ''")
        )
    if default_id:
        conn.execute(
            text(
                "UPDATE conversation_states SET company_id = :cid "
                "WHERE company_id IS NULL OR company_id = ''"
            ),
            {"cid": default_id},
        )
    if not _sql_has_column(conn, "conversation_states", "id"):
        conn.execute(text("ALTER TABLE conversation_states ADD COLUMN id VARCHAR(36)"))
        phones = conn.execute(text("SELECT phone FROM conversation_states")).fetchall()
        for (phone,) in phones:
            conn.execute(
                text("UPDATE conversation_states SET id = :id WHERE phone = :phone AND id IS NULL"),
                {"id": str(uuid.uuid4()), "phone": phone},
            )
        pk_name = None
        for name, kind, _cols in _pg_constraint_columns(conn, "conversation_states"):
            if kind == "p":
                pk_name = name
                break
        if pk_name:
            conn.execute(
                text(f'ALTER TABLE conversation_states DROP CONSTRAINT "{pk_name}"')
            )
        conn.execute(text("ALTER TABLE conversation_states ALTER COLUMN id SET NOT NULL"))
        conn.execute(text("ALTER TABLE conversation_states ADD PRIMARY KEY (id)"))
    _pg_ensure_unique(conn, "conversation_states", ("company_id", "phone"))


def _mysql_drop_unique_on_phone(conn: Connection, table: str) -> None:
    if not _sql_has_table(conn, table):
        return
    rows = conn.execute(
        text(
            "SELECT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t "
            "AND NON_UNIQUE = 0 AND COLUMN_NAME = 'phone'"
        ),
        {"t": table},
    ).fetchall()
    names = {str(r[0]) for r in rows}
    for name in names:
        cols = conn.execute(
            text(
                "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t "
                "AND INDEX_NAME = :n ORDER BY SEQ_IN_INDEX"
            ),
            {"t": table, "n": name},
        ).fetchall()
        if [c[0] for c in cols] == ["phone"] and name.upper() != "PRIMARY":
            conn.execute(text(f"ALTER TABLE {table} DROP INDEX `{name}`"))


def _mysql_ensure_unique(conn: Connection, table: str, columns: tuple[str, ...]) -> None:
    if not _sql_has_table(conn, table):
        return
    index_name = f"uq_{table}_{'_'.join(columns)}"
    exists = conn.execute(
        text(
            "SELECT 1 FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :n LIMIT 1"
        ),
        {"t": table, "n": index_name},
    ).scalar()
    if exists:
        return
    cols_sql = ", ".join(f"`{c}`" for c in columns)
    conn.execute(text(f"ALTER TABLE {table} ADD UNIQUE INDEX {index_name} ({cols_sql})"))


def _mysql_patch_conversation_states(conn: Connection, default_id: str) -> None:
    if not _sql_has_table(conn, "conversation_states"):
        return
    if not _sql_has_column(conn, "conversation_states", "company_id"):
        conn.execute(
            text("ALTER TABLE conversation_states ADD COLUMN company_id VARCHAR(36) DEFAULT ''")
        )
    if default_id:
        conn.execute(
            text(
                "UPDATE conversation_states SET company_id = :cid "
                "WHERE company_id IS NULL OR company_id = ''"
            ),
            {"cid": default_id},
        )
    if not _sql_has_column(conn, "conversation_states", "id"):
        conn.execute(text("ALTER TABLE conversation_states ADD COLUMN id VARCHAR(36) NULL"))
        phones = conn.execute(text("SELECT phone FROM conversation_states")).fetchall()
        for (phone,) in phones:
            conn.execute(
                text("UPDATE conversation_states SET id = :id WHERE phone = :phone AND id IS NULL"),
                {"id": str(uuid.uuid4()), "phone": phone},
            )
        conn.execute(text("ALTER TABLE conversation_states DROP PRIMARY KEY"))
        conn.execute(text("ALTER TABLE conversation_states MODIFY id VARCHAR(36) NOT NULL"))
        conn.execute(text("ALTER TABLE conversation_states ADD PRIMARY KEY (id)"))
    _mysql_ensure_unique(conn, "conversation_states", ("company_id", "phone"))
