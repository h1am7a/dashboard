#!/usr/bin/env python3
"""Generate realistic 30-day development test data for local dashboard testing."""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app import store, logic


def seed():
    print("Generating development database life.db...")
    db_file = BASE_DIR / "db" / "life.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)

    # Set baseline facts
    store.set_fact("city", "Moscow")
    store.set_fact("wake_target", "14:30")
    store.set_fact("user_name", "user")

    # Current logical day
    today_str = logic.get_logical_day()
    curr_date = date.fromisoformat(today_str)

    # Generate 30 days of history
    for i in range(30, -1, -1):
        day_date = curr_date - timedelta(days=i)
        iso_day = day_date.isoformat()

        # 1. Sleep & schedule
        drift_factor = (30 - i) * 0.04
        wake_val = round(14.2 + drift_factor + ((i % 3) * 0.1), 2)
        bed_val = round(6.8 + drift_factor + ((i % 2) * 0.15), 2)
        sleep_hours = round(7.0 + ((i % 4) * 0.4) - ((i % 5) * 0.2), 1)

        store.add_metric("wake_time", wake_val, ts=iso_day, note="wake")
        store.add_metric("bed_time", bed_val, ts=iso_day, note="bed")
        store.add_metric("sleep_hours", sleep_hours, ts=iso_day)
        store.add_metric("sleep_quality", 4 if sleep_hours >= 7 else 3, ts=iso_day)

        # 2. Mood & energy
        mood = 6 + (i % 3)
        energy = 5 + (i % 4)
        store.add_metric("mood", mood, ts=iso_day)
        store.add_metric("energy", energy, ts=iso_day)

        # 3. Masturbation count: 0 almost always, occasionally 1
        porn_cnt = 1 if (i in (4, 11, 18, 25)) else 0
        store.add_metric("porn_count", porn_cnt, ts=iso_day)

        # 4. Daily minimum checks
        store.add_metric("wake_light", 1 if i % 4 != 0 else 0, ts=iso_day)
        store.add_metric("protein_ok", 1 if i % 3 != 0 else 0, ts=iso_day)
        store.add_metric("routine_ok", 1 if i % 2 == 0 else 0, ts=iso_day)

        # 5. Workouts (every 2-3 days)
        if i % 3 == 1:
            load = "hard" if i % 2 == 0 else "moderate"
            duration = 65 if load == "hard" else 55
            note = "Bench press 3x8 85kg, dumbbell row 34kg 3x10, pull-ups" if load == "hard" else "Full body, moderate tempo, warmup"
            store.add_workout(date_=iso_day, kind="gym", duration_min=duration, load=load, note=note)

        # 6. Waist & weight (weekly)
        if i % 7 == 0:
            waist = round(85.5 - ((30 - i) / 30) * 1.3, 1)
            weight = round(79.0 - ((30 - i) / 30) * 0.6, 1)
            store.add_metric("waist", waist, ts=iso_day, note="morning measurement")
            store.add_metric("weight", weight, ts=iso_day, note="fasting weight")

        # 7. Screen time (Sundays: 50h, 48h, 41h)
        if day_date.weekday() == 6:  # Sunday
            screen_hrs = 41.0 if i < 7 else (50.0 if i < 14 else 48.0)
            store.add_metric("screen_time", screen_hrs, ts=iso_day, note="weekly total")

    # 8. Inventory items
    try:
        store.add_item("Facial Cleanser", category="hygiene", cadence="daily", role="facial cleansing")
        store.add_item("Creatine Monohydrate", category="supplement", cadence="daily", role="recovery")
        store.add_item("Whey Protein", category="supplement", cadence="daily", role="daily protein")
    except Exception:
        pass

    # 9. Tasks
    with store.session() as con:
        con.execute("DELETE FROM tasks")
        con.execute("DELETE FROM events")

    store.add_task("Deploy FastAPI backend to VPS", project="tg_app", due=(curr_date + timedelta(days=2)).isoformat(), priority=1)
    store.add_task("Configure Cloudflare Tunnel / SSL", project="devops", due=(curr_date + timedelta(days=4)).isoformat(), priority=2)
    store.add_task("Verify sqlite3 WAL backups", project="infra", due=None, priority=3)
    
    # Completed tasks
    t_done1 = store.add_task("Build architecture UI mockup", project="tg_app", priority=1)
    store.close_task(t_done1, status="done")
    t_done2 = store.add_task("Design API routes", project="tg_app", priority=2)
    store.close_task(t_done2, status="done")

    # 10. Events
    store.add_event((curr_date + timedelta(days=32)).isoformat(), "VPS Server Renewal", type_="infra", remind="3d")
    store.add_event((curr_date + timedelta(days=47)).isoformat(), "Domain Renewal", type_="billing", remind="7d")

    print("Done! Generated realistic 30-day dev metrics.")


if __name__ == "__main__":
    seed()
