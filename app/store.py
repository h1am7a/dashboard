"""Слой доступа к данным профиля priest.

Единая точка правды для БД: и CLI (bin/life.py), и будущее Telegram-приложение
ходят в базу только через эти функции. Никаких ручных INSERT'ов.

Путь к БД: $DB_PATH или <workspace>/db/life.db
"""
from __future__ import annotations

import os
import json
import sqlite3
from datetime import date as date_cls
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 6

METRIC_KINDS = {
    # kind: (unit, допустимый диапазон или None)
    "weight": ("kg", (30, 250)),
    "waist": ("cm", (40, 200)),
    "biceps_r": ("cm", (15, 70)),
    "forearm_r": ("cm", (15, 55)),
    "thigh_r": ("cm", (25, 95)),
    "thigh_l": ("cm", (25, 95)),
    "walk_min": ("min", (0, 600)),
    "routine_am": ("0-1", (0, 1)),
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
    "screen_time": ("h", (0, 24)),
    "porn_count": ("раз", (0, 30)),
    # отметки минимума дня: 0 = нет, 1 = сделано
    "wake_light": ("0-1", (0, 1)),
    "protein_ok": ("0-1", (0, 1)),
    "routine_ok": ("0-1", (0, 1)),
}

DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,                 -- ISO date (YYYY-MM-DD) или datetime
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
  priority INTEGER NOT NULL DEFAULT 3,   -- 1 высокий .. 5 низкий
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

CREATE TABLE IF NOT EXISTS habits(
  key TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  rule TEXT NOT NULL,               -- JSON: см. HABIT_RULE
  active_from TEXT NOT NULL,
  active_to TEXT,                   -- NULL = действует
  sort INTEGER NOT NULL DEFAULT 100,
  note TEXT
);

CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  category TEXT,                    -- hygiene / training / supplement / household
  brand TEXT,
  volume TEXT,
  cadence TEXT,                     -- daily / 2x_week / weekly / as_needed
  role TEXT,                        -- зачем в рутине, как применять
  note TEXT,                        -- нюансы состава/использования
  status TEXT NOT NULL DEFAULT 'active',   -- active / out / dropped
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_items_cat ON items(category, status);

CREATE TABLE IF NOT EXISTS item_log(
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
  item_name TEXT,                   -- денормализация: лог живёт даже если айтем удалён
  action TEXT NOT NULL DEFAULT 'used',   -- used / skipped / restocked
  note TEXT,
  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_item_log_date ON item_log(date, item_id);

"""

# Представления пересоздаются на каждом migrate() — это дешево и избавляет
# от ручных миграций при изменении состава колонок.
DAILY_VIEW = """
CREATE VIEW v_daily AS
SELECT d.day,
  (SELECT value FROM metrics m WHERE m.kind='sleep_hours' AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS sleep_hours,
  (SELECT value FROM metrics m WHERE m.kind='wake_time'   AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS wake_time,
  (SELECT value FROM metrics m WHERE m.kind='bed_time'    AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS bed_time,
  (SELECT value FROM metrics m WHERE m.kind='weight'      AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS weight,
  (SELECT value FROM metrics m WHERE m.kind='waist'       AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS waist,
  (SELECT value FROM metrics m WHERE m.kind='biceps_r'    AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS biceps_r,
  (SELECT value FROM metrics m WHERE m.kind='forearm_r'   AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS forearm_r,
  (SELECT value FROM metrics m WHERE m.kind='thigh_r'     AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS thigh_r,
  (SELECT value FROM metrics m WHERE m.kind='thigh_l'     AND m.ts=d.day ORDER BY m.id DESC LIMIT 1) AS thigh_l,
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
    return Path(os.environ.get("DB_PATH") or (WORKSPACE / "db" / "life.db"))


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
    cur = None
    try:
        cur = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        pass
    old = int(cur["value"]) if cur else 0
    if not cur or old < SCHEMA_VERSION:
        con.executescript(DDL)
        con.executescript("DROP VIEW IF EXISTS v_daily;")
        seed_habits(con)
        con.executescript(DAILY_VIEW)
        con.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )


# ---------- утилиты валидации ----------
def _today() -> str:
    return date.today().isoformat()


def _norm_date(value: str | None) -> str:
    if not value or value in ("today", "сегодня", "now"):
        return _today()
    s = value.strip()
    if s in ("yesterday", "вчера"):
        return (date.today() - timedelta(days=1)).isoformat()
    try:
        datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"дата '{value}' не парсится, нужен ISO: YYYY-MM-DD или YYYY-MM-DDTHH:MM")
    return s


def _check_metric(kind: str, value: float, unit: str | None) -> tuple[float, str | None]:
    kind = kind.strip().lower()
    try:
        value = float(str(value).replace(",", "."))
    except ValueError:
        raise ValueError(f"значение '{value}' не число")
    unit = unit or (METRIC_KINDS.get(kind) or (None, None))[0]
    rng = (METRIC_KINDS.get(kind) or (None, None))[1]
    if rng and not (rng[0] <= value <= rng[1]):
        raise ValueError(f"{kind}={value} вне правдоподобного диапазона {rng[0]}..{rng[1]} — проверь единицы")
    return value, unit


# ---------- запись ----------
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
            raise ValueError("duration_min должен быть 1..600 минут")
    if load and load not in ("easy", "moderate", "hard"):
        raise ValueError("load: easy / moderate / hard")
    with session() as c:
        return c.execute(
            "INSERT INTO workouts(date,kind,duration_min,load,note) VALUES(?,?,?,?,?)",
            (d, kind, duration_min, load, note),
        ).lastrowid


def add_task(title, project=None, due=None, priority=3, note=None) -> int:
    if not title or not title.strip():
        raise ValueError("task: пустой title")
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
            raise ValueError(f"открытая задача id={task_id} не найдена")


def add_event(date_, title, type_=None, remind=None, note=None) -> int:
    if not title:
        raise ValueError("event: пустой title")
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
        raise ValueError("amount должен быть > 0, направление задаётся direction")
    if direction not in ("in", "out"):
        raise ValueError("direction: in / out")
    with session() as c:
        return c.execute(
            "INSERT INTO tx(ts,amount,currency,direction,category,account,note) VALUES(?,?,?,?,?,?,?)",
            (_norm_date(ts), amount, currency.upper(), direction, category, account, note),
        ).lastrowid


# ---------- инвентарь и рутины ----------
ITEM_CADENCES = ("daily", "daily_2x", "2x_week", "weekly", "as_needed")
ITEM_ACTIONS = ("used", "skipped", "restocked")


def add_item(name, category=None, brand=None, volume=None, cadence=None,
             role=None, note=None, status="active") -> int:
    if not name or not name.strip():
        raise ValueError("item: пустое name")
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
        raise ValueError(f"не нашёл средство '{needle}' — проверь команду items")
    names = ", ".join(r["name"] for r in found)
    raise ValueError(f"под '{needle}' подходит несколько: {names} — уточни")


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


# ---------- чтение ----------
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


# --------------------------------------------------------------------------
# Якоря дня (habits). Один источник правды для хитмапа и «Today».
# Правило — JSON-строка, интерпретируется в _rule_done. Пользователь ничего
# не отмечает руками: всё выводится из метрик, тренировок и задач.
# --------------------------------------------------------------------------
DEFAULT_HABITS = [
    ("sleep_ge_7",   "Sleep ≥ 7h",           {"t": "metric_ge", "kind": "sleep_hours", "value": 7}, 10),
    ("wake_window",  "Wake in window",       {"t": "wake_window", "tol_min": 60}, 20),
    ("workout_day",  "Workout",              {"t": "workouts_ge", "value": 1}, 30),
    ("protein_ok",   "Protein target",       {"t": "metric_ge", "kind": "protein_ok", "value": 1}, 40),
    ("routine_eve",  "Evening routine",      {"t": "metric_ge", "kind": "routine_ok", "value": 1}, 50),
    ("routine_am",   "Morning routine",      {"t": "metric_ge", "kind": "routine_am", "value": 1}, 55),
    ("walk_30",      "Walk ≥ 30m",           {"t": "metric_ge", "kind": "walk_min", "value": 30}, 60),
]

# Якоря, выключенные на старте (пользователь ещё не начал).
HABITS_OFF_AT_START = {"walk_30", "routine_am"}


def seed_habits(con: sqlite3.Connection | None = None) -> None:
    """Заводит якоря, если их ещё нет. Существующие не трогает."""
    if con is None:
        with session() as c:
            return seed_habits(c)
    c = con
    for key, title, rule, sort in DEFAULT_HABITS:
        if c.execute("SELECT 1 FROM habits WHERE key=?", (key,)).fetchone():
            continue
        active_to = "2000-01-01" if key in HABITS_OFF_AT_START else None
        c.execute(
            "INSERT INTO habits(key,title,rule,active_from,active_to,sort) VALUES(?,?,?,?,?,?)",
            (key, title, json.dumps(rule, ensure_ascii=False), "2026-01-01", active_to, sort),
        )


def habits_list(day: str | None = None, include_inactive: bool = False) -> list[dict]:
    day = _norm_date(day) if day else _today()
    out = []
    with session() as c:
        for r in c.execute("SELECT * FROM habits ORDER BY sort, key"):
            rule = json.loads(r["rule"])
            out.append({
                "key": r["key"], "title": r["title"],
                "active_from": r["active_from"], "active_to": r["active_to"],
                "rule": rule, "sort": r["sort"],
                "active": _habit_active(r, day),
                "done": _rule_done(c, rule, day),
            })
    return out if include_inactive else [h for h in out if h["active"]]


def add_habit(key: str, title: str, rule: dict, active_from: str | None = None,
              sort: int = 100, note: str | None = None) -> None:
    if not key or not title:
        raise ValueError("habit: key and title are required")
    if rule.get("t") not in ("metric_ge", "workouts_ge", "wake_window"):
        raise ValueError("habit: rule.t ∈ metric_ge / workouts_ge / wake_window")
    with session() as c:
        c.execute(
            "INSERT INTO habits(key,title,rule,active_from,active_to,sort,note) VALUES(?,?,?,?,NULL,?,?) "
            "ON CONFLICT(key) DO UPDATE SET title=excluded.title, rule=excluded.rule, "
            "active_to=NULL, sort=excluded.sort, note=excluded.note",
            (key, title, json.dumps(rule, ensure_ascii=False), active_from or _today(), int(sort), note),
        )


def deactivate_habit(key: str, day: str | None = None) -> None:
    """Закрывает якорь с указанной даты: история до неё сохраняется, дальше не считается."""
    day = _norm_date(day) if day else _today()
    # действует по вчера включительно: сегодняшний день ещё можно закрыть
    cutoff = (date_cls.fromisoformat(day) - timedelta(days=1)).isoformat()
    with session() as c:
        cur = c.execute("UPDATE habits SET active_to=? WHERE key=? AND (active_to IS NULL OR active_to>?)",
                        (cutoff, key, cutoff))
        if cur.rowcount == 0:
            raise ValueError(f"активный якорь {key} не найден")


def _habit_active(row, day: str) -> bool:
    if row["active_from"] and day < row["active_from"]:
        return False
    if row["active_to"] and day > row["active_to"]:
        return False
    return True


def _rule_done(c, rule: dict, day: str) -> bool:
    t = rule.get("t")
    if t == "metric_ge":
        v = c.execute("SELECT MAX(value) v FROM metrics WHERE kind=? AND ts=?",
                      (rule["kind"], day)).fetchone()["v"]
        return v is not None and v >= float(rule.get("value", 1))
    if t == "workouts_ge":
        n = c.execute("SELECT COUNT(*) n FROM workouts WHERE date=?", (day,)).fetchone()["n"]
        return n >= int(rule.get("value", 1))
    if t == "wake_window":
        v = c.execute("SELECT MAX(value) v FROM metrics WHERE kind='wake_time' AND ts=?", (day,)).fetchone()["v"]
        if v is None:
            return False
        target = c.execute("SELECT value FROM facts WHERE key='wake_target'").fetchone()
        try:
            th, tm = (target["value"] if target else "14:30").replace(".", ":").split(":")[:2]
            tgt = int(float(th)) + int(float(tm)) / 60
        except Exception:
            tgt = 14.5
        return abs(float(v) - tgt) * 60 <= float(rule.get("tol_min", 60))
    return False


def day_score(day: str | None = None) -> dict:
    """Счёт дня: якоря + задачи со сроком на этот день. total=0 → нет данных."""
    day = _norm_date(day) if day else _today()
    items = [{"key": h["key"], "title": h["title"], "done": h["done"], "type": "habit"}
             for h in habits_list(day)]
    with session() as c:
        for r in c.execute("SELECT id,title,status FROM tasks WHERE due=? ORDER BY priority,id", (day,)):
            items.append({"key": f"task:{r['id']}", "title": r["title"],
                          "done": r["status"] == "done", "type": "task"})
        # день без единой записи — это не «всё провалено», а «нет данных»
        has_data = bool(c.execute(
            "SELECT (SELECT COUNT(*) FROM metrics WHERE ts=?) + (SELECT COUNT(*) FROM workouts WHERE date=?) n",
            (day, day)).fetchone()["n"])
    total = len(items)
    done = sum(1 for i in items if i["done"])
    # нет записей за день — процент неизвестен, но сами якоря показываем:
    # иначе чеклист пропадает и отметить пункт нечем
    percent = round(100 * done / total) if (total and has_data) else None
    return {"day": day, "done": done, "total": total, "percent": percent, "items": items}


def hitmap(days: int = 84) -> list[dict]:
    """Хитмап: процент выполнения по дням, от старых к новым. total=0 → нет данных."""
    end = date_cls.fromisoformat(_today())
    with session() as c:
        wdays = {r["date"] for r in c.execute("SELECT DISTINCT date FROM workouts")}
    out = []
    for i in range(days - 1, -1, -1):
        d = (end - timedelta(days=i)).isoformat()
        s = day_score(d)
        out.append({"day": d, "done": s["done"], "total": s["total"], "percent": s["percent"],
                    "has_workout": d in wdays})
    return out


BODY_KINDS = ["waist", "biceps_r", "forearm_r", "thigh_r", "thigh_l"]


def measurements(days: int = 365, kinds: list[str] | None = None) -> list[dict]:
    """Замеры тела для экрана Body: последнее значение, дельта и история.

    delta_cm — разница с предыдущим замером; < 1 см помечается шумом (significant=False),
    потому что точность портняжной ленты ±1–1,5 см.
    """
    kinds = kinds or BODY_KINDS
    since = (date_cls.fromisoformat(_today()) - timedelta(days=days)).isoformat()
    out = []
    with session() as c:
        for k in kinds:
            rows = c.execute(
                "SELECT ts, value FROM metrics WHERE kind=? AND ts>=? ORDER BY ts, id", (k, since)
            ).fetchall()
            if not rows:
                out.append({"kind": k, "unit": METRIC_KINDS.get(k, ("cm",))[0],
                            "latest": None, "date": None, "delta_cm": None,
                            "significant": False, "history": []})
                continue
            latest = rows[-1]
            prev = rows[-2] if len(rows) > 1 else None
            delta = round(latest["value"] - prev["value"], 1) if prev else None
            out.append({
                "kind": k, "unit": METRIC_KINDS.get(k, ("cm",))[0],
                "latest": latest["value"], "date": latest["ts"],
                "delta_cm": delta,
                "significant": delta is not None and abs(delta) >= 1.0,
                "since": prev["ts"] if prev else None,
                "history": [{"date": r["ts"], "value": r["value"]} for r in rows],
            })
    return out


def thigh_asymmetry(day: str | None = None) -> dict | None:
    """Разница правого и левого бедра на последнюю дату, где есть оба замера."""
    with session() as c:
        r = c.execute(
            "SELECT r.ts, r.value r, l.value l FROM metrics r JOIN metrics l "
            "ON l.ts=r.ts AND l.kind='thigh_l' WHERE r.kind='thigh_r' "
            "ORDER BY r.ts DESC, r.id DESC LIMIT 1").fetchone()
    if not r:
        return None
    return {"date": r["ts"], "right": r["r"], "left": r["l"], "diff": round(r["r"] - r["l"], 1)}
