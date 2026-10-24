"""Data access layer for life database profile.

Single source of truth for the database: both CLI (bin/life.py) and
the Telegram Mini App interface access the database exclusively through these functions.
No raw SQL INSERTs anywhere else.

Database path: $PRIEST_DB or <workspace>/db/life.db
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 4

METRIC_KINDS = {
    # kind: (unit, valid range or None)
    "weight": ("kg", (30, 250)),
    "waist": ("cm", (40, 200)),
    "sleep_hours": ("h", (0, 24)),
    "wake_time": ("hh", (0, 24)),
    "bed_time": ("hh", (0, 24)),
    "sleep_quality": ("1-5", (1, 5)),
    "steps": ("steps", (0, 100000)),
    "hr_rest": ("bpm", (25, 150)),
    "hr_max": ("bpm", (60, 240)),
    "mood": ("1-10", (1, 10)),
    "energy": ("1-10", (1, 10)),
    "stress": ("1-10", (1, 10)),
    "focus": ("1-10", (1, 10)),
    "calories": ("kcal", (0, 10000)),
    "protein": ("g", (0, 500)),
    "water": ("l", (0, 15)),
    "screen_time": ("h", (0, 168)),
    "porn_count": ("count", (0, 30)),
    # daily minimum checks: 0 = not done, 1 = completed
    "wake_light": ("0-1", (0, 1)),
    "protein_ok": ("0-1", (0, 1)),
    "routine_ok": ("0-1", (0, 1)),
}

DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,                 -- ISO date (YYYY-MM-DD) or datetime
  kind TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT,
  note TEXT,
  source TEXT DEFAULT 'chat',
  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_metrics_kind_ts ON metrics(kind, ts);

CREATE TABLE IF NOT EXISTS workouts(
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  kind TEXT,
  duration_min INTEGER,
  load TEXT,                        -- easy / moderate / hard
  note TEXT,
  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_workouts_date ON workouts(date);

CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY,
  created TEXT NOT NULL DEFAULT (datetime('now')),
  title TEXT NOT NULL,
  project TEXT,
  due TEXT,
  status TEXT NOT NULL DEFAULT 'open',   -- open / done / dropped
  priority INTEGER NOT NULL DEFAULT 3,   -- 1 highest .. 5 lowest
  note TEXT,
  done_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status, due);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  title TEXT NOT NULL,
  type TEXT,
  remind TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  note TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_date ON events(date);

CREATE TABLE IF NOT EXISTS journal(
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  mood INTEGER,
  energy INTEGER,
  text TEXT,
  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS facts(
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,
  value TEXT,
  updated TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tx(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  amount REAL NOT NULL,
  currency TEXT NOT NULL DEFAULT 'RUB',
  direction TEXT NOT NULL,          -- in / out
  category TEXT,
  account TEXT,
  note TEXT,
  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_tx_ts ON tx(ts);

CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  category TEXT,                    -- hygiene / training / supplement / household
  brand TEXT,
  volume TEXT,
  cadence TEXT,                     -- daily / 2x_week / weekly / as_needed
  role TEXT,                        -- routine purpose, usage instructions
  note TEXT,                        -- formulation / usage nuances
  status TEXT NOT NULL DEFAULT 'active',   -- active / out / dropped
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_items_cat ON items(category, status);

CREATE TABLE IF NOT EXISTS item_log(
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
  item_name TEXT,                   -- denormalization: log persists even if item is deleted
  action TEXT NOT NULL DEFAULT 'used',   -- used / skipped / restocked
  note TEXT,
  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_item_log_date ON item_log(date, item_id);

"""

# Views are recreated upon each migrate() call
DAILY_VIEW = """
CREATE VIEW v_daily AS
SELECT d.day,
  (SELECT value FROM metrics m WHERE m.kind='sleep_hours' AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS sleep_hours,
  (SELECT value FROM metrics m WHERE m.kind='wake_time'   AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS wake_time,
  (SELECT value FROM metrics m WHERE m.kind='bed_time'    AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS bed_time,
  (SELECT value FROM metrics m WHERE m.kind='weight'      AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS weight,
  (SELECT value FROM metrics m WHERE m.kind='waist'       AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS waist,
  (SELECT value FROM metrics m WHERE m.kind='mood'        AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS mood,
  (SELECT value FROM metrics m WHERE m.kind='energy'      AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS energy,
  (SELECT value FROM metrics m WHERE m.kind='porn_count'  AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS porn_count,
  (SELECT value FROM metrics m WHERE m.kind='screen_time' AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS screen_time,
  (SELECT value FROM metrics m WHERE m.kind='wake_light'  AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS wake_light,
  (SELECT value FROM metrics m WHERE m.kind='protein_ok'  AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS protein_ok,
  (SELECT value FROM metrics m WHERE m.kind='routine_ok'  AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS routine_ok,
  (SELECT SUM(duration_min) FROM workouts w WHERE w.date=d.day) AS workout_min,
  (SELECT COUNT(*) FROM workouts w WHERE w.date=d.day) AS workout_count,
  (SELECT SUM(CASE direction WHEN 'out' THEN -amount ELSE amount END) FROM tx t WHERE substr(t.ts,1,10)=d.day) AS net_money
FROM (SELECT DISTINCT ts AS day FROM metrics UNION SELECT DISTINCT date FROM workouts UNION SELECT DISTINCT substr(ts,1,10) FROM tx) d;
"""


def db_path() -> Path:
    return Path(os.environ.get("PRIEST_DB") or (WORKSPACE / "db" / "life.db"))


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


@contextmanager
def session(path=None):
    con = connect(path)
    try:
        migrate(con)
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def migrate(con: sqlite3.Connection) -> None:
    con.executescript(DDL)
    con.executescript("DROP VIEW IF EXISTS v_daily;")
    con.executescript(DAILY_VIEW)
    cur = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    old = int(cur["value"]) if cur else 0
    if old < SCHEMA_VERSION:
        con.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )


# ---------- validation utilities ----------
def _today() -> str:
    return date.today().isoformat()


def _norm_date(value: str | None) -> str:
    if not value or value in ("today",):
        return _today()
    s = value.strip()
    if s in ("yesterday",):
        return (date.today() - timedelta(days=1)).isoformat()
    try:
        datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"cannot parse date '{value}', ISO format required: YYYY-MM-DD or YYYY-MM-DDTHH:MM")
    return s


def _check_metric(kind: str, value: float, unit: str | None) -> tuple[float, str | None]:
    kind = kind.strip().lower()
    try:
        value = float(str(value).replace(",", "."))
    except ValueError:
        raise ValueError(f"value '{value}' is not a number")
    unit = unit or (METRIC_KINDS.get(kind) or (None, None))[0]
    rng = (METRIC_KINDS.get(kind) or (None, None))[1]
    if rng and not (rng[0] <= value <= rng[1]):
        raise ValueError(f"{kind}={value} is outside valid range {rng[0]}..{rng[1]} — check units")
    return value, unit


# ---------- write methods ----------
def add_metric(kind, value, ts=None, unit=None, note=None, source="chat", con=None) -> int:
    value, unit = _check_metric(kind, value, unit)
    ts = _norm_date(ts)
    with session() as c:
        cur = c.execute(
            "INSERT INTO metrics(ts,kind,value,unit,note,source) VALUES(?,?,?,?,?,?)",
            (ts, kind.strip().lower(), value, unit, note, source),
        )
        return cur.lastrowid


def add_workout(date_=None, kind=None, duration_min=None, load=None, note=None) -> int:
    d = _norm_date(date_)
    if duration_min is not None:
        duration_min = int(duration_min)
        if not 0 < duration_min <= 600:
            raise ValueError("duration_min must be 1..600 minutes")
    if load and load not in ("easy", "moderate", "hard"):
        raise ValueError("load: easy / moderate / hard")
    with session() as c:
        return c.execute(
            "INSERT INTO workouts(date,kind,duration_min,load,note) VALUES(?,?,?,?,?)",
            (d, kind, duration_min, load, note),
        ).lastrowid


def add_task(title, project=None, due=None, priority=3, note=None) -> int:
    if not title or not title.strip():
        raise ValueError("task: empty title")
    priority = int(priority)
    if not 1 <= priority <= 5:
        raise ValueError("priority 1..5")
    with session() as c:
        return c.execute(
            "INSERT INTO tasks(title,project,due,priority,note) VALUES(?,?,?,?,?)",
            (title.strip(), project, _norm_date(due) if due else None, priority, note),
        ).lastrowid


def close_task(task_id: int, status="done") -> None:
    if status not in ("done", "dropped"):
        raise ValueError("status: done / dropped")
    with session() as c:
        cur = c.execute(
            "UPDATE tasks SET status=?, done_at=datetime('now') WHERE id=? AND status='open'",
            (status, int(task_id)),
        )
        if cur.rowcount == 0:
            raise ValueError(f"open task with id={task_id} not found")


def add_event(date_, title, type_=None, remind=None, note=None) -> int:
    if not title:
        raise ValueError("event: empty title")
    with session() as c:
        return c.execute(
            "INSERT INTO events(date,title,type,remind,note) VALUES(?,?,?,?,?)",
            (_norm_date(date_), title, type_, remind, note),
        ).lastrowid


def add_journal(date_=None, mood=None, energy=None, text=None) -> int:
    with session() as c:
        return c.execute(
            "INSERT INTO journal(date,mood,energy,text) VALUES(?,?,?,?)",
            (_norm_date(date_), mood, energy, text),
        ).lastrowid


def add_tx(amount, direction="out", ts=None, currency="RUB", category=None, account=None, note=None) -> int:
    amount = float(str(amount).replace(",", "."))
    if amount <= 0:
        raise ValueError("amount must be > 0, direction is specified via direction param")
    if direction not in ("in", "out"):
        raise ValueError("direction: in / out")
    with session() as c:
        return c.execute(
            "INSERT INTO tx(ts,amount,currency,direction,category,account,note) VALUES(?,?,?,?,?,?,?)",
            (_norm_date(ts), amount, currency.upper(), direction, category, account, note),
        ).lastrowid


# ---------- inventory and routines ----------
ITEM_CADENCES = ("daily", "daily_2x", "2x_week", "weekly", "as_needed")
ITEM_ACTIONS = ("used", "skipped", "restocked")


def add_item(name, category=None, brand=None, volume=None, cadence=None,
             role=None, note=None, status="active") -> int:
    if not name or not name.strip():
        raise ValueError("item: empty name")
    if cadence and cadence not in ITEM_CADENCES:
        raise ValueError(f"cadence: {' / '.join(ITEM_CADENCES)}")
    if status not in ("active", "out", "dropped"):
        raise ValueError("status: active / out / dropped")
    with session() as c:
        cur = c.execute(
            "INSERT INTO items(name,category,brand,volume,cadence,role,note,status) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
            "category=excluded.category, brand=excluded.brand, volume=excluded.volume, "
            "cadence=excluded.cadence, role=excluded.role, note=excluded.note, status=excluded.status",
            (name.strip(), category, brand, volume, cadence, role, note, status),
        )
        if cur.lastrowid:
            return cur.lastrowid
        return c.execute("SELECT id FROM items WHERE name=?", (name.strip(),)).fetchone()["id"]


def _find_item(con, needle: str) -> sqlite3.Row:
    needle = needle.strip()
    exact = con.execute("SELECT * FROM items WHERE name=?", (needle,)).fetchone()
    if exact:
        return exact
    found = con.execute(
        "SELECT * FROM items WHERE name LIKE ? AND status='active' ORDER BY id", (f"%{needle}%",)
    ).fetchall()
    if len(found) == 1:
        return found[0]
    if not found:
        raise ValueError(f"item '{needle}' not found — check items list")
    names = ", ".join(r["name"] for r in found)
    raise ValueError(f"multiple items match '{needle}': {names} — please clarify")


def log_item(name, action="used", date_=None, note=None) -> int:
    if action not in ITEM_ACTIONS:
        raise ValueError(f"action: {' / '.join(ITEM_ACTIONS)}")
    d = _norm_date(date_)
    with session() as c:
        it = _find_item(c, name)
        return c.execute(
            "INSERT INTO item_log(date,item_id,item_name,action,note) VALUES(?,?,?,?,?)",
            (d, it["id"], it["name"], action, note),
        ).lastrowid


def items_list(category=None, include_dropped=False) -> list[dict]:
    with session() as c:
        q = "SELECT id,name,category,brand,volume,cadence,role,note,status FROM items WHERE 1=1"
        p: list = []
        if category:
            q += " AND category=?"
            p.append(category)
        if not include_dropped:
            q += " AND status!='dropped'"
        q += " ORDER BY category, name"
        return rows(c, q, p)


def item_stats(days: int = 30) -> list[dict]:
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    with session() as c:
        return rows(c, """
            SELECT i.name, i.cadence,
                   SUM(CASE WHEN l.action='used' THEN 1 ELSE 0 END) used,
                   SUM(CASE WHEN l.action='skipped' THEN 1 ELSE 0 END) skipped,
                   MAX(l.date) last_used
            FROM items i LEFT JOIN item_log l ON l.item_id=i.id AND l.date>=?
            WHERE i.status='active' GROUP BY i.id ORDER BY used ASC, i.name""", (since,))


def set_fact(key, value) -> None:
    with session() as c:
        c.execute(
            "INSERT INTO facts(key,value,updated) VALUES(?,?,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=datetime('now')",
            (key, str(value)),
        )


# ---------- read methods ----------
def rows(con: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in con.execute(sql, params)]


def report(days: int = 7, con: sqlite3.Connection | None = None) -> dict:
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    with session() as c:
        m = rows(c, "SELECT kind, ROUND(AVG(value),2) avg, COUNT(*) n, MIN(value) min, MAX(value) max "
                    "FROM metrics WHERE ts>=? GROUP BY kind ORDER BY kind", (since,))
        w = rows(c, "SELECT COUNT(*) n, COALESCE(SUM(duration_min),0) minutes FROM workouts WHERE date>=?", (since,))
        t = rows(c, "SELECT status, COUNT(*) n FROM tasks GROUP BY status")
        money = rows(c, "SELECT direction, currency, ROUND(SUM(amount),2) total FROM tx WHERE ts>=? "
                        "GROUP BY direction, currency", (since,))
        daily = rows(c, "SELECT * FROM v_daily WHERE day>=? ORDER BY day", (since,))
    return {"period_days": days, "since": since, "metrics": m, "workouts": w[0],
            "tasks": t, "money": money, "daily": daily}
