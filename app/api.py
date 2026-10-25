"""HTTP-слой панели priest.

Только чтение базы (через app.store) и одна запись — отметить задачу.
Расчёты живут в app/store.py, здесь ничего не пересчитывается.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import store

MSK = timezone(timedelta(hours=3))
DAY_START_HOUR = 9

app = FastAPI(title="priest panel", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
def _http_exc_handler(request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(exc.detail, status_code=exc.status_code)
    code = exc.detail.get("code", str(exc.status_code)) if isinstance(exc.detail, dict) else str(exc.status_code)
    msg = exc.detail.get("message", str(exc.detail)) if isinstance(exc.detail, dict) else str(exc.detail)
    return JSONResponse({"error": {"code": code, "message": msg}}, status_code=exc.status_code)


MOOD_LABELS = [(2, "Bad"), (4, "Meh"), (6, "Ok"), (8, "Good"), (10, "Great")]
BODY_META = {
    "waist": ("Waist", "weekly"),
    "biceps_r": ("Biceps", "monthly"),
    "forearm_r": ("Forearm", "monthly"),
    "thigh_r": ("Thigh (R)", "monthly"),
}
BODY_ORDER = ["waist", "biceps_r", "forearm_r", "thigh_r"]


# --- время и формат -------------------------------------------------------

def _now() -> datetime:
    return datetime.now(MSK)


def logical_day(now: datetime | None = None) -> str:
    """Логический день начинается в 09:00 MSK."""
    now = now or _now()
    d = now.date()
    if now.hour < DAY_START_HOUR:
        d -= timedelta(days=1)
    return d.isoformat()


def _hhmm(hours: float | None) -> str | None:
    if hours is None:
        return None
    total = int(round(float(hours) * 60)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _wake_target() -> tuple[int, str]:
    """Цель подъёма из facts.wake_target: (минуты от полуночи, 'HH:MM')."""
    with store.session() as c:
        row = c.execute("SELECT value FROM facts WHERE key='wake_target'").fetchone()
    raw = row["value"] if row else "14:30"
    m = re.search(r"(\d{1,2}):(\d{2})", raw or "")
    if m:
        minutes = int(m.group(1)) * 60 + int(m.group(2))
    else:
        minutes = 14 * 60 + 30
    return minutes, f"{minutes // 60:02d}:{minutes % 60:02d}"


def _mood_label(value) -> str | None:
    if value is None:
        return None
    return min(MOOD_LABELS, key=lambda p: abs(p[0] - float(value)))[1]


# --- авторизация Telegram ------------------------------------------------

def _validate_init_data(raw: str) -> dict:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise HTTPException(500, {"error": {"code": "no_token", "message": "BOT_TOKEN is not set"}})
    pairs = dict(parse_qsl(raw, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        raise HTTPException(401, {"error": {"code": "no_hash", "message": "missing hash"}})
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        raise HTTPException(401, {"error": {"code": "bad_signature", "message": "invalid signature"}})
    auth_date = int(pairs.get("auth_date", "0") or 0)
    if _now().timestamp() - auth_date > 86400:
        raise HTTPException(401, {"error": {"code": "expired", "message": "initData expired"}})
    allowed = os.environ.get("ALLOWED_USER_ID")
    user = json.loads(pairs.get("user") or "{}")
    if allowed and str(user.get("id")) != str(allowed):
        raise HTTPException(403, {"error": {"code": "forbidden", "message": "forbidden user_id"}})
    return user


def _auth(x_init_data: str | None = None, x_telegram_init_data: str | None = None) -> dict:
    data = x_init_data or x_telegram_init_data
    dev = os.environ.get("DEV_USER_ID")
    dev_mode = os.environ.get("DEV_MODE")
    if not data:
        if dev:
            return {"id": int(dev), "dev": True}
        if dev_mode and dev_mode.lower() in ("1", "true", "yes"):
            return {"id": 1, "dev": True}
        raise HTTPException(401, {"error": {"code": "no_init_data", "message": "missing X-Init-Data"}})
    return _validate_init_data(data)


# --- эндпоинты -----------------------------------------------------------

@app.get("/api/health")
def health(
    x_init_data: str | None = Header(default=None),
    x_telegram_init_data: str | None = Header(default=None),
):
    return {"ok": True, "version": "1.0"}


@app.get("/api/today")
def today(
    x_init_data: str | None = Header(default=None),
    x_telegram_init_data: str | None = Header(default=None),
):
    _auth(x_init_data, x_telegram_init_data)
    day = logical_day()
    score = store.day_score(day)
    with store.session() as c:
        row = c.execute("SELECT * FROM v_daily WHERE day=?", (day,)).fetchone()
    target_min, target_str = _wake_target()
    wake = row["wake_time"] if row else None
    delta = int(round((float(wake) * 60 - target_min))) if wake is not None else None
    stats = {
        "day": day,
        "weekday": date_cls.fromisoformat(day).strftime("%A"),
        "in_progress": True,
        "score": score,
        "sleep": {
            "hours": row["sleep_hours"] if row else None,
            "bed": _hhmm(row["bed_time"] if row else None),
            "wake": _hhmm(wake),
            "target": target_str,
            "delta_min": delta,
        },
        "mood": {
            "value": (row["mood"] if row else None),
            "label": _mood_label(row["mood"] if row else None),
        },
        "hitmap": store.hitmap(126),
    }
    return stats


@app.get("/api/sleep")
def sleep(
    days: int = Query(default=7),
    x_init_data: str | None = Header(default=None),
    x_telegram_init_data: str | None = Header(default=None),
):
    _auth(x_init_data, x_telegram_init_data)
    if days not in (7, 30, 90):
        raise HTTPException(400, {"error": {"code": "bad_period", "message": "days must be 7, 30, or 90"}})
    end = date_cls.fromisoformat(_today_date())
    start = end - timedelta(days=days - 1)
    with store.session() as c:
        rows = c.execute(
            "SELECT day, sleep_hours, wake_time, bed_time FROM v_daily "
            "WHERE day BETWEEN ? AND ? ORDER BY day", (start.isoformat(), end.isoformat())
        ).fetchall()
        prev = c.execute(
            "SELECT AVG(sleep_hours) a FROM v_daily WHERE sleep_hours IS NOT NULL "
            "AND day BETWEEN ? AND ?",
            ((start - timedelta(days=days)).isoformat(), (start - timedelta(days=1)).isoformat()),
        ).fetchone()
    by_day = {r["day"]: r for r in rows}
    series = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        r = by_day.get(d)
        series.append({
            "day": d,
            "hours": r["sleep_hours"] if r else None,
            "wake": _hhmm(r["wake_time"]) if r else None,
            "bed": _hhmm(r["bed_time"]) if r else None,
        })
    values = [s["hours"] for s in series if s["hours"] is not None]
    avg = round(sum(values) / len(values), 2) if values else None
    prev_avg = round(prev["a"], 2) if prev and prev["a"] is not None else None
    target_min, target_str = _wake_target()
    wakes = [r["wake_time"] for r in rows if r["wake_time"] is not None]
    avg_delta = int(round(sum(float(w) * 60 for w in wakes) / len(wakes) - target_min)) if wakes else None
    delta_hours = round(avg - prev_avg, 2) if avg is not None and prev_avg is not None else None
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat(), "days": days},
        "avg_hours": avg,
        "avg_delta_min": avg_delta,
        "target_hours": 7.5,
        "target_wake": target_str,
        "series": series,
        "prev_week": {
            "avg_hours": prev_avg,
            "delta_hours": delta_hours,
            "significant": bool(delta_hours is not None and abs(delta_hours) >= 0.5),
        },
    }


def _today_date() -> str:
    return _now().date().isoformat()


@app.get("/api/body")
def body(
    x_init_data: str | None = Header(default=None),
    x_telegram_init_data: str | None = Header(default=None),
):
    _auth(x_init_data, x_telegram_init_data)
    raw = store.measurements(365, BODY_ORDER)
    out = []
    for m in raw:
        label, cadence = BODY_META.get(m["kind"], (m["kind"], "monthly"))
        out.append({**m, "label": label, "cadence": cadence})
    since = (date_cls.fromisoformat(_today_date()) - timedelta(days=56)).isoformat()
    with store.session() as c:
        wrows = c.execute(
            "SELECT date, kind, duration_min, load, note FROM workouts "
            "WHERE date >= ? ORDER BY date DESC", (since,)
        ).fetchall()
    return {
        "measurements": out,
        "thigh_asymmetry": store.thigh_asymmetry(),
        "workouts": [dict(r) for r in wrows],
    }


@app.post("/api/tasks/{task_id}/done")
def task_done(
    task_id: int,
    x_init_data: str | None = Header(default=None),
    x_telegram_init_data: str | None = Header(default=None),
):
    _auth(x_init_data, x_telegram_init_data)
    with store.session() as c:
        row = c.execute("SELECT id, status FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, {"error": {"code": "not_found", "message": "task not found"}})
    if row["status"] != "done":
        store.close_task(task_id)
    return {"ok": True}


@app.api_route("/api/{unhandled_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def api_unhandled(unhandled_path: str):
    return JSONResponse(
        status_code=501,
        content={"error": {"code": "not_implemented", "message": "endpoint not implemented"}},
    )


# --- static & assets ---------------------------------------------------

_WEB_BASE = Path(__file__).resolve().parent.parent / "web"
_DIST = _WEB_BASE / "dist"
_STATIC_ROOT = _DIST if (_DIST / "index.html").is_file() and (_DIST / "assets").is_dir() else _WEB_BASE

_ASSETS_DIR = _STATIC_ROOT / "assets" if (_STATIC_ROOT / "assets").is_dir() else _WEB_BASE / "assets"
if _ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

@app.get("/{path:path}")
def spa(path: str):
    if path.startswith("api/") or path == "api":
        return JSONResponse(
            status_code=501,
            content={"error": {"code": "not_implemented", "message": "endpoint not implemented"}},
        )

    forbidden_exts = (".py", ".db", ".sqlite", ".env", ".sh", ".sql", ".md", ".toml", ".ini")
    clean = path.lower()
    if ".." in clean or clean.startswith("/") or clean.endswith(forbidden_exts) or "/." in clean or clean.startswith("."):
        raise HTTPException(404, {"error": {"code": "not_found", "message": "file not found"}})

    if path:
        target = (_STATIC_ROOT / path).resolve()
        if target.is_file() and target.is_relative_to(_STATIC_ROOT.resolve()):
            return FileResponse(str(target))
        target_web = (_WEB_BASE / path).resolve()
        if target_web.is_file() and target_web.is_relative_to(_WEB_BASE.resolve()):
            return FileResponse(str(target_web))

    index_file = _STATIC_ROOT / "index.html"
    if index_file.is_file():
        return FileResponse(str(index_file))
    index_web = _WEB_BASE / "index.html"
    if index_web.is_file():
        return FileResponse(str(index_web))
    raise HTTPException(404, {"error": {"code": "not_found", "message": "index.html not found"}})
