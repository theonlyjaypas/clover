"""
CLOVE Orders Server — FastAPI orders management dashboard with admin auth.
Serves login.html / orders.html and handles /api/orders CRUD + auth endpoints.

Run with:
    uvicorn orders_server:app --reload --port 8001
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from ..database.db import DB_PATH, _ensure_orders_table

app = FastAPI(title="CLOVE Orders API")

FRONTEND_PATH = Path(__file__).parent.parent.parent / "static/templates/orders.html"
LOGIN_PATH    = Path(__file__).parent.parent.parent / "static/templates/login.html"


# ── Auth ──────────────────────────────────────────────────────────────────────

_ADMIN_USERNAME = "jaypas"

# pbkdf2-sha256, 260 000 iterations; hash of "R3load24"
_PASS_SALT = "c3f8a2d14e7b9056f2e1a3c4d5b6f789"
_PASS_HASH = hashlib.pbkdf2_hmac(
    "sha256", b"R3load24", _PASS_SALT.encode(), 260_000
).hex()

# In-memory session store: token -> expiry (unix timestamp)
_sessions: dict[str, float] = {}
_SESSION_TTL = 8 * 3600   # 8 hours
_COOKIE     = "clove_session"


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
    """Raises HTTP 401 when the session cookie is missing or expired."""
    if not _is_valid(request.cookies.get(_COOKIE)):
        raise HTTPException(status_code=401, detail="Not authenticated")


# ── Pydantic models ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class StatusUpdate(BaseModel):
    status: str


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.get("/login")
async def serve_login():
    return FileResponse(LOGIN_PATH)


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


# ── Orders endpoints ──────────────────────────────────────────────────────────

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


@app.patch("/api/orders/{order_id}/status")
async def update_order_status(order_id: int, body: StatusUpdate, request: Request):
    _require_auth(request)
    valid = {"Pending", "In Progress", "Ready", "Completed", "Cancelled"}
    if body.status not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(sorted(valid))}",
        )
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


# ── Serve orders frontend ─────────────────────────────────────────────────────

@app.get("/")
async def serve_frontend(request: Request):
    if not _is_valid(request.cookies.get(_COOKIE)):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(FRONTEND_PATH)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orders_server:app", host="0.0.0.0", port=8001, reload=True)
