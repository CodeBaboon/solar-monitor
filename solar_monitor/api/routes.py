"""
api/routes.py — FastAPI REST endpoints.

Endpoints:
  GET /api/status          — poller connection state + last reading summary
  GET /api/latest          — most recent reading as JSON
  GET /api/readings        — historical readings (query params: hours, limit)
  GET /api/daily           — daily kWh summaries (query param: days)
  GET /api/logs            — last N log lines as JSON
  GET /api/logs/download   — serve raw log file for download
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from solar_monitor import config
from solar_monitor.database.repository import Reading, ReadingRepository

logger = logging.getLogger(__name__)

# The router is registered in main.py with app.include_router(router).
router = APIRouter(prefix="/api")

# These are injected by main.py after construction so routes.py does not
# need to import the poller (which would create a circular import with the
# WebSocket broadcast callable).
_repository: Optional[ReadingRepository] = None
_poller = None  # SolarPoller, typed as Any to avoid circular import


def init_routes(repository: ReadingRepository, poller) -> None:
    """
    Inject dependencies into this module.

    Called once from main.py before the server starts accepting requests.

    Args:
        repository: The shared ReadingRepository instance.
        poller:     The SolarPoller instance (for connection status).
    """
    global _repository, _poller
    _repository = repository
    _poller = poller


def _require_repository() -> ReadingRepository:
    """Return the repository or raise 503 if not yet initialised."""
    if _repository is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return _repository


def _reading_to_dict(reading: Reading) -> dict:
    """Serialise a Reading to a JSON-safe dict."""
    return {
        "timestamp": reading.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pv_voltage": reading.pv_voltage,
        "pv_current": reading.pv_current,
        "pv_watts": reading.pv_watts,
        "output_watts": reading.output_watts,
        "charge_state": reading.charge_state,
        "heatsink_temp": reading.heatsink_temp,
        "daily_kwh": reading.daily_kwh,
        "lifetime_kwh": reading.lifetime_kwh,
        "battery_voltage": reading.battery_voltage,
        "battery_current": reading.battery_current,
        "battery_soc": reading.battery_soc,
        "battery_net_current": reading.battery_net_current,
        "battery_ah_remaining": reading.battery_ah_remaining,
        "battery_temp": reading.battery_temp,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def get_status() -> dict:
    """
    Return poller connection state and the timestamp of the last good poll.

    Used by the dashboard connection indicator and by health-check scripts.
    """
    last_reading = _poller.last_reading if _poller else None
    is_connected = _poller.is_connected if _poller else False

    last_seen = None
    if last_reading is not None:
        last_seen = last_reading.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "connected": is_connected,
        "last_seen": last_seen,
    }


@router.get("/latest")
async def get_latest() -> dict:
    """
    Return the most recent reading stored in the database.

    Falls back to the database rather than the in-memory poller cache so
    that a dashboard refresh after a server restart shows real data.

    Raises:
        404: If no readings have been stored yet.
    """
    repo = _require_repository()
    reading = repo.get_latest_reading()

    if reading is None:
        raise HTTPException(status_code=404, detail="No readings available yet")

    return _reading_to_dict(reading)


@router.get("/readings")
async def get_readings(
    hours: float = Query(default=24.0, ge=0.1, le=720.0,
                         description="How many hours of history to return"),
    limit: int  = Query(default=10_000, ge=1, le=50_000,
                        description="Maximum number of data points"),
) -> list[dict]:
    """
    Return historical readings for the requested time window.

    Args:
        hours: Window size in hours (1–720, i.e. up to 30 days).
        limit: Maximum rows returned (guards against very large responses).

    Returns:
        List of reading dicts ordered oldest-first.
    """
    repo = _require_repository()
    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    readings = repo.get_readings_since(since=since, limit=limit)
    return [_reading_to_dict(r) for r in readings]


@router.get("/daily")
async def get_daily_summary(
    days: int = Query(default=30, ge=1, le=365,
                      description="Number of past days to summarise"),
) -> list[dict]:
    """
    Return one summary record per calendar day for the last N days.

    Args:
        days: Number of past calendar days to include (1–365).

    Returns:
        List of daily summary dicts ordered oldest-first.
    """
    repo = _require_repository()
    summaries = repo.get_daily_kwh_summary(days=days)
    return [
        {
            "date": s.date,
            "total_kwh": s.total_kwh,
            "avg_soc": round(s.avg_soc, 1),
            "peak_pv_watts": s.peak_pv_watts,
            "reading_count": s.reading_count,
        }
        for s in summaries
    ]


@router.get("/logs")
async def get_logs(
    lines: int = Query(
        default=config.LOG_LINES_DEFAULT, ge=1, le=5000,
        description="Number of log lines to return (most recent first)",
    ),
    level: str = Query(
        default="ALL",
        description="Filter by minimum level: ALL, DEBUG, INFO, WARNING, ERROR, CRITICAL",
    ),
) -> dict:
    """
    Return the last N lines from the rotating log file as a JSON array.

    Args:
        lines: How many lines to return (default from config).
        level: Minimum log level to include.

    Returns:
        Dict with "lines" list and "total_available" count.
    """
    log_path = config.LOG_FILE_PATH
    if not os.path.exists(log_path):
        return {"lines": [], "total_available": 0}

    level_upper = level.upper()
    level_order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    min_level_index = level_order.index(level_upper) if level_upper in level_order else 0

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
            all_lines = log_file.readlines()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read log file: {exc}")

    # Filter by level if requested
    if min_level_index > 0:
        def _line_matches_level(line: str) -> bool:
            for lvl in level_order[min_level_index:]:
                if f"] [{lvl}]" in line:
                    return True
            return False
        all_lines = [l for l in all_lines if _line_matches_level(l)]

    # Return the last N lines, stripped of trailing newlines
    tail = [line.rstrip("\n") for line in all_lines[-lines:]]

    return {
        "lines": tail,
        "total_available": len(all_lines),
    }


@router.get("/logs/download")
async def download_logs() -> FileResponse:
    """
    Serve the current log file as a downloadable attachment.

    Returns:
        The raw log file with a Content-Disposition: attachment header.

    Raises:
        404: If the log file does not exist yet.
    """
    log_path = config.LOG_FILE_PATH
    if not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(
        path=log_path,
        media_type="text/plain",
        filename="solar_monitor.log",
    )
