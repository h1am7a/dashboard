from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from app import config

MSK_TZ = timezone(timedelta(hours=config.TIMEZONE_OFFSET_HOURS))

MONTH_NAMES_EN = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
]


def get_msk_now() -> datetime:
    """Current time in MSK timezone (UTC+3)."""
    return datetime.now(timezone.utc).astimezone(MSK_TZ)


def get_logical_day(dt: datetime | None = None) -> str:
    """Calculate logical day date with 09:00 MSK threshold.
    
    Everything occurring between 09:00 and 08:59 of the next calendar day
    belongs to the starting date.
    """
    now = dt or get_msk_now()
    if now.hour < 9:
        logical_date = now.date() - timedelta(days=1)
    else:
        logical_date = now.date()
    return logical_date.isoformat()


def decimal_to_time_str(val: float | None) -> str:
    """Convert decimal hours (15.5) to formatted time string (15:30)."""
    if val is None:
        return "—"
    hours = int(val) % 24
    minutes = int(round((val - int(val)) * 60))
    if minutes >= 60:
        hours = (hours + 1) % 24
        minutes = 0
    return f"{hours:02d}:{minutes:02d}"


def time_str_to_decimal(time_str: str) -> float:
    """Convert '15:30' to 15.5."""
    parts = time_str.strip().split(":")
    hours = int(parts[0])
    minutes = int(parts[1]) if len(parts) > 1 else 0
    return round(hours + minutes / 60.0, 2)


def format_date_en(iso_day: str, with_year: bool = False) -> str:
    """Format '2026-10-23' -> '23 Oct'."""
    try:
        d = date.fromisoformat(iso_day[:10])
        month = MONTH_NAMES_EN[d.month]
        if with_year:
            return f"{d.day} {month} {d.year}"
        return f"{d.day} {month}"
    except Exception:
        return iso_day


format_date_ru = format_date_en


def compute_phase_drift(wake_points: list[tuple[str, float]]) -> dict:
    """Compute sleep phase drift using Ordinary Least Squares (OLS).
    
    wake_points: list of [(day_iso, wake_time_decimal), ...] sorted chronologically.
    Values < 09:00 are shifted by +24 to avoid midnight discontinuities.
    """
    if len(wake_points) < 4:
        return {"drift_min_day": None, "label": "insufficient data", "points_count": len(wake_points)}

    # Use up to the last 7 points
    recent = wake_points[-7:]
    
    first_date = date.fromisoformat(recent[0][0])
    xs: list[float] = []
    ys: list[float] = []
    
    for day_str, val in recent:
        d = date.fromisoformat(day_str)
        x = float((d - first_date).days)
        y = float(val)
        if y < 9.0:
            y += 24.0
        xs.append(x)
        ys.append(y)

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return {"drift_min_day": 0, "label": "0 min/day", "points_count": n}

    slope = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n)) / denom
    drift_min_day = int(round(slope * 60))
    
    sign = "+" if drift_min_day > 0 else ""
    return {
        "drift_min_day": drift_min_day,
        "label": f"{sign}{drift_min_day} min/day",
        "points_count": n,
        "is_anomaly": abs(drift_min_day) > 60
    }


def get_screen_time_ring(con: sqlite3.Connection, current_day: str) -> dict:
    """Return data for screen time progress ring on Today view.
    
    Displays the latest completed week with date range (e.g., 13–19 Oct).
    """
    cur = con.execute(
        "SELECT ts, value FROM metrics WHERE kind='screen_time' ORDER BY ts DESC, id DESC LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return {
            "hours": 0.0,
            "target": 50.0,
            "percent": 0,
            "range_label": "screen time not logged yet",
            "is_empty": True,
            "is_over": False,
        }

    hours = float(row["value"])
    target = 50.0
    percent = int(min(round((hours / target) * 100), 100)) if target > 0 else 0
    is_over = hours > target

    try:
        rec_date = date.fromisoformat(row["ts"][:10])
        start_date = rec_date - timedelta(days=6)
        range_label = f"{format_date_en(start_date.isoformat())} – {format_date_en(rec_date.isoformat())}"
    except Exception:
        range_label = f"logged {row['ts']}"

    return {
        "hours": round(hours, 1),
        "target": target,
        "percent": percent,
        "range_label": range_label,
        "is_empty": False,
        "is_over": is_over,
        "over_hours": round(hours - target, 1) if is_over else 0.0
    }


def compute_insight(con: sqlite3.Connection, current_day: str) -> str | None:
    """Evaluate insight rules in strict priority order (Section 13.9).
    
    Returns the first matching insight string or None.
    """
    # 1. No entries for 3+ days
    latest_metric = con.execute("SELECT MAX(ts) as max_ts FROM metrics").fetchone()
    if latest_metric and latest_metric["max_ts"]:
        max_d = date.fromisoformat(latest_metric["max_ts"][:10])
        curr_d = date.fromisoformat(current_day[:10])
        diff_days = (curr_d - max_d).days
        if diff_days >= 3:
            return f"{diff_days} days without logs"

    # 2. Wake drift over 3 days > 45 min
    wake_rows = con.execute(
        "SELECT ts, value FROM metrics WHERE kind='wake_time' ORDER BY ts DESC LIMIT 3"
    ).fetchall()
    if len(wake_rows) >= 3:
        w_curr = wake_rows[0]["value"]
        w_prev = wake_rows[-1]["value"]
        if w_curr < 9.0:
            w_curr += 24.0
        if w_prev < 9.0:
            w_prev += 24.0
        diff_hours = w_curr - w_prev
        diff_minutes = int(round(diff_hours * 60))
        if diff_minutes > 45:
            hh = diff_minutes // 60
            mm = diff_minutes % 60
            return f"wake time shifted by +{hh}:{mm:02d} over 3 days"

    # 3 & 4. Screen time for week
    screen_rows = con.execute(
        "SELECT ts, value FROM metrics WHERE kind='screen_time' ORDER BY ts DESC LIMIT 2"
    ).fetchall()
    if len(screen_rows) > 0:
        latest_screen = screen_rows[0]["value"]
        if latest_screen > 50:
            over = int(round(latest_screen - 50))
            return f"screen time {int(latest_screen)}h — {over}h above target"
        if len(screen_rows) >= 2:
            prev_screen = screen_rows[1]["value"]
            if (prev_screen - latest_screen) >= 5:
                delta = int(round(prev_screen - latest_screen))
                return f"screen time {int(latest_screen)}h — {delta}h below last week"

    # 5. No workouts for 5+ days
    latest_wo = con.execute("SELECT MAX(date) as max_d FROM workouts").fetchone()
    if latest_wo and latest_wo["max_d"]:
        wo_d = date.fromisoformat(latest_wo["max_d"][:10])
        curr_d = date.fromisoformat(current_day[:10])
        wo_gap = (curr_d - wo_d).days
        if wo_gap >= 5:
            return f"{wo_gap} days without workouts"

    # 6. Average mood for 3 days < 4
    mood_rows = con.execute(
        "SELECT AVG(value) as avg_mood, COUNT(*) as cnt FROM metrics "
        "WHERE kind='mood' AND ts >= date(?, '-2 days')",
        (current_day,)
    ).fetchone()
    if mood_rows and mood_rows["cnt"] >= 2 and mood_rows["avg_mood"] and mood_rows["avg_mood"] < 4.0:
        return "mood lower than usual for 3 days"

    return None
