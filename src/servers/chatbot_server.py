"""
CLOVE Chatbot Server — FastAPI + Claude with tool use.
Serves chatbot.html and handles /api/chat, /api/menu, and order placement.

Run with:
    uvicorn chatbot_server:app --reload --port 8000
"""
from __future__ import annotations

import json
import os

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..database.db import get_db, tool_place_order

load_dotenv()

app = FastAPI(title="CLOVE Chatbot API")

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

FRONTEND_PATH = os.path.join(os.path.dirname(__file__), "../../static/templates/chatbot.html")
MODEL = "claude-haiku-4-5-20251001"


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
- Knowledgeable about Indian cuisine: ingredients, spice levels, vegetarian vs non-veg
- Patient and helpful with dietary questions
- Concise: keep responses under 150 words unless the guest asks for detail

Formatting rules:
- NEVER use emojis in any response
- Use colons (:) instead of em dashes (--)
- Use hyphens (-) or double hyphens (--) for other punctuation needs

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
    messages: list[dict] = [
        {"role": m.role, "content": m.content}
        for m in req.messages
        if m.role != "system"
    ]

    cart_items: list[dict] = []

    for _ in range(10):
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

        break

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


# ── Order placement endpoint (called by the cart checkout button) ─────────────

@app.post("/api/orders")
async def create_order(req: OrderRequest):
    return json.loads(tool_place_order(
        req.items,
        customer_name=req.customer_name,
        phone_number=req.phone_number,
        pickup_datetime=req.pickup_datetime,
        source="manual",
    ))


# ── Serve chatbot frontend ────────────────────────────────────────────────────

@app.get("/")
async def serve_frontend():
    return FileResponse(FRONTEND_PATH)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("chatbot_server:app", host="0.0.0.0", port=8000, reload=True)
