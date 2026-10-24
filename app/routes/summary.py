from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from app import auth, config, logic, store

router = APIRouter(prefix="/api", tags=["summary"])


def _get_sparkline(con: sqlite3.Connection, kind: str, days: int = 14) -> list[float | None]:
    """Return sparkline points for the last N days."""
    logical_today = logic.get_logical_day()
    start_d = (date.fromisoformat(logical_today) - timedelta(days=days - 1)).isoformat()
    
    rows = store.rows(
        con,
        "SELECT ts, value FROM metrics WHERE kind=? AND ts>=? ORDER BY ts ASC",
        (kind, start_d),
    )
    mapping = {r["ts"][:10]: float(r["value"]) for r in rows}
    
    result: list[float | None] = []
    curr = date.fromisoformat(start_d)
    end = date.fromisoformat(logical_today)
    while curr <= end:
        iso_curr = curr.isoformat()
        result.append(mapping.get(iso_curr))
        curr += timedelta(days=1)
    return result


@router.get("/health")
def health_check():
    logical_today = logic.get_logical_day()
    with store.session() as con:
        m_count = con.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    return {
        "ok": True,
        "logical_day": logical_today,
        "metrics_rows": m_count,
        "db": str(config.PRIEST_DB),
    }


@router.get("/summary")
def get_summary(user: dict = Depends(auth.get_current_user)):
    """Complete summary for Today view."""
    logical_today = logic.get_logical_day()
    curr_date = date.fromisoformat(logical_today)
    date_7d_ago = (curr_date - timedelta(days=6)).isoformat()
    date_14d_ago = (curr_date - timedelta(days=13)).isoformat()

    with store.session() as con:
        # Insight calculation
        insight = logic.compute_insight(con, logical_today)

        # Screen time progress ring
        screen_ring = logic.get_screen_time_ring(con, logical_today)

        # Today's row from v_daily
        daily_today = con.execute(
            "SELECT * FROM v_daily WHERE day=?", (logical_today,)
        ).fetchone()
        today_data = dict(daily_today) if daily_today else {}

        # 1. Sleep
        sleep_hours = today_data.get("sleep_hours")
        wake_time = today_data.get("wake_time")
        wake_time_str = logic.decimal_to_time_str(wake_time)
        sleep_avg_7d = con.execute(
            "SELECT ROUND(AVG(value), 1) FROM metrics WHERE kind='sleep_hours' AND ts>=?",
            (date_7d_ago,),
        ).fetchone()[0]
        sleep_spark = _get_sparkline(con, "sleep_hours", 14)

        # 2. Mood
        mood_val = today_data.get("mood")
        # delta compared to previous week
        mood_this_week = con.execute(
            "SELECT AVG(value) FROM metrics WHERE kind='mood' AND ts>=?",
            (date_7d_ago,),
        ).fetchone()[0]
        mood_prev_week = con.execute(
            "SELECT AVG(value) FROM metrics WHERE kind='mood' AND ts>=? AND ts<?",
            (date_14d_ago, date_7d_ago),
        ).fetchone()[0]
        mood_delta = (
            round(mood_this_week - mood_prev_week, 1)
            if (mood_this_week is not None and mood_prev_week is not None)
            else None
        )
        mood_spark = _get_sparkline(con, "mood", 14)

        # 3. Masturbation counter
        porn_today = today_data.get("porn_count", 0) or 0
        porn_avg_7d = con.execute(
            "SELECT ROUND(AVG(value), 1) FROM metrics WHERE kind='porn_count' AND ts>=?",
            (date_7d_ago,),
        ).fetchone()[0]
        porn_spark = _get_sparkline(con, "porn_count", 14)

        # 4. Workouts over 7 days
        wo_count_7d = con.execute(
            "SELECT COUNT(*) FROM workouts WHERE date>=?", (date_7d_ago,)
        ).fetchone()[0]
        last_wo = con.execute(
            "SELECT date, kind, duration_min, load FROM workouts ORDER BY date DESC, id DESC LIMIT 1"
        ).fetchone()
        if last_wo:
            wo_d = date.fromisoformat(last_wo["date"][:10])
            days_ago = (curr_date - wo_d).days
            days_ago_label = (
                "today" if days_ago == 0 else f"{days_ago}d ago"
            )
            last_wo_label = f"{last_wo['kind'] or 'workout'} · {days_ago_label}"
        else:
            last_wo_label = "no logs yet"

        # Workout sparkline (minutes)
        wo_rows = store.rows(
            con,
            "SELECT date, SUM(duration_min) as m FROM workouts WHERE date>=? GROUP BY date",
            (date_14d_ago,),
        )
        wo_map = {r["date"][:10]: r["m"] for r in wo_rows}
        wo_spark = []
        c = date.fromisoformat(date_14d_ago)
        while c <= curr_date:
            wo_spark.append(wo_map.get(c.isoformat(), 0))
            c += timedelta(days=1)

        # Daily minimum checks (wake_light, protein_ok, routine_ok)
        wake_light = int(today_data.get("wake_light") or 0)
        protein_ok = int(today_data.get("protein_ok") or 0)
        routine_ok = int(today_data.get("routine_ok") or 0)

        # Next step (open task with highest priority)
        task_row = con.execute(
            "SELECT id, title, project, due, priority FROM tasks WHERE status='open' "
            "ORDER BY priority ASC, COALESCE(due, '9999') ASC, id ASC LIMIT 1"
        ).fetchone()
        next_task = dict(task_row) if task_row else None

        # 90-day heatmap (12-13 weeks)
        date_90d_ago = (curr_date - timedelta(days=89)).isoformat()
        daily_90d = store.rows(
            con, "SELECT * FROM v_daily WHERE day>=? ORDER BY day ASC", (date_90d_ago,)
        )
        daily_map = {r["day"][:10]: r for r in daily_90d}

        # Wake target fact
        target_fact = con.execute("SELECT value FROM facts WHERE key='wake_target'").fetchone()
        wake_target = (
            logic.time_str_to_decimal(target_fact["value"]) if target_fact else 14.5
        )

        heatmap_days = []
        c = date.fromisoformat(date_90d_ago)
        while c <= curr_date:
            iso_d = c.isoformat()
            d_row = daily_map.get(iso_d)
            if not d_row:
                heatmap_days.append({
                    "day": iso_d,
                    "sleep_ok": None,
                    "wake_ok": None,
                    "workout_ok": None,
                    "routine_ok": None,
                    "protein_ok": None,
                })
            else:
                s_h = d_row.get("sleep_hours")
                w_t = d_row.get("wake_time")
                if w_t is not None and w_t < 9.0:
                    w_t += 24.0
                wake_target_adj = wake_target if wake_target >= 9.0 else wake_target + 24.0

                heatmap_days.append({
                    "day": iso_d,
                    "sleep_ok": (s_h >= 7.0) if s_h is not None else None,
                    "wake_ok": (abs(w_t - wake_target_adj) <= 1.0) if w_t is not None else None,
                    "workout_ok": bool(d_row.get("workout_count", 0) > 0),
                    "routine_ok": bool(d_row.get("routine_ok", 0) == 1),
                    "protein_ok": bool(d_row.get("protein_ok", 0) == 1),
                })
            c += timedelta(days=1)

    return {
        "logical_day": logical_today,
        "logical_day_title": logic.format_date_en(logical_today, with_year=False),
        "server_time_msk": logic.get_msk_now().isoformat(),
        "insight": insight,
        "screen_ring": screen_ring,
        "today": {
            "sleep_hours": sleep_hours,
            "wake_time_str": wake_time_str,
            "sleep_avg_7d": sleep_avg_7d,
            "sleep_spark": sleep_spark,
            "mood": mood_val,
            "mood_delta": mood_delta,
            "mood_spark": mood_spark,
            "porn_count": porn_today,
            "porn_avg_7d": porn_avg_7d,
            "porn_spark": porn_spark,
            "workouts_7d": wo_count_7d,
            "last_workout_label": last_wo_label,
            "workouts_spark": wo_spark,
        },
        "minimum": {
            "wake_light": wake_light,
            "protein_ok": protein_ok,
            "routine_ok": routine_ok,
            "completed_count": (wake_light + protein_ok + routine_ok),
        },
        "next_task": next_task,
        "heatmap": heatmap_days,
    }
