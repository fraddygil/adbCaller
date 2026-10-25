PROTOCOL_VERSION = 1
MIN_PROTOCOL_VERSION = 1


def negotiate_version(agent_minimum: int, agent_maximum: int) -> int | None:
    minimum = max(MIN_PROTOCOL_VERSION, agent_minimum)
    maximum = min(PROTOCOL_VERSION, agent_maximum)
    return maximum if minimum <= maximum else None
