from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AgentConfig:
    server_url: str
    agent_id: UUID
    token: str
    agent_name: str
    adb_path: str = "adb"


def default_config_path() -> Path:
    base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "adbcaller-agent" / "config.json"


def save_config(config: AgentConfig, path: Path) -> None:
    validate_server_url(config.server_url)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    payload = asdict(config)
    payload["agent_id"] = str(config.agent_id)
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def load_config(path: Path) -> AgentConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    config = AgentConfig(
        server_url=payload["server_url"],
        agent_id=UUID(payload["agent_id"]),
        token=payload["token"],
        agent_name=payload["agent_name"],
        adb_path=payload.get("adb_path", "adb"),
    )
    validate_server_url(config.server_url)
    return config


def validate_server_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("La URL del servidor debe comenzar con http:// o https://")
