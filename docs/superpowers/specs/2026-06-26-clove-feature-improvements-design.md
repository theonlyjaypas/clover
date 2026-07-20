# CLOVE Feature Improvements Design
**Date:** 2026-06-26
**Status:** Approved

## Overview

Three additive improvements to the CLOVE Indian restaurant ordering app. All changes are non-breaking — existing endpoints and tables remain intact. The goals are production quality and showcasing meaningful AI/agent patterns.

**Features:**
1. Streaming chat responses (SSE + Anthropic streaming API)
2. Preference-aware personalization (new tools + DB table)
3. Analytics dashboard (new admin page + SQL aggregations)

---

## Architecture

```
server.py  (additions only)
├── POST /api/chat/stream   ← new SSE streaming endpoint
├── GET  /api/analytics     ← new auth-protected analytics endpoint
├── tool: save_preferences  ← writes customer prefs to DB
└── tool: get_recommendations  ← reads prefs + returns ranked menu items

db.py  (addition)
└── preferences table  ← spice_level, dietary keyed by phone_number

chatbot.html  (change)
└── fetch with ReadableStream instead of single-response fetch

orders.html  (change)
└── Add navigation link to analytics page

analytics.html  (new file)
└── Chart.js dashboard reading /api/analytics
```

No new servers, no new frameworks. The old `/api/chat` endpoint stays intact as a fallback.

---

## Feature 1: Streaming Chat Responses

### Problem
The current `/api/chat` endpoint runs the full tool-use loop then returns one JSON blob. Users wait with no feedback, then receive a wall of text.

### Solution
New `POST /api/chat/stream` endpoint using Anthropic's streaming API and Server-Sent Events (SSE).

### Backend
- Uses `_client.messages.stream()` context manager from the Anthropic SDK
- Tool-use rounds complete synchronously (all tools are fast DB lookups), then the final assistant text streams token by token
- SSE event format:
  - `data: {"type": "delta", "text": "..."}` — per token
  - `data: {"type": "cart", "items": [...]}` — when cart is updated
  - `data: {"type": "done"}` — stream complete
- FastAPI `StreamingResponse` with `media_type="text/event-stream"`
- Safety cap of 10 tool-use rounds retained

### Frontend (`chatbot.html`)
- Replace `fetch('/api/chat')` with `fetch('/api/chat/stream')` reading `response.body` as a `ReadableStream`
- As `delta` events arrive, append text into the current message bubble in real time
- On `cart` event, update cart panel as before
- On `done`, re-enable the input field
- Show a blinking cursor while streaming is in progress

### Why it matters
Demonstrates Anthropic's streaming API — one of the most practically important patterns for production AI apps. Response feel goes from "waiting... wall of text" to "Priya is typing in real time."

---

## Feature 2: Preference-Aware Personalization

### Problem
Priya is stateless — every session starts from scratch. Returning customers must re-state dietary needs and spice preferences every time, and recommendations are generic.

### Solution
Two new tools (`save_preferences`, `get_recommendations`) + a `preferences` table in SQLite keyed by phone number.

### Database Schema

```sql
CREATE TABLE IF NOT EXISTS preferences (
    phone_number   TEXT PRIMARY KEY,
    spice_level    TEXT,   -- 'mild', 'medium', 'hot', 'extra hot'
    dietary        TEXT,   -- 'vegetarian', 'non-vegetarian', 'vegan'
    updated_at     TEXT DEFAULT (datetime('now'))
)
```

Keyed by `phone_number` — already collected at order time, so no new data collection is needed from the customer.

### New Tools

**`save_preferences`**
- Input: `phone_number` (string), `spice_level` (string, optional), `dietary` (string, optional)
- Action: Upserts into the `preferences` table
- Priya calls this as soon as a customer mentions dietary needs or spice preference ("I'm vegetarian", "I like it really spicy")

**`get_recommendations`**
- Input: `phone_number` (string)
- Action: Looks up stored preferences, queries menu items, returns ranked matches
- Ranking logic: dietary filter applied first (hard filter); then items scored by spice keyword match in name/description (`spicy`, `mild`, `hot`, `pepper`, etc.); unmatched items appended at the end so the response is never empty
- Priya calls this whenever a customer asks for suggestions

### Phone number bootstrapping
`save_preferences` and `get_recommendations` both require a `phone_number`. For first-time customers who haven't placed an order yet, Priya asks for their phone number early in the conversation — framed naturally: *"What's your number? I can pull up your preferences if you've ordered with us before."* If the customer declines, both tools are skipped and Priya falls back to asking preference questions directly in conversation.

### System Prompt Changes
Priya is instructed to:
- Ask for the customer's phone number early in the conversation, then call `get_recommendations` to check for stored preferences
- If no preferences are found, naturally ask about spice tolerance and dietary needs before making suggestions
- Call `save_preferences` immediately when she learns a preference — not at the end of the conversation
- Always call `get_recommendations` before suggesting dishes, rather than guessing from memory

### Why it matters
A genuine multi-tool agent pattern: one tool to learn (save), another to act on that knowledge (recommend). Also a real production feature — a regular customer never repeats "I'm vegetarian, medium spice" again.

---

## Feature 3: Analytics Dashboard

### Problem
The admin has no visibility into business performance. Seeing which dishes are popular, when orders peak, and revenue trends requires querying the database directly.

### Solution
New `/api/analytics` endpoint (auth-protected) + new `analytics.html` admin page with Chart.js visualizations.

### Backend — `/api/analytics`

Four aggregations over the existing `orders` table:

| Dataset | Logic |
|---|---|
| Top items | Parse `items_json` in Python; count occurrences of each item name; return top 10 |
| Revenue by day | `SELECT date(created_at), SUM(total) FROM orders GROUP BY date(created_at)` — last 30 days |
| Orders by hour | `SELECT strftime('%H', created_at), COUNT(*) FROM orders GROUP BY hour` |
| Summary KPIs | Total orders, total revenue, avg order value, orders today |

Items parsed in Python (simple loop over rows) because SQLite JSON support is limited. All four datasets returned in one response. Endpoint protected with `_require_auth`.

### Frontend — `analytics.html`

Four panels:
- **Bar chart** — Top 10 most ordered items (Chart.js)
- **Line chart** — Revenue over last 30 days (Chart.js)
- **Bar chart** — Orders by hour of day / peak hours (Chart.js)
- **KPI cards** — Total orders · Total revenue · Avg order value · Orders today

Navigation: `/analytics` link added to the orders page header. Same auth guard — redirects to `/login` if session is invalid.

### Scalability note
No pre-aggregation at current scale — SQLite handles this trivially for thousands of orders. The aggregation logic is isolated in one endpoint, making it straightforward to add caching or a summary table later if volume grows.

### Why it matters
Turns a raw orders list into actionable operational intelligence. Essential for any real restaurant.

---

## Implementation Order

1. **Streaming** — highest user-visible impact, self-contained backend + frontend change
2. **Personalization** — new DB table + two tools + system prompt update
3. **Analytics** — new endpoint + new page, no changes to existing code

Each feature is independently shippable.

---

## Files Changed

| File | Change type |
|---|---|
| `server.py` | Add `/api/chat/stream`, `/api/analytics`, two new tools, `save_preferences` + `get_recommendations` tool implementations |
| `db.py` | Add `preferences` table creation + `tool_save_preferences` + `tool_get_recommendations` functions |
| `chatbot.html` | Switch from fetch to streaming fetch; add streaming UI (cursor, incremental render) |
| `orders.html` | Add analytics nav link |
| `analytics.html` | New file — Chart.js dashboard |
