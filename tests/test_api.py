import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# Ensure DEV_MODE is on for testing
os.environ["DEV_MODE"] = "true"

from app.api import app
from app import store, config

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_test_db(tmp_path, monkeypatch):
    """Isolate every test in an ephemeral clean SQLite database."""
    test_db = tmp_path / "life.db"
    monkeypatch.setenv("DB_PATH", str(test_db))
    monkeypatch.setattr(config, "DB_PATH", str(test_db))
    with store.session() as con:
        store.migrate(con)



def test_health():
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "version": "1.0"}


def test_today():
    res = client.get("/api/today")
    assert res.status_code == 200
    data = res.json()

    # Required top-level keys
    for key in ["day", "weekday", "in_progress", "score", "sleep", "mood", "hitmap"]:
        assert key in data

    # Score structure
    assert "done" in data["score"]
    assert "total" in data["score"]
    assert "percent" in data["score"]
    assert "items" in data["score"]

    # Sleep structure
    assert "hours" in data["sleep"]
    assert "wake" in data["sleep"]
    assert "bed" in data["sleep"]

    # Hitmap has entries
    assert len(data["hitmap"]) > 0


def test_day_score_empty_day():
    score = store.day_score("2099-01-01")
    assert score["day"] == "2099-01-01"
    assert score["percent"] is None


def test_sleep_periods():
    for days in [7, 30, 90]:
        res = client.get(f"/api/sleep?days={days}")
        assert res.status_code == 200
        data = res.json()

        assert data["period"]["days"] == days
        assert len(data["series"]) == days
        assert "target_hours" in data
        assert "target_wake" in data
        assert "avg_wake" in data
        assert "avg_bed" in data
        assert "prev_week" in data
        assert "significant" in data["prev_week"]

    # Invalid days returns 400
    res_bad = client.get("/api/sleep?days=14")
    assert res_bad.status_code == 400
    assert res_bad.json()["error"]["code"] == "bad_period"


def test_body():
    # 1. Clean empty database state
    res = client.get("/api/body")
    assert res.status_code == 200
    data = res.json()

    assert "measurements" in data
    kinds = [m["kind"] for m in data["measurements"]]
    for k in ["waist", "biceps_r", "forearm_r"]:
        assert k in kinds

    labels = {m["kind"]: m["label"] for m in data["measurements"]}
    assert labels["biceps_r"] == "Biceps"
    assert labels["forearm_r"] == "Forearm"
    assert labels["waist"] == "Waist"

    for m in data["measurements"]:
        assert "label" in m
        assert "unit" in m
        assert "latest" in m
        assert "delta_cm" in m
        assert "significant" in m
        assert isinstance(m["history"], list)

    assert "workouts" in data

    # 2. Populated measurement state verification
    for i in range(9):
        store.add_metric("waist", 82.0 - i * 0.2, ts=f"2026-09-{i+1:02d}")
    for i in range(5):
        store.add_metric("biceps_r", 37.0 + i * 0.1, ts=f"2026-09-{i+1:02d}")
        store.add_metric("forearm_r", 30.0 + i * 0.1, ts=f"2026-09-{i+1:02d}")

    res_pop = client.get("/api/body")
    assert res_pop.status_code == 200
    data_pop = res_pop.json()

    waist_m = next(m for m in data_pop["measurements"] if m["kind"] == "waist")
    assert len(waist_m["history"]) == 9
    assert waist_m["latest"] == 80.4
    assert waist_m["delta_cm"] == -1.6

    biceps_m = next(m for m in data_pop["measurements"] if m["kind"] == "biceps_r")
    assert len(biceps_m["history"]) == 5

    forearm_m = next(m for m in data_pop["measurements"] if m["kind"] == "forearm_r")
    assert len(forearm_m["history"]) == 5


def test_task_done_flow():
    with store.session() as con:
        con.execute(
            "INSERT INTO tasks(title, project, priority, status) VALUES('Test task for API', 'test', 1, 'open')"
        )
        task_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    res = client.post(f"/api/tasks/{task_id}/done")
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    with store.session() as con:
        row = con.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        assert row["status"] == "done"

    # Idempotent call returns ok
    res_repeat = client.post(f"/api/tasks/{task_id}/done")
    assert res_repeat.status_code == 200
    assert res_repeat.json() == {"ok": True}

    # 404 for non-existent task
    res_notfound = client.post("/api/tasks/999999/done")
    assert res_notfound.status_code == 404
    assert res_notfound.json()["error"]["code"] == "not_found"


def test_unhandled_route_returns_501():
    res = client.get("/api/unknown/endpoint")
    assert res.status_code == 501
    assert res.json()["error"]["code"] == "not_implemented"


def test_auth_protection(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("DEV_USER_ID", raising=False)

    res = client.get("/api/today")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "no_init_data"


def test_config_db_path_and_no_logic():
    from app import config
    from pathlib import Path

    assert hasattr(config, "DB_PATH")
    assert not hasattr(config, "PRIEST_DB")
    p = Path(config.DB_PATH)
    assert p.name == "life.db"

    # Verify logic module is eliminated
    with pytest.raises(ImportError):
        from app import logic  # noqa: F401


def test_static_assets():
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")


def test_dynamic_facts_and_habits_titles_and_targets():
    # 1. When facts has no targets, API returns null (None)
    res_sleep_empty = client.get("/api/sleep?days=7")
    assert res_sleep_empty.status_code == 200
    s_empty = res_sleep_empty.json()
    assert s_empty["target_wake"] is None
    assert s_empty["target_bed"] is None
    assert s_empty["target_hours"] is None
    assert s_empty["avg_delta_min"] is None

    res_today_empty = client.get("/api/today")
    assert res_today_empty.status_code == 200
    t_empty = res_today_empty.json()
    assert t_empty["sleep"]["target"] is None
    assert t_empty["sleep"]["target_wake"] is None
    assert t_empty["sleep"]["target_bed"] is None
    assert t_empty["sleep"]["target_hours"] is None
    assert t_empty["sleep"]["delta_min"] is None

    # 2. When facts has targets, API returns exactly what is in database
    store.set_fact("wake_target", "08:15")
    store.set_fact("bed_target", "00:30")
    store.set_fact("sleep_target", "8.0")

    res_sleep = client.get("/api/sleep?days=7")
    assert res_sleep.status_code == 200
    sleep_data = res_sleep.json()
    assert sleep_data["target_wake"] == "08:15"
    assert sleep_data["target_bed"] == "00:30"
    assert sleep_data["target_hours"] == 8.0

    res_today = client.get("/api/today")
    assert res_today.status_code == 200
    today_data = res_today.json()
    assert today_data["sleep"]["target"] == "08:15"
    assert today_data["sleep"]["target_wake"] == "08:15"
    assert today_data["sleep"]["target_bed"] == "00:30"
    assert today_data["sleep"]["target_hours"] == 8.0

    # 3. Test custom habit title in database reflects in today items
    with store.session() as con:
        con.execute(
            "INSERT INTO habits(key, title, rule, active_from, sort) "
            "VALUES('custom_reading', 'Read 20 pages of book', '{\"t\": \"metric_ge\", \"kind\": \"read_pages\", \"value\": 20}', '2026-01-01', 99)"
        )

    res_today_custom = client.get("/api/today")
    items = res_today_custom.json()["score"]["items"]
    custom_item = next((it for it in items if it["key"] == "custom_reading"), None)
    assert custom_item is not None
    assert custom_item["title"] == "Read 20 pages of book"
