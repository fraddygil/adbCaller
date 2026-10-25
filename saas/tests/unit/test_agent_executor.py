import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from adbcaller_agent.client import resolve_adb_path, websocket_url
from adbcaller_agent.config import AgentConfig, load_config, save_config
from adbcaller_agent.executor import DeviceExecutor
from adbcaller_agent.journal import CommandJournal
from adbcaller_core.call_controller import CallController
from adbcaller_core.device_discovery import DeviceDiscovery
from adbcaller_protocol.commands import (
    AnswerCommand,
    CallCommand,
    CallParameters,
    CommandMessagePayload,
    HangupAllCommand,
)
from fake_adb import FakeAdb


def test_executor_only_calls_mapped_device() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        executor = DeviceExecutor(CallController(adb), DeviceDiscovery(adb))
        agent_id = uuid4()
        device_id = uuid4()
        now = datetime.now(UTC)
        payload = CommandMessagePayload(
            command_id=uuid4(),
            agent_id=agent_id,
            device_id=device_id,
            created_at=now,
            expires_at=now + timedelta(seconds=30),
            command=CallCommand(parameters=CallParameters(number="8095550101")),
        )

        result = await executor.execute(payload, {device_id: "SERIAL-1"})

        assert result == {"state": "DIALING"}
        assert all(serial == "SERIAL-1" for serial, _ in adb.commands)

    asyncio.run(scenario())


def test_linux_config_is_private_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "agent" / "config.json"
    config = AgentConfig(
        server_url="https://saas.example.com",
        agent_id=uuid4(),
        token="opaque-token",
        agent_name="linux-test",
    )

    save_config(config, path)

    assert load_config(path) == config
    assert path.stat().st_mode & 0o777 == 0o600
    assert websocket_url(config.server_url) == "wss://saas.example.com/agent/ws"


def test_command_journal_does_not_start_a_command_twice(tmp_path: Path) -> None:
    journal = CommandJournal(tmp_path / "journal.db")
    command_id = uuid4()

    assert journal.start(command_id)
    assert not journal.start(command_id)
    journal.finish(command_id, "DONE", {"state": "IDLE"})

    entry = journal.get(command_id)
    assert entry is not None
    assert entry.state == "DONE"
    assert entry.result == {"state": "IDLE"}


def test_explicit_adb_path_is_preserved() -> None:
    assert resolve_adb_path("/opt/android/adb") == "/opt/android/adb"


def test_executor_answers_only_the_target_device() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        executor = DeviceExecutor(CallController(adb), DeviceDiscovery(adb))
        agent_id = uuid4()
        device_id = uuid4()
        now = datetime.now(UTC)

        result = await executor.execute(
            CommandMessagePayload(
                command_id=uuid4(),
                agent_id=agent_id,
                device_id=device_id,
                created_at=now,
                expires_at=now + timedelta(seconds=30),
                command=AnswerCommand(),
            ),
            {device_id: "SERIAL-1"},
        )

        assert result == {"state": "ACTIVE"}
        assert adb.commands == [
            ("SERIAL-1", ("WAKE",)),
            ("SERIAL-1", ("input", "keyevent", "KEYCODE_CALL")),
        ]

    asyncio.run(scenario())


def test_executor_hangs_up_every_mapped_device() -> None:
    async def scenario() -> None:
        adb = FakeAdb()
        executor = DeviceExecutor(CallController(adb), DeviceDiscovery(adb))
        agent_id = uuid4()
        first = uuid4()
        second = uuid4()
        now = datetime.now(UTC)

        result = await executor.execute(
            CommandMessagePayload(
                command_id=uuid4(),
                agent_id=agent_id,
                created_at=now,
                expires_at=now + timedelta(seconds=30),
                command=HangupAllCommand(),
            ),
            {first: "SERIAL-1", second: "SERIAL-2"},
        )

        assert set(result["results"]) == {str(first), str(second)}
        assert ("SERIAL-1", ("input", "keyevent", "KEYCODE_ENDCALL")) in adb.commands
        assert ("SERIAL-2", ("input", "keyevent", "KEYCODE_ENDCALL")) in adb.commands

    asyncio.run(scenario())
