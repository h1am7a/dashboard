#!/usr/bin/env python3
"""Generate realistic development test data for local dashboard testing."""
import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app import store


def seed():
    print("Generating development database life.db...")
    db_file = BASE_DIR / "db" / "life.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)

    # Set baseline facts
    store.set_fact("city", "London")
    store.set_fact("wake_target", "07:30")
    store.set_fact("bed_target", "23:30")
    store.set_fact("user_name", "user")

    # Current logical day
    today_str = store._today()
    curr_date = date.fromisoformat(today_str)

    # Generate 30 days of sleep & habit history
    for i in range(30, -1, -1):
        day_date = curr_date - timedelta(days=i)
        iso_day = day_date.isoformat()

        # 1. Sleep & schedule
        wake_m = 20 + (i % 20)
        wake_val = f"07:{wake_m:02d}"
        bed_m = 15 + (i % 30)
        bed_val = f"23:{bed_m:02d}"
        sleep_hours = round(7.4 + ((i % 5) * 0.2) - ((i % 3) * 0.1), 1)

        store.add_metric("wake_time", wake_val, ts=iso_day, note="wake")
        store.add_metric("bed_time", bed_val, ts=iso_day, note="bed")
        store.add_metric("sleep_hours", sleep_hours, ts=iso_day)
        store.add_metric("sleep_quality", 4 if sleep_hours >= 7.5 else 3, ts=iso_day)

        # 2. Mood & energy
        mood = 6 + (i % 3)
        energy = 5 + (i % 4)
        store.add_metric("mood", mood, ts=iso_day)
        store.add_metric("energy", energy, ts=iso_day)

        # 3. Daily minimum checks
        store.add_metric("protein_ok", 1 if i % 3 != 0 else 0, ts=iso_day)
        store.add_metric("routine_ok", 1 if i % 2 == 0 else 0, ts=iso_day)

    # 4. Body measurements: exact intervals (9 for waist, 5 for biceps & forearm)
    with store.session() as con:
        con.execute("DELETE FROM metrics WHERE kind IN ('waist', 'biceps_r', 'forearm_r')")

    waist_vals = [86.4, 86.0, 85.6, 85.2, 84.8, 84.4, 84.0, 83.6, 83.2]
    for idx, val in enumerate(waist_vals):
        pt_date = (curr_date - timedelta(weeks=(8 - idx))).isoformat()
        store.add_metric("waist", val, ts=pt_date, unit="cm")

    biceps_vals = [37.2, 37.5, 37.8, 38.2, 38.6]
    for idx, val in enumerate(biceps_vals):
        pt_date = (curr_date - timedelta(weeks=(4 - idx))).isoformat()
        store.add_metric("biceps_r", val, ts=pt_date, unit="cm")

    forearm_vals = [30.4, 30.7, 31.0, 31.3, 31.6]
    for idx, val in enumerate(forearm_vals):
        pt_date = (curr_date - timedelta(weeks=(4 - idx))).isoformat()
        store.add_metric("forearm_r", val, ts=pt_date, unit="cm")

    # 5. Workouts with clear newline-separated exercises
    with store.session() as con:
        con.execute("DELETE FROM workouts")

    store.add_workout(
        date_=curr_date.isoformat(),
        kind="gym",
        duration_min=55,
        load="moderate",
        note="Overhead press 4x8 50kg\nDumbbell lateral raises 4x15 12kg\nFace pulls 3x15\nTriceps rope pushdowns 3x12\nHammer curls 3x10 16kg",
    )
    store.add_workout(
        date_=(curr_date - timedelta(days=2)).isoformat(),
        kind="gym",
        duration_min=70,
        load="hard",
        note="Barbell back squat 4x6 110kg\nRomanian deadlift 3x10 90kg\nBulgarian split squats 3x10\nHanging leg raises 3x15",
    )
    store.add_workout(
        date_=(curr_date - timedelta(days=4)).isoformat(),
        kind="gym",
        duration_min=65,
        load="hard",
        note="Weighted pull-ups 4x6 (+15kg)\nBarbell bent-over row 4x8 75kg\nSeated cable row 3x12 65kg\nIncline dumbbell curls 3x12 14kg",
    )
    store.add_workout(
        date_=(curr_date - timedelta(days=6)).isoformat(),
        kind="gym",
        duration_min=60,
        load="hard",
        note="Bench press 4x8 85kg\nIncline dumbbell press 3x10 30kg\nWeighted dips 3x10 (+15kg)\nCable chest flyes 3x15",
    )

    # 6. Tasks
    with store.session() as con:
        con.execute("DELETE FROM tasks")

    store.add_task("Check resting heart rate & morning HRV", project="routine", due=curr_date.isoformat(), priority=1)
    store.add_task("Take post-workout protein shake & creatine", project="routine", due=curr_date.isoformat(), priority=1)
    store.add_task("Prepare healthy meals for tomorrow", project="routine", due=curr_date.isoformat(), priority=2)
    # Open task for interactive Done testing
    store.add_task("Evening screen detox 30m before bed", project="routine", due=curr_date.isoformat(), priority=2)
    store.add_task("Morning sunlight walk 15 mins", project="routine", due=curr_date.isoformat(), priority=3)

    # Mark first 3 as done
    with store.session() as con:
        con.execute("UPDATE tasks SET status='done' WHERE id IN (1, 2, 3, 5)")
        con.execute("UPDATE tasks SET status='open', done_at=NULL WHERE id=4")

    print("Done! Generated realistic clean dev metrics.")


if __name__ == "__main__":
    seed()
