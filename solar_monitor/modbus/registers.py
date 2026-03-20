"""
modbus/registers.py — Midnite Classic 200 Modbus register definitions.

All register addresses, scaling factors, and data-type handling are
centralised here so they never appear as magic numbers elsewhere.

Sources:
  • MidNite Solar MODBUS Network Spec Rev C.5 (December 8, 2013)
  • ClassicDIY/ClassicMQTT — classic_modbusdecoder.py (open-source reference)

-----------------------------------------------------------------------------
Addressing convention
-----------------------------------------------------------------------------
The Midnite spec uses 1-based register *numbers* (e.g. 4116).
The Modbus wire address is (register_number - 1), i.e. 0-based.
pymodbus read_holding_registers() takes the 0-based wire address.

We therefore define two contiguous read blocks:

  BLOCK_MAIN  — wire address 4100, count 44  (spec registers 4101–4144)
  BLOCK_WBJR  — wire address 4360, count 22  (spec registers 4361–4381)

Within each block, a register's *offset* is (wire_address - block_start).

-----------------------------------------------------------------------------
32-bit value word order
-----------------------------------------------------------------------------
The Classic stores 32-bit values as two consecutive 16-bit registers in
little-endian *word* order: low word first, high word second.
  value_32 = (high_word << 16) | low_word
"""

from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Block definitions — what to request from the device
# ---------------------------------------------------------------------------

# Block 1: core charge controller data
BLOCK_MAIN_START: int = 4100   # 0-based wire address
BLOCK_MAIN_COUNT: int = 44     # reads registers 4101–4144 (1-based)

# Block 2: WhizBang Jr shunt monitor data (SOC, net current, AH remaining)
BLOCK_WBJR_START: int = 4360   # 0-based wire address
BLOCK_WBJR_COUNT: int = 22     # reads registers 4361–4381 (1-based)

# ---------------------------------------------------------------------------
# Register offsets within BLOCK_MAIN (wire_address - BLOCK_MAIN_START)
# ---------------------------------------------------------------------------

# Battery voltage — INT16, divide by 10 → Volts
OFFSET_BATTERY_VOLTAGE: int = 4114 - BLOCK_MAIN_START        # reg 4115

# PV terminal voltage — UINT16, divide by 10 → Volts
OFFSET_PV_VOLTAGE: int = 4115 - BLOCK_MAIN_START             # reg 4116

# Battery charge current (Classic output terminal) — UINT16, divide by 10 → Amps
OFFSET_BATTERY_CHARGE_CURRENT: int = 4116 - BLOCK_MAIN_START # reg 4117

# Energy delivered to battery today — UINT16, divide by 10 → kWh (resets ~23:59)
OFFSET_DAILY_KWH: int = 4117 - BLOCK_MAIN_START              # reg 4118

# Power output to battery — UINT16, ×1 → Watts
OFFSET_OUTPUT_WATTS: int = 4118 - BLOCK_MAIN_START           # reg 4119

# Charge stage — UINT16; high byte = stage code (see CHARGE_STAGE_NAMES)
OFFSET_CHARGE_STAGE: int = 4119 - BLOCK_MAIN_START           # reg 4120

# PV terminal current — UINT16, divide by 10 → Amps
OFFSET_PV_CURRENT: int = 4120 - BLOCK_MAIN_START             # reg 4121

# Lifetime kWh — UINT32, low word first; divide by 10 → kWh
OFFSET_LIFETIME_KWH_LOW: int = 4125 - BLOCK_MAIN_START       # reg 4126 (low word)
OFFSET_LIFETIME_KWH_HIGH: int = 4126 - BLOCK_MAIN_START      # reg 4127 (high word)

# Battery temperature (external sensor) — INT16, divide by 10 → °C
# Returns 25.0 if the sensor is not connected.
OFFSET_BATTERY_TEMP: int = 4131 - BLOCK_MAIN_START           # reg 4132

# Heat sink / power FET temperature — INT16, divide by 10 → °C
OFFSET_HEATSINK_TEMP: int = 4132 - BLOCK_MAIN_START          # reg 4133

# ---------------------------------------------------------------------------
# Register offsets within BLOCK_WBJR (wire_address - BLOCK_WBJR_START)
# ---------------------------------------------------------------------------

# Net battery current from WBJr shunt — INT16, divide by 10 → Amps
# Positive = charging, negative = discharging (accounts for loads)
OFFSET_WBJR_NET_CURRENT: int = 4370 - BLOCK_WBJR_START       # reg 4371

# Battery State of Charge from WBJr coulomb counter — UINT16, ×1 → %
OFFSET_WBJR_SOC: int = 4372 - BLOCK_WBJR_START               # reg 4373

# Amp-hours remaining per WBJr — UINT16, ×1 → AH
OFFSET_WBJR_AH_REMAINING: int = 4376 - BLOCK_WBJR_START      # reg 4377

# ---------------------------------------------------------------------------
# Charge stage code → human-readable label
# ---------------------------------------------------------------------------
# Extracted from the high byte of register 4120.

CHARGE_STAGE_NAMES: dict[int, str] = {
    0:  "Resting",
    3:  "Absorb",
    4:  "Bulk MPPT",
    5:  "Float",
    6:  "Float MPPT",
    7:  "Equalize",
    10: "HyperVOC",
    18: "Equalize MPPT",
}

CHARGE_STAGE_UNKNOWN: str = "Unknown"


# ---------------------------------------------------------------------------
# Parsed reading dataclass
# ---------------------------------------------------------------------------

@dataclass
class RawRegisters:
    """
    Holds raw register blocks straight off the wire before any decoding.
    Keeping these separate from the decoded Reading makes unit-testing the
    decode logic straightforward.
    """
    main: list[int]   # BLOCK_MAIN_COUNT values
    wbjr: list[int]   # BLOCK_WBJR_COUNT values


def decode_charge_stage(raw_value: int) -> str:
    """
    Extract the charge stage name from raw register 4120.

    The high byte (most-significant byte) of the 16-bit register holds the
    stage code; the low byte is an internal sub-state we do not surface.

    Args:
        raw_value: The raw UINT16 value of register 4120.

    Returns:
        A human-readable charge stage string, e.g. "Float".
    """
    high_byte: int = (raw_value >> 8) & 0xFF
    return CHARGE_STAGE_NAMES.get(high_byte, CHARGE_STAGE_UNKNOWN)


def decode_lifetime_kwh(low_word: int, high_word: int) -> float:
    """
    Reconstruct a 32-bit lifetime kWh value from two 16-bit registers.

    The Classic stores 32-bit values in little-endian word order:
    low word at the lower address, high word at the higher address.

    Args:
        low_word:  Raw UINT16 from register 4126 (the lower address).
        high_word: Raw UINT16 from register 4127 (the higher address).

    Returns:
        Lifetime kWh as a float.
    """
    raw_32: int = (high_word << 16) | low_word
    return raw_32 / 10.0


def decode_registers(raw: RawRegisters) -> "DecodedReading":
    """
    Convert raw register values into engineering-unit values.

    All arithmetic is performed here so callers work with meaningful
    quantities and never touch scaling factors directly.

    Args:
        raw: A RawRegisters instance containing both Modbus blocks.

    Returns:
        A DecodedReading with all values in proper engineering units.
    """
    main = raw.main
    wbjr = raw.wbjr

    pv_voltage: float = main[OFFSET_PV_VOLTAGE] / 10.0
    pv_current: float = main[OFFSET_PV_CURRENT] / 10.0

    # PV input watts is computed — there is no dedicated register for it.
    pv_watts: float = round(pv_voltage * pv_current, 1)

    # Battery voltage is signed (INT16) — already handled by pymodbus when
    # the register is read with the signed=True flag or decoded via struct.
    # We cast to signed here defensively.
    raw_batt_v: int = main[OFFSET_BATTERY_VOLTAGE]
    battery_voltage: float = _to_signed16(raw_batt_v) / 10.0

    raw_batt_temp: int = main[OFFSET_BATTERY_TEMP]
    battery_temp: Optional[float] = _to_signed16(raw_batt_temp) / 10.0

    raw_heatsink: int = main[OFFSET_HEATSINK_TEMP]
    heatsink_temp: float = _to_signed16(raw_heatsink) / 10.0

    return DecodedReading(
        pv_voltage=pv_voltage,
        pv_current=pv_current,
        pv_watts=pv_watts,
        output_watts=float(main[OFFSET_OUTPUT_WATTS]),
        charge_state=decode_charge_stage(main[OFFSET_CHARGE_STAGE]),
        heatsink_temp=heatsink_temp,
        daily_kwh=main[OFFSET_DAILY_KWH] / 10.0,
        lifetime_kwh=decode_lifetime_kwh(
            main[OFFSET_LIFETIME_KWH_LOW],
            main[OFFSET_LIFETIME_KWH_HIGH],
        ),
        battery_voltage=battery_voltage,
        battery_current=main[OFFSET_BATTERY_CHARGE_CURRENT] / 10.0,
        battery_soc=float(wbjr[OFFSET_WBJR_SOC]),
        battery_net_current=_to_signed16(wbjr[OFFSET_WBJR_NET_CURRENT]) / 10.0,
        battery_ah_remaining=float(wbjr[OFFSET_WBJR_AH_REMAINING]),
        battery_temp=battery_temp,
    )


def _to_signed16(value: int) -> int:
    """
    Interpret a raw UINT16 value (0–65535) as a signed INT16 (-32768–32767).

    pymodbus returns register values as unsigned integers. Registers that
    the Classic defines as INT16 (e.g. temperatures) must be reinterpreted
    as signed before dividing by the scaling factor.

    Args:
        value: Raw unsigned 16-bit integer from pymodbus.

    Returns:
        The same bit pattern interpreted as a signed 16-bit integer.
    """
    if value >= 0x8000:
        return value - 0x10000
    return value


@dataclass
class DecodedReading:
    """
    All Midnite Classic 200 metrics in engineering units, ready for storage
    or transmission.  Field names match the database schema columns exactly
    so that the repository can use dataclasses.asdict() without remapping.
    """
    pv_voltage: float           # Volts
    pv_current: float           # Amps
    pv_watts: float             # Watts (computed: pv_voltage × pv_current)
    output_watts: float         # Watts delivered to battery
    charge_state: str           # e.g. "Float", "Bulk MPPT"
    heatsink_temp: float        # °C
    daily_kwh: float            # kWh harvested today
    lifetime_kwh: float         # kWh harvested all-time
    battery_voltage: float      # Volts
    battery_current: float      # Amps (Classic output terminal)
    battery_soc: float          # % (WhizBang Jr)
    battery_net_current: float  # Amps (WhizBang Jr, + = charging)
    battery_ah_remaining: float # Amp-hours remaining (WhizBang Jr)
    battery_temp: Optional[float]  # °C (None-equivalent: 25.0 if sensor absent)
