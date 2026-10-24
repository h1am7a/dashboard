from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from app import config

MSK_TZ = timezone(timedelta(hours=config.TIMEZONE_OFFSET_HOURS))

MONTH_NAMES_RU = [
    "", "янв", "фев", "мар", "апр", "май", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек"
]


def get_msk_now() -> datetime:
    """Текущее время в часовом поясе MSK (UTC+3)."""
    return datetime.now(timezone.utc).astimezone(MSK_TZ)


def get_logical_day(dt: datetime | None = None) -> str:
    """Вычисляет дату логического дня с границей в 09:00 MSK.
    
    Всё, что происходит с 09:00 до 08:59 следующего календарного дня,
    относится к дате начала.
    """
    now = dt or get_msk_now()
    if now.hour < 9:
        logical_date = now.date() - timedelta(days=1)
    else:
        logical_date = now.date()
    return logical_date.isoformat()


def decimal_to_time_str(val: float | None) -> str:
    """Конвертирует десятичные часы (15.5) в строку времени (15:30)."""
    if val is None:
        return "—"
    hours = int(val) % 24
    minutes = int(round((val - int(val)) * 60))
    if minutes >= 60:
        hours = (hours + 1) % 24
        minutes = 0
    return f"{hours:02d}:{minutes:02d}"


def time_str_to_decimal(time_str: str) -> float:
    """Конвертирует '15:30' в 15.5."""
    parts = time_str.strip().split(":")
    hours = int(parts[0])
    minutes = int(parts[1]) if len(parts) > 1 else 0
    return round(hours + minutes / 60.0, 2)


def format_date_ru(iso_day: str, with_year: bool = False) -> str:
    """Форматирует '2026-10-23' -> '23 окт'."""
    try:
        d = date.fromisoformat(iso_day[:10])
        month = MONTH_NAMES_RU[d.month]
        if with_year:
            return f"{d.day} {month} {d.year}"
        return f"{d.day} {month}"
    except Exception:
        return iso_day


def compute_phase_drift(wake_points: list[tuple[str, float]]) -> dict:
    """Вычисляет дрейф фазы сна по методу наименьших квадратов (МНК).
    
    wake_points: список [(day_iso, wake_time_decimal), ...] отсортированный по дате.
    Значения < 09:00 сдвигаются на +24, чтобы не разрываться через полночь.
    """
    if len(wake_points) < 4:
        return {"drift_min_day": None, "label": "мало данных", "points_count": len(wake_points)}

    # Берём максимум последние 7 точек
    recent = wake_points[-7:]
    
    # Преобразуем точки в (x_day_index, y_adjusted_hours)
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
        return {"drift_min_day": 0, "label": "0 мин/день", "points_count": n}

    slope = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n)) / denom
    drift_min_day = int(round(slope * 60))
    
    sign = "+" if drift_min_day > 0 else ""
    return {
        "drift_min_day": drift_min_day,
        "label": f"{sign}{drift_min_day} мин/день",
        "points_count": n,
        "is_anomaly": abs(drift_min_day) > 60
    }


def get_screen_time_ring(con: sqlite3.Connection, current_day: str) -> dict:
    """Возвращает данные для кольца скринтайма на экране 'Сегодня'.
    
    Показывает последнюю завершённую неделю с диапазоном дат под кольцом (например, 13–19 окт).
    """
    # Ищем последнюю запись screen_time
    cur = con.execute(
        "SELECT ts, value FROM metrics WHERE kind='screen_time' ORDER BY ts DESC, id DESC LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return {
            "hours": 0.0,
            "target": 50.0,
            "percent": 0,
            "range_label": "скринтайм ещё не вносился",
            "is_empty": True,
            "is_over": False,
        }

    hours = float(row["value"])
    target = 50.0
    percent = int(min(round((hours / target) * 100), 100)) if target > 0 else 0
    is_over = hours > target

    # Вычисляем диапазон недели для записи
    # Запись обычно вносится в воскресенье за предшествующие 7 дней (пн-вс)
    try:
        rec_date = date.fromisoformat(row["ts"][:10])
        start_date = rec_date - timedelta(days=6)
        range_label = f"{format_date_ru(start_date.isoformat())} – {format_date_ru(rec_date.isoformat())}"
    except Exception:
        range_label = f"запись от {row['ts']}"

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
    """Проверяет правила инсайта строго по порядку (п. 13.9).
    
    Возвращает первую сработавшую фразу или None.
    """
    # 1. Нет записей 3+ дня
    latest_metric = con.execute("SELECT MAX(ts) as max_ts FROM metrics").fetchone()
    if latest_metric and latest_metric["max_ts"]:
        max_d = date.fromisoformat(latest_metric["max_ts"][:10])
        curr_d = date.fromisoformat(current_day[:10])
        diff_days = (curr_d - max_d).days
        if diff_days >= 3:
            return f"{diff_days} дня без записей" if diff_days < 5 else f"{diff_days} дней без записей"

    # 2. Дрейф подъёма за 3 дня > 45 мин
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
            return f"подъём сдвинулся на +{hh}:{mm:02d} за 3 дня"

    # 3 & 4. Экран за неделю
    screen_rows = con.execute(
        "SELECT ts, value FROM metrics WHERE kind='screen_time' ORDER BY ts DESC LIMIT 2"
    ).fetchall()
    if len(screen_rows) > 0:
        latest_screen = screen_rows[0]["value"]
        if latest_screen > 50:
            over = int(round(latest_screen - 50))
            return f"экран {int(latest_screen)} ч — на {over} ч выше цели"
        if len(screen_rows) >= 2:
            prev_screen = screen_rows[1]["value"]
            if (prev_screen - latest_screen) >= 5:
                delta = int(round(prev_screen - latest_screen))
                return f"экран {int(latest_screen)} ч — на {delta} ч меньше прошлой недели"

    # 5. Нет тренировок 5+ дней
    latest_wo = con.execute("SELECT MAX(date) as max_d FROM workouts").fetchone()
    if latest_wo and latest_wo["max_d"]:
        wo_d = date.fromisoformat(latest_wo["max_d"][:10])
        curr_d = date.fromisoformat(current_day[:10])
        wo_gap = (curr_d - wo_d).days
        if wo_gap >= 5:
            return f"{wo_gap} дней без тренировок"

    # 6. Среднее настроение за 3 дня < 4
    mood_rows = con.execute(
        "SELECT AVG(value) as avg_mood, COUNT(*) as cnt FROM metrics "
        "WHERE kind='mood' AND ts >= date(?, '-2 days')",
        (current_day,)
    ).fetchone()
    if mood_rows and mood_rows["cnt"] >= 2 and mood_rows["avg_mood"] and mood_rows["avg_mood"] < 4.0:
        return "настроение ниже обычного 3 дня"

    return None
