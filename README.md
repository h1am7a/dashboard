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

* **Single Origin:** Frontend and API are hosted on the same origin; no CORS configuration required.
* **Single Source of Truth:** `app/store.py` + `db/life.db` (SQLite WAL mode).
* **Read-Only + Done Action:** The dashboard displays lifestyle metrics and allows completing tasks via `POST /api/tasks/{id}/done`.
* **Logical Day:** Begins at 09:00 MSK (UTC+3) and ends at 08:59 the following calendar day.

## Application Views

1. **Today**:
   - Dynamic date & weekday, progress counter (`done / total`);
   - 18-week (126 days) activity heatmap with 6-level gradient scale and workout indicator dots;
   - Sleep summary card with bedtime & wake indicator (`bed → wake`) with white contrasting arrow;
   - Single mood face (5-state scale: Bad, Meh, Ok, Good, Great);
   - Daily checklist (active habits without buttons; interactive actionable tasks with authentic iOS Cyan Done button).

2. **Sleep**:
   - Time period selector: `7 Days`, `30 Days`, `90 Days`;
   - Metric cards: **Sleep Duration**, **Wake Time**, and **Bed Time**;
   - Daily Average headers with period comparison deltas (`↑` / `↓` in minutes or hours);
   - Target values styled in iOS Cyan (`#32ADE6`) matching the prominent dashed target lines;
   - Sleep duration chart: full-width dashed target line (`7h30m`) spanning across all bars;
   - Wake and Bed spline charts: target lines running above the connecting curves and seamlessly behind data nodes;
   - Tap-to-inspect interactive tooltips for data points and bars showing exact values and formatted dates.

3. **Body**:
   - Three primary measurement cards: **Waist** (wide card, 9 weekly points across 8 weeks), **Biceps** (5 weekly points across 4 weeks), and **Forearm** (5 weekly points across 4 weeks);
   - Each card displays overall period delta (`latest - first`) with directional Apple-style badges;
   - Y-axis size labels (`cm`) and X-axis date labels under every measurement point;
   - Tap-to-inspect interaction: tapped point highlights in pure white, displays a floating speech bubble tooltip with the exact measurement, and updates the primary card value and timestamp;
   - Weekly workout strip with select-day exercise breakdowns and iOS Cyan bullet indicators.

## API Contract

* `GET /api/today` — Current logical day data, progress counter, sleep, mood, 18-week heatmap.
* `GET /api/sleep?days=7|30|90` — Sleep duration & schedule series with gaps preserved for missing days, period averages (`avg_hours`, `avg_wake`, `avg_bed`), and week-over-week deltas.
* `GET /api/body` — 3 measurement cards (Waist, Biceps, Forearm), thigh asymmetry, and recent workouts.
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
python bin/run.py
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
* **Config:** `nano /opt/priest-panel/.env`
