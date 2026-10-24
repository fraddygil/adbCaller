from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from models import DeviceSnapshot


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        path = Path(self.path)
        if self.path != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    serial TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    adb_state TEXT NOT NULL,
                    android_version TEXT NOT NULL DEFAULT '',
                    api_level TEXT NOT NULL DEFAULT '',
                    one_ui_version TEXT NOT NULL DEFAULT '',
                    last_seen TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    serial TEXT NOT NULL,
                    number TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    result TEXT NOT NULL,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_calls_open
                    ON calls(serial, ended_at);
                """
            )

    async def upsert_device(self, device: DeviceSnapshot) -> None:
        await self._run(
            self._execute,
            """INSERT INTO devices
               (serial, model, adb_state, android_version, api_level, one_ui_version, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(serial) DO UPDATE SET
                 model=excluded.model, adb_state=excluded.adb_state,
                 android_version=excluded.android_version,
                 api_level=excluded.api_level, one_ui_version=excluded.one_ui_version,
                 last_seen=excluded.last_seen""",
            (
                device.serial,
                device.model,
                device.adb_state.value,
                device.android_version,
                device.api_level,
                device.one_ui_version,
                utc_now(),
            ),
        )

    async def start_call(self, serial: str, number: str, started_at: str) -> int:
        return await self._run(
            self._insert,
            "INSERT INTO calls(serial, number, started_at, result) VALUES (?, ?, ?, ?)",
            (serial, number, started_at, "STARTED"),
        )

    async def record_failed_call(
        self, serial: str, number: str, started_at: str, error: str
    ) -> None:
        await self._run(
            self._execute,
            """INSERT INTO calls
               (serial, number, started_at, ended_at, result, error)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (serial, number, started_at, utc_now(), "ERROR", error),
        )

    async def finish_open_call(self, serial: str, result: str, error: str | None = None) -> None:
        await self._run(
            self._execute,
            """UPDATE calls SET ended_at=?, result=?, error=COALESCE(?, error)
               WHERE id=(SELECT id FROM calls
                         WHERE serial=? AND ended_at IS NULL
                         ORDER BY id DESC LIMIT 1)""",
            (utc_now(), result, error, serial),
        )

    async def finish_open_calls(self, serial: str, result: str, error: str | None = None) -> None:
        await self._run(
            self._execute,
            """UPDATE calls SET ended_at=?, result=?, error=COALESCE(?, error)
               WHERE serial=? AND ended_at IS NULL""",
            (utc_now(), result, error, serial),
        )

    async def _run(self, function, *args):
        async with self._lock:
            return await asyncio.to_thread(function, *args)

    def _execute(self, query: str, parameters: tuple) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(query, parameters)

    def _insert(self, query: str, parameters: tuple) -> int:
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(query, parameters)
            return int(cursor.lastrowid)
