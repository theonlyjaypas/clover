"""
CLOVE Restaurant Backend — FastAPI + Claude with tool use.
Serves chatbot.html and proxies AI chat using Anthropic tool-use loop.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="CLOVE Restaurant API")

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

DB_PATH       = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "../../src/database/menu.db"))
FRONTEND_PATH = os.path.join(os.path.dirname(__file__), "../../static/templates/chatbot.html")
MODEL         = "claude-haiku-4-5-20251001"

# ── Auth ──────────────────────────────────────────────────────────────────────

_ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
if not _ADMIN_USERNAME:
    raise ValueError("ADMIN_USERNAME environment variable is required")

# Derive password hash at startup from ADMIN_PASSWORD env var
_PASS_SALT = "c3f8a2d14e7b9056f2e1a3c4d5b6f789"
_admin_pw_raw = os.getenv("ADMIN_PASSWORD")
if not _admin_pw_raw:
    raise ValueError("ADMIN_PASSWORD environment variable is required")
_PASS_HASH = hashlib.pbkdf2_hmac("sha256", _admin_pw_raw.encode(), _PASS_SALT.encode(), 260_000).hex()

_sessions: dict[str, float] = {}
_SESSION_TTL = 8 * 3600
_COOKIE      = "clove_session"


def _verify_password(password: str) -> bool:
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), _PASS_SALT.encode(), 260_000
    ).hex()
    return secrets.compare_digest(candidate, _PASS_HASH)


def _create_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + _SESSION_TTL
    return token


def _is_valid(token: str | None) -> bool:
    if not token:
        return False
    expiry = _sessions.get(token)
    if expiry is None or time.time() > expiry:
        _sessions.pop(token, None)
        return False
    return True


def _require_auth(request: Request) -> None:
    if not _is_valid(request.cookies.get(_COOKIE)):
        raise HTTPException(status_code=401, detail="Not authenticated")


# ── Database helpers ──────────────────────────────────────────────────────────

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
    # Migrate existing tables that predate these columns
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


# ── Tool implementations ──────────────────────────────────────────────────────

def tool_search_menu(query: str) -> str:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT mi.name, mi.price, mi.description, c.name AS category
            FROM   menu_items mi
            JOIN   categories c ON mi.category_id = c.id
            WHERE  mi.name LIKE ? OR mi.description LIKE ?
            ORDER  BY c.id, mi.name
            """,
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
    if not rows:
        return f"No items found matching '{query}'."
    return json.dumps([dict(r) for r in rows], indent=2)


def tool_get_menu_categories() -> str:
    with get_db() as conn:
        rows = conn.execute("SELECT name FROM categories ORDER BY id").fetchall()
    return json.dumps([r["name"] for r in rows])


def tool_get_items_by_category(category: str) -> str:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT mi.name, mi.price, mi.description
            FROM   menu_items mi
            JOIN   categories c ON mi.category_id = c.id
            WHERE  LOWER(c.name) = LOWER(?)
            ORDER  BY mi.name
            """,
            (category,),
        ).fetchall()
    if not rows:
        return f"No items found in category '{category}'."
    return json.dumps([dict(r) for r in rows], indent=2)


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


# ── Tool definitions (Anthropic format) ──────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "search_menu",
        "description": (
            "Search the live menu database by keyword. "
            "Returns matching items with name, price, description, and category."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword (e.g. 'paneer', 'spicy', 'dosa', 'chicken', 'vegan')",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_menu_categories",
        "description": "List all menu categories available at the restaurant.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_items_by_category",
        "description": "Get all menu items within a specific category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category name (e.g. 'Soups', 'Desserts', 'Biryani & Rice', 'Hot Breads')",
                }
            },
            "required": ["category"],
        },
    },
    {
        "name": "add_to_cart",
        "description": (
            "Add one or more items to the customer's cart in the UI. "
            "Call this IMMEDIATELY whenever a customer asks to add an item, says they want something, "
            "or asks you to add something to their order. "
            "Use this to build up the cart before the customer is ready to place the final order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Items to add to the cart",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name":  {"type": "string"},
                            "price": {"type": "string"},
                            "qty":   {"type": "integer"},
                        },
                        "required": ["name", "price", "qty"],
                    },
                }
            },
            "required": ["items"],
        },
    },
    {
        "name": "place_order",
        "description": (
            "Save a confirmed customer order to the database. "
            "Only call when the customer explicitly says they are ready to place/submit/finalize the order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "List of ordered items",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name":  {"type": "string"},
                            "price": {"type": "string"},
                            "qty":   {"type": "integer"},
                        },
                        "required": ["name", "price", "qty"],
                    },
                }
            },
            "required": ["items"],
        },
    },
]

SYSTEM_PROMPT = """\
You are Priya, a warm and knowledgeable waiter at CLOVE, an authentic Indian restaurant. \
You help guests explore the menu, understand dishes, get recommendations, and place their order.

Your personality:
- Warm, friendly, and enthusiastic about the food
- Knowledgeable about Indian cuisine — ingredients, spice levels, vegetarian vs non-veg
- Patient and helpful with dietary questions
- Concise — keep responses under 150 words unless the guest asks for detail

You have tools to query the live menu database. Always use them when asked about dishes, \
prices, availability, or categories — never make up menu items.

IMPORTANT — Cart management:
- When a guest asks you to add an item to their cart/order (e.g. "add one veg biryani", "I'll have the dosa"), \
  call add_to_cart IMMEDIATELY with the correct name, price, and quantity. Always search_menu first if you need \
  to confirm the price.
- Use place_order ONLY when the guest explicitly says they are ready to place, submit, or finalize the order.
- Never just say you've added something — always call add_to_cart to actually do it.

When a guest asks for recommendations, ask about their preferences (spicy/mild, veg/non-veg, type of cuisine).

Vegetarian note: Almost all items are vegetarian except Tandoori Breasts Murg, Tandoori Chicken, \
and Butter Chicken Masala.

Pairing tip: Always suggest a bread or rice to pair with curries, and a drink to complete the meal.

Keep responses conversational and warm.\
"""


# ── Tool dispatch ─────────────────────────────────────────────────────────────

_HANDLERS = {
    "search_menu":           lambda inp: tool_search_menu(inp["query"]),
    "get_menu_categories":   lambda inp: tool_get_menu_categories(),
    "get_items_by_category": lambda inp: tool_get_items_by_category(inp["category"]),
    "add_to_cart":           lambda inp: json.dumps({"status": "added", "items": inp["items"]}),
    "place_order":           lambda inp: tool_place_order(inp["items"]),
}


def _run_tool(name: str, inputs: dict) -> str:
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool: {name}"
    try:
        return handler(inputs)
    except Exception as exc:
        return f"Tool error ({name}): {exc}"


# ── Pydantic models ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]


class OrderRequest(BaseModel):
    items: list[dict]
    customer_name: str | None = None
    phone_number: str | None = None
    pickup_datetime: str | None = None


# ── Chat endpoint — agentic tool-use loop ─────────────────────────────────────

@app.post("/api/chat")
async def chat(req: ChatRequest):
    # Strip any system-role messages — backend owns the system prompt
    messages: list[dict] = [
        {"role": m.role, "content": m.content}
        for m in req.messages
        if m.role != "system"
    ]

    cart_items: list[dict] = []

    for _ in range(10):  # Safety cap: max 10 tool-use rounds
        response = _client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            text = "".join(
                block.text
                for block in response.content
                if hasattr(block, "text")
            )
            result: dict = {"response": text, "model": MODEL}
            if cart_items:
                result["cart_items"] = cart_items
            return result

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name in ("add_to_cart", "place_order"):
                    cart_items.extend(block.input.get("items", []))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": _run_tool(block.name, block.input),
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        break  # Unexpected stop reason

    raise HTTPException(status_code=500, detail="Agentic loop did not terminate cleanly")


# ── Menu endpoint ─────────────────────────────────────────────────────────────

@app.get("/api/menu")
async def get_menu():
    with get_db() as conn:
        cats = conn.execute("SELECT id, name FROM categories ORDER BY id").fetchall()
        menu = [
            {
                "category": cat["name"],
                "items": [
                    dict(r)
                    for r in conn.execute(
                        "SELECT name, price, description FROM menu_items "
                        "WHERE category_id = ? ORDER BY name",
                        (cat["id"],),
                    ).fetchall()
                ],
            }
            for cat in cats
        ]
    return {"restaurant": "CLOVE", "menu": menu}


# ── Orders endpoint ───────────────────────────────────────────────────────────

@app.post("/api/orders")
async def create_order(req: OrderRequest):
    return json.loads(tool_place_order(
        req.items,
        customer_name=req.customer_name,
        phone_number=req.phone_number,
        pickup_datetime=req.pickup_datetime,
        source="manual",
    ))


@app.get("/api/orders")
async def list_orders(request: Request):
    _require_auth(request)
    with sqlite3.connect(DB_PATH) as conn:
        _ensure_orders_table(conn)
        rows = conn.execute(
            "SELECT id, created_at, items_json, total, customer_name, phone_number, pickup_datetime, status "
            "FROM orders ORDER BY id DESC"
        ).fetchall()
    orders = [
        {
            "id": r[0],
            "created_at": r[1],
            "items": json.loads(r[2]) if r[2] else [],
            "total": r[3],
            "customer_name": r[4],
            "phone_number": r[5],
            "pickup_datetime": r[6],
            "status": r[7] or "Pending",
        }
        for r in rows
    ]
    return {"orders": orders}


class StatusUpdate(BaseModel):
    status: str


@app.patch("/api/orders/{order_id}/status")
async def update_order_status(order_id: int, body: StatusUpdate, request: Request):
    _require_auth(request)
    valid = {"Pending", "In Progress", "Ready", "Completed", "Cancelled"}
    if body.status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(sorted(valid))}")
    with sqlite3.connect(DB_PATH) as conn:
        _ensure_orders_table(conn)
        cur = conn.execute(
            "UPDATE orders SET status = ? WHERE id = ?",
            (body.status, order_id),
        )
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"id": order_id, "status": body.status}


@app.delete("/api/orders/{order_id}")
async def delete_order(order_id: int, request: Request):
    _require_auth(request)
    with sqlite3.connect(DB_PATH) as conn:
        _ensure_orders_table(conn)
        cur = conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"deleted": order_id}


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.get("/admin")
async def serve_login():
    return FileResponse(Path(__file__).parent.parent.parent / "static/templates/login.html")


@app.post("/api/login")
async def login(body: LoginRequest):
    if body.username != _ADMIN_USERNAME or not _verify_password(body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = _create_session()
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=_SESSION_TTL,
    )
    return response


@app.post("/api/logout")
async def logout(request: Request):
    token = request.cookies.get(_COOKIE)
    _sessions.pop(token, None)
    response = JSONResponse({"ok": True})
    response.delete_cookie(_COOKIE)
    return response


# ── Serve frontend ────────────────────────────────────────────────────────────

@app.get("/")
async def serve_frontend():
    return FileResponse(FRONTEND_PATH)


@app.get("/chatbot")
async def serve_chatbot():
    return FileResponse(FRONTEND_PATH)


@app.get("/orders")
async def serve_orders(request: Request):
    if not _is_valid(request.cookies.get(_COOKIE)):
        return RedirectResponse(url="/admin", status_code=302)
    return FileResponse(Path(__file__).parent.parent.parent / "static/templates/orders.html")
