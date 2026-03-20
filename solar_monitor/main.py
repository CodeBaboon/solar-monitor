"""
main.py — Application entry point for the Solar Monitor.

Wires all components together and starts the FastAPI server with uvicorn.

Run directly:
    python main.py

Or via uvicorn (for development with auto-reload):
    uvicorn solar_monitor.main:app --reload --host 0.0.0.0 --port 8000
"""

import asyncio
import logging
import logging.handlers
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from solar_monitor import config
from solar_monitor.api import routes, websocket as ws_module
from solar_monitor.collector.poller import SolarPoller
from solar_monitor.database.repository import ReadingRepository

# ---------------------------------------------------------------------------
# Logging setup — must happen before any logger.xxx() calls
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    """
    Set up the root logger with both a rotating file handler and a console
    (stdout) handler so systemd's journal captures everything.

    Log format: [TIMESTAMP] [LEVEL] [MODULE] message
    """
    # Ensure the logs directory exists before creating the file handler.
    log_dir = os.path.dirname(config.LOG_FILE_PATH)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(module)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler — 10 MB max, 5 backups
    file_handler = logging.handlers.RotatingFileHandler(
        filename=config.LOG_FILE_PATH,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    # Console handler — captured by systemd journal
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


configure_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application-level state (created once, shared across requests)
# ---------------------------------------------------------------------------

repository = ReadingRepository(db_path=config.DATABASE_PATH)
poller: SolarPoller | None = None
poller_task: asyncio.Task | None = None


# ---------------------------------------------------------------------------
# FastAPI lifespan — startup and shutdown hooks
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Async context manager that replaces @app.on_event("startup/shutdown").

    Everything before the `yield` runs at startup; everything after runs at
    shutdown.  FastAPI guarantees this runs even if startup raises.
    """
    global poller, poller_task

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------
    logger.info("Solar Monitor starting up")

    # Ensure required directories exist
    os.makedirs(os.path.dirname(config.DATABASE_PATH) or ".", exist_ok=True)

    # Initialise the database schema (idempotent)
    repository.init_db()

    # Build the poller, injecting the WebSocket broadcast function so the
    # poller does not import the WebSocket layer directly.
    poller = SolarPoller(
        repository=repository,
        broadcast_fn=ws_module.manager.broadcast,
    )

    # Inject dependencies into the REST route module
    routes.init_routes(repository=repository, poller=poller)

    # Start the polling loop as a background asyncio task
    poller_task = asyncio.create_task(poller.run(), name="modbus-poller")
    logger.info("Modbus poller task started")

    logger.info(
        "Solar Monitor ready — dashboard at http://%s:%d",
        config.WEB_HOST if config.WEB_HOST != "0.0.0.0" else "<pi-ip>",
        config.WEB_PORT,
    )

    yield  # Server is now running and accepting requests

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    logger.info("Solar Monitor shutting down")

    if poller_task is not None and not poller_task.done():
        poller_task.cancel()
        try:
            await poller_task
        except asyncio.CancelledError:
            pass  # Expected — task was cancelled cleanly

    logger.info("Solar Monitor stopped")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Solar Monitor",
    description="Midnite Classic 200 monitoring dashboard",
    version="1.0.0",
    lifespan=lifespan,
)

# REST API routes
app.include_router(routes.router)

# WebSocket endpoint
@app.websocket("/ws")
async def websocket_route(websocket: WebSocket) -> None:
    """Real-time data push to dashboard clients."""
    await ws_module.websocket_endpoint(websocket)

# Serve the static dashboard files at the root URL.
# This must be mounted AFTER all API routes so /api/* is not shadowed.
_static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "solar_monitor.main:app",
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        log_config=None,  # We configure logging ourselves above
        access_log=False,  # Suppress uvicorn's own access log (noisy at 10s)
    )
