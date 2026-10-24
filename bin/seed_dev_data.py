#!/usr/bin/env python3
"""Генератор реалистичных тестовых данных за 30 дней для локальной разработки дашборда."""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app import store, logic


def seed():
    print("Генерация тестовой базы life.db...")
    # Очищаем или инициализируем
    db_file = BASE_DIR / "db" / "life.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)

    # Устанавливаем базовые факты (set_fact открывает свою сессию)
    store.set_fact("city", "Moscow")
    store.set_fact("wake_target", "14:30")
    store.set_fact("user_name", "user")

    # Базовая дата — сегодня
    today_str = logic.get_logical_day()
    curr_date = date.fromisoformat(today_str)

    # Генерируем 30 дней истории
    for i in range(30, -1, -1):
        day_date = curr_date - timedelta(days=i)
        iso_day = day_date.isoformat()

        # 1. Сон и режим
        # Дрейф подъёма: от 14.2 (14:12) месяц назад к 15.5 (15:30) сейчас
        drift_factor = (30 - i) * 0.04
        wake_val = round(14.2 + drift_factor + ((i % 3) * 0.1), 2)
        bed_val = round(6.8 + drift_factor + ((i % 2) * 0.15), 2)
        sleep_hours = round(7.0 + ((i % 4) * 0.4) - ((i % 5) * 0.2), 1)

        store.add_metric("wake_time", wake_val, ts=iso_day, note="подъём")
        store.add_metric("bed_time", bed_val, ts=iso_day, note="отбой")
        store.add_metric("sleep_hours", sleep_hours, ts=iso_day)
        store.add_metric("sleep_quality", 4 if sleep_hours >= 7 else 3, ts=iso_day)

        # 2. Настроение и энергия
        mood = 6 + (i % 3)
        energy = 5 + (i % 4)
        store.add_metric("mood", mood, ts=iso_day)
        store.add_metric("energy", energy, ts=iso_day)

        # 3. Фап (порно): 0 почти всегда, изредка 1
        porn_cnt = 1 if (i in (4, 11, 18, 25)) else 0
        store.add_metric("porn_count", porn_cnt, ts=iso_day)

        # 4. Минимум дня
        store.add_metric("wake_light", 1 if i % 4 != 0 else 0, ts=iso_day)
        store.add_metric("protein_ok", 1 if i % 3 != 0 else 0, ts=iso_day)
        store.add_metric("routine_ok", 1 if i % 2 == 0 else 0, ts=iso_day)

        # 5. Тренировки (каждые 2-3 дня)
        if i % 3 == 1:
            load = "hard" if i % 2 == 0 else "moderate"
            duration = 65 if load == "hard" else 55
            note = "Жим лёжа 3х8 85кг, тяга гантели 34кг 3х10, подтягивания" if load == "hard" else "Фуллбоди, лёгкий темп, разминка"
            store.add_workout(date_=iso_day, kind="gym", duration_min=duration, load=load, note=note)

        # 6. Обхват живота и вес (раз в неделю)
        if i % 7 == 0:
            waist = round(85.5 - ((30 - i) / 30) * 1.3, 1)
            weight = round(79.0 - ((30 - i) / 30) * 0.6, 1)
            store.add_metric("waist", waist, ts=iso_day, note="утренний замер")
            store.add_metric("weight", weight, ts=iso_day, note="натощак")

        # 7. Скринтайм (по воскресеньям, 50 ч, 48 ч, 41 ч)
        if day_date.weekday() == 6:  # Воскресенье
            screen_hrs = 41.0 if i < 7 else (50.0 if i < 14 else 48.0)
            store.add_metric("screen_time", screen_hrs, ts=iso_day, note="недельный итог")

    # 8. Инвентарь
    try:
        store.add_item("Пенка для умывания", category="hygiene", cadence="daily", role="очищение лица")
        store.add_item("Креатин моногидрат", category="supplement", cadence="daily", role="восстановление")
        store.add_item("Сывороточный протеин", category="supplement", cadence="daily", role="белок")
    except Exception:
        pass

    # 9. Дела
    with store.session() as con:
        con.execute("DELETE FROM tasks")
        con.execute("DELETE FROM events")

    store.add_task("Задеплоить FastAPI бэкенд на VPS", project="tg_app", due=(curr_date + timedelta(days=2)).isoformat(), priority=1)
    store.add_task("Настроить Cloudflare Tunnel / SSL", project="devops", due=(curr_date + timedelta(days=4)).isoformat(), priority=2)
    store.add_task("Проверить sqlite3 WAL бэкапы", project="infra", due=None, priority=3)
    
    # Несколько выполненных задач
    t_done1 = store.add_task("Собрать архитектурный макет UI", project="tg_app", priority=1)
    store.close_task(t_done1, status="done")
    t_done2 = store.add_task("Спроектировать API роуты", project="tg_app", priority=2)
    store.close_task(t_done2, status="done")

    # 10. События
    store.add_event((curr_date + timedelta(days=32)).isoformat(), "Оплата сервера VPS", type_="infra", remind="3d")
    store.add_event((curr_date + timedelta(days=47)).isoformat(), "Продление домена", type_="billing", remind="7d")

    print("Готово! Сгенерированы реалистичные метрики за 30 дней.")


if __name__ == "__main__":
    seed()
