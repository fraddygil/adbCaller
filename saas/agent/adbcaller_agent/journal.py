from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True, slots=True)
class JournalEntry:
    state: str
    result: dict[str, object]
    error_code: str | None
    error_message: str | None


class CommandJournal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = path
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    error_message TEXT
                )"""
            )

    def get(self, command_id: UUID) -> JournalEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state, result, error_code, error_message FROM commands WHERE command_id = ?",
                (str(command_id),),
            ).fetchone()
        if row is None:
            return None
        return JournalEntry(row[0], json.loads(row[1]), row[2], row[3])

    def start(self, command_id: UUID) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO commands(command_id, state) VALUES (?, 'RUNNING')",
                (str(command_id),),
            )
        return cursor.rowcount == 1

    def finish(
        self,
        command_id: UUID,
        state: str,
        result: dict[str, object],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE commands
                   SET state = ?, result = ?, error_code = ?, error_message = ?
                   WHERE command_id = ?""",
                (
                    state,
                    json.dumps(result),
                    error_code,
                    error_message,
                    str(command_id),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
