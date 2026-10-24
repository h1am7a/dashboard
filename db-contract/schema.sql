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
FROM (SELECT DISTINCT ts AS day FROM metrics UNION SELECT DISTINCT date FROM workouts UNION SELECT DISTINCT substr(ts,1,10) FROM tx) d
CREATE TABLE events(
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  title TEXT NOT NULL,
  type TEXT,
  remind TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  note TEXT
)
CREATE TABLE facts(
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,
  value TEXT,
  updated TEXT NOT NULL DEFAULT (datetime('now'))
)
CREATE TABLE item_log(
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
  item_name TEXT,                   -- denormalization: log persists even if item is deleted
  action TEXT NOT NULL DEFAULT 'used',   -- used / skipped / restocked
  note TEXT,
  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
)
CREATE TABLE items(
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
)
CREATE TABLE journal(
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  mood INTEGER,
  energy INTEGER,
  text TEXT,
  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
)
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)
CREATE TABLE metrics(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,                 -- ISO date (YYYY-MM-DD) or datetime
  kind TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT,
  note TEXT,
  source TEXT DEFAULT 'chat',
  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
)
CREATE TABLE tasks(
  id INTEGER PRIMARY KEY,
  created TEXT NOT NULL DEFAULT (datetime('now')),
  title TEXT NOT NULL,
  project TEXT,
  due TEXT,
  status TEXT NOT NULL DEFAULT 'open',   -- open / done / dropped
  priority INTEGER NOT NULL DEFAULT 3,   -- 1 highest .. 5 lowest
  note TEXT,
  done_at TEXT
)
CREATE TABLE tx(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  amount REAL NOT NULL,
  currency TEXT NOT NULL DEFAULT 'RUB',
  direction TEXT NOT NULL,          -- in / out
  category TEXT,
  account TEXT,
  note TEXT,
  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
)
CREATE TABLE workouts(
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  kind TEXT,
  duration_min INTEGER,
  load TEXT,                        -- easy / moderate / hard
  note TEXT,
  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
)
CREATE INDEX ix_events_date ON events(date)
CREATE INDEX ix_item_log_date ON item_log(date, item_id)
CREATE INDEX ix_items_cat ON items(category, status)
CREATE INDEX ix_metrics_kind_ts ON metrics(kind, ts)
CREATE INDEX ix_tasks_status ON tasks(status, due)
CREATE INDEX ix_tx_ts ON tx(ts)
CREATE INDEX ix_workouts_date ON workouts(date)
