from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from app import auth, logic, store

router = APIRouter(prefix="/api", tags=["week"])


@router.get("/week")
def get_week_data(
    offset: int = Query(0, ge=0, le=52),
    user: dict = Depends(auth.get_current_user),
):
    """Data for Week view.
    
    offset=0: rolling 7 days up to current logical day.
    offset>0: shifted back by offset weeks (7 * offset days).
    """
    logical_today = logic.get_logical_day()
    anchor_date = date.fromisoformat(logical_today) - timedelta(days=offset * 7)
    start_date = anchor_date - timedelta(days=6)
    
    prev_anchor = anchor_date - timedelta(days=7)
    prev_start = start_date - timedelta(days=7)

    with store.session() as con:
        # Rows for current week window
        rows_current = store.rows(
            con,
            "SELECT * FROM v_daily WHERE day>=? AND day<=? ORDER BY day DESC",
            (start_date.isoformat(), anchor_date.isoformat()),
        )
        curr_map = {r["day"][:10]: r for r in rows_current}

        # Rows for previous week window for delta calculations
        rows_prev = store.rows(
            con,
            "SELECT * FROM v_daily WHERE day>=? AND day<=? ORDER BY day DESC",
            (prev_start.isoformat(), prev_anchor.isoformat()),
        )
        
        # Assemble 7 rows of table (from anchor backwards to start)
        days_table = []
        c = anchor_date
        has_data_count = 0
        while c >= start_date:
            iso_d = c.isoformat()
            d_row = curr_map.get(iso_d)
            if d_row:
                has_data_count += 1
                days_table.append({
                    "day": iso_d,
                    "date_formatted": logic.format_date_en(iso_d),
                    "is_today": (iso_d == logical_today),
                    "wake_time": logic.decimal_to_time_str(d_row.get("wake_time")),
                    "bed_time": logic.decimal_to_time_str(d_row.get("bed_time")),
                    "sleep_hours": round(d_row["sleep_hours"], 1) if d_row.get("sleep_hours") is not None else None,
                    "mood": d_row.get("mood"),
                    "porn_count": d_row.get("porn_count", 0),
                    "has_workout": bool(d_row.get("workout_count", 0) > 0),
                })
            else:
                days_table.append({
                    "day": iso_d,
                    "date_formatted": logic.format_date_en(iso_d),
                    "is_today": (iso_d == logical_today),
                    "wake_time": "—",
                    "bed_time": "—",
                    "sleep_hours": None,
                    "mood": None,
                    "porn_count": None,
                    "has_workout": False,
                })
            c -= timedelta(days=1)

        # Deltas: Sleep (average)
        curr_sleep_vals = [r["sleep_hours"] for r in rows_current if r.get("sleep_hours") is not None]
        prev_sleep_vals = [r["sleep_hours"] for r in rows_prev if r.get("sleep_hours") is not None]
        curr_sleep_avg = round(sum(curr_sleep_vals) / len(curr_sleep_vals), 1) if curr_sleep_vals else None
        prev_sleep_avg = round(sum(prev_sleep_vals) / len(prev_sleep_vals), 1) if prev_sleep_vals else None
        sleep_delta = round(curr_sleep_avg - prev_sleep_avg, 1) if (curr_sleep_avg and prev_sleep_avg) else None

        # Deltas: Workouts (count of days with workouts)
        curr_wo_count = sum(1 for r in rows_current if r.get("workout_count", 0) > 0)
        prev_wo_count = sum(1 for r in rows_prev if r.get("workout_count", 0) > 0)
        wo_delta = curr_wo_count - prev_wo_count

        # Deltas: Screen time
        curr_screen_row = con.execute(
            "SELECT value FROM metrics WHERE kind='screen_time' AND ts>=? AND ts<=? ORDER BY ts DESC LIMIT 1",
            (start_date.isoformat(), anchor_date.isoformat()),
        ).fetchone()
        prev_screen_row = con.execute(
            "SELECT value FROM metrics WHERE kind='screen_time' AND ts>=? AND ts<=? ORDER BY ts DESC LIMIT 1",
            (prev_start.isoformat(), prev_anchor.isoformat()),
        ).fetchone()

        curr_screen = float(curr_screen_row["value"]) if curr_screen_row else None
        prev_screen = float(prev_screen_row["value"]) if prev_screen_row else None
        screen_delta = round(curr_screen - prev_screen, 1) if (curr_screen and prev_screen) else None

    range_title = f"{logic.format_date_en(start_date.isoformat())} – {logic.format_date_en(anchor_date.isoformat())}"

    return {
        "range_title": range_title,
        "offset": offset,
        "has_data_days": has_data_count,
        "days": days_table,
        "deltas": {
            "sleep": {
                "curr": curr_sleep_avg,
                "prev": prev_sleep_avg,
                "delta": sleep_delta,
                "label": f"{prev_sleep_avg or '—'} → {curr_sleep_avg or '—'} ({'+' if sleep_delta and sleep_delta > 0 else ''}{sleep_delta or '—'} h)"
            },
            "workouts": {
                "curr": curr_wo_count,
                "prev": prev_wo_count,
                "delta": wo_delta,
                "label": f"{prev_wo_count} → {curr_wo_count} ({'+' if wo_delta > 0 else ''}{wo_delta})"
            },
            "screen": {
                "curr": curr_screen,
                "prev": prev_screen,
                "delta": screen_delta,
                "target": 50.0,
                "percent": int(min(round((curr_screen / 50.0) * 100), 100)) if curr_screen else 0
            }
        }
    }
