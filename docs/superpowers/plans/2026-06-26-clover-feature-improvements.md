# Clover Feature Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add streaming chat responses, preference-aware personalization, and an analytics dashboard to the Clover restaurant app.

**Architecture:** Three additive features built on the existing FastAPI + SQLite + Claude Haiku stack. Streaming uses `AsyncAnthropic` + FastAPI `StreamingResponse` emitting SSE events. Personalization adds a `preferences` SQLite table and two new tools (`save_preferences`, `get_recommendations`). Analytics adds a single `/api/analytics` endpoint and a new `analytics.html` page using Chart.js.

**Tech Stack:** Python 3.11+, FastAPI, `anthropic` SDK (sync + async), SQLite, React 18 (UMD, in-browser Babel), Chart.js 4 (CDN)

**Product Name:** Use "Clover" consistently throughout (capitalized for proper nouns, lowercase for CSS variables and config keys)

## Global Constraints

- Model ID: `claude-haiku-4-5-20251001` — do not change
- No new Python dependencies beyond what's already installed (`anthropic`, `fastapi`, `uvicorn`, `python-dotenv`)
- All HTML files use React 18 UMD + Babel standalone (no bundler)
- CSS theme: `--accent: #2563EB`, `--bg: #EFF6FF`, `--surface: #FFFFFF`, `--border: #BFDBFE`, font: `Karla`
- The old `/api/chat` endpoint must remain unchanged and functional
- All admin routes (`/orders`, `/analytics`) must redirect to `/login` when unauthenticated
- Product name is "Clover" (use "Clover" for UI text, "clover_*" for config keys and variables)

---

## File Map

| File | Change |
|---|---|
| `server.py` | Add `_async_client`, `POST /api/chat/stream`, `GET /api/analytics`, `GET /analytics` route, `save_preferences`+`get_recommendations` tool defs + handlers, updated `SYSTEM_PROMPT` |
| `db.py` | Add `_ensure_preferences_table()`, `tool_save_preferences()`, `tool_get_recommendations()` |
| `chatbot.html` | Replace `send()` fetch logic with SSE ReadableStream consumer |
| `orders.html` | Add Analytics nav link in header-actions |
| `analytics.html` | New file — Chart.js dashboard |

---

## Task 1: Streaming backend — `POST /api/chat/stream`

**Files:**
- Modify: `server.py`

**Interfaces:**
- Produces: `POST /api/chat/stream` — accepts `{"messages": [...]}`, returns `text/event-stream` with events `{"type":"delta","text":"..."}`, `{"type":"cart","items":[...]}`, `{"type":"done"}`, `{"type":"error","message":"..."}`

- [ ] **Step 1: Add `AsyncAnthropic` client and `StreamingResponse` import**

In `server.py`, add to the existing imports block:

```python
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
```

And after the existing `_client = anthropic.Anthropic(...)` line, add:

```python
_async_client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
```

- [ ] **Step 2: Add the streaming endpoint**

Add this entire function to `server.py`, after the existing `chat()` endpoint (after line ~403):

```python
@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    messages: list[dict] = [
        {"role": m.role, "content": m.content}
        for m in req.messages
        if m.role != "system"
    ]

    async def generate():
        cart_items: list[dict] = []

        for _ in range(10):
            content_blocks: list[dict] = []
            current_block: dict | None = None
            stop_reason: str | None = None

            async with _async_client.messages.stream(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            ) as stream:
                async for event in stream:
                    etype = event.type

                    if etype == "content_block_start":
                        cb = event.content_block
                        current_block = {"type": cb.type}
                        if cb.type == "tool_use":
                            current_block["id"]         = cb.id
                            current_block["name"]       = cb.name
                            current_block["input_json"] = ""
                        elif cb.type == "text":
                            current_block["text"] = ""

                    elif etype == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta" and current_block:
                            current_block["text"] += delta.text
                            yield f"data: {json.dumps({'type': 'delta', 'text': delta.text})}\n\n"
                        elif delta.type == "input_json_delta" and current_block:
                            current_block["input_json"] += delta.partial_json

                    elif etype == "content_block_stop":
                        if current_block:
                            if current_block["type"] == "tool_use":
                                raw = current_block.pop("input_json")
                                current_block["input"] = json.loads(raw) if raw else {}
                            content_blocks.append(current_block)
                            current_block = None

                    elif etype == "message_delta":
                        stop_reason = event.delta.stop_reason

            if stop_reason == "end_turn":
                if cart_items:
                    yield f"data: {json.dumps({'type': 'cart', 'items': cart_items})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            if stop_reason == "tool_use":
                anthropic_content: list[dict] = []
                for block in content_blocks:
                    if block["type"] == "text":
                        anthropic_content.append({"type": "text", "text": block["text"]})
                    elif block["type"] == "tool_use":
                        anthropic_content.append({
                            "type": "tool_use",
                            "id":    block["id"],
                            "name":  block["name"],
                            "input": block["input"],
                        })
                messages.append({"role": "assistant", "content": anthropic_content})

                tool_results = []
                for block in content_blocks:
                    if block["type"] != "tool_use":
                        continue
                    if block["name"] in ("add_to_cart", "place_order"):
                        cart_items.extend(block["input"].get("items", []))
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block["id"],
                        "content":     _run_tool(block["name"], block["input"]),
                    })
                messages.append({"role": "user", "content": tool_results})
                continue

            break

        yield f"data: {json.dumps({'type': 'error', 'message': 'Agentic loop did not terminate cleanly'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 3: Verify the endpoint responds**

Start the server:
```bash
cd "/Users/jaypas/MLENG/MINI/clover"
uvicorn server:app --port 8000
```

In a second terminal, send a test request and watch the SSE stream:
```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hello Priya!"}]}'
```

Expected output (tokens will vary):
```
data: {"type": "delta", "text": "Namaste"}
data: {"type": "delta", "text": "!"}
...
data: {"type": "done"}
```

You must see multiple `delta` lines followed by `done`. If you see a single JSON blob, the streaming is not working.

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "feat: add POST /api/chat/stream SSE streaming endpoint"
```

---

## Task 2: Streaming frontend — update `send()` in `chatbot.html`

**Files:**
- Modify: `chatbot.html`

**Interfaces:**
- Consumes: `POST /api/chat/stream` from Task 1 — SSE events `delta`, `cart`, `done`, `error`
- The `send()` function signature and all its callers remain identical; only the internal fetch logic changes.

- [ ] **Step 1: Replace the `send()` function body**

In `chatbot.html`, find the `send` function (starts around line 1210 with `const send = useCallback(async (text) => {`). Replace the entire function body — from the opening `useCallback` through its closing `}, [input, history]);` — with:

```javascript
const send = useCallback(async (text) => {
  const trimmed = (text || input).trim();
  if (!trimmed || streamingRef.current) return;
  setInput('');
  if (textRef.current) { textRef.current.style.height = 'auto'; }

  const userMsg = { id: Date.now(), role: 'user', time: getTime(), content: trimmed };
  setMessages(m => [...m, userMsg]);

  const newHistory = [...history, { role: 'user', content: trimmed }];
  setHistory(newHistory);

  const botId = Date.now() + 1;
  setMessages(m => [...m, { id: botId, role: 'bot', time: getTime(), content: '', streaming: true }]);
  setStreaming(true);
  streamingRef.current = true;

  let fullText = '';

  try {
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: newHistory }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err?.detail || `HTTP ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let event;
        try { event = JSON.parse(line.slice(6)); } catch { continue; }

        if (event.type === 'delta') {
          fullText += event.text;
          setMessages(m => m.map(msg =>
            msg.id === botId ? { ...msg, content: fullText } : msg
          ));
        } else if (event.type === 'cart') {
          const cartItems = event.items;
          setCart(c => {
            let next = [...c];
            for (const item of cartItems) {
              const qty = parseInt(item.qty) || 1;
              const idx = next.findIndex(x => x.name === item.name);
              if (idx >= 0) {
                next[idx] = { ...next[idx], qty: next[idx].qty + qty };
              } else {
                next = [...next, { name: item.name, price: item.price, qty }];
              }
            }
            return next;
          });
          addToast(`🛒 ${cartItems.length} item(s) added to cart!`);
        } else if (event.type === 'done') {
          setMessages(m => m.map(msg =>
            msg.id === botId ? { ...msg, streaming: false } : msg
          ));
          setHistory(h => [...h, { role: 'assistant', content: fullText }]);
        } else if (event.type === 'error') {
          throw new Error(event.message);
        }
      }
    }

  } catch (err) {
    setMessages(m => m.map(msg =>
      msg.id === botId
        ? { ...msg, content: `Error: ${err.message}`, error: true, streaming: false }
        : msg
    ));
  } finally {
    setStreaming(false);
    streamingRef.current = false;
  }
}, [input, history]);
```

- [ ] **Step 2: Verify streaming in the browser**

Open `http://localhost:8000/chatbot` and type "Hello Priya!". 

Expected: Priya's response text appears token by token, with the message bubble growing as text arrives. The input field stays disabled while the response streams and re-enables on completion.

If you see the response appear all at once after a pause, the ReadableStream is not being consumed incrementally — double-check the `reader.read()` loop is inside the React component and not blocked.

- [ ] **Step 3: Test cart streaming**

Type "Add one Masala Dosa to my cart". 

Expected: Priya acknowledges the add in streaming text, and the cart panel updates with Masala Dosa after the `cart` SSE event arrives (around when the response finishes).

- [ ] **Step 4: Commit**

```bash
git add chatbot.html
git commit -m "feat: switch chatbot to streaming SSE via ReadableStream"
```

---

## Task 3: Personalization DB layer — `db.py`

**Files:**
- Modify: `db.py`

**Interfaces:**
- Produces:
  - `_ensure_preferences_table(conn: sqlite3.Connection) -> None`
  - `tool_save_preferences(phone_number: str, spice_level: str | None, dietary: str | None) -> str` — returns JSON `{"status":"saved","phone_number":"..."}`
  - `tool_get_recommendations(phone_number: str) -> str` — returns JSON `{"preferences":{...}|None, "recommendations":[...]}` or `{"preferences":None,"message":"..."}`

- [ ] **Step 1: Add `_ensure_preferences_table` to `db.py`**

After the existing `_ensure_orders_table` function, add:

```python
def _ensure_preferences_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS preferences (
            phone_number   TEXT PRIMARY KEY,
            spice_level    TEXT,
            dietary        TEXT,
            updated_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
```

- [ ] **Step 2: Add `tool_save_preferences` to `db.py`**

```python
def tool_save_preferences(
    phone_number: str,
    spice_level: str | None = None,
    dietary: str | None = None,
) -> str:
    with get_db() as conn:
        _ensure_preferences_table(conn)
        conn.execute(
            """
            INSERT INTO preferences (phone_number, spice_level, dietary, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(phone_number) DO UPDATE SET
                spice_level = COALESCE(?, spice_level),
                dietary     = COALESCE(?, dietary),
                updated_at  = datetime('now')
            """,
            (phone_number, spice_level, dietary, spice_level, dietary),
        )
        conn.commit()
    return json.dumps({"status": "saved", "phone_number": phone_number})
```

- [ ] **Step 3: Add `tool_get_recommendations` to `db.py`**

```python
_SPICE_KEYWORDS: dict[str, list[str]] = {
    "mild":      ["mild", "gentle", "subtle", "light", "creamy"],
    "medium":    ["medium", "moderate", "balanced", "tangy"],
    "hot":       ["spicy", "hot", "bold", "chilli", "chili", "pepper", "fiery"],
    "extra hot": ["spicy", "very hot", "extra hot", "chilli", "chili", "pepper", "fiery"],
}

_NON_VEG_ITEMS = {"Tandoori Breasts Murg", "Tandoori Chicken", "Butter Chicken Masala"}


def tool_get_recommendations(phone_number: str) -> str:
    with get_db() as conn:
        _ensure_preferences_table(conn)
        row = conn.execute(
            "SELECT spice_level, dietary FROM preferences WHERE phone_number = ?",
            (phone_number,),
        ).fetchone()

        if not row:
            return json.dumps({
                "preferences": None,
                "message": "No preferences found for this customer. Ask about their dietary needs and spice preference.",
            })

        spice_level: str | None = row["spice_level"]
        dietary:     str | None = row["dietary"]

        base_query = """
            SELECT mi.name, mi.price, mi.description, c.name AS category
            FROM   menu_items mi
            JOIN   categories c ON mi.category_id = c.id
        """
        if dietary in ("vegetarian", "vegan"):
            rows = conn.execute(
                base_query + " WHERE mi.name NOT IN (?,?,?) ORDER BY mi.name",
                tuple(_NON_VEG_ITEMS),
            ).fetchall()
        elif dietary == "non-vegetarian":
            rows = conn.execute(
                base_query + " WHERE mi.name IN (?,?,?) ORDER BY mi.name",
                tuple(_NON_VEG_ITEMS),
            ).fetchall()
        else:
            rows = conn.execute(base_query + " ORDER BY mi.name").fetchall()

    keywords = _SPICE_KEYWORDS.get(spice_level or "", [])

    def _score(item: dict) -> int:
        text = (item["name"] + " " + (item["description"] or "")).lower()
        return sum(1 for kw in keywords if kw in text)

    items = [dict(r) for r in rows]
    items.sort(key=_score, reverse=True)

    return json.dumps({
        "preferences":   {"spice_level": spice_level, "dietary": dietary},
        "recommendations": items[:10],
    })
```

- [ ] **Step 4: Verify the functions work**

Run Python interactively:
```bash
cd "/Users/jaypas/MLENG/MINI/clover"
python3 -c "
from src.database.db import tool_save_preferences, tool_get_recommendations
print(tool_save_preferences('555-1234', spice_level='hot', dietary='vegetarian'))
print(tool_get_recommendations('555-1234'))
print(tool_get_recommendations('999-0000'))
"
```

Expected output (truncated for readability):
```
{"status": "saved", "phone_number": "555-1234"}
{"preferences": {"spice_level": "hot", "dietary": "vegetarian"}, "recommendations": [...up to 10 vegetarian items...]}
{"preferences": null, "message": "No preferences found..."}
```

- [ ] **Step 5: Commit**

```bash
git add db.py
git commit -m "feat: add preferences table and tool_save/get_recommendations to db.py"
```

---

## Task 4: Personalization tools + system prompt — `server.py`

**Files:**
- Modify: `server.py`

**Interfaces:**
- Consumes: `tool_save_preferences`, `tool_get_recommendations` from `db.py` (Task 3)
- Produces: Two new tools available to Priya in the tool-use loop; updated `SYSTEM_PROMPT` with phone-number bootstrapping and preference instructions

- [ ] **Step 1: Import new db functions**

In `server.py`, find the line that imports from `db`:
```python
# server.py currently has no db imports at the top — it duplicates db functions inline
```

Actually `server.py` does NOT import from `db.py` (it has its own copies of the DB functions). Add these imports near the top of the file, after the `load_dotenv()` call:

```python
from db import tool_save_preferences as _db_save_preferences
from db import tool_get_recommendations as _db_get_recommendations
```

- [ ] **Step 2: Add two tool definitions to `TOOLS` list**

In `server.py`, find the `TOOLS: list[dict] = [` block. Append these two entries inside the list (before the closing `]`):

```python
    {
        "name": "save_preferences",
        "description": (
            "Save or update a customer's spice level and dietary preferences. "
            "Call this IMMEDIATELY whenever the customer mentions their preference — "
            "e.g. 'I'm vegetarian', 'I like it really spicy', 'medium heat for me'. "
            "Requires the customer's phone number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Customer's phone number"},
                "spice_level":  {
                    "type": "string",
                    "enum": ["mild", "medium", "hot", "extra hot"],
                    "description": "Customer's preferred spice level",
                },
                "dietary": {
                    "type": "string",
                    "enum": ["vegetarian", "non-vegetarian", "vegan"],
                    "description": "Customer's dietary preference",
                },
            },
            "required": ["phone_number"],
        },
    },
    {
        "name": "get_recommendations",
        "description": (
            "Get personalised menu recommendations for a customer based on their stored preferences. "
            "Always call this before suggesting dishes when you have the customer's phone number. "
            "Returns stored spice/dietary preferences plus a ranked list of matching menu items."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Customer's phone number"},
            },
            "required": ["phone_number"],
        },
    },
```

- [ ] **Step 3: Add handlers to `_HANDLERS`**

In `server.py`, find the `_HANDLERS` dict. Add two entries:

```python
    "save_preferences":    lambda inp: _db_save_preferences(
                               inp["phone_number"],
                               inp.get("spice_level"),
                               inp.get("dietary"),
                           ),
    "get_recommendations": lambda inp: _db_get_recommendations(inp["phone_number"]),
```

- [ ] **Step 4: Replace `SYSTEM_PROMPT`**

Find the entire `SYSTEM_PROMPT = """..."""` block in `server.py` and replace it with:

```python
SYSTEM_PROMPT = """\
You are Priya, a warm and knowledgeable waiter at Clover, an authentic Indian restaurant. \
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

IMPORTANT — Personalisation:
- Within your first 1-2 responses, naturally ask for the guest's phone number: \
  "What's your number? I can pull up your preferences if you've ordered with us before."
- If the guest provides a phone number, immediately call get_recommendations to retrieve their stored preferences.
- If get_recommendations returns no preferences, ask about spice tolerance (mild/medium/hot/extra hot) \
  and dietary preference (vegetarian/non-vegetarian/vegan) before making suggestions.
- Whenever you learn a preference (e.g. "I'm vegetarian", "I like it spicy"), call save_preferences \
  immediately with the phone number and the new preference — do not wait until the end.
- Always call get_recommendations before suggesting dishes. Never guess based on memory.
- If a guest declines to share their phone number, skip get_recommendations and ask about preferences conversationally; \
  skip save_preferences too.

Vegetarian note: Almost all items are vegetarian except Tandoori Breasts Murg, Tandoori Chicken, \
and Butter Chicken Masala.

Pairing tip: Always suggest a bread or rice to pair with curries, and a drink to complete the meal.

Keep responses conversational and warm.\
"""
```

- [ ] **Step 5: End-to-end test**

Start the server and open `http://localhost:8000/chatbot`. Have this conversation:

1. Say: "Hi Priya"
2. When she asks for your number, say: "555-9876"
3. She should call `get_recommendations` (you'll see no preferences yet)
4. Say: "I'm vegetarian and I like it spicy"
5. She should call `save_preferences` with phone 555-9876, dietary=vegetarian, spice_level=hot
6. Say: "What do you recommend?"
7. She should call `get_recommendations` and return vegetarian items

Verify in the DB that preferences were saved:
```bash
sqlite3 "/Users/jaypas/MLENG/MINI/clover/src/database/menu.db" \
  "SELECT * FROM preferences;"
```

Expected:
```
555-9876|hot|vegetarian|2026-06-26 ...
```

- [ ] **Step 6: Commit**

```bash
git add server.py
git commit -m "feat: add save_preferences and get_recommendations tools with updated system prompt"
```

---

## Task 5: Analytics backend — `GET /api/analytics` + `/analytics` route

**Files:**
- Modify: `server.py`

**Interfaces:**
- Produces: `GET /api/analytics` — auth-protected, returns:
  ```json
  {
    "top_items":      [{"name": "Masala Dosa", "count": 12}, ...],
    "revenue_by_day": [{"day": "2026-06-01", "revenue": 142.50}, ...],
    "orders_by_hour": [{"hour": "12", "count": 8}, ...],
    "summary": {
      "total_orders": 42,
      "total_revenue": 1890.00,
      "avg_order_value": 45.00,
      "orders_today": 3
    }
  }
  ```
- Produces: `GET /analytics` — auth-gated HTML route (serves `analytics.html`)

- [ ] **Step 1: Add `/api/analytics` endpoint to `server.py`**

Add after the existing `delete_order` endpoint:

```python
@app.get("/api/analytics")
async def get_analytics(request: Request):
    _require_auth(request)
    with get_db() as conn:
        _ensure_orders_table(conn)

        # Top items — parse items_json in Python
        all_orders = conn.execute("SELECT items_json FROM orders").fetchall()
        item_counts: dict[str, int] = {}
        for row in all_orders:
            try:
                items = json.loads(row[0]) if row[0] else []
                for item in items:
                    name = item.get("name", "").strip()
                    qty  = int(item.get("qty", 1))
                    if name:
                        item_counts[name] = item_counts.get(name, 0) + qty
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        top_items = sorted(item_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        # Revenue by day — last 30 days
        rev_rows = conn.execute("""
            SELECT date(created_at) AS day, ROUND(SUM(total), 2) AS revenue
            FROM   orders
            WHERE  created_at >= date('now', '-30 days')
            GROUP  BY day
            ORDER  BY day
        """).fetchall()

        # Orders by hour of day
        hour_rows = conn.execute("""
            SELECT strftime('%H', created_at) AS hour, COUNT(*) AS count
            FROM   orders
            GROUP  BY hour
            ORDER  BY hour
        """).fetchall()

        # Summary KPIs
        kpi = conn.execute("""
            SELECT
                COUNT(*)                                                   AS total_orders,
                COALESCE(ROUND(SUM(total), 2), 0)                         AS total_revenue,
                COALESCE(ROUND(AVG(total), 2), 0)                         AS avg_order_value,
                COUNT(CASE WHEN date(created_at) = date('now') THEN 1 END) AS orders_today
            FROM orders
        """).fetchone()

    return {
        "top_items":      [{"name": n, "count": c} for n, c in top_items],
        "revenue_by_day": [{"day": r[0], "revenue": r[1]} for r in rev_rows],
        "orders_by_hour": [{"hour": r[0], "count": r[1]} for r in hour_rows],
        "summary": {
            "total_orders":    kpi[0],
            "total_revenue":   kpi[1],
            "avg_order_value": kpi[2],
            "orders_today":    kpi[3],
        },
    }
```

- [ ] **Step 2: Add `/analytics` HTML route to `server.py`**

Add after the existing `serve_orders` route:

```python
@app.get("/analytics")
async def serve_analytics(request: Request):
    if not _is_valid(request.cookies.get(_COOKIE)):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(Path(__file__).parent / "analytics.html")
```

- [ ] **Step 3: Verify the analytics endpoint**

With the server running and while logged in (cookie present), run:
```bash
# First, log in and grab the session cookie
COOKIE=$(curl -s -c /tmp/clove_cookies.txt -X POST http://localhost:8000/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"jaypas","password":"R3load24"}' \
  -w "\n" && cat /tmp/clove_cookies.txt | grep clove_session | awk '{print $7}')

# Then hit analytics
curl -s -b "clove_session=$COOKIE" http://localhost:8000/api/analytics | python3 -m json.tool
```

Expected: JSON with `top_items`, `revenue_by_day`, `orders_by_hour`, and `summary` keys. If you have no orders yet, `top_items` will be `[]` and `summary.total_orders` will be `0` — that's correct.

Also verify auth guard:
```bash
curl -s http://localhost:8000/analytics
```
Expected: `302` redirect to `/login`.

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "feat: add /api/analytics endpoint and /analytics route"
```

---

## Task 6: Analytics frontend — `analytics.html` + orders nav link

**Files:**
- Create: `analytics.html`
- Modify: `orders.html`

**Interfaces:**
- Consumes: `GET /api/analytics` from Task 5
- Consumes: `POST /api/logout` (existing)

- [ ] **Step 1: Create `analytics.html`**

Create `/Users/jaypas/MLENG/MINI/clover/static/templates/analytics.html` with the following content:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Clover — Analytics</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Karla:wght@300;400;500;600;700&family=Playfair+Display+SC:wght@400;700&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
          integrity="sha384-e6nUZLBkQ86NJ6TVVKAeSaK8jWa3NhkYWZFomE39AvDbQWeie9PlQqM3pmYW5d1g"
          crossorigin="anonymous"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #EFF6FF; --surface: #FFFFFF; --surface-2: #EFF6FF;
      --border: #BFDBFE; --accent: #2563EB; --accent-d: #1E40AF;
      --text: #0F172A; --muted: #64748B; --green: #059669; --red: #DC2626;
    }
    body {
      background: var(--bg); color: var(--text);
      font-family: 'Karla', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      min-height: 100vh;
    }
    main { max-width: 1100px; margin: 0 auto; padding: 24px 20px 48px; }

    .page-header {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 28px; gap: 16px; flex-wrap: wrap;
    }
    .page-header h1 {
      font-family: 'Playfair Display SC', Georgia, serif;
      font-size: 26px; color: var(--accent-d); letter-spacing: 0.04em;
    }
    .page-header .subtitle { font-size: 13px; color: var(--muted); margin-top: 3px; }
    .header-actions { display: flex; gap: .6rem; align-items: center; }

    .btn { padding: 8px 16px; border-radius: 8px; font-size: 13px; font-weight: 600;
           font-family: inherit; cursor: pointer; border: 1px solid; transition: all .15s; }
    .btn-nav { background: var(--surface); border-color: var(--border); color: var(--accent);
               text-decoration: none; display: inline-flex; align-items: center; }
    .btn-nav:hover { background: var(--surface-2); }
    .btn-logout { background: var(--surface); border-color: var(--border); color: var(--muted); }
    .btn-logout:hover { background: #FEF2F2; color: var(--red); border-color: #FECACA; }

    /* KPI cards */
    .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 28px; }
    .kpi-card {
      background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
      padding: 20px 22px; box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    }
    .kpi-label { font-size: 11px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }
    .kpi-value { font-size: 28px; font-weight: 700; color: var(--accent-d); margin-top: 6px; }

    /* Chart grid */
    .chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    @media (max-width: 700px) { .chart-grid { grid-template-columns: 1fr; } }
    .chart-card {
      background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
      padding: 20px 22px; box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    }
    .chart-card h2 { font-size: 14px; font-weight: 700; margin-bottom: 16px; color: var(--text); }
    .chart-card.wide { grid-column: span 2; }
    @media (max-width: 700px) { .chart-card.wide { grid-column: span 1; } }

    .loading { text-align: center; padding: 60px; color: var(--muted); font-size: 15px; }
    .error   { text-align: center; padding: 60px; color: var(--red);   font-size: 15px; }
  </style>
</head>
<body>
<main id="app">
  <div class="loading">Loading analytics…</div>
</main>

<script>
const BLUE      = '#2563EB';
const BLUE_DIMS = 'rgba(37,99,235,0.15)';
const BLUE_FILL = 'rgba(37,99,235,0.08)';

function fmtMoney(v) { return '$' + Number(v).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }

async function handleLogout() {
  await fetch('/api/logout', { method: 'POST' });
  window.location.href = '/login';
}

function makeChart(id, cfg) {
  return new Chart(document.getElementById(id), cfg);
}

async function init() {
  const app = document.getElementById('app');
  let data;
  try {
    const res = await fetch('/api/analytics');
    if (res.status === 401) { window.location.href = '/login'; return; }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (e) {
    app.innerHTML = `<div class="error">Failed to load analytics: ${e.message}</div>`;
    return;
  }

  const { summary, top_items, revenue_by_day, orders_by_hour } = data;

  app.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Analytics</h1>
        <p class="subtitle">Restaurant performance overview</p>
      </div>
      <div class="header-actions">
        <a href="/orders" class="btn btn-nav">← Orders</a>
        <button class="btn btn-logout" onclick="handleLogout()">Sign Out</button>
      </div>
    </div>

    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">Total Orders</div>
        <div class="kpi-value">${summary.total_orders}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Total Revenue</div>
        <div class="kpi-value">${fmtMoney(summary.total_revenue)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Avg Order Value</div>
        <div class="kpi-value">${fmtMoney(summary.avg_order_value)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Orders Today</div>
        <div class="kpi-value">${summary.orders_today}</div>
      </div>
    </div>

    <div class="chart-grid">
      <div class="chart-card wide">
        <h2>Revenue — Last 30 Days</h2>
        <canvas id="chartRevenue" height="90"></canvas>
      </div>
      <div class="chart-card">
        <h2>Top 10 Items Ordered</h2>
        <canvas id="chartItems" height="220"></canvas>
      </div>
      <div class="chart-card">
        <h2>Orders by Hour of Day</h2>
        <canvas id="chartHours" height="220"></canvas>
      </div>
    </div>
  `;

  // Revenue line chart
  makeChart('chartRevenue', {
    type: 'line',
    data: {
      labels: revenue_by_day.map(r => r.day),
      datasets: [{
        label: 'Revenue',
        data: revenue_by_day.map(r => r.revenue),
        borderColor: BLUE, backgroundColor: BLUE_FILL,
        fill: true, tension: 0.3, pointRadius: 3,
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        y: { ticks: { callback: v => '$' + v }, grid: { color: '#E2E8F0' } },
        x: { grid: { display: false } },
      },
    },
  });

  // Top items bar chart
  makeChart('chartItems', {
    type: 'bar',
    data: {
      labels: top_items.map(i => i.name),
      datasets: [{
        label: 'Orders',
        data: top_items.map(i => i.count),
        backgroundColor: BLUE_DIMS, borderColor: BLUE, borderWidth: 1,
      }],
    },
    options: {
      indexAxis: 'y',
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { precision: 0 }, grid: { color: '#E2E8F0' } },
        y: { grid: { display: false }, ticks: { font: { size: 11 } } },
      },
    },
  });

  // Peak hours bar chart
  const allHours = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'));
  const hourMap  = Object.fromEntries(orders_by_hour.map(r => [r.hour, r.count]));
  makeChart('chartHours', {
    type: 'bar',
    data: {
      labels: allHours.map(h => `${h}:00`),
      datasets: [{
        label: 'Orders',
        data: allHours.map(h => hourMap[h] || 0),
        backgroundColor: BLUE_DIMS, borderColor: BLUE, borderWidth: 1,
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        y: { ticks: { precision: 0 }, grid: { color: '#E2E8F0' } },
        x: { grid: { display: false }, ticks: { font: { size: 10 } } },
      },
    },
  });
}

init();
</script>
</body>
</html>
```

- [ ] **Step 2: Add Analytics nav link to `orders.html`**

In `orders.html`, find the `header-actions` div. It currently looks like:
```jsx
<div className="header-actions">
  <button
    className="btn-refresh"
    onClick={fetchOrders}
    disabled={loading}
  >
    {loading ? 'Loading…' : '↻ Refresh'}
  </button>
  <button className="btn-logout" onClick={handleLogout}>
    Sign Out
  </button>
</div>
```

Add an Analytics link before the refresh button:
```jsx
<div className="header-actions">
  <a href="/analytics" style={{
    padding: '8px 16px', borderRadius: '8px', fontSize: '13px', fontWeight: 600,
    fontFamily: 'inherit', border: '1px solid #BFDBFE', background: '#FFFFFF',
    color: '#2563EB', textDecoration: 'none', display: 'inline-flex', alignItems: 'center',
  }}>
    Analytics
  </a>
  <button
    className="btn-refresh"
    onClick={fetchOrders}
    disabled={loading}
  >
    {loading ? 'Loading…' : '↻ Refresh'}
  </button>
  <button className="btn-logout" onClick={handleLogout}>
    Sign Out
  </button>
</div>
```

- [ ] **Step 3: Verify the analytics page**

1. Start the server, log in at `http://localhost:8000/login`
2. Navigate to `http://localhost:8000/analytics`
3. Expected: 4 KPI cards + 3 Chart.js charts render. If you have no orders yet, charts will be empty but the KPI cards will show zeroes (not errors).
4. Click "← Orders" — verify you land on the orders page.
5. Click "Analytics" on the orders page — verify you land on the analytics page.
6. Log out, then try `http://localhost:8000/analytics` directly — expected: redirect to `/login`.

- [ ] **Step 4: Place a few test orders and verify charts populate**

Use the chatbot to place 2-3 orders with different items. Then refresh the analytics page.

Expected:
- Top Items bar chart shows the items you ordered
- Revenue by Day shows today's revenue
- Orders Today KPI matches the number of orders placed

- [ ] **Step 5: Commit**

```bash
git add analytics.html orders.html
git commit -m "feat: add analytics dashboard and Orders→Analytics nav link"
```

---

## Done

All three features are now implemented and independently verified:

1. ✅ **Streaming** — chatbot responses stream token by token via SSE
2. ✅ **Personalization** — Priya learns and remembers customer preferences across sessions
3. ✅ **Analytics** — admin dashboard shows top items, revenue trends, peak hours, and KPIs
