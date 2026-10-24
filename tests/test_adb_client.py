from adb_client import parse_devices
from models import AdbState


def test_parse_devices_includes_non_ready_states() -> None:
    output = """List of devices attached
R58M123 device product:a56 model:SM-A566B transport_id:1
R58M456 unauthorized usb:2-1 transport_id:2
R58M789 offline transport_id:3
"""

    devices = parse_devices(output)

    assert [(item.serial, item.model, item.state) for item in devices] == [
        ("R58M123", "SM-A566B", AdbState.DEVICE),
        ("R58M456", "Desconocido", AdbState.UNAUTHORIZED),
        ("R58M789", "Desconocido", AdbState.OFFLINE),
    ]
