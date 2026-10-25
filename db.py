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
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT NOT NULL,
                    changed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS number_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    serial TEXT NOT NULL,
                    number TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_number_queue_serial
                    ON number_queue(serial, id);
                CREATE TABLE IF NOT EXISTS conference_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    serial TEXT NOT NULL,
                    state TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conference_sessions_serial
                    ON conference_sessions(serial, id);
                CREATE TABLE IF NOT EXISTS conference_participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    number TEXT NOT NULL,
                    state TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES conference_sessions(id)
                );
                """
            )

    async def get_settings(self) -> dict[str, str]:
        rows = await self._run(self._fetchall, "SELECT key, value FROM app_settings", ())
        return {row[0]: row[1] for row in rows}

    async def set_settings(
        self, values: dict[str, str], old: dict[str, object] | None = None
    ) -> None:
        await self._run(self._set_settings_sync, values, old or {})

    def _set_settings_sync(self, values: dict[str, str], old: dict[str, object]) -> None:
        now = utc_now()
        with sqlite3.connect(self.path) as connection:
            for key, value in values.items():
                connection.execute(
                    """INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET
                         value=excluded.value, updated_at=excluded.updated_at""",
                    (key, value, now),
                )
                if key in old:
                    connection.execute(
                        """INSERT INTO settings_audit
                           (key, old_value, new_value, changed_at) VALUES (?, ?, ?, ?)""",
                        (key, str(old[key]), value, now),
                    )

    async def enqueue_numbers(self, serial: str, numbers: list[str]) -> None:
        await self._run(self._enqueue_numbers_sync, serial, numbers)

    def _enqueue_numbers_sync(self, serial: str, numbers: list[str]) -> None:
        now = utc_now()
        with sqlite3.connect(self.path) as connection:
            connection.executemany(
                "INSERT INTO number_queue(serial, number, created_at) VALUES (?, ?, ?)",
                ((serial, number, now) for number in numbers),
            )

    async def load_queued_numbers(self, serial: str) -> list[str]:
        rows = await self._run(
            self._fetchall,
            "SELECT number FROM number_queue WHERE serial=? ORDER BY id",
            (serial,),
        )
        return [row[0] for row in rows]

    async def remove_queued_number(self, serial: str, number: str) -> None:
        await self._run(
            self._execute,
            """DELETE FROM number_queue WHERE id=(
                   SELECT id FROM number_queue WHERE serial=? AND number=?
                   ORDER BY id LIMIT 1)""",
            (serial, number),
        )

    async def create_conference_session(self, serial: str, numbers: list[str]) -> int:
        return await self._run(self._create_conference_session_sync, serial, numbers)

    def _create_conference_session_sync(self, serial: str, numbers: list[str]) -> int:
        now = utc_now()
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                """INSERT INTO conference_sessions
                   (serial, state, total, progress, created_at, updated_at)
                   VALUES (?, 'STARTING', ?, 0, ?, ?)""",
                (serial, len(numbers), now, now),
            )
            session_id = int(cursor.lastrowid)
            connection.executemany(
                """INSERT INTO conference_participants
                   (session_id, position, number, state, updated_at)
                   VALUES (?, ?, ?, 'PENDING', ?)""",
                ((session_id, index, number, now) for index, number in enumerate(numbers)),
            )
            return session_id

    async def update_conference_session(
        self, session_id: int, state: str, progress: int, error: str = ""
    ) -> None:
        await self._run(
            self._execute,
            """UPDATE conference_sessions
               SET state=?, progress=?, error=?, updated_at=? WHERE id=?""",
            (state, progress, error, utc_now(), session_id),
        )

    async def update_participant(
        self, session_id: int, position: int, state: str, error: str = ""
    ) -> None:
        await self._run(
            self._execute,
            """UPDATE conference_participants
               SET state=?, error=?, updated_at=? WHERE session_id=? AND position=?""",
            (state, error, utc_now(), session_id, position),
        )

    async def get_recoverable_session(self, serial: str):
        rows = await self._run(
            self._fetchall,
            """SELECT id, state, total, progress, error FROM conference_sessions
               WHERE serial=? AND state NOT IN ('ENDED', 'CANCELLED', 'FAILED')
               ORDER BY id DESC LIMIT 1""",
            (serial,),
        )
        return rows[0] if rows else None

    async def get_session_pending_numbers(self, session_id: int) -> list[tuple[int, str]]:
        rows = await self._run(
            self._fetchall,
            """SELECT position, number FROM conference_participants
               WHERE session_id=? AND state='PENDING' ORDER BY position""",
            (session_id,),
        )
        return [(int(row[0]), row[1]) for row in rows]

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

    def _fetchall(self, query: str, parameters: tuple) -> list[tuple]:
        with sqlite3.connect(self.path) as connection:
            return connection.execute(query, parameters).fetchall()
