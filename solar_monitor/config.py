"""
config.py — All application configuration in one place.

Edit the values in this file to match your installation. No other files
need to be changed for a standard deployment.
"""

# ---------------------------------------------------------------------------
# Midnite Classic 200 Modbus TCP settings
# ---------------------------------------------------------------------------

# IP address of the Classic 200 on your local network.
# Assign a static IP via your router's DHCP reservation or the Classic's
# own network settings.
CLASSIC_IP: str = "192.168.1.100"

# Modbus TCP port (Classic default is 502; change only if you forwarded a
# different port on a firewall/router).
CLASSIC_PORT: int = 502

# Modbus unit / slave ID (Classic default is 10).
CLASSIC_MODBUS_ADDRESS: int = 10

# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

# How often to poll the Classic for new data, in seconds.
POLL_INTERVAL_SECONDS: int = 10

# Maximum number of consecutive Modbus failures before the poller logs a
# CRITICAL message. The poller always retries regardless.
MAX_CONSECUTIVE_FAILURES: int = 10

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

# Path to the SQLite database file.  Created automatically on first run.
DATABASE_PATH: str = "data/solar_monitor.db"

# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------

# Host to bind the FastAPI server to.
# "0.0.0.0" listens on all interfaces (needed so other devices on your LAN
# can access the dashboard).
WEB_HOST: str = "0.0.0.0"

# Port for the web server.  Access the dashboard at http://<pi-ip>:8000
WEB_PORT: int = 8000

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Logging level for the application.
# Options: "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
# Use "DEBUG" to see every register read; "INFO" for normal operation.
LOG_LEVEL: str = "INFO"

# Path to the rotating log file.
LOG_FILE_PATH: str = "logs/solar_monitor.log"

# Maximum size of a single log file in bytes (10 MB).
LOG_MAX_BYTES: int = 10 * 1024 * 1024

# Number of rotated log files to keep (5 backups + 1 active = up to 60 MB).
LOG_BACKUP_COUNT: int = 5

# ---------------------------------------------------------------------------
# Log API
# ---------------------------------------------------------------------------

# Number of log lines returned by GET /api/logs by default.
LOG_LINES_DEFAULT: int = 200
