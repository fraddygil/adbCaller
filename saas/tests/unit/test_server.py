import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from adbcaller_server.config import Settings
from adbcaller_server.main import app
from adbcaller_server.main import health
from adbcaller_server.models import Base
from adbcaller_server.number_lists import normalize_number, parse_csv_numbers
from adbcaller_server.realtime import EventHub
from adbcaller_server.web import INDEX_PATH
from adbcaller_server.security import (
    csrf_token,
    hash_password,
    new_scoped_secret,
    parse_scoped_secret,
    secret_hash,
    verify_password,
)
from adbcaller_server.settings_api import AutomationSettings


def test_server_health_is_process_liveness() -> None:
    assert asyncio.run(health()) == {"status": "ok"}


def test_all_customer_tables_are_tenant_scoped() -> None:
    assert len(Base.metadata.tables) == 17
    for name, table in Base.metadata.tables.items():
        if name != "tenants":
            assert "tenant_id" in table.columns, name


def test_production_rejects_development_secrets() -> None:
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        Settings(
            app_env="production",
            public_url="https://saas.example.com",
            _env_file=None,
        )


def test_passwords_and_opaque_tokens_are_not_stored_plaintext() -> None:
    password_hash = hash_password("correct-horse-battery")
    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, "correct-horse-battery")
    assert not verify_password(password_hash, "incorrect-password")

    tenant_id = uuid4()
    token = new_scoped_secret(tenant_id)
    assert parse_scoped_secret(token, 1) == (tenant_id,)
    assert token.encode() not in secret_hash(token, "pepper")
    assert csrf_token(token, "session-secret") != token


def test_auth_and_pairing_routes_are_registered_without_models_alias() -> None:
    routes = {route.path for route in app.routes}
    assert {"/auth/login", "/auth/logout", "/auth/me"} <= routes
    assert {"/admin/pairing-codes", "/agent/pair"} <= routes
    assert {"/agent/ws", "/devices"} <= routes
    assert "/admin/users" in routes
    assert "/devices/{device_id}/commands" in routes
    assert "/hangup-all" in routes
    assert "/number-lists/{number_list_id}/call-all" in routes
    assert "/number-lists/{number_list_id}/conference-all" in routes
    assert {"/calls", "/audit", "/settings", "/conferences"} <= routes
    assert {"/", "/events"} <= routes
    assert "/v1/models" not in routes


def test_sse_hub_delivers_tenant_event() -> None:
    async def scenario() -> None:
        hub = EventHub()
        tenant_id = uuid4()
        stream = hub.subscribe(tenant_id)
        assert "event: ready" in await anext(stream)
        await hub.publish(tenant_id, "device.updated", {"id": "safe"})
        event = await anext(stream)
        assert "device.updated" in event
        assert "safe" in event
        await stream.aclose()

    asyncio.run(scenario())


def test_panel_preserves_editable_device_fields() -> None:
    html = INDEX_PATH.read_text(encoding="utf-8")
    assert "data-preserve" in html
    assert 'id="tenant-slug"' in html
    assert 'new EventSource("/events")' in html
    assert ">Contestar</button>" in html
    assert 'id="hangup-all"' in html


def test_csv_numbers_are_limited_to_first_column_and_validated() -> None:
    assert parse_csv_numbers("number,name\n809-555-0101,A\n+18095550102,B\n") == [
        "809-555-0101",
        "+18095550102",
    ]
    assert normalize_number("809-555-0101") == "8095550101"
    with pytest.raises(ValueError, match="Número inválido"):
        normalize_number("809ABC0101")


def test_number_entries_link_to_durable_tasks() -> None:
    assert "task_id" in Base.metadata.tables["numbers"].columns


def test_automation_settings_validate_safe_bounds() -> None:
    with pytest.raises(ValidationError):
        AutomationSettings(max_conference_calls=1)
