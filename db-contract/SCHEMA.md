# Architecture: Data, Scripts, Telegram Mini App

Updated: 2026-10-23 (added specification for Mini App)

## Principles (Future-proof design)

1. **One Database, One Access Layer.** `db/life.db` (SQLite, WAL mode) + `app/store.py`.
   Bot, dashboard, cron jobs, user in chat — everything writes exclusively through `store.py`.
   No direct or raw SQL INSERTs anywhere else.
2. **Validation in One Place.** Metric value ranges, units, and ISO dates are enforced in `store.py`.
   Invalid data is rejected before writing, never "fixed later".
3. **Versioned Schema.** `meta.schema_version` and migrations in `store.migrate()`.
   Adding tables/columns requires a version increment and `IF NOT EXISTS` / `ALTER TABLE`.
4. **Time Representation.** Stored in database as ISO-8601. Daily metrics use `YYYY-MM-DD`, timestamped events include exact time.
   Display in MSK is handled by the presentation layer, not the database.
5. **Dashboard-Ready.** Every view has a ready data source: `v_daily` (trends),
   `report()` (summaries), `tasks`/`events` (lists), `tx` (financials). The dashboard does not compute
   business logic on its own — it consumes pre-aggregated metrics.

## Files

```text
app/store.py      # Data layer: connect/session/migrate + add_*/report
bin/life.py       # CLI: manual entry and management
db/life.db        # SQLite database
db-contract/      # Canonical contracts and schemas
```

## Tables

| Table | Key Columns | Purpose |
|---|---|---|
| `metrics` | `ts`, `kind`, `value`, `unit` | Numeric measurements: sleep, weight, mood, steps, heart rate, calories |
| `workouts` | `date`, `kind`, `duration_min`, `load` | Workout logs |
| `tasks` | `title`, `project`, `due`, `priority`, `status` | To-do items |
| `events` | `date`, `title`, `type`, `remind` | Calendar, deadlines, reminders |
| `journal` | `date`, `mood`, `energy`, `text` | Daily reflection log |
| `tx` | `ts`, `amount`, `currency`, `direction`, `category` | Financial transactions |
| `facts` | `key` → `value` | Persistent facts (city, height, goals) |
| `items` | `name`, `category`, `brand`, `volume`, `cadence`, `role`, `note`, `status` | Routine inventory: hygiene, training gear, supplements |
| `item_log` | `date`, `item_id`, `item_name`, `action`, `note` | Routine check-ins: `used` / `skipped` / `restocked` |
| `meta` | `key` → `value` | Schema version and internal metadata |

Validated metric kinds: `weight`, `sleep_hours`, `sleep_quality`, `steps`, `hr_rest`, `hr_max`,
`mood`, `energy`, `stress`, `focus`, `calories`, `protein`, `water`, `screen_time`, `wake_time`, `bed_time`, `porn_count`.
Adding a new metric kind requires updating `METRIC_KINDS` in `app/store.py` (unit + valid range).

## Logical Day

The day boundary is **09:00**: everything occurring from 09:00 to 08:59 of the next calendar day is attributed to the starting date. This accommodates a shifted sleep phase (wake ~15:00, bedtime ~08:00); otherwise, a single night's sleep would be split across two calendar days, distorting analytics. The `--date` argument in commands refers to the logical day, not the calendar day.

## CLI (Canonical Write Path)

```bash
life.py sleep_hours 6.5 --date 2026-10-22 --note "went to bed at 2"
life.py weight 78.4
life.py workout --kind gym --min 60 --load hard
life.py task "Finish feature X" --project income --due 2026-10-30 --prio 1
life.py tasks / done 3 / drop 4
life.py event 2026-11-25 "VPN renewal" --remind 1d
life.py tx 1500 --dir out --cat food
life.py journal --mood 6 --energy 5 --text "..."
life.py fact city "..."
life.py items [--cat hygiene]                              # inventory
life.py item "Name" --cat hygiene --cadence daily --role "..." --note "..."
life.py used "<item>" [--date 2026-10-22]                  # check-in as used
life.py skip "<item>"                                      # mark as skipped
life.py usage --days 30                                    # unused items
life.py report --days 14
life.py get                                                # complete current snapshot
life.py sql "select ..."                                   # read-only query
```
Outputs JSON so scripts and bots can parse results reliably.

## Telegram Mini App Specification

### 0. Scope & Target User

Personal analytics dashboard **for a single user**. Not a SaaS, no multi-tenancy, no public registration. Goal: **understand current status within 10 seconds** — sleep, screen time, schedule, workouts, tasks — and log updates in two taps.

Platform: **Telegram Mini App** (runs inside Telegram WebView). Presentation timezone is **MSK (UTC+3)**; all dates use ISO format.

Key rule: The application **reads** the database and **writes only through `app/store.py`**. It never invents custom calculation logic — it uses `v_daily` and `report()` aggregates.

### 1. Data (Canonical Schema)

SQLite `db/life.db` in WAL mode. Full DDL is defined in `app/store.py` → `DDL`. Fields used by the panel:

- **`metrics`** — `ts` (ISO `YYYY-MM-DD` for daily entries), `kind`, `value` REAL, `unit`, `note`, `source` (default `chat`). Index on `(kind, ts)`.
- **`workouts`** — `date`, `kind` (`strength`/`gym`/`cardio`/...), `duration_min`, `load` (`easy`/`moderate`/`hard`), `note`.
- **`tasks`** — `title`, `project`, `due`, `priority` (1 highest ... 5 lowest), `status` (`open`/`done`/`dropped`), `done_at`.
- **`events`** — `date`, `title`, `type`, `remind`, `status` (`active`/`cancelled`).
- **`journal`** — `date`, `mood`, `energy`, `text`.
- **`items`** / **`item_log`** — inventory items and logs (`used`/`skipped`/`restocked`).
- **`facts`** — key-value persistent configuration facts.
- **`tx`** — financial ledger (reserved; not exposed in Mini App).
- **`v_daily`** — pre-aggregated daily view (sleep, workouts, mood). The panel reads **this view** rather than querying raw metrics.

Metrics (`METRIC_KINDS`, unit + allowed range): `weight, waist, sleep_hours, wake_time, bed_time, sleep_quality, steps, hr_rest, hr_max, mood, energy, stress, focus, calories, protein, water, screen_time, porn_count`. New kinds must be added to backend `METRIC_KINDS`, not on the frontend.

### 2. Domain Constraints

1. **Logical day starts at 09:00.** All records between 09:00 and 08:59 of the next calendar day belong to the start date.
2. **"Morning" = wake time, "Night" = bedtime**, regardless of clock hours.
3. **`wake_time` / `bed_time` are stored as decimal hours** (`15.5` = 15:30). Frontend displays `HH:MM`. Bedtimes past midnight are valid values in range 0–24; when calculating duration across midnight, wrap around (`bed - wake`, if $\le 0 \rightarrow +24$).
4. **Porn / masturbation is a daily counter** (`porn_count`), without streaks or "clean days". Any streak counter is strictly prohibited to avoid relapse-shame cycles.
5. **Screen time is logged weekly** (Sunday), value represents weekly hours. Daily goal $\le 7$ h, weekly goal $\le 50$ h. Never display an unverified "today's screen time".
6. **No moralizing in the UI.** Neutral language only: no "failure", "well done", or "bad day". Only facts and trends.
7. **Empty days are normal.** A missed entry does not reset streaks or flash red.

### 3. Application Views

**1. "Today"** — Default landing view.
- Header: Large logical date + wake time.
- Overview cards: Sleep (hours, 7-day average), Screen time (hours/week, target $\le 50$), counter (today / average), Mood (latest).
- Sparkline on each card (7–14 days).
- Quick action button for logging.
- Daily minimum checklist (wake & light, protein, routine).

**2. "Sleep"**
- 7 / 30 / 90 day switcher.
- Average sleep and delta comparison to previous period ($\ge 0.5$ h threshold).
- Sleep duration bar chart with 4h baseline and 7.5h target line.
- Wake time and bedtime spline charts with targets.
- Interactive inspection tooltips.

**3. "Body"**
- Four measurement cards: waist (weekly), biceps, forearm, thigh (monthly) with sparklines.
- 1 cm noise threshold: deltas under 1 cm show as "no change".
- Thigh asymmetry indicator.
- 7-day workout strip and selected day details.

**4. "Tasks"**
- Task list ordered by `priority`, then by `due`. Single-tap completion.
- Upcoming events.

### 4. API Specification

Prefix `/api`, JSON format. Flat responses without excessive nesting.

```text
GET /api/health
    -> {"ok": true, "version": "1.0"}

GET /api/today
    -> Today data, logical date, progress counter, sleep, mood, 18-week heatmap, checklist

GET /api/sleep?days=7|30|90
    -> Duration averages, deltas, sleep duration bars, wake/bed series

GET /api/body
    -> 4 measurement cards, thigh asymmetry, 7-day workout strip and recent workout logs

POST /api/tasks/{id}/done
    -> Idempotent task completion {"ok": true}
```

**API Rules:**
- Read-only data queries must specify column selections.
- Dates are ISO strings, never raw unix timestamps.
- Aggregates come from `store.py` / `v_daily`, never recalculated manually in routers.
- Errors return structured `{error: {code, message}}` with proper HTTP 4xx/5xx status codes.

### 5. Writing Data

The write endpoints invoke canonical functions in `app/store.py` (`add_metric`, `add_workout`, `add_task`) with server-side validation:

```text
POST /api/metrics   {kind, value, date?, note?}
POST /api/workouts  {kind, duration_min, load, date?, note?}
POST /api/tasks     {title, project?, due?, priority?}
POST /api/tasks/{id}/done
```

Rules: Default `date` is the current logical day (09:00 MSK threshold); duplicates for the same metric kind on the same day are allowed (latest value takes precedence).

### 6. Authorization & Access Control

Telegram Mini App supplies authentication parameters via `Telegram.WebApp.initData`.
The backend validates HMAC-SHA256 signature against `BOT_TOKEN`, verifies `auth_date` freshness (within 24 hours), and restricts access strictly to `user.id == ALLOWED_USER_ID`. All unauthorized requests receive HTTP 403 Forbidden.

Production traffic must use HTTPS (via reverse proxy with TLS certificates or secure tunnel). Port 8099 binds locally to `127.0.0.1`. Secrets live in `.env`, never in code or repositories.

### 7. Tech Stack

- **Backend:** Python + FastAPI, SQLite via `app/store.py` with WAL mode. Lightweight without heavy ORM or Alembic overhead.
- **Frontend:** Single-page application using modern vanilla JavaScript, CSS custom properties, and native SVG for performance inside Telegram WebView.
- **Theme:** iOS 18 True Dark OLED design system with SF Pro typography.
- **Deployment:** systemd service (`priest-panel.service`) listening on `127.0.0.1:8099`.

### 8. Anti-Patterns to Avoid

- Do not introduce secondary sources of truth (JSON files, Google Sheets, local storage state).
- Do not execute raw SQL queries from API routes.
- Do not compute conflicting metric formulas on the frontend.
- Do not add gamification mechanics: streaks, badges, confetti, or guilt-inducing warnings.
- Do not add unrequested push notifications or marketing splash screens.

### 9. Definition of Done

1. Opens reliably in Telegram via HTTPS.
2. "Today" displays live data from `life.db`.
3. Sleep view supports 7, 30, and 90 day adaptive views with gaps for missing days.
4. Tasks can be inspected and marked as done idempotently.
5. `initData` is validated; unauthorized IDs receive 403 Forbidden.
6. Writes execute exclusively via `store.py`.
7. Zero crashes on empty databases or missing dates.

### 10. Design & Visual System

- **Palette:** Pure OLED `#000000`, card background `rgba(28, 28, 30, 0.65)`, borders `rgba(255, 255, 255, 0.08)`.
- **Text:** Primary `#FFFFFF`, secondary `rgba(235, 235, 245, 0.6)`, tertiary `rgba(235, 235, 245, 0.3)`.
- **Accents:** iOS Blue (`#007AFF`), iOS Cyan (`#32ADE6`), iOS Green (`#34C759`), iOS Orange (`#FF9500`).
- **Typography:** `-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", sans-serif`. Tabular figures for numbers.
- **Spacing & Radius:** 16px horizontal container padding, 20px card border radius, clean separation without visual clutter.

### 11. Heatmap Specification

18 weeks (126 days), GitHub-style grid organized by weeks.
- Legend: 6 clear gradient levels (`none`, `0`, `1–49`, `50–74`, `75–99`, `100`).
- Gray dot indicator for days with completed workouts.
- Tap interaction shows formatted day details.
- Strictly prohibited: streak counters or judgment labels.

### 12. Sleep Drift & Trend Charts

- Sleep duration: Bar chart with 4h baseline and 7.5h target line.
- Wake time and Bedtime: Spline curves with target markers and time formatted as `HH:MM`.
- Interactive tooltip reveals exact values on tap/hover.

### 13. Service Management

- **Status:** `sudo systemctl status priest-panel`
- **Restart:** `sudo systemctl restart priest-panel`
- **Logs:** `sudo journalctl -u priest-panel -f`
- **Config:** `nano /root/priest/.env`
