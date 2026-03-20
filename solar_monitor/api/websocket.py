"""
api/websocket.py — WebSocket hub for real-time dashboard updates.

The WebSocket handler maintains a set of active connections. After each
successful Modbus poll, the poller calls broadcast() to push the latest
reading to every connected browser tab.

If a client disconnects mid-send, the error is caught and the connection
is removed from the active set so it does not block future broadcasts.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from solar_monitor.database.repository import Reading

logger = logging.getLogger(__name__)


def _reading_to_dict(reading: Reading) -> dict:
    """
    Serialise a Reading into a plain dict suitable for JSON.

    datetime objects are converted to ISO 8601 strings; None values become
    JSON null automatically via json.dumps.

    Args:
        reading: The Reading to serialise.

    Returns:
        A dict ready for json.dumps().
    """
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


class ConnectionManager:
    """
    Tracks all active WebSocket connections and broadcasts messages to them.

    Thread-safety note: FastAPI runs on a single-threaded asyncio event loop,
    so set mutations here are safe without locks.
    """

    def __init__(self) -> None:
        # Using a set so duplicate WebSocket objects are deduplicated.
        self._active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """
        Accept a new WebSocket connection and register it.

        Args:
            websocket: The incoming FastAPI WebSocket instance.
        """
        await websocket.accept()
        self._active_connections.add(websocket)
        logger.debug(
            "WebSocket client connected — %d total", len(self._active_connections)
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """
        Remove a WebSocket from the active set (call on disconnect or error).

        Args:
            websocket: The WebSocket to remove.
        """
        self._active_connections.discard(websocket)
        logger.debug(
            "WebSocket client disconnected — %d remaining",
            len(self._active_connections),
        )

    async def broadcast(self, reading: Reading) -> None:
        """
        Send the latest reading to every connected WebSocket client.

        Dead connections are silently removed — we catch send errors and
        call disconnect() so the next broadcast skips them.

        Args:
            reading: The Reading to broadcast.
        """
        if not self._active_connections:
            return

        payload = json.dumps(_reading_to_dict(reading))

        # Snapshot the set before iterating so we can mutate it in the loop.
        connections_snapshot = set(self._active_connections)

        send_tasks = [
            self._send_to(websocket, payload)
            for websocket in connections_snapshot
        ]
        await asyncio.gather(*send_tasks, return_exceptions=True)

    async def broadcast_status(self, connected: bool, last_seen: Optional[datetime]) -> None:
        """
        Broadcast a connection status message (used when Modbus is down).

        Args:
            connected:  True if the poller is currently connected.
            last_seen:  Timestamp of the last successful poll (may be None).
        """
        if not self._active_connections:
            return

        payload = json.dumps({
            "type": "status",
            "connected": connected,
            "last_seen": last_seen.strftime("%Y-%m-%dT%H:%M:%SZ") if last_seen else None,
        })

        connections_snapshot = set(self._active_connections)
        send_tasks = [
            self._send_to(websocket, payload)
            for websocket in connections_snapshot
        ]
        await asyncio.gather(*send_tasks, return_exceptions=True)

    async def _send_to(self, websocket: WebSocket, payload: str) -> None:
        """
        Send a text payload to one client; remove it if the send fails.

        Args:
            websocket: The target WebSocket.
            payload:   JSON string to send.
        """
        try:
            await websocket.send_text(payload)
        except Exception as exc:
            logger.debug("WebSocket send failed (%s) — removing client", exc)
            self.disconnect(websocket)

    @property
    def connection_count(self) -> int:
        """Number of currently active WebSocket connections."""
        return len(self._active_connections)


# Module-level singleton shared by routes.py and main.py.
manager = ConnectionManager()


async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    FastAPI WebSocket route handler.

    Registers the connection, then keeps it open by waiting for messages
    (the dashboard does not send anything — we just need to hold the socket
    open until the client disconnects).

    Args:
        websocket: Injected by FastAPI.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Wait for data from the client; we don't use it but we need to
            # keep receiving so we detect a clean disconnect (WebSocketDisconnect)
            # or an abrupt close (asyncio.CancelledError / exception).
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
