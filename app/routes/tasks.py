from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app import auth, logic, store

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1)
    project: str | None = None
    due: str | None = None
    priority: int = Field(3, ge=1, le=5)
    note: str | None = None


@router.get("")
def get_tasks_screen(user: dict = Depends(auth.get_current_user)):
    """Данные для экрана 'Дела'."""
    logical_today = logic.get_logical_day()
    curr_d = date.fromisoformat(logical_today)
    d7_ago = (curr_d - timedelta(days=7)).isoformat()

    with store.session() as con:
        # 1. Открытые дела
        open_tasks = store.rows(
            con,
            "SELECT id, title, project, due, priority, status, created FROM tasks "
            "WHERE status='open' ORDER BY priority ASC, COALESCE(due, '9999') ASC, id ASC",
        )
        formatted_open = []
        for t in open_tasks:
            due_str = t.get("due")
            is_overdue = False
            due_label = None
            if due_str:
                try:
                    d_obj = date.fromisoformat(due_str[:10])
                    due_label = f"до {logic.format_date_ru(due_str)}"
                    if d_obj < curr_d:
                        is_overdue = True
                        diff = (curr_d - d_obj).days
                        due_label = f"просрочен на {diff} д"
                except Exception:
                    due_label = f"до {due_str}"

            formatted_open.append({
                "id": t["id"],
                "title": t["title"],
                "project": t["project"],
                "due": t["due"],
                "due_label": due_label,
                "is_overdue": is_overdue,
                "priority": t["priority"],
            })

        # 2. Выполненные за 7 дней
        done_rows = store.rows(
            con,
            "SELECT id, title, project, done_at FROM tasks "
            "WHERE status='done' AND done_at>=? ORDER BY done_at DESC",
            (d7_ago,),
        )
        formatted_done = [{
            "id": r["id"],
            "title": r["title"],
            "project": r["project"],
            "done_at": r["done_at"],
            "date_ru": logic.format_date_ru(r["done_at"][:10]) if r.get("done_at") else "",
        } for r in done_rows]

        # 3. Ближайшие 5 событий
        event_rows = store.rows(
            con,
            "SELECT id, date, title, type, remind FROM events "
            "WHERE status='active' AND date>=? ORDER BY date ASC LIMIT 5",
            (logical_today,),
        )
        formatted_events = [{
            "id": e["id"],
            "date": e["date"],
            "date_ru": logic.format_date_ru(e["date"]),
            "title": e["title"],
            "type": e["type"],
            "remind": e["remind"],
        } for e in event_rows]

    return {
        "open_tasks": formatted_open,
        "completed_7d": formatted_done,
        "completed_count": len(formatted_done),
        "events": formatted_events,
    }


@router.post("")
def create_task(payload: TaskCreate, user: dict = Depends(auth.get_current_user)):
    """Создание новой задачи через store.add_task."""
    try:
        new_id = store.add_task(
            title=payload.title,
            project=payload.project,
            due=payload.due,
            priority=payload.priority,
            note=payload.note,
        )
        return {"ok": True, "id": new_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/done")
def complete_task(task_id: int, user: dict = Depends(auth.get_current_user)):
    """Отметка задачи выполненной через store.close_task."""
    try:
        store.close_task(task_id, status="done")
        return {"ok": True, "closed_id": task_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
