import os
os.environ["DEV_MODE"] = "1"
os.environ["DEV_USER_ID"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app import store

client = TestClient(app)


def test_health():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data == {"ok": True, "version": "1.0"}


def test_today():
    res = client.get("/api/today")
    assert res.status_code == 200
    data = res.json()

    assert "day" in data
    assert "weekday" in data
    assert "in_progress" in data
    assert data["in_progress"] is True
    assert "score" in data
    assert "done" in data["score"]
    assert "total" in data["score"]
    assert "items" in data["score"]
    assert len(data["score"]["items"]) > 0
    assert "sleep" in data
    assert "target" in data["sleep"]
    assert "mood" in data
    assert "hitmap" in data
    assert len(data["hitmap"]) == 126
    assert "has_workout" in data["hitmap"][0]
    assert isinstance(data["hitmap"][0]["has_workout"], bool)


def test_day_score_empty_day():
    score = store.day_score("2035-01-01")
    assert score["percent"] is None
    assert score["total"] > 0
    assert len(score["items"]) > 0


def test_sleep_periods():
    for days in [7, 30, 90]:
        res = client.get(f"/api/sleep?days={days}")
        assert res.status_code == 200
        data = res.json()

        assert data["period"]["days"] == days
        assert len(data["series"]) == days
        assert "target_hours" in data
        assert "target_wake" in data
        assert "prev_week" in data
        assert "significant" in data["prev_week"]

    # Invalid days returns 400
    res_bad = client.get("/api/sleep?days=14")
    assert res_bad.status_code == 400
    assert res_bad.json()["error"]["code"] == "bad_period"


def test_body():
    res = client.get("/api/body")
    assert res.status_code == 200
    data = res.json()

    assert "measurements" in data
    kinds = [m["kind"] for m in data["measurements"]]
    for k in ["waist", "biceps_r", "forearm_r"]:
        assert k in kinds

    for m in data["measurements"]:
        assert "label" in m
        assert "unit" in m
        assert "latest" in m
        assert "delta_cm" in m
        assert "significant" in m
        assert "history" in m

    assert "workouts" in data


def test_task_done_flow():
    # 1. Create a task via store
    with store.session() as con:
        cur = con.execute(
            "INSERT INTO tasks(title, project, priority, status) VALUES('Test task for API', 'test', 1, 'open')"
        )
        task_id = cur.lastrowid

    # 2. Mark task done via API
    res = client.post(f"/api/tasks/{task_id}/done")
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    # 3. Verify task is closed
    with store.session() as con:
        row = con.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        assert row["status"] == "done"

    # 4. Idempotency test (calling done again succeeds)
    res_repeat = client.post(f"/api/tasks/{task_id}/done")
    assert res_repeat.status_code == 200
    assert res_repeat.json() == {"ok": True}


def test_unhandled_route_returns_501():
    res = client.get("/api/unsupported_route")
    assert res.status_code == 501
    assert res.json()["error"]["code"] == "not_implemented"


def test_auth_protection():
    orig_dev = os.environ.get("DEV_MODE")
    orig_user = os.environ.get("DEV_USER_ID")
    os.environ.pop("DEV_MODE", None)
    os.environ.pop("DEV_USER_ID", None)

    try:
        res = client.get("/api/today")
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "no_init_data"
    finally:
        if orig_dev is not None:
            os.environ["DEV_MODE"] = orig_dev
        if orig_user is not None:
            os.environ["DEV_USER_ID"] = orig_user


def test_config_db_path_and_no_logic():
    from app import config
    from pathlib import Path
    assert hasattr(config, "DB_PATH")
    assert not hasattr(config, "PRIEST_DB")
    assert not (Path(__file__).parent.parent / "app" / "logic.py").exists()
