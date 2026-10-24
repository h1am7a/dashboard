from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from fastapi import APIRouter, Depends
from app import auth, logic, store

router = APIRouter(prefix="/api", tags=["body"])


@router.get("/body")
def get_body_data(user: dict = Depends(auth.get_current_user)):
    """Данные для экрана 'Тело'."""
    logical_today = logic.get_logical_day()
    curr_d = date.fromisoformat(logical_today)

    with store.session() as con:
        # 1. Обхват живота
        waist_rows = store.rows(
            con,
            "SELECT ts, value FROM metrics WHERE kind='waist' ORDER BY ts DESC, id DESC LIMIT 10",
        )
        if waist_rows:
            waist_latest = float(waist_rows[0]["value"])
            waist_date = waist_rows[0]["ts"][:10]
            waist_prev = float(waist_rows[1]["value"]) if len(waist_rows) > 1 else None
            waist_delta = round(waist_latest - waist_prev, 1) if waist_prev else None
            waist_spark = [float(r["value"]) for r in reversed(waist_rows)]
        else:
            waist_latest = waist_date = waist_delta = waist_spark = None

        # 2. Вес
        weight_rows = store.rows(
            con,
            "SELECT ts, value FROM metrics WHERE kind='weight' ORDER BY ts DESC, id DESC LIMIT 10",
        )
        if weight_rows:
            weight_latest = float(weight_rows[0]["value"])
            weight_date = weight_rows[0]["ts"][:10]
            weight_prev = float(weight_rows[1]["value"]) if len(weight_rows) > 1 else None
            weight_delta = round(weight_latest - weight_prev, 1) if weight_prev else None
            weight_spark = [float(r["value"]) for r in reversed(weight_rows)]
        else:
            weight_latest = weight_date = weight_delta = weight_spark = None

        # 3. Статистика тренировок
        d7 = (curr_d - timedelta(days=6)).isoformat()
        d30 = (curr_d - timedelta(days=29)).isoformat()
        wo_count_7d = con.execute("SELECT COUNT(*) FROM workouts WHERE date>=?", (d7,)).fetchone()[0]
        wo_count_30d = con.execute("SELECT COUNT(*) FROM workouts WHERE date>=?", (d30,)).fetchone()[0]

        # Последняя тренировка
        last_wo_row = con.execute(
            "SELECT date, kind, duration_min, load, note FROM workouts ORDER BY date DESC, id DESC LIMIT 1"
        ).fetchone()
        if last_wo_row:
            w_d = date.fromisoformat(last_wo_row["date"][:10])
            days_ago = (curr_d - w_d).days
            days_label = "сегодня" if days_ago == 0 else f"{days_ago} д назад"
            last_workout = {
                "date": last_wo_row["date"][:10],
                "date_ru": logic.format_date_ru(last_wo_row["date"]),
                "days_ago": days_ago,
                "days_label": days_label,
                "kind": last_wo_row["kind"] or "тренировка",
                "duration_min": last_wo_row["duration_min"],
                "load": last_wo_row["load"],
                "note": last_wo_row["note"],
            }
        else:
            last_workout = None

        # 4. Календарь нагрузки за 28 дней (4x7)
        d28 = (curr_d - timedelta(days=27)).isoformat()
        wo_dates = {
            r["date"][:10]
            for r in store.rows(con, "SELECT DISTINCT date FROM workouts WHERE date>=?", (d28,))
        }

        activity_grid = []
        c = date.fromisoformat(d28)
        while c <= curr_d:
            iso_c = c.isoformat()
            activity_grid.append({
                "date": iso_c,
                "has_workout": iso_c in wo_dates,
                "is_today": (iso_c == logical_today),
            })
            c += timedelta(days=1)

    return {
        "waist": {
            "latest": waist_latest,
            "date": waist_date,
            "date_ru": logic.format_date_ru(waist_date) if waist_date else None,
            "delta": waist_delta,
            "sparkline": waist_spark,
        },
        "weight": {
            "latest": weight_latest,
            "date": weight_date,
            "date_ru": logic.format_date_ru(weight_date) if weight_date else None,
            "delta": weight_delta,
            "sparkline": weight_spark,
        },
        "workouts": {
            "count_7d": wo_count_7d,
            "count_30d": wo_count_30d,
            "last": last_workout,
            "activity_grid_28d": activity_grid,
        },
    }
