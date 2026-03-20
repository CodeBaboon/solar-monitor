# Solar Monitor

A self-hosted dashboard for monitoring a **Midnite Classic 200** charge controller on a **Raspberry Pi 3**, with real-time WebSocket updates, historical charting, and a built-in log viewer.

![Dashboard screenshot placeholder](docs/screenshot.png)

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Hardware Prerequisites](#hardware-prerequisites)
3. [Software Prerequisites](#software-prerequisites)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Starting, Stopping, and Restarting the Service](#starting-stopping-and-restarting-the-service)
7. [Accessing the Dashboard](#accessing-the-dashboard)
8. [Dashboard Walkthrough](#dashboard-walkthrough)
9. [REST API Reference](#rest-api-reference)
10. [Inspecting the Database Directly](#inspecting-the-database-directly)
11. [Viewing Logs](#viewing-logs)
12. [Troubleshooting](#troubleshooting)
13. [Project Structure](#project-structure)
14. [Future Expansion](#future-expansion)

---

## System Overview

```
 Raspberry Pi 3
 ┌────────────────────────────────────────┐
 │  solar_monitor/                        │
 │  ├── main.py          (FastAPI + uvicorn)
 │  ├── collector/       (Modbus poller)  │
 │  ├── database/        (SQLite repo)    │
 │  ├── api/             (REST + WS)      │
 │  └── static/          (dashboard HTML) │
 └──────────────┬─────────────────────────┘
                │ Modbus TCP (port 502)
 ┌──────────────▼─────────────────────────┐
 │  Midnite Classic 200                   │
 │  (with WhizBang Jr shunt monitor)      │
 └────────────────────────────────────────┘
```

- The **poller** reads two Modbus register blocks every 10 seconds.
- Each reading is written to a **SQLite** database and broadcast via **WebSocket** to all open browser tabs.
- The **FastAPI** server also exposes REST endpoints for historical data, log access, and health checks.
- The **dashboard** is a single-page application served as static files — no Node.js, no build step.

---

## Hardware Prerequisites

### Networking

1. **Connect the Classic 200 to your local network via ethernet.**
   The Classic 200 has a built-in ethernet port. Plug it into the same router or switch as the Pi.

2. **Assign the Classic a static IP address.**
   You can do this in one of two ways:
   - **Router DHCP reservation** (recommended): Log in to your router, find the Classic 200's MAC address in the DHCP client list, and create a reservation (e.g. `192.168.1.100`). The address will be stable across reboots without any change to the Classic itself.
   - **Classic's built-in network settings**: Use the Classic's front-panel menu → *Setup* → *Network* to set a static IP, subnet mask, and gateway.

3. **Verify the Classic is reachable from the Pi before installing:**
   ```bash
   ping 192.168.1.100          # substitute your Classic's IP
   ```

4. **Verify Modbus TCP is accessible** (optional but useful for debugging):
   ```bash
   # Install ncat if needed: sudo apt install ncat
   nc -zv 192.168.1.100 502
   # Expected output: Connection to 192.168.1.100 502 port [tcp/*] succeeded!
   ```

### WhizBang Jr

The WhizBang Jr shunt monitor is **required** for SOC%, net battery current, and amp-hours-remaining data. If it is not installed, those fields will read `0` (the Classic returns zeroed registers when the WBJr is absent). All other fields (PV voltage/current/watts, battery voltage, charge state, temperatures, daily/lifetime kWh) come directly from the Classic and are always available.

---

## Software Prerequisites

- **Raspberry Pi OS** (Bullseye or Bookworm, 32-bit or 64-bit) — fresh install recommended.
- **Python 3.9 or newer** — included in Raspberry Pi OS Bullseye and later.
- **Internet access** on the Pi during installation (to download Python packages).
- `rsync` — usually pre-installed; if missing: `sudo apt install rsync`.

---

## Installation

### Step 1 — Copy the project to your Pi

**Option A — USB / SD card copy**
Copy the `solar/` folder from your development machine to `/home/pi/solar/` on the Pi (via `scp`, USB stick, or any file transfer method).

**Option B — git clone** (if you push this to a git remote)
```bash
git clone https://github.com/yourusername/solar-monitor.git /home/pi/solar
```

### Step 2 — Configure the Classic 200 IP address

Before running the installer, open `config.py` and set your Classic's IP:

```bash
nano /home/pi/solar/solar_monitor/config.py
```

Change this line:
```python
CLASSIC_IP: str = "192.168.1.100"   # ← set this to your Classic's actual IP
```

See [Configuration](#configuration) for all available settings.

### Step 3 — Run the installer

```bash
cd /home/pi/solar
sudo bash install.sh
```

The installer will:
- Check Python 3.9+ is available
- Create a `solar` system user to run the service
- Copy files to `/opt/solar_monitor/`
- Create a Python virtual environment and install all dependencies
- Create `data/` and `logs/` directories
- Install and enable `solar-monitor.service` via systemd
- Start the service immediately

**Expected output (abbreviated):**
```
[INFO]  Solar Monitor installer starting
[INFO]  Python 3.11 — OK
[INFO]  Creating system user 'solar'…
[INFO]  Installing application files to /opt/solar_monitor…
[INFO]  Installing Python dependencies…
[INFO]  Service is running
============================================================
 Solar Monitor installed successfully!
============================================================
  Dashboard URL : http://192.168.1.50:8000
```

### Step 4 — Verify the service is running

```bash
sudo systemctl status solar-monitor
```

Expected output includes `Active: active (running)`.

---

## Configuration

All configuration lives in `/opt/solar_monitor/solar_monitor/config.py`. After editing, restart the service:

```bash
sudo nano /opt/solar_monitor/solar_monitor/config.py
sudo systemctl restart solar-monitor
```

| Setting | Default | Description |
|---|---|---|
| `CLASSIC_IP` | `"192.168.1.100"` | **Must set.** IP address of your Classic 200. |
| `CLASSIC_PORT` | `502` | Modbus TCP port. Do not change unless you forwarded a different port. |
| `CLASSIC_MODBUS_ADDRESS` | `10` | Modbus slave/unit ID. Classic default is 10. |
| `POLL_INTERVAL_SECONDS` | `10` | How often to poll the Classic. Lower = more data, more CPU. |
| `DATABASE_PATH` | `"data/solar_monitor.db"` | Path to the SQLite file (relative to install dir). |
| `WEB_HOST` | `"0.0.0.0"` | Bind address. `0.0.0.0` = all interfaces (required for LAN access). |
| `WEB_PORT` | `8000` | HTTP port for the dashboard and API. |
| `LOG_LEVEL` | `"INFO"` | `"DEBUG"` shows every register read; `"INFO"` is normal. |
| `LOG_FILE_PATH` | `"logs/solar_monitor.log"` | Rotating log file path. |
| `LOG_MAX_BYTES` | `10485760` (10 MB) | Max size of one log file before rotation. |
| `LOG_BACKUP_COUNT` | `5` | Number of old log files to keep. |
| `LOG_LINES_DEFAULT` | `200` | Default lines returned by `/api/logs`. |

### Finding the Classic 200's Modbus unit ID

If you are unsure of the Modbus address, check the Classic 200's front panel:
*Setup* → *Network* → scroll to *Modbus Address* (default 10).

---

## Starting, Stopping, and Restarting the Service

```bash
# Check status
sudo systemctl status solar-monitor

# Stop the service
sudo systemctl stop solar-monitor

# Start the service
sudo systemctl start solar-monitor

# Restart (e.g. after editing config.py)
sudo systemctl restart solar-monitor

# Disable auto-start on boot
sudo systemctl disable solar-monitor

# Re-enable auto-start on boot
sudo systemctl enable solar-monitor
```

---

## Accessing the Dashboard

Open a browser on any device connected to your local network and navigate to:

```
http://<raspberry-pi-ip>:8000
```

To find your Pi's IP address:
```bash
hostname -I
```

The dashboard is a single-page application — no login required, no internet connection required after the initial Chart.js CDN load.

> **Tip:** Bookmark the URL on your phone for quick SOC checks.

---

## Dashboard Walkthrough

### Dashboard Tab (real-time)

Updates live every 10 seconds via WebSocket. The **connection status pill** in the top-right corner shows:
- 🟢 **Live** — data is current
- 🟡 **Stale** — Modbus connection lost; showing last known values
- 🔴 **Disconnected** — browser lost its WebSocket connection to the Pi

**Metric cards:**

| Card | What it shows |
|---|---|
| Battery SOC | State of charge from the WhizBang Jr coulomb counter. Colour-coded: green >80%, yellow 50–80%, orange 20–50%, red <20%. |
| PV Input | Current PV wattage (computed: PV voltage × PV current) with a 30-minute sparkline. |
| Battery Voltage | Terminal voltage of the battery bank. |
| Net Current | Net battery current from the WhizBang Jr shunt. Positive = charging, negative = discharging. |
| Charge State | Classic's current charge algorithm stage: Resting / Bulk MPPT / Absorb / Float / Float MPPT / Equalize / HyperVOC. |
| Today's Harvest | kWh delivered to the battery today (resets around midnight). |
| Heat Sink Temp | Temperature of the Classic's internal power FETs. |
| PV Array | PV terminal voltage and current. |

### History Tab (charts)

Select a time range (24 h / 7 d / 30 d) to load historical charts:

- **Battery SOC** — line chart showing charge level over time
- **PV Input Power** — area chart of solar production
- **Battery Voltage** — useful for spotting under/over-voltage events
- **Net Battery Current** — positive = charging, negative = load
- **Heat Sink Temperature** — useful for spotting thermal issues
- **Daily kWh Harvested** — bar chart, one bar per day

### Logs Tab

Displays the last N lines from the application log file, colour-coded by level:
- Gray = DEBUG
- Blue = INFO
- Yellow = WARNING
- Red = ERROR / CRITICAL

Use the **Level** filter to hide noisy DEBUG lines. Click **Download** to save the full log file for offline analysis.

---

## REST API Reference

All endpoints return JSON. Base URL: `http://<pi-ip>:8000`

### `GET /api/status`
Poller connection state.
```json
{ "connected": true, "last_seen": "2024-03-15T14:23:01Z" }
```

### `GET /api/latest`
Most recent reading from the database.
```json
{
  "timestamp": "2024-03-15T14:23:01Z",
  "pv_voltage": 84.2,
  "pv_current": 21.9,
  "pv_watts": 1843.9,
  "output_watts": 1820.0,
  "charge_state": "Float",
  "heatsink_temp": 38.5,
  "daily_kwh": 12.4,
  "lifetime_kwh": 8421.3,
  "battery_voltage": 54.8,
  "battery_current": 33.2,
  "battery_soc": 97.0,
  "battery_net_current": 28.4,
  "battery_ah_remaining": 218.0,
  "battery_temp": 23.1
}
```

### `GET /api/readings?hours=24&limit=10000`
Historical readings. `hours` = window size (0.1–720), `limit` = max rows (1–50000).

### `GET /api/daily?days=30`
Daily summaries. Returns one record per calendar day.
```json
[
  { "date": "2024-03-14", "total_kwh": 15.2, "avg_soc": 84.3, "peak_pv_watts": 2840.0, "reading_count": 8640 },
  ...
]
```

### `GET /api/logs?lines=200&level=INFO`
Last N log lines. `level` = `ALL` / `INFO` / `WARNING` / `ERROR`.

### `GET /api/logs/download`
Downloads the raw log file (`solar_monitor.log`) as a file attachment.

---

## Inspecting the Database Directly

The SQLite database lives at `/opt/solar_monitor/data/solar_monitor.db`.

```bash
# Open the database
sqlite3 /opt/solar_monitor/data/solar_monitor.db

# Show table schema
.schema readings

# Last 5 readings
SELECT timestamp, pv_watts, battery_soc, charge_state
FROM readings
ORDER BY timestamp DESC
LIMIT 5;

# Today's total kWh
SELECT MAX(daily_kwh) AS total_kwh_today
FROM readings
WHERE DATE(timestamp) = DATE('now');

# Average SOC over the past 7 days
SELECT DATE(timestamp) AS day, ROUND(AVG(battery_soc), 1) AS avg_soc
FROM readings
WHERE timestamp >= DATETIME('now', '-7 days')
GROUP BY day
ORDER BY day;

# How many readings are stored
SELECT COUNT(*) FROM readings;

# Database file size
.quit
du -h /opt/solar_monitor/data/solar_monitor.db
```

**Expected database growth:** At 10-second polling, you accumulate ~8,640 readings/day. Each row is roughly 150–200 bytes, so expect ~1.5–1.7 MB/day. At this rate, 1 GB of storage holds roughly 18 months of data.

**To export all data as CSV:**
```bash
sqlite3 -csv /opt/solar_monitor/data/solar_monitor.db \
  "SELECT * FROM readings ORDER BY timestamp;" \
  > solar_readings.csv
```

---

## Viewing Logs

### Via the dashboard

Open the **Logs** tab in the browser — auto-refreshes every 5 seconds.

### Via systemd journal (live)

```bash
# Live tail (follows new entries)
sudo journalctl -u solar-monitor -f

# Last 100 lines
sudo journalctl -u solar-monitor -n 100

# Since last boot
sudo journalctl -u solar-monitor -b

# Filter by priority (err = ERROR and above)
sudo journalctl -u solar-monitor -p err
```

### Via the log file directly

```bash
# Live tail of the rotating log file
tail -f /opt/solar_monitor/logs/solar_monitor.log

# Search for warnings
grep '\[WARNING\]' /opt/solar_monitor/logs/solar_monitor.log

# Search for Modbus errors
grep -i 'modbus\|connection' /opt/solar_monitor/logs/solar_monitor.log
```

---

## Troubleshooting

### Service won't start

```bash
sudo systemctl status solar-monitor
sudo journalctl -u solar-monitor -n 50
```

Common causes:
- **Python version too old** — upgrade Python or install Python 3.11 from deadsnakes PPA.
- **Missing dependencies** — run `sudo bash install.sh` again to re-install.
- **Port 8000 already in use** — change `WEB_PORT` in config.py.

---

### Dashboard shows "Disconnected" or "Stale"

The browser WebSocket connected to the Pi but the Pi cannot reach the Classic 200.

1. **Confirm the Classic's IP is correct in config.py:**
   ```bash
   grep CLASSIC_IP /opt/solar_monitor/solar_monitor/config.py
   ```

2. **Ping the Classic from the Pi:**
   ```bash
   ping -c 4 192.168.1.100
   ```
   If ping fails: check ethernet cable, router configuration, Classic network settings.

3. **Test Modbus port connectivity:**
   ```bash
   nc -zv 192.168.1.100 502
   ```
   If this fails but ping works: confirm Modbus TCP is enabled on the Classic (front panel → Setup → Network → Modbus enabled).

4. **Check the live logs for the actual error:**
   ```bash
   sudo journalctl -u solar-monitor -f
   ```
   Look for lines like `[WARNING] [poller] Modbus failure #1 — ConnectionError: …`

---

### Dashboard loads but all values show `—`

The WebSocket connected but no readings have been stored yet (service just started).
Wait 10–15 seconds for the first poll. If values remain `—`, check the logs.

---

### SOC / net current / AH remaining always show 0

The WhizBang Jr is either:
- Not installed
- Not communicating with the Classic (check the WBJr wiring and the Classic's front panel — it should show the WBJr as connected under *Meters* → *WBJr*)

All other metrics will work normally without the WBJr.

---

### Log file is not appearing

The `logs/` directory must be writable by the `solar` user. Re-run the installer or:
```bash
sudo chown solar:solar /opt/solar_monitor/logs
sudo chmod 770 /opt/solar_monitor/logs
```

---

### Database grows too large

Run this SQL periodically to delete readings older than 90 days:
```bash
sqlite3 /opt/solar_monitor/data/solar_monitor.db \
  "DELETE FROM readings WHERE timestamp < DATETIME('now', '-90 days'); VACUUM;"
```

You can automate this with a cron job:
```bash
sudo crontab -u solar -e
# Add this line (runs at 3 AM on the 1st of every month):
0 3 1 * * sqlite3 /opt/solar_monitor/data/solar_monitor.db "DELETE FROM readings WHERE timestamp < DATETIME('now', '-90 days'); VACUUM;"
```

---

### Re-installing after a code change

```bash
cd /home/pi/solar    # or wherever you have the source
sudo bash install.sh
```

The installer will overwrite application files while preserving the database and logs. The service is restarted automatically.

---

### Running in development mode (without installing)

You can run the application directly from the source directory without installing:

```bash
cd /home/pi/solar

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Edit config.py to set your Classic's IP
nano solar_monitor/config.py

# Run (from the solar/ directory so Python finds the solar_monitor package)
python -m solar_monitor.main
```

The dashboard will be at `http://localhost:8000`.
Press `Ctrl+C` to stop.

---

## Project Structure

```
solar/
├── install.sh                  # Installation script (run as root)
├── solar-monitor.service       # systemd unit file
├── requirements.txt            # Python dependencies
├── README.md                   # This file
└── solar_monitor/              # Python package
    ├── main.py                 # App entry point: FastAPI + uvicorn + lifespan
    ├── config.py               # All configuration constants
    ├── modbus/
    │   ├── __init__.py
    │   ├── client.py           # Modbus TCP connection (pymodbus wrapper)
    │   └── registers.py        # Register addresses, scaling, decode logic
    ├── collector/
    │   ├── __init__.py
    │   └── poller.py           # Polling loop with exponential backoff
    ├── database/
    │   ├── __init__.py
    │   └── repository.py       # SQLite abstraction (Reading, DailySummary)
    ├── api/
    │   ├── __init__.py
    │   ├── routes.py           # REST endpoints (/api/*)
    │   └── websocket.py        # WebSocket hub (ConnectionManager)
    ├── static/
    │   ├── index.html          # Dashboard single-page app
    │   ├── dashboard.js        # Chart.js charts, WebSocket client, log viewer
    │   └── styles.css          # Dark theme styles
    ├── data/                   # SQLite database (created by installer)
    └── logs/                   # Rotating log files (created by installer)
```

---

## Future Expansion

The following are explicitly out of scope for this version but the codebase is structured to accommodate them:

- **Magnum MS4448PAE inverter** — would be a new `modbus/` client using RS-485 (via a USB-RS485 adapter), a new poller, and additional dashboard cards. The repository schema can be extended with new columns; old rows will simply have `NULL` for inverter fields.
- **User authentication** — FastAPI supports OAuth2/JWT middleware that can be added in front of all routes.
- **Email/SMS alerting** — add a `notifications/` module that the poller calls when thresholds are crossed (low SOC, high temperature, etc.).
- **Multiple charge controllers** — the `ClassicModbusClient` accepts `host` as a constructor argument; a second poller instance targeting a different IP would work with minimal changes.
- **Grafana / InfluxDB** — the `ReadingRepository` pattern makes it easy to add a second backend that writes to InfluxDB in parallel with SQLite.
