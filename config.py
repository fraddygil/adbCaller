from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.casefold() not in {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class Settings:
    adb_path: str = os.getenv("ADB_PATH", "adb")
    adb_timeout: float = float(os.getenv("ADB_TIMEOUT", "15"))
    poll_interval: float = float(os.getenv("POLL_INTERVAL", "2.5"))
    database_path: Path = Path(os.getenv("DATABASE_PATH", BASE_DIR / "adb_caller.db"))
    log_dir: Path = Path(os.getenv("LOG_DIR", BASE_DIR / "logs"))
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    auto_speaker: bool = env_bool("AUTO_SPEAKER", True)
    auto_merge: bool = env_bool("AUTO_MERGE", True)
    conference_call_timeout: float = float(os.getenv("CONFERENCE_CALL_TIMEOUT", "15"))
    conference_merge_timeout: float = float(os.getenv("CONFERENCE_MERGE_TIMEOUT", "30"))
    max_conference_calls: int = int(os.getenv("MAX_CONFERENCE_CALLS", "4"))
    merge_attempts: int = int(os.getenv("MERGE_ATTEMPTS", "5"))
    skip_failed_numbers: bool = env_bool("SKIP_FAILED_NUMBERS", True)


settings = Settings()


def get_device_logger(serial: str) -> logging.Logger:
    """Crea un archivo separado sin permitir rutas provenientes del serial."""
    safe_serial = re.sub(r"[^A-Za-z0-9_.-]", "_", serial)
    logger = logging.getLogger(f"device.{safe_serial}")
    if logger.handlers:
        return logger

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(settings.log_dir / f"{safe_serial}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
