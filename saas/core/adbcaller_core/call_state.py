from __future__ import annotations

import re

from adbcaller_core.models import CallLeg, CallState


CURRENT_CALLS_BLOCK = re.compile(
    r"^  mCalls:\s*\n(?P<body>.*?)(?=^  \S.*:\s*$|\Z)", re.MULTILINE | re.DOTALL
)
TELECOM_STATE = re.compile(
    r"(?:mState|state)\s*[:=]\s*"
    r"(ACTIVE|HOLDING|ON_HOLD|DIALING|CONNECTING|SELECT_PHONE_ACCOUNT|RINGING)",
    re.IGNORECASE,
)
REGISTRY_STATE = re.compile(r"mCallState\s*=\s*([012])")
CALL_ID = re.compile(r"\bCall(?:\[id=|\s+id=|\s+)(?P<id>TC@[\w.-]+)", re.IGNORECASE)


def parse_current_calls(telecom: str) -> list[CallLeg]:
    block = CURRENT_CALLS_BLOCK.search(telecom)
    if not block:
        return []

    calls: dict[str, CallState] = {}
    current_id: str | None = None
    for line in block.group("body").splitlines():
        call_id = CALL_ID.search(line)
        if call_id:
            current_id = call_id.group("id")
        state_match = TELECOM_STATE.search(line)
        if current_id and state_match:
            calls[current_id] = normalize_telecom_state(state_match.group(1))
    return [CallLeg(call_id, state) for call_id, state in calls.items()]


def parse_call_state(telecom: str, registry: str) -> tuple[CallState, list[CallLeg]]:
    calls = parse_current_calls(telecom)
    states = {call.state for call in calls}
    for state in (
        CallState.RINGING,
        CallState.DIALING,
        CallState.ACTIVE,
        CallState.HOLDING,
    ):
        if state in states:
            return state, calls

    registry_states = set(REGISTRY_STATE.findall(registry))
    if "1" in registry_states:
        return CallState.RINGING, calls
    if "2" in registry_states:
        return CallState.ACTIVE, calls
    return CallState.IDLE, calls


def normalize_telecom_state(value: str) -> CallState:
    value = value.upper()
    if value in {"CONNECTING", "SELECT_PHONE_ACCOUNT"}:
        return CallState.DIALING
    if value == "ON_HOLD":
        return CallState.HOLDING
    return CallState(value)
