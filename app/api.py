"""FastAPI backend for Priest Life Telegram Mini App.

Implements the official API contract according to TZ-tg-app.md (v1.0).
Provides endpoints:
- GET  /api/today
- GET  /api/sleep?days=7|30|90
- GET  /api/body
- POST /api/tasks/{id}/done
- GET  /api/health
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config, logic, store

BASE_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = BASE_DIR / "web" / "dist"
WEB_DIR = BASE_DIR / "web"

app = FastAPI(title="Priest Life Mini App API", version="1.0.0")

# Error handler matching TZ contract: {"error": {"code": "...", "message": "..."}}
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": str(exc.status_code), "message": exc.detail}},
    )


# -----------------------------------------------------------------------------
# 4.1 Authorization Dependency
# -----------------------------------------------------------------------------
def get_current_user(
    request: Request,
    x_init_data: str | None = Header(None, alias="X-Init-Data"),
    x_tg_init_data: str | None = Header(None, alias="X-Telegram-Init-Data"),
) -> dict:
    """Validate Telegram WebApp initData HMAC-SHA256 signature."""
    client_host = request.client.host if request.client else ""
    is_local = client_host in ("127.0.0.1", "localhost", "testclient")

    # Local dev bypass
    if (getattr(config, "DEV_USER_ID", None) or getattr(config, "DEV_MODE", False)) and is_local:
        if not x_init_data and not x_tg_init_data:
            return {
                "id": getattr(config, "DEV_USER_ID", None) or config.ALLOWED_USER_ID,
                "first_name": "DevUser",
                "is_dev": True,
            }

    raw_init_data = x_init_data or x_tg_init_data
    if not raw_init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Init-Data authorization header",
        )

    if not config.BOT_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="BOT_TOKEN not configured on server",
        )

    try:
        parsed = dict(parse_qsl(raw_init_data, keep_blank_values=True))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid initData format",
        )

    if "hash" not in parsed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing hash in initData",
        )

    received_hash = parsed.pop("hash")

    # Verify auth_date within 24 hours
    auth_date = int(parsed.get("auth_date", 0))
    if time.time() - auth_date > 86400:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="initData authorization expired",
        )

    # Reconstruct data_check_string
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid initData signature",
        )

    user_raw = parsed.get("user")
    if not user_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing user in initData",
        )

    try:
        user_data = json.loads(unquote(user_raw))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cannot parse user in initData",
        )

    user_id = user_data.get("id")
    allowed_id = getattr(config, "ALLOWED_USER_ID", None)
    if allowed_id and user_id != allowed_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden for this user_id",
        )

    return user_data


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def get_clean_wake_target(con) -> tuple[str, float]:
    """Returns ('14:30', 14.5) parsed from facts.wake_target."""
    cur = con.execute("SELECT value FROM facts WHERE key='wake_target'").fetchone()
    raw = cur["value"] if cur else "14:30"
    match = re.search(r"\d{1,2}:\d{2}", raw)
    time_str = match.group(0) if match else "14:30"
    dec = logic.time_str_to_decimal(time_str)
    return time_str, dec


def calculate_day_score(con, day_str: str, wake_target_dec: float) -> dict:
    """Calculates active anchors + due tasks for a single day."""
    row = con.execute(
        "SELECT sleep_hours, wake_time, protein_ok, routine_ok FROM v_daily WHERE day=?",
        (day_str,),
    ).fetchone()

    has_row = row is not None and any(v is not None for v in dict(row).values())
    has_wo = bool(con.execute("SELECT 1 FROM workouts WHERE date=?", (day_str,)).fetchone())

    # Check tasks due on this day
    tasks = store.rows(con, "SELECT id, title, status FROM tasks WHERE due=?", (day_str,))

    # If day has zero records anywhere in DB
    if not has_row and not has_wo and not tasks:
        return {"done": 0, "total": 0, "percent": None, "items": []}

    sleep_val = row["sleep_hours"] if row and row["sleep_hours"] is not None else None
    wake_val = row["wake_time"] if row and row["wake_time"] is not None else None
    protein_val = row["protein_ok"] if row and row["protein_ok"] is not None else None
    routine_val = row["routine_ok"] if row and row["routine_ok"] is not None else None

    # Calculate delta_min for wake_window
    wake_window_ok = False
    if wake_val is not None:
        delta_m = int(round((wake_val - wake_target_dec) * 60))
        wake_window_ok = abs(delta_m) <= 60

    items = [
        {"key": "sleep_ge_7", "title": "Sleep ≥ 7 hrs", "done": sleep_val is not None and sleep_val >= 7.0, "type": "habit"},
        {"key": "wake_window", "title": "Wake window ±60m", "done": wake_window_ok, "type": "habit"},
        {"key": "workout_day", "title": "Workout session", "done": has_wo, "type": "habit"},
        {"key": "protein_ok", "title": "Protein with 1st meal", "done": protein_val == 1.0, "type": "habit"},
        {"key": "routine_eve", "title": "Evening routine", "done": routine_val == 1.0, "type": "habit"},
    ]

    for t in tasks:
        items.append({
            "key": f"task:{t['id']}",
            "title": t["title"],
            "done": t["status"] == "done",
            "type": "task",
        })

    done_count = sum(1 for it in items if it["done"])
    total_count = len(items)
    pct = round(done_count / total_count * 100) if total_count > 0 else None

    return {
        "done": done_count,
        "total": total_count,
        "percent": pct,
        "items": items,
    }


# -----------------------------------------------------------------------------
# 4.2 GET /api/today
# -----------------------------------------------------------------------------
@app.get("/api/today")
def get_today(user: dict = Depends(get_current_user)):
    with store.session() as con:
        logical_day = logic.get_logical_day()
        dt_day = date.fromisoformat(logical_day)
        weekday_name = dt_day.strftime("%A")

        target_str, target_dec = get_clean_wake_target(con)

        # Query v_daily for today
        row = con.execute(
            "SELECT sleep_hours, wake_time, bed_time, mood FROM v_daily WHERE day=?",
            (logical_day,),
        ).fetchone()

        sleep_hours = float(row["sleep_hours"]) if row and row["sleep_hours"] is not None else None
        wake_time_dec = float(row["wake_time"]) if row and row["wake_time"] is not None else None
        bed_time_dec = float(row["bed_time"]) if row and row["bed_time"] is not None else None

        wake_str = logic.decimal_to_time_str(wake_time_dec) if wake_time_dec is not None else None
        bed_str = logic.decimal_to_time_str(bed_time_dec) if bed_time_dec is not None else None

        delta_min = None
        if wake_time_dec is not None:
            delta_min = int(round((wake_time_dec - target_dec) * 60))

        # Mood (2: Bad, 4: Meh, 6: Ok, 8: Good, 10: Great)
        mood_row = con.execute(
            "SELECT value FROM metrics WHERE kind='mood' AND ts=? ORDER BY id DESC LIMIT 1",
            (logical_day,),
        ).fetchone()
        mood_val = int(round(float(mood_row["value"]))) if mood_row and mood_row["value"] is not None else (
            int(round(float(row["mood"]))) if row and row["mood"] is not None else None
        )

        mood_label = None
        if mood_val is not None:
            if mood_val <= 2:
                mood_label = "Bad"
            elif mood_val <= 4:
                mood_label = "Meh"
            elif mood_val <= 6:
                mood_label = "Ok"
            elif mood_val <= 8:
                mood_label = "Good"
            else:
                mood_label = "Great"

        # Today score
        score_data = calculate_day_score(con, logical_day, target_dec)

        # Hitmap: 126 days (18 weeks)
        # End at logical_day (or following Sunday to complete the 18-week grid)
        # 126 days total
        hitmap = []
        for i in range(125, -1, -1):
            d_i = (dt_day - timedelta(days=i)).isoformat()
            d_score = calculate_day_score(con, d_i, target_dec)
            has_wo = bool(con.execute("SELECT 1 FROM workouts WHERE date=?", (d_i,)).fetchone())
            hitmap.append({
                "day": d_i,
                "done": d_score["done"],
                "total": d_score["total"],
                "percent": d_score["percent"],
                "has_workout": has_wo,
            })

        return {
            "day": logical_day,
            "weekday": weekday_name,
            "in_progress": True,
            "score": score_data,
            "sleep": {
                "hours": sleep_hours,
                "bed": bed_str,
                "wake": wake_str,
                "target": target_str,
                "delta_min": delta_min,
            },
            "mood": {
                "value": mood_val,
                "label": mood_label,
            },
            "hitmap": hitmap,
        }


# -----------------------------------------------------------------------------
# 4.3 GET /api/sleep?days=7|30|90
# -----------------------------------------------------------------------------
@app.get("/api/sleep")
def get_sleep(days: int = Query(7), user: dict = Depends(get_current_user)):
    if days not in (7, 30, 90):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parameter 'days' must be 7, 30, or 90",
        )

    with store.session() as con:
        logical_day = logic.get_logical_day()
        end_d = date.fromisoformat(logical_day)
        start_d = end_d - timedelta(days=days - 1)

        target_str, target_dec = get_clean_wake_target(con)

        rows = store.rows(
            con,
            "SELECT day, sleep_hours, wake_time, bed_time FROM v_daily "
            "WHERE day >= ? AND day <= ? ORDER BY day",
            (start_d.isoformat(), end_d.isoformat()),
        )
        data_by_day = {r["day"]: r for r in rows}

        series = []
        hours_list = []
        delta_list = []

        for i in range(days):
            d_str = (start_d + timedelta(days=i)).isoformat()
            r = data_by_day.get(d_str)

            sh = float(r["sleep_hours"]) if r and r["sleep_hours"] is not None else None
            wt = float(r["wake_time"]) if r and r["wake_time"] is not None else None
            bt = float(r["bed_time"]) if r and r["bed_time"] is not None else None

            if sh is not None:
                hours_list.append(sh)
            if wt is not None:
                delta_list.append(int(round((wt - target_dec) * 60)))

            series.append({
                "day": d_str,
                "hours": sh,
                "wake": logic.decimal_to_time_str(wt) if wt is not None else None,
                "bed": logic.decimal_to_time_str(bt) if bt is not None else None,
            })

        avg_hours = round(sum(hours_list) / len(hours_list), 1) if hours_list else None
        avg_delta_min = int(round(sum(delta_list) / len(delta_list))) if delta_list else None

        # Compare with previous 7-day period
        prev_start = start_d - timedelta(days=7)
        prev_end = start_d - timedelta(days=1)
        prev_rows = store.rows(
            con,
            "SELECT sleep_hours FROM v_daily WHERE day >= ? AND day <= ? AND sleep_hours IS NOT NULL",
            (prev_start.isoformat(), prev_end.isoformat()),
        )
        prev_hours = [float(r["sleep_hours"]) for r in prev_rows if r["sleep_hours"] is not None]
        prev_avg = round(sum(prev_hours) / len(prev_hours), 1) if prev_hours else None

        # Last 7 days avg for comparison
        last_7_hours = [s["hours"] for s in series[-7:] if s["hours"] is not None]
        last_7_avg = round(sum(last_7_hours) / len(last_7_hours), 1) if last_7_hours else None

        delta_hours = None
        significant = False
        if last_7_avg is not None and prev_avg is not None:
            delta_hours = round(last_7_avg - prev_avg, 1)
            significant = abs(delta_hours) >= 0.5

        return {
            "period": {
                "from": start_d.isoformat(),
                "to": end_d.isoformat(),
                "days": days,
            },
            "avg_hours": avg_hours,
            "avg_delta_min": avg_delta_min,
            "target_hours": 7.5,
            "target_wake": target_str,
            "series": series,
            "prev_week": {
                "avg_hours": prev_avg,
                "delta_hours": delta_hours,
                "significant": significant,
            },
        }


# -----------------------------------------------------------------------------
# 4.4 GET /api/body
# -----------------------------------------------------------------------------
@app.get("/api/body")
def get_body(user: dict = Depends(get_current_user)):
    with store.session() as con:
        # 4 cards strictly in order: waist, biceps_r, forearm_r, thigh_r
        kinds_spec = [
            ("waist", "Waist", "cm", "weekly"),
            ("biceps_r", "Bicep", "cm", "monthly"),
            ("forearm_r", "Forearm", "cm", "monthly"),
            ("thigh_r", "Thigh", "cm", "monthly"),
        ]

        measurements = []
        for kind, label, unit, cadence in kinds_spec:
            records = store.rows(
                con,
                "SELECT ts, value FROM metrics WHERE kind=? ORDER BY ts DESC, id DESC LIMIT 10",
                (kind,),
            )
            if records:
                latest_val = float(records[0]["value"])
                latest_date = records[0]["ts"][:10]
                delta_cm = None
                since_date = None
                significant = False

                if len(records) > 1:
                    prev_val = float(records[1]["value"])
                    since_date = records[1]["ts"][:10]
                    delta_cm = round(latest_val - prev_val, 1)
                    significant = abs(delta_cm) >= 1.0

                history = [
                    {"date": r["ts"][:10], "value": float(r["value"])}
                    for r in reversed(records)
                ]

                measurements.append({
                    "kind": kind,
                    "label": label,
                    "unit": unit,
                    "latest": latest_val,
                    "date": latest_date,
                    "delta_cm": delta_cm,
                    "significant": significant,
                    "since": since_date,
                    "cadence": cadence,
                    "history": history,
                })
            else:
                measurements.append({
                    "kind": kind,
                    "label": label,
                    "unit": unit,
                    "latest": None,
                    "date": None,
                    "delta_cm": None,
                    "significant": False,
                    "since": None,
                    "cadence": cadence,
                    "history": [],
                })

        # Thigh asymmetry (thigh_r vs thigh_l)
        tr = con.execute(
            "SELECT ts, value FROM metrics WHERE kind='thigh_r' ORDER BY ts DESC, id DESC LIMIT 1"
        ).fetchone()
        tl = con.execute(
            "SELECT ts, value FROM metrics WHERE kind='thigh_l' ORDER BY ts DESC, id DESC LIMIT 1"
        ).fetchone()

        thigh_asymmetry = None
        if tr and tl and tr["value"] is not None and tl["value"] is not None:
            r_val = float(tr["value"])
            l_val = float(tl["value"])
            thigh_asymmetry = {
                "date": tr["ts"][:10],
                "right": r_val,
                "left": l_val,
                "diff": round(r_val - l_val, 1),
            }

        # Workouts for last 8 weeks (56 days)
        w_rows = store.rows(
            con,
            "SELECT date, kind, duration_min, load, note FROM workouts "
            "WHERE date >= date('now', '-56 days') ORDER BY date DESC, id DESC",
        )
        workouts = [
            {
                "date": w["date"],
                "kind": w["kind"],
                "duration_min": w["duration_min"],
                "load": w["load"],
                "note": w["note"] or "",
            }
            for w in w_rows
        ]

        return {
            "measurements": measurements,
            "thigh_asymmetry": thigh_asymmetry,
            "workouts": workouts,
        }


# -----------------------------------------------------------------------------
# 4.5 POST /api/tasks/{id}/done
# -----------------------------------------------------------------------------
@app.post("/api/tasks/{task_id}/done")
def mark_task_done(task_id: int, user: dict = Depends(get_current_user)):
    with store.session() as con:
        row = con.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task id={task_id} not found",
            )
        if row["status"] == "done":
            # Idempotent return according to TZ section 4.5
            return {"ok": True}

    try:
        store.close_task(task_id, "done")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot close task: {e}",
        )
    return {"ok": True}


# -----------------------------------------------------------------------------
# 4.6 GET /api/health
# -----------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "version": "1.0"}


# -----------------------------------------------------------------------------
# 501 for unhandled /api/*
# -----------------------------------------------------------------------------
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def unhandled_api(path: str):
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"API endpoint '/api/{path}' is not implemented",
    )


# -----------------------------------------------------------------------------
# Static files & SPA fallback
# -----------------------------------------------------------------------------
assets_dir = DIST_DIR / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    # Prevent intercepting /api
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")

    # Serve static file if exists in DIST_DIR or WEB_DIR
    if DIST_DIR.exists():
        candidate = DIST_DIR / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        index_file = DIST_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))

    candidate = WEB_DIR / full_path
    if candidate.is_file():
        return FileResponse(str(candidate))

    index_fallback = WEB_DIR / "index.html"
    if index_fallback.exists():
        return FileResponse(str(index_fallback))

    return {"message": "Priest Life API running. Frontend not found."}
