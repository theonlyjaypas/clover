"""
Shared database utilities for CLOVE chatbot and orders servers.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Generator

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "menu.db"))


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _ensure_orders_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT    DEFAULT (datetime('now')),
            items_json       TEXT    NOT NULL,
            total            REAL    NOT NULL,
            customer_name    TEXT,
            phone_number     TEXT,
            pickup_datetime  TEXT,
            status           TEXT    DEFAULT 'Pending',
            source           TEXT    DEFAULT 'chatbot'
        )
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
    for col, typedef in [
        ("customer_name",   "TEXT"),
        ("phone_number",    "TEXT"),
        ("pickup_datetime", "TEXT"),
        ("status",          "TEXT DEFAULT 'Pending'"),
        ("source",          "TEXT DEFAULT 'chatbot'"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {typedef}")
    conn.commit()


def tool_place_order(
    items: list[dict],
    customer_name: str | None = None,
    phone_number: str | None = None,
    pickup_datetime: str | None = None,
    source: str = "chatbot",
) -> str:
    with get_db() as conn:
        _ensure_orders_table(conn)
        total = sum(
            float(str(i.get("price", "0")).replace("$", "")) * int(i.get("qty", 1))
            for i in items
        )
        cur = conn.execute(
            """INSERT INTO orders
               (items_json, total, customer_name, phone_number, pickup_datetime, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (json.dumps(items), round(total, 2), customer_name, phone_number, pickup_datetime, source),
        )
        conn.commit()
        order_id = cur.lastrowid
    return json.dumps({"order_id": order_id, "total": round(total, 2), "status": "confirmed"})
