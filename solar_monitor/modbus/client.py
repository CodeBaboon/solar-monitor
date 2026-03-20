"""
modbus/client.py — Modbus TCP connection management for the Classic 200.

This module owns the pymodbus client lifecycle: connecting, reading the two
register blocks, and disconnecting.  All retry / backoff logic lives in the
poller (collector/poller.py); this layer just makes one attempt and raises
on failure so the caller can decide what to do.
"""

import logging
from typing import Optional

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from solar_monitor import config
from solar_monitor.modbus.registers import (
    BLOCK_MAIN_COUNT,
    BLOCK_MAIN_START,
    BLOCK_WBJR_COUNT,
    BLOCK_WBJR_START,
    RawRegisters,
)

logger = logging.getLogger(__name__)


class ClassicModbusClient:
    """
    Thin wrapper around a pymodbus TCP client scoped to one Classic 200.

    Usage (async context manager — preferred):

        async with ClassicModbusClient() as client:
            raw = await client.read_all()

    Or manually:

        client = ClassicModbusClient()
        await client.connect()
        raw = await client.read_all()
        await client.disconnect()
    """

    def __init__(
        self,
        host: str = config.CLASSIC_IP,
        port: int = config.CLASSIC_PORT,
        unit_id: int = config.CLASSIC_MODBUS_ADDRESS,
    ) -> None:
        """
        Args:
            host:    IP address of the Classic 200.
            port:    Modbus TCP port (default 502).
            unit_id: Modbus slave / unit ID (default 10).
        """
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._client: Optional[ModbusTcpClient] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """
        Open the TCP connection to the Classic 200.

        Raises:
            ConnectionError: If the TCP handshake fails.
        """
        logger.debug("Connecting to Classic 200 at %s:%d", self._host, self._port)

        # ModbusTcpClient is synchronous inside pymodbus 3.x.
        # We instantiate and connect here; async wrapping is done by the
        # poller using asyncio.to_thread() so the event loop stays free.
        self._client = ModbusTcpClient(
            host=self._host,
            port=self._port,
            timeout=10,
        )

        connected: bool = self._client.connect()
        if not connected:
            self._client = None
            raise ConnectionError(
                f"Could not connect to Classic 200 at {self._host}:{self._port}"
            )

        logger.debug("Connected to Classic 200 at %s:%d", self._host, self._port)

    async def disconnect(self) -> None:
        """Close the TCP connection if one is open."""
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.debug("Disconnected from Classic 200")

    # ------------------------------------------------------------------
    # Context manager support  (async with ClassicModbusClient() as c: ...)
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ClassicModbusClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # Register reads
    # ------------------------------------------------------------------

    def read_all(self) -> RawRegisters:
        """
        Read both register blocks from the Classic 200 in a single call.

        This is a *synchronous* method — call it via asyncio.to_thread()
        from async code so it does not block the event loop.

        Returns:
            A RawRegisters instance with both blocks populated.

        Raises:
            ConnectionError: If the client is not connected.
            ModbusReadError: If either Modbus read fails.
        """
        if self._client is None:
            raise ConnectionError("Not connected — call connect() first")

        main_registers = self._read_block(BLOCK_MAIN_START, BLOCK_MAIN_COUNT, "main")
        wbjr_registers = self._read_block(BLOCK_WBJR_START, BLOCK_WBJR_COUNT, "WBJr")

        return RawRegisters(main=main_registers, wbjr=wbjr_registers)

    def _read_block(
        self, start_address: int, count: int, block_name: str
    ) -> list[int]:
        """
        Read a contiguous block of holding registers (Modbus FC3).

        Args:
            start_address: 0-based wire address of the first register.
            count:         Number of registers to read.
            block_name:    Human-readable name used in log messages.

        Returns:
            A list of raw UINT16 integer values, length == count.

        Raises:
            ModbusReadError: If the response indicates an error.
        """
        logger.debug(
            "Reading %s block: address=%d count=%d unit=%d",
            block_name, start_address, count, self._unit_id,
        )

        response = self._client.read_holding_registers(
            address=start_address,
            count=count,
            slave=self._unit_id,
        )

        if response.isError():
            raise ModbusReadError(
                f"Modbus error reading {block_name} block "
                f"(address={start_address}, count={count}): {response}"
            )

        registers: list[int] = response.registers
        logger.debug(
            "Read %s block OK: %d registers", block_name, len(registers)
        )
        return registers


class ModbusReadError(Exception):
    """Raised when a Modbus read response contains an error code."""
