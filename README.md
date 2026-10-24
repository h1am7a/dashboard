# Priest Life — Telegram Mini App «Panel»

Single-user personal routine and lifestyle analytics dashboard for Telegram Mini App (version 1.0 specification).

## Architecture

```text
Antigravity (Local)           GitHub             Server (/root/priest)
  frontend/  ──push──▶   Repository   ──pull──▶  web/ (static files)
                                                     │
                                               FastAPI :8099
                                                 ├── /api/*   (JSON endpoints)
                                                 └── /        (Single Page Application)
                                                     │
                                               Cloudflared / Tailscale Funnel
                                                 │
                                               https://... → Telegram Mini App
```

* **Single Origin:** Frontend and API are hosted on the same origin; no CORS setup required.
* **Single Source of Truth:** `app/store.py` + `db/life.db` (SQLite WAL mode).
* **Read-Only + Done Action:** The dashboard displays lifestyle metrics and allows completing tasks via `POST /api/tasks/{id}/done`.
* **Logical Day:** Begins at 09:00 MSK (UTC+3) and ends at 08:59 the following calendar day.

## Application Views

1. **Today**:
   - Dynamic date & weekday, progress counter (`done / total`);
   - 18-week (126 days) activity heatmap with 6-level gradient scale and workout indicator dots;
   - Sleep summary card with delta comparison against target (`facts.wake_target`);
   - Single mood face (5-state scale: Bad, Meh, Ok, Good, Great);
   - Daily checklist (active anchors without buttons; interactive actionable tasks with Done button).

2. **Sleep**:
   - Time period selector: `7 Days`, `30 Days`, `90 Days`;
   - Period average sleep duration and delta comparison against previous period ($\ge 0.5$ h threshold);
   - Sleep duration chart: bars with 4h baseline and 7.5h target line in iOS Cyan (`#32ADE6`);
   - Wake time (target 14:30) and Bedtime (target 08:00) spline charts;
   - Tap-to-inspect interactive tooltips for data points and bars.

3. **Body**:
   - Four measurement cards: Waist (weekly), Biceps, Forearm, Thigh (monthly) with SVG sparklines;
   - 1 cm noise threshold: deltas under 1 cm display as «no change»;
   - Thigh asymmetry indicator (`thigh_asymmetry`);
   - 7-day workout strip and selected day details (`kind`, `load`, `duration_min`, `note`).

## API Contract

* `GET /api/today` — Current logical day data, progress counter, sleep, mood, 18-week heatmap.
* `GET /api/sleep?days=7|30|90` — Sleep duration & schedule series with gaps preserved for missing days.
* `GET /api/body` — 4 measurement cards, thigh asymmetry, and recent workouts.
* `POST /api/tasks/{id}/done` — Idempotent task completion.
* `GET /api/health` — Health check endpoint (`{"ok": true, "version": "1.0"}`).

## Quick Install (One-Line Automated Setup)

On a clean Ubuntu/Debian/RHEL server, run as root:

```bash
curl -fsSL https://raw.githubusercontent.com/h1am7a/dashboard/main/install.sh | sudo bash
```

Or non-interactively with pre-configured Telegram credentials:

```bash
BOT_TOKEN="your_bot_token" ALLOWED_USER_ID="your_user_id" curl -fsSL https://raw.githubusercontent.com/h1am7a/dashboard/main/install.sh | sudo -E bash
```

This automated script installs system packages, sets up `.venv`, installs dependencies, configures `.env`, and enables the `priest-panel.service` systemd daemon.

## Manual Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your BOT_TOKEN and ALLOWED_USER_ID
```

### 3. Run Development Server
```bash
python -m uvicorn app.api:app --host 127.0.0.1 --port 8099
```
The panel is now accessible at [http://127.0.0.1:8099](http://127.0.0.1:8099).

### 4. Run Tests
```bash
python -m pytest
```

## Service Management
* **Status:** `sudo systemctl status priest-panel`
* **Restart:** `sudo systemctl restart priest-panel`
* **Logs:** `sudo journalctl -u priest-panel -f`
* **Config:** `nano /root/priest/.env`
