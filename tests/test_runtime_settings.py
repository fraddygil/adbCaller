import asyncio
import sqlite3
from pathlib import Path

import pytest

from db import Database
from runtime_settings import SETTING_SPECS, RuntimeSettings


def _bootstrap() -> dict[str, object]:
    return {spec.key: spec.default for spec in SETTING_SPECS}


def test_settings_are_validated_persisted_and_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        for spec in SETTING_SPECS:
            monkeypatch.delenv(spec.env_name, raising=False)
        path = tmp_path / "test.db"
        database = Database(path)
        await database.initialize()
        settings = RuntimeSettings(database, _bootstrap())
        await settings.initialize()

        await settings.update(
            {"conference_call_timeout": 20, "auto_merge": False}
        )

        restored = RuntimeSettings(database, _bootstrap())
        await restored.initialize()
        assert restored.values["conference_call_timeout"] == 20
        assert restored.values["auto_merge"] is False
        with pytest.raises(ValueError):
            await restored.update({"conference_call_timeout": 4})
        with pytest.raises(ValueError):
            await restored.update({"auto_merge": "quizás"})

        with sqlite3.connect(path) as connection:
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM settings_audit"
            ).fetchone()[0]
        assert audit_count == 2

    asyncio.run(scenario())


def test_environment_setting_is_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("AUTO_MERGE", "false")
        database = Database(tmp_path / "test.db")
        await database.initialize()
        settings = RuntimeSettings(database, {**_bootstrap(), "auto_merge": False})
        await settings.initialize()

        assert "auto_merge" in settings.locked
        with pytest.raises(ValueError, match="bloqueado"):
            await settings.update({"auto_merge": True})

    asyncio.run(scenario())
