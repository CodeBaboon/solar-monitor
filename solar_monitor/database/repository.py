"""
database/repository.py — SQLite time-series storage for solar readings.

The repository pattern used here keeps all SQL confined to this file.
The rest of the application only works with Reading and DailySummary
dataclasses and never sees raw SQL or tuples.

Schema is created automatically on first run via init_db().
"""

import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from solar_monitor import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL — run once at startup
# ---------------------------------------------------------------------------

_CREATE_READINGS_TABLE = """
CREATE TABLE IF NOT EXISTS readings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT    NOT NULL,
    pv_voltage          REAL,
    pv_current          REAL,
    pv_watts            REAL,
    output_watts        REAL,
    charge_state        TEXT,
    heatsink_temp       REAL,
    daily_kwh           REAL,
    lifetime_kwh        REAL,
    battery_voltage     REAL,
    battery_current     REAL,
    battery_soc         REAL,
    battery_net_current REAL,
    battery_ah_remaining REAL,
    battery_temp        REAL
);
"""

_CREATE_TIMESTAMP_INDEX = """
CREATE INDEX IF NOT EXISTS idx_readings_timestamp ON readings (timestamp);
"""

# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class Reading:
    """
    One complete set of metrics from a single poll of the Classic 200.

    Timestamp is always UTC.  All numeric fields can be None if the
    underlying sensor is absent (e.g. battery_temp without a temp sensor).
    """
    timestamp: datetime
    pv_voltage: float
    pv_current: float
    pv_watts: float
    output_watts: float
    charge_state: str
    heatsink_temp: float
    daily_kwh: float
    lifetime_kwh: float
    battery_voltage: float
    battery_current: float
    battery_soc: float
    battery_net_current: float
    battery_ah_remaining: float
    battery_temp: Optional[float]


@dataclass
class DailySummary:
    """Aggregated metrics for a single *local* calendar day."""
    date: str           # "YYYY-MM-DD" in the host's local timezone
    total_kwh: float    # Maximum daily_kwh seen (the register resets nightly)
    avg_soc: float      # Average battery SOC across all readings that day
    peak_pv_watts: float  # Maximum PV wattage recorded that day
    reading_count: int  # Number of readings stored that day


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class ReadingRepository:
    """
    Abstracts all SQLite reads and writes for solar readings.

    Args:
        db_path: Path to the SQLite file.  Created if it does not exist.
    """

    def __init__(self, db_path: str = config.DATABASE_PATH) -> None:
        self._db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        """
        Open a new SQLite connection with recommended settings.

        We open a connection per operation rather than a long-lived
        connection because SQLite handles concurrent readers well this way
        and avoids "database is locked" errors on the Pi.

        Returns:
            A configured sqlite3.Connection.
        """
        conn = sqlite3.connect(self._db_path)
        # Return rows as sqlite3.Row objects so we can access columns by name.
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrent read/write performance.
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def init_db(self) -> None:
        """
        Create the database schema if it does not already exist.

        Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS.
        """
        logger.info("Initialising database at %s", self._db_path)
        with self._get_connection() as conn:
            conn.execute(_CREATE_READINGS_TABLE)
            conn.execute(_CREATE_TIMESTAMP_INDEX)
        logger.info("Database ready")

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def insert_reading(self, reading: Reading) -> None:
        """
        Persist one poll result to the database.

        Args:
            reading: The Reading dataclass to store.

        Raises:
            sqlite3.Error: On any database write failure.
        """
        timestamp_str = reading.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

        sql = """
            INSERT INTO readings (
                timestamp, pv_voltage, pv_current, pv_watts, output_watts,
                charge_state, heatsink_temp, daily_kwh, lifetime_kwh,
                battery_voltage, battery_current, battery_soc,
                battery_net_current, battery_ah_remaining, battery_temp
            ) VALUES (
                :timestamp, :pv_voltage, :pv_current, :pv_watts, :output_watts,
                :charge_state, :heatsink_temp, :daily_kwh, :lifetime_kwh,
                :battery_voltage, :battery_current, :battery_soc,
                :battery_net_current, :battery_ah_remaining, :battery_temp
            )
        """

        params = {
            "timestamp": timestamp_str,
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

        with self._get_connection() as conn:
            conn.execute(sql, params)

        logger.debug("Inserted reading at %s", timestamp_str)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_latest_reading(self) -> Optional[Reading]:
        """
        Fetch the most recent reading from the database.

        Returns:
            The latest Reading, or None if the table is empty.
        """
        sql = """
            SELECT * FROM readings
            ORDER BY timestamp DESC
            LIMIT 1
        """
        with self._get_connection() as conn:
            row = conn.execute(sql).fetchone()

        if row is None:
            return None

        return self._row_to_reading(row)

    def get_readings_since(
        self,
        since: datetime,
        limit: int = 10_000,
        max_points: Optional[int] = None,
    ) -> list[Reading]:
        """
        Fetch readings after a given UTC datetime.

        Two modes:

        * ``max_points`` set (recommended for charts) — readings are averaged
          into equal-width time buckets so the whole window is represented by
          at most ``max_points`` points.  A 30-day window at 10s polling is
          259,200 rows; bucketing turns that into ~2,000 without losing the
          shape of the curve.

        * ``max_points`` None — raw rows, but ordered newest-first internally
          so that hitting ``limit`` truncates the *oldest* data rather than the
          newest.  The previous implementation used ``ORDER BY timestamp ASC
          LIMIT n``, which silently returned the first n rows of the window and
          made long ranges appear to stop a day or more in the past.

        Args:
            since:      Only return readings with timestamp > this value (UTC).
            limit:      Maximum number of raw rows to return.
            max_points: If set, downsample to at most this many points.

        Returns:
            A list of Reading objects ordered oldest-first.
        """
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        if max_points is None:
            # Newest-first with the limit applied, then flipped back to
            # chronological order for the caller.
            sql = """
                SELECT * FROM readings
                WHERE timestamp > :since
                ORDER BY timestamp DESC
                LIMIT :limit
            """
            with self._get_connection() as conn:
                rows = conn.execute(
                    sql, {"since": since_str, "limit": limit}
                ).fetchall()
            return [self._row_to_reading(row) for row in reversed(rows)]

        # --- Downsampled path -------------------------------------------
        window_seconds = max(
            1,
            int((datetime.now(tz=timezone.utc) - since).total_seconds()),
        )
        bucket = max(config.POLL_INTERVAL_SECONDS, window_seconds // max_points)

        # strftime('%s', ...) parses the trailing 'Z' correctly; integer
        # division floors each reading into its bucket, and the bucket start
        # is re-rendered in the same format _row_to_reading expects.
        sql = """
            SELECT
                strftime('%Y-%m-%dT%H:%M:%SZ',
                         strftime('%s', timestamp) / :bucket * :bucket,
                         'unixepoch')        AS timestamp,
                AVG(pv_voltage)              AS pv_voltage,
                AVG(pv_current)              AS pv_current,
                AVG(pv_watts)                AS pv_watts,
                AVG(output_watts)            AS output_watts,
                MAX(charge_state)            AS charge_state,
                AVG(heatsink_temp)           AS heatsink_temp,
                MAX(daily_kwh)               AS daily_kwh,
                MAX(lifetime_kwh)            AS lifetime_kwh,
                AVG(battery_voltage)         AS battery_voltage,
                AVG(battery_current)         AS battery_current,
                AVG(battery_soc)             AS battery_soc,
                AVG(battery_net_current)     AS battery_net_current,
                AVG(battery_ah_remaining)    AS battery_ah_remaining,
                AVG(battery_temp)            AS battery_temp
            FROM readings
            WHERE timestamp > :since
            GROUP BY strftime('%s', timestamp) / :bucket
            ORDER BY timestamp ASC
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                sql, {"since": since_str, "bucket": bucket}
            ).fetchall()

        logger.debug(
            "Downsampled %d buckets at %ds for a %ds window",
            len(rows), bucket, window_seconds,
        )
        return [self._row_to_reading(row) for row in rows]

    def get_daily_kwh_summary(self, days: int = 30) -> list[DailySummary]:
        """
        Return one DailySummary per *local* calendar day for the last N days.

        daily_kwh is a cumulative-today counter that the Classic resets at its
        own local midnight, so the grouping has to use local calendar days too.
        Grouping by UTC days (the previous behaviour) put the boundary at 19:00
        local in CDT — after the sun had finished for the day — so each bucket
        inherited the *previous* local day's completed total and the chart grew
        a spurious extra bar for "tomorrow".

        Args:
            days: How many past days to include (including today).

        Returns:
            A list of DailySummary objects ordered oldest-first.
        """
        # The cutoff must be rendered in exactly the stored format.  Comparing
        # against DATETIME('now', ...) is a *string* comparison of
        # "YYYY-MM-DDTHH:MM:SSZ" against "YYYY-MM-DD HH:MM:SS", and 'T' sorts
        # after ' ', so every row sharing the cutoff date compared greater
        # regardless of its time.
        #
        # One extra day is fetched so the local-time shift cannot clip the
        # oldest bucket; the surplus is trimmed after grouping.
        sql = """
            SELECT
                DATE(timestamp, 'localtime')     AS date,
                MAX(daily_kwh)                   AS total_kwh,
                MAX(lifetime_kwh)
                    - MIN(lifetime_kwh)          AS measured_kwh,
                AVG(battery_soc)                 AS avg_soc,
                MAX(pv_watts)                    AS peak_pv_watts,
                COUNT(*)                         AS reading_count
            FROM readings
            WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', :offset)
            GROUP BY DATE(timestamp, 'localtime')
            ORDER BY date ASC
        """
        offset = f"-{days + 1} days"
        with self._get_connection() as conn:
            rows = conn.execute(sql, {"offset": offset}).fetchall()

        summaries = [
            DailySummary(
                date=row["date"],
                total_kwh=row["total_kwh"] or 0.0,
                avg_soc=row["avg_soc"] or 0.0,
                peak_pv_watts=row["peak_pv_watts"] or 0.0,
                reading_count=row["reading_count"],
            )
            for row in rows
        ]
        return summaries[-days:]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_reading(row: sqlite3.Row) -> Reading:
        """
        Convert a sqlite3.Row (from the readings table) into a Reading.

        Args:
            row: A row from the readings table.

        Returns:
            A fully populated Reading dataclass.
        """
        # Parse the ISO 8601 UTC string back into a timezone-aware datetime.
        timestamp = datetime.strptime(row["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
        timestamp = timestamp.replace(tzinfo=timezone.utc)

        return Reading(
            timestamp=timestamp,
            pv_voltage=row["pv_voltage"],
            pv_current=row["pv_current"],
            pv_watts=row["pv_watts"],
            output_watts=row["output_watts"],
            charge_state=row["charge_state"],
            heatsink_temp=row["heatsink_temp"],
            daily_kwh=row["daily_kwh"],
            lifetime_kwh=row["lifetime_kwh"],
            battery_voltage=row["battery_voltage"],
            battery_current=row["battery_current"],
            battery_soc=row["battery_soc"],
            battery_net_current=row["battery_net_current"],
            battery_ah_remaining=row["battery_ah_remaining"],
            battery_temp=row["battery_temp"],
        )
