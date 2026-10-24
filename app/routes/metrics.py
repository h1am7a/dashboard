from __future__ import annotations

import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app import auth, logic, store

router = APIRouter(prefix="/api", tags=["metrics"])


class MetricCreate(BaseModel):
    kind: str
    value: float
    date: str | None = None
    unit: str | None = None
    note: str | None = None


class WorkoutCreate(BaseModel):
    kind: str = "gym"
    duration_min: int = Field(60, ge=1, le=600)
    load: str = Field("moderate", pattern="^(easy|moderate|hard)$")
    date: str | None = None
    note: str | None = None


class MinimumToggle(BaseModel):
    metric_kind: str = Field(..., pattern="^(wake_light|protein_ok|routine_ok)$")


@router.post("/metrics")
def add_metric_api(payload: MetricCreate, user: dict = Depends(auth.get_current_user)):
    """Add metric via store.add_metric."""
    target_ts = payload.date or logic.get_logical_day()
    try:
        new_id = store.add_metric(
            kind=payload.kind,
            value=payload.value,
            ts=target_ts,
            unit=payload.unit,
            note=payload.note,
            source="webapp",
        )
        return {"ok": True, "id": new_id, "kind": payload.kind, "value": payload.value, "ts": target_ts}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/workouts")
def add_workout_api(payload: WorkoutCreate, user: dict = Depends(auth.get_current_user)):
    """Add workout via store.add_workout."""
    target_date = payload.date or logic.get_logical_day()
    try:
        new_id = store.add_workout(
            date_=target_date,
            kind=payload.kind,
            duration_min=payload.duration_min,
            load=payload.load,
            note=payload.note,
        )
        return {"ok": True, "id": new_id, "date": target_date}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/minimum/toggle")
def toggle_minimum_metric(payload: MinimumToggle, user: dict = Depends(auth.get_current_user)):
    """Toggle a daily minimum check (wake_light, protein_ok, routine_ok) for the current logical day."""
    logical_today = logic.get_logical_day()
    kind = payload.metric_kind

    with store.session() as con:
        cur = con.execute(
            "SELECT value FROM metrics WHERE kind=? AND ts=? ORDER BY id DESC LIMIT 1",
            (kind, logical_today),
        )
        row = cur.fetchone()
        curr_val = int(row["value"]) if row else 0
        new_val = 0 if curr_val == 1 else 1

    store.add_metric(
        kind=kind,
        value=new_val,
        ts=logical_today,
        unit="0-1",
        note="daily minimum",
        source="webapp",
    )

    return {
        "ok": True,
        "kind": kind,
        "value": new_val,
        "logical_day": logical_today,
    }
