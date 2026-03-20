# Solar Monitoring System — Project Brief

## Project Overview
Build a solar monitoring application for a Raspberry Pi 3 that polls a Midnite Classic 200
charge controller via Modbus TCP, stores time-series data, and serves a web-based dashboard
showing both real-time and historical solar system metrics.

---

## Hardware Context

### Solar System Components
- 12x Canadian Solar 330W panels (3960W total array)
- Midnite Classic 200 Charge Controller (primary data source)
  - Connected via ethernet to local network
  - Exposes Modbus TCP on port 502 (default device address: 10)
  - Assign it a static IP on the local network (e.g. 192.168.1.100)
- Midnite Solar WhizBang Jr (current/SOC sensor, feeds data through the Classic's Modbus registers)
- 3x SimpliPhi 3.8kWh 48V 75AH LiFePO4 batteries (225AH total)
- Magnum MS4448PAE 4400W 48V Pure Sine inverter (NOT connected — future expansion)

### Host Hardware
- Raspberry Pi 3 running Raspberry Pi OS (Debian-based Linux)
- Python 3.9+ available
- Connected to same local network as the Classic 200 via ethernet

---

## Data Sources — Midnite Classic 200 Modbus Registers

Use the `pymodbus` library to poll these registers over Modbus TCP.
The Classic's Modbus register map is publicly documented by Midnite Solar.

Key registers to poll (all from the Classic 200 Modbus specification):

### Charge Controller / PV Array
- PV input voltage (V)
- PV input current (A)
- PV input wattage (W)
- Output wattage to battery (W)
- Charge controller state (integer: maps to Resting/Absorb/Bulk/Float/EQ/etc.)
- Heat sink temperature (°C)
- Daily kWh harvested (kWh, resets at midnight)
- Lifetime kWh harvested (kWh)

### Battery (via WhizBang Jr through Classic Modbus)
- Battery voltage (V)
- Battery charge current (A)
- Battery State of Charge / SOC (%, from WhizBang Jr coulomb counting)
- Net battery current (A, positive = charging, negative = discharging)
- Amp-hours remaining (AH)
- Battery temperature (°C, if sensor connected)

Please look up the exact Midnite Classic 200 Modbus register map to confirm register
addresses and data types/scaling factors before implementing the polling code.

---

## Application Architecture

### Technology Stack
- **Language:** Python 3.9+
- **Modbus polling:** `pymodbus`
- **Database:** SQLite via Python's built-in `sqlite3` module (no external service required).
  Use a simple time-series schema with one `readings` table, a UTC timestamp column, and one
  column per metric. Write a thin repository class to abstract all queries so the database
  can be swapped out later if needed.
- **Web framework:** FastAPI (modern, async-friendly, clean)
- **Real-time push to browser:** WebSockets (via FastAPI's built-in WebSocket support)
- **Frontend:** Vanilla HTML/CSS/JavaScript with Chart.js for graphs
  - No frontend build tools or Node.js required — keep it simple and Pi-friendly
  - Single-page application served directly by FastAPI as static files

### Project Structure
Please organize the code with clear separation of concerns, for example:

```
solar_monitor/
├── main.py                  # Application entry point
├── config.py                # All configuration (IP, ports, poll interval, etc.)
├── modbus/
│   ├── __init__.py
│   ├── client.py            # Modbus TCP connection management
│   └── registers.py         # Register definitions, addresses, scaling
├── collector/
│   ├── __init__.py
│   └── poller.py            # Polling loop, data collection, error handling
├── database/
│   ├── __init__.py
│   └── repository.py        # SQLite read/write abstraction (repository pattern)
├── api/
│   ├── __init__.py
│   ├── routes.py            # FastAPI REST endpoints
│   └── websocket.py         # WebSocket handler for real-time data
├── static/
│   ├── index.html           # Dashboard HTML
│   ├── dashboard.js         # Chart.js charts and WebSocket client
│   └── styles.css           # Dashboard styles
└── logs/                    # Log file output directory
```

---

## Database Schema

Create a single `readings` table in SQLite with the following columns:

```sql
CREATE TABLE readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,         -- ISO 8601 UTC, e.g. "2024-03-15T14:23:01Z"
    pv_voltage  REAL,                  -- Volts
    pv_current  REAL,                  -- Amps
    pv_watts    REAL,                  -- Watts
    output_watts REAL,                 -- Watts to battery
    charge_state TEXT,                 -- Human-readable: "Float", "Bulk", etc.
    heatsink_temp REAL,                -- Celsius
    daily_kwh   REAL,                  -- kWh harvested today
    lifetime_kwh REAL,                 -- kWh harvested all time
    battery_voltage REAL,              -- Volts
    battery_current REAL,              -- Amps
    battery_soc REAL,                  -- Percent (0-100)
    battery_net_current REAL,          -- Amps (positive=charging, negative=discharging)
    battery_ah_remaining REAL,         -- Amp-hours
    battery_temp REAL                  -- Celsius (nullable if sensor not connected)
);

CREATE INDEX idx_readings_timestamp ON readings (timestamp);
```

The repository class should expose clearly named methods such as:
- `insert_reading(reading: Reading) -> None`
- `get_readings_since(since: datetime, limit: int) -> list[Reading]`
- `get_daily_kwh_summary(days: int) -> list[DailySummary]`
- `get_latest_reading() -> Reading | None`

Use a `Reading` dataclass to pass data between layers — never pass raw tuples or dicts
between the database layer and the rest of the application.

---

## Polling Behavior
- Poll the Classic 200 every **10 seconds** for real-time data
- Write every poll result to the database
- Broadcast latest readings to all connected WebSocket clients after each poll
- Handle Modbus connection failures gracefully:
  - Log the error with full context
  - Attempt reconnection with exponential backoff
  - Dashboard should clearly indicate when data is stale or connection is lost
  - Never crash the application on a transient connection error

---

## Dashboard Requirements

### Real-Time Panel (top of page, updates live via WebSocket)
Display the following as live metric cards:
- Battery SOC % (large, color-coded: green >80%, yellow 50-80%, orange 20-50%, red <20%)
- PV input watts (with a small sparkline of the last 30 minutes)
- Battery voltage
- Net battery current (positive = charging, negative = discharging)
- Charge controller state (text label, e.g. "Float", "Bulk", "Absorb")
- Today's kWh harvested
- Heat sink temperature
- Connection status indicator (green = live, red = disconnected, with last-seen timestamp)

### Historical Charts (below real-time panel)
All charts should support selectable time ranges: Last 24h / 7 days / 30 days

1. **Battery SOC over time** — line chart
2. **PV watts over time** — area chart
3. **Daily kWh harvested** — bar chart (one bar per day)
4. **Charge controller state timeline** — color-band or stacked bar showing time in each state
5. **Battery voltage over time** — line chart
6. **Heat sink temperature over time** — line chart

### Log Viewer (separate tab or section on the dashboard)
- Display the last N log lines (configurable, default 200)
- Color-code by log level (DEBUG=gray, INFO=blue, WARNING=yellow, ERROR=red, CRITICAL=red+bold)
- Auto-refresh every 5 seconds or provide a manual refresh button
- Allow filtering by log level (show all / INFO and above / WARNING and above / ERROR only)
- Provide a download button to download the full log file

---

## Logging Requirements

Use Python's built-in `logging` module with a clear, consistent setup:

- **Log levels used throughout:** DEBUG, INFO, WARNING, ERROR, CRITICAL
- **Log format:** `[TIMESTAMP] [LEVEL] [MODULE] message`
  - Example: `[2024-03-15 14:23:01] [INFO] [poller] Poll successful — PV: 1842W, SOC: 87%, State: Float`
- **Output to both:**
  - Rotating file handler: `logs/solar_monitor.log`, max 10MB per file, keep 5 backups
  - Console (stdout) for systemd journal capture
- **What to log at each level:**
  - DEBUG: Every register value read, raw Modbus responses, WebSocket client connect/disconnect
  - INFO: Successful poll summaries, application start/stop, daily kWh milestones, charge state transitions
  - WARNING: Modbus timeouts, reconnection attempts, stale data being served, unusual readings (e.g. battery temp high)
  - ERROR: Failed polls, database write failures, unexpected register values
  - CRITICAL: Application-level failures, unable to connect after all retries

- Create a `/api/logs` endpoint that returns the last N log lines as JSON
- Create a `/api/logs/download` endpoint that serves the raw log file for download

---

## Configuration

All configurable values should live in `config.py`, including:
- Classic 200 IP address and port (default: port 502)
- Modbus device address (default: 10)
- Poll interval in seconds (default: 10)
- SQLite database file path (default: `data/solar_monitor.db`)
- Log level (default: INFO, can be overridden to DEBUG for troubleshooting)
- Web server host and port (default: 0.0.0.0:8000)
- Number of log lines to return via API (default: 200)

---

## Deployment

Please generate the following deployment files:

1. **`requirements.txt`** — all Python dependencies with pinned versions
2. **`install.sh`** — shell script to:
   - Install system dependencies (Python packages via pip)
   - Create a dedicated `solar` user to run the service
   - Set up the directory structure and permissions
   - Create the SQLite database file and run the schema migration
3. **`solar-monitor.service`** — systemd unit file so the app runs on boot and
   auto-restarts on failure
4. **`README.md`** — setup instructions covering:
   - Hardware wiring/network prerequisites
   - Configuration steps
   - How to install and start the service
   - How to access the dashboard and logs
   - How to inspect the SQLite database directly for debugging
   - Basic troubleshooting steps

---

## Code Style Guidelines

The developer has a strong background in JavaScript and C# but is not experienced
with Python. Please prioritize:

- **Explicit over implicit** — avoid Python "magic" where possible
- **Type hints on all functions** — use Python's typing module throughout
- **Docstrings on all classes and public methods** — Google style preferred
- **Named constants instead of magic numbers** — especially for register addresses
  and Modbus scaling factors
- **Clear variable names** — no single-letter variables except loop counters
- **Descriptive comments** for any Python-specific idioms that might be unfamiliar
  to a JS/C# developer (e.g. context managers, decorators, async/await patterns)
- **Async/await throughout** — use Python's asyncio consistently since FastAPI is async
- Keep functions small and single-purpose

---

## Out of Scope (Future Expansion)
- Magnum ME-ARC / MS4448PAE inverter integration (RS-485) — design with this in mind
  but do not implement yet
- User authentication on the web interface
- Email/SMS alerting
- Multiple charge controller support
