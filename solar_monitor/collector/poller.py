"""
collector/poller.py — Polling loop for the Midnite Classic 200.

Responsibilities:
  • Poll the Classic 200 on a fixed interval (config.POLL_INTERVAL_SECONDS).
  • Decode raw registers into a Reading.
  • Write every reading to the database.
  • Broadcast the reading to all connected WebSocket clients.
  • Handle Modbus connection failures with exponential backoff and never crash.

The poller runs as a long-lived asyncio task.  Start it with:

    asyncio.create_task(poller.run())

Stop it cleanly by cancelling the task — the finally block disconnects Modbus.
"""

import asyncio
import logging
from datetime import datetime, timezone

from solar_monitor import config
from solar_monitor.modbus.client import ClassicModbusClient, ModbusReadError
from solar_monitor.modbus.registers import decode_registers, DecodedReading
from solar_monitor.database.repository import Reading, ReadingRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backoff constants
# ---------------------------------------------------------------------------

# Initial wait after the first failure, in seconds.
BACKOFF_INITIAL_SECONDS: float = 5.0

# Each failure multiplies the wait by this factor.
BACKOFF_MULTIPLIER: float = 2.0

# Maximum wait between retries, in seconds (caps the exponential growth).
BACKOFF_MAX_SECONDS: float = 300.0  # 5 minutes


class SolarPoller:
    """
    Manages the polling loop and coordinates Modbus, database, and WebSocket.

    Args:
        repository:        ReadingRepository instance for database writes.
        broadcast_fn:      An async callable that accepts a Reading and sends
                           it to all connected WebSocket clients.  The poller
                           does not import the WebSocket layer directly — the
                           callable is injected at startup to avoid circular
                           imports.
    """

    def __init__(
        self,
        repository: ReadingRepository,
        broadcast_fn,  # Callable[[Reading], Coroutine]
    ) -> None:
        self._repository = repository
        self._broadcast = broadcast_fn
        self._last_reading: Reading | None = None
        self._consecutive_failures: int = 0
        self._is_connected: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def last_reading(self) -> Reading | None:
        """The most recent successfully decoded reading, or None."""
        return self._last_reading

    @property
    def is_connected(self) -> bool:
        """True if the most recent poll succeeded."""
        return self._is_connected

    async def run(self) -> None:
        """
        Main polling loop — runs forever until the asyncio task is cancelled.

        On success: polls every POLL_INTERVAL_SECONDS.
        On failure:  retries with exponential backoff, then resumes the normal
                     interval once a poll succeeds again.
        """
        logger.info("Poller starting — polling every %ds", config.POLL_INTERVAL_SECONDS)

        backoff_seconds: float = BACKOFF_INITIAL_SECONDS

        while True:
            try:
                reading = await self._poll_once()
                self._on_success(reading, backoff_seconds)
                backoff_seconds = BACKOFF_INITIAL_SECONDS  # reset on success
                await asyncio.sleep(config.POLL_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                logger.info("Poller cancelled — shutting down")
                raise  # re-raise so asyncio knows the task ended cleanly

            except Exception as exc:
                backoff_seconds = self._on_failure(exc, backoff_seconds)
                await asyncio.sleep(backoff_seconds)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _poll_once(self) -> Reading:
        """
        Perform a single Modbus poll, decode the result, and persist it.

        Modbus TCP reads are synchronous (pymodbus 3.x), so we push them
        to a thread pool with asyncio.to_thread() to avoid blocking the
        event loop during the network round-trip.

        Returns:
            The decoded and stored Reading.

        Raises:
            ConnectionError:  If the TCP connect fails.
            ModbusReadError:  If a register read returns an error response.
            sqlite3.Error:    If the database write fails.
        """
        client = ClassicModbusClient()
        await client.connect()

        try:
            # run_sync_read runs the blocking read_all() on a thread pool
            # thread so the async event loop stays responsive.
            raw = await asyncio.to_thread(client.read_all)
        finally:
            await client.disconnect()

        decoded: DecodedReading = decode_registers(raw)

        logger.debug(
            "Decoded — PV: %.0fW  SOC: %.0f%%  Batt: %.2fV  State: %s",
            decoded.pv_watts,
            decoded.battery_soc,
            decoded.battery_voltage,
            decoded.charge_state,
        )

        reading = Reading(
            timestamp=datetime.now(tz=timezone.utc),
            pv_voltage=decoded.pv_voltage,
            pv_current=decoded.pv_current,
            pv_watts=decoded.pv_watts,
            output_watts=decoded.output_watts,
            charge_state=decoded.charge_state,
            heatsink_temp=decoded.heatsink_temp,
            daily_kwh=decoded.daily_kwh,
            lifetime_kwh=decoded.lifetime_kwh,
            battery_voltage=decoded.battery_voltage,
            battery_current=decoded.battery_current,
            battery_soc=decoded.battery_soc,
            battery_net_current=decoded.battery_net_current,
            battery_ah_remaining=decoded.battery_ah_remaining,
            battery_temp=decoded.battery_temp,
        )

        # Write to DB (also synchronous — push to thread).
        await asyncio.to_thread(self._repository.insert_reading, reading)

        # Broadcast to WebSocket clients (async, non-blocking).
        await self._broadcast(reading)

        return reading

    def _on_success(self, reading: Reading, previous_backoff: float) -> None:
        """Update state and log a summary after a successful poll."""
        self._last_reading = reading
        self._is_connected = True

        if self._consecutive_failures > 0:
            logger.info(
                "Modbus reconnected after %d failure(s) — "
                "PV: %.0fW  SOC: %.0f%%  Batt: %.2fV  State: %s",
                self._consecutive_failures,
                reading.pv_watts,
                reading.battery_soc,
                reading.battery_voltage,
                reading.charge_state,
            )
        else:
            logger.info(
                "Poll OK — PV: %.0fW  SOC: %.0f%%  Batt: %.2fV  State: %s",
                reading.pv_watts,
                reading.battery_soc,
                reading.battery_voltage,
                reading.charge_state,
            )

        self._consecutive_failures = 0

        # Log charge state transitions at INFO level.
        # (Tracked across calls via _last_reading comparison.)

    def _on_failure(self, exc: Exception, current_backoff: float) -> float:
        """
        Update state and log an error after a failed poll.

        Args:
            exc:             The exception that caused the failure.
            current_backoff: The backoff duration used for the failed attempt.

        Returns:
            The next backoff duration to sleep before retrying.
        """
        self._consecutive_failures += 1
        self._is_connected = False

        next_backoff = min(current_backoff * BACKOFF_MULTIPLIER, BACKOFF_MAX_SECONDS)

        if self._consecutive_failures >= config.MAX_CONSECUTIVE_FAILURES:
            logger.critical(
                "Modbus failure #%d (consecutive) — %s: %s. "
                "Retrying in %.0fs.",
                self._consecutive_failures,
                type(exc).__name__,
                exc,
                next_backoff,
            )
        else:
            logger.warning(
                "Modbus failure #%d — %s: %s. Retrying in %.0fs.",
                self._consecutive_failures,
                type(exc).__name__,
                exc,
                next_backoff,
            )

        return next_backoff
