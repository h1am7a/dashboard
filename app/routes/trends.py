from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from app import auth, logic, store

router = APIRouter(prefix="/api", tags=["trends"])

ALLOWED_TREND_KINDS = {
    "sleep_hours": "Sleep (hrs)",
    "wake_time": "Wake Time (phase)",
    "bed_time": "Bedtime",
    "mood": "Mood",
    "screen_time": "Screen Time (hrs)",
    "porn_count": "Masturbation (count)",
    "waist": "Waist Circumference (cm)",
    "weight": "Weight (kg)",
}


@router.get("/trends")
def get_trends(
    kind: str = Query("sleep_hours"),
    days: int = Query(30, ge=7, le=180),
    user: dict = Depends(auth.get_current_user),
):
    """Data for Trends view."""
    kind = kind.strip().lower()
    if kind not in store.METRIC_KINDS:
        raise HTTPException(status_code=404, detail=f"Metric '{kind}' not found")

    logical_today = logic.get_logical_day()
    curr_d = date.fromisoformat(logical_today)
    start_d = curr_d - timedelta(days=days - 1)

    unit = store.METRIC_KINDS.get(kind, ("?", None))[0]

    with store.session() as con:
        rows = store.rows(
            con,
            "SELECT ts, value, note FROM metrics WHERE kind=? AND ts>=? ORDER BY ts ASC, id ASC",
            (kind, start_d.isoformat()),
        )

    # Deduplication: if multiple records exist for the same day, take the latest
    point_map: dict[str, dict] = {}
    for r in rows:
        day_key = r["ts"][:10]
        point_map[day_key] = {
            "day": day_key,
            "value": float(r["value"]),
            "note": r["note"],
        }

    sorted_days = sorted(point_map.keys())
    points_data = [point_map[k] for k in sorted_days]

    # Line break detection (> 2 days gap between points)
    formatted_points = []
    is_time_metric = kind in ("wake_time", "bed_time")

    prev_date: date | None = None
    for p in points_data:
        p_date = date.fromisoformat(p["day"])
        is_break = False
        if prev_date is not None:
            gap_days = (p_date - prev_date).days
            if gap_days > 2:
                is_break = True

        val = p["value"]
        # Shift < 09:00 by +24 for sleep schedule phase continuity
        adjusted_val = val
        if is_time_metric and val < 9.0:
            adjusted_val = val + 24.0

        formatted_points.append({
            "day": p["day"],
            "date_formatted": logic.format_date_en(p["day"]),
            "value": val,
            "adjusted_value": round(adjusted_val, 2),
            "display_value": logic.decimal_to_time_str(val) if is_time_metric else str(round(val, 1)),
            "is_break": is_break,
            "note": p["note"],
        })
        prev_date = p_date

    # Statistical calculations
    raw_vals = [p["value"] for p in points_data]
    if raw_vals:
        avg_val = round(sum(raw_vals) / len(raw_vals), 1)
        min_val = round(min(raw_vals), 1)
        max_val = round(max(raw_vals), 1)
    else:
        avg_val = min_val = max_val = None

    # Wake time phase drift
    phase_drift = None
    if kind == "wake_time":
        wake_tuples = [(p["day"], p["value"]) for p in points_data]
        phase_drift = logic.compute_phase_drift(wake_tuples)

    return {
        "kind": kind,
        "title": ALLOWED_TREND_KINDS.get(kind, kind),
        "unit": unit,
        "days": days,
        "is_time_metric": is_time_metric,
        "stats": {
            "avg": avg_val,
            "min": min_val,
            "max": max_val,
            "display_avg": logic.decimal_to_time_str(avg_val) if is_time_metric and avg_val else avg_val,
            "display_min": logic.decimal_to_time_str(min_val) if is_time_metric and min_val else min_val,
            "display_max": logic.decimal_to_time_str(max_val) if is_time_metric and max_val else max_val,
            "points_count": len(points_data),
            "total_days": days,
        },
        "phase_drift": phase_drift,
        "points": formatted_points,
    }
