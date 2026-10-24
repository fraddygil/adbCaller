import asyncio
from pathlib import Path

from api import _device_card, health
from models import AdbState, DeviceSnapshot


def test_device_number_input_is_preserved_during_refresh() -> None:
    snapshot = DeviceSnapshot("SERIAL-1", "SM-A566E", AdbState.DEVICE)

    card = _device_card(snapshot)

    assert 'id="number-SERIAL-1"' in card
    assert "hx-preserve" in card
    assert 'pattern="\\+?[0-9]{3,20}"' in card
    assert "replace(/[^0-9+]/g, '')" in card
    assert f'/ui/devices/{snapshot.serial}/add-call' in card
    assert f'/ui/devices/{snapshot.serial}/calls' in card
    assert f'/ui/devices/{snapshot.serial}/merge' in card
    assert f'/ui/devices/{snapshot.serial}/speaker' in card
    assert f'/ui/devices/{snapshot.serial}/conference' not in card


def test_health_reports_available_devices() -> None:
    result = asyncio.run(health())

    assert result["status"] == "ok"
    assert isinstance(result["available_devices"], int)


def test_global_buttons_distinguish_single_calls_from_conferences() -> None:
    page = Path("templates/index.html").read_text(encoding="utf-8")

    assert "Iniciar conferencias con pendientes" in page
    assert 'hx-post="/ui/conference-all"' in page
    assert "Llamar 1 por celular" in page
