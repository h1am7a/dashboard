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


def _hhmm(hours: float | str | None) -> str | None:
    if hours is None:
        return None
    if isinstance(hours, str) and ":" in hours:
        return hours
    try:
        total = int(round(float(hours) * 60)) % (24 * 60)
        return f"{total // 60:02d}:{total % 60:02d}"
    except (ValueError, TypeError):
        return str(hours)


def _parse_time_str_or_float(val: str | float | int | None) -> tuple[int, str] | tuple[None, None]:
    if val is None:
        return None, None
    s = str(val).strip()
    if not s:
        return None, None
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        return (h * 60 + mn), f"{h:02d}:{mn:02d}"
    try:
        f = float(s)
        if f.is_integer():
            h = int(f)
            return (h * 60) % (24 * 60), f"{h % 24:02d}:00"
        h = int(f)
        frac = round(f - h, 4)
        if frac == 0.5:
            mn = 30
        elif frac == 0.25:
            mn = 15
        elif frac == 0.75:
            mn = 45
        elif frac < 0.60:
            mn = int(round(frac * 100))
        else:
            mn = int(round(frac * 60))
        return (h * 60 + mn) % (24 * 60), f"{h % 24:02d}:{mn:02d}"
    except (ValueError, TypeError):
        pass
    return None, None


def _wake_target() -> tuple[int, str]:
    """Цель подъёма из facts (wake_target/target_wake): (минуты от полуночи, 'HH:MM')."""
    with store.session() as c:
        row = c.execute(
            "SELECT value FROM facts WHERE key IN ('wake_target', 'target_wake') "
            "ORDER BY CASE key WHEN 'wake_target' THEN 1 ELSE 2 END LIMIT 1"
        ).fetchone()
    if row and row["value"]:
        mins, s = _parse_time_str_or_float(row["value"])
        if mins is not None and s is not None:
            return mins, s
    return 7 * 60 + 30, "07:30"


def _bed_target() -> str:
    """Цель отбоя из facts (bed_target/target_bed): 'HH:MM'."""
    with store.session() as c:
        row = c.execute(
            "SELECT value FROM facts WHERE key IN ('bed_target', 'target_bed') "
            "ORDER BY CASE key WHEN 'bed_target' THEN 1 ELSE 2 END LIMIT 1"
        ).fetchone()
    if row and row["value"]:
        _, s = _parse_time_str_or_float(row["value"])
        if s is not None:
            return s
        return str(row["value"])
    return "23:30"


def _sleep_target() -> float:
    """Цель сна из facts (sleep_target/target_hours) или habits: количество часов (float)."""
    with store.session() as c:
        keys = ("sleep_target", "sleep_target_hours", "target_sleep", "target_hours", "sleep_hours_target", "sleep_hours")
        placeholders = ",".join("?" for _ in keys)
        row = c.execute(
            f"SELECT key, value FROM facts WHERE key IN ({placeholders}) "
            "ORDER BY CASE key "
            "WHEN 'sleep_target' THEN 1 "
            "WHEN 'sleep_target_hours' THEN 2 "
            "WHEN 'target_sleep' THEN 3 "
            "WHEN 'target_hours' THEN 4 "
            "ELSE 5 END LIMIT 1",
            keys,
        ).fetchone()
        if row and row["value"]:
            try:
                val = float(str(row["value"]).replace(",", "."))
                if 0 < val <= 24:
                    return val
            except ValueError:
                pass

        hrow = c.execute(
            "SELECT rule FROM habits WHERE (key LIKE '%sleep%' OR rule LIKE '%sleep_hours%') "
            "ORDER BY sort LIMIT 1"
        ).fetchone()
        if hrow and hrow["rule"]:
            try:
                rule = json.loads(hrow["rule"])
                if rule.get("kind") == "sleep_hours" and "value" in rule:
                    return float(rule["value"])
            except Exception:
                pass

        w_row = c.execute("SELECT value FROM facts WHERE key IN ('wake_target', 'target_wake') LIMIT 1").fetchone()
        b_row = c.execute("SELECT value FROM facts WHERE key IN ('bed_target', 'target_bed') LIMIT 1").fetchone()
        if w_row and b_row and w_row["value"] and b_row["value"]:
            w_min, _ = _parse_time_str_or_float(w_row["value"])
            b_min, _ = _parse_time_str_or_float(b_row["value"])
            if w_min is not None and b_min is not None:
                diff_min = (w_min - b_min) % (24 * 60)
                if diff_min > 0:
                    return round(diff_min / 60, 1)

    return 7.5


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
    if wake is not None:
        try:
            if isinstance(wake, str) and ":" in wake:
                wh, wm = wake.split(":")[:2]
                w_min = int(float(wh)) * 60 + int(float(wm))
            else:
                w_min = int(round(float(wake) * 60))
            delta = w_min - target_min
        except Exception:
            delta = None
    else:
        delta = None
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
            "target_wake": target_str,
            "target_bed": _bed_target(),
            "target_hours": _sleep_target(),
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
        prev_rows = c.execute(
            "SELECT day, sleep_hours, wake_time, bed_time FROM v_daily "
            "WHERE day BETWEEN ? AND ?",
            ((start - timedelta(days=days)).isoformat(), (start - timedelta(days=1)).isoformat()),
        ).fetchall()
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
    def _to_min(val):
        if isinstance(val, str) and ":" in val:
            h, m = val.split(":")[:2]
            return int(float(h)) * 60 + int(float(m))
        return float(val) * 60

    def _to_bed_min(val):
        m = _to_min(val)
        return m + 24 * 60 if m < 12 * 60 else m

    values = [s["hours"] for s in series if s["hours"] is not None]
    avg = round(sum(values) / len(values), 2) if values else None

    prev_sleep_vals = [r["sleep_hours"] for r in prev_rows if r["sleep_hours"] is not None]
    prev_avg = round(sum(prev_sleep_vals) / len(prev_sleep_vals), 2) if prev_sleep_vals else None
    delta_hours = round(avg - prev_avg, 2) if avg is not None and prev_avg is not None else None

    cur_wake_vals = [_to_min(r["wake_time"]) for r in rows if r["wake_time"] is not None]
    prev_wake_vals = [_to_min(r["wake_time"]) for r in prev_rows if r["wake_time"] is not None]
    cur_wake_avg = sum(cur_wake_vals) / len(cur_wake_vals) if cur_wake_vals else None
    prev_wake_avg = sum(prev_wake_vals) / len(prev_wake_vals) if prev_wake_vals else None
    delta_wake_min = int(round(cur_wake_avg - prev_wake_avg)) if (cur_wake_avg is not None and prev_wake_avg is not None) else None

    cur_bed_vals = [_to_bed_min(r["bed_time"]) for r in rows if r["bed_time"] is not None]
    prev_bed_vals = [_to_bed_min(r["bed_time"]) for r in prev_rows if r["bed_time"] is not None]
    cur_bed_avg = sum(cur_bed_vals) / len(cur_bed_vals) if cur_bed_vals else None
    prev_bed_avg = sum(prev_bed_vals) / len(prev_bed_vals) if prev_bed_vals else None
    delta_bed_min = int(round(cur_bed_avg - prev_bed_avg)) if (cur_bed_avg is not None and prev_bed_avg is not None) else None

    def _format_min(total_m: float | None) -> str | None:
        if total_m is None:
            return None
        m = int(round(total_m)) % (24 * 60)
        return f"{m // 60:02d}:{m % 60:02d}"

    avg_wake_str = _format_min(cur_wake_avg)
    avg_bed_str = _format_min(cur_bed_avg)

    target_min, target_str = _wake_target()
    avg_delta = int(round(cur_wake_avg - target_min)) if cur_wake_avg is not None else None

    return {
        "period": {"from": start.isoformat(), "to": end.isoformat(), "days": days},
        "avg_hours": avg,
        "avg_wake": avg_wake_str,
        "avg_bed": avg_bed_str,
        "avg_delta_min": avg_delta,
        "target_hours": _sleep_target(),
        "target_wake": target_str,
        "target_bed": _bed_target(),
        "series": series,
        "prev_week": {
            "avg_hours": prev_avg,
            "delta_hours": delta_hours,
            "significant": bool(delta_hours is not None and abs(delta_hours) >= 0.2),
            "delta_wake_min": delta_wake_min,
            "delta_bed_min": delta_bed_min,
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
