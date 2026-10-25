from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from db import Database


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    label: str
    kind: type
    default: Any
    env_name: str
    minimum: float | None = None
    maximum: float | None = None


SETTING_SPECS = (
    SettingSpec("poll_interval", "Intervalo de sondeo", float, 2.5, "POLL_INTERVAL", 0.5, 10),
    SettingSpec("auto_speaker", "Altavoz automático", bool, True, "AUTO_SPEAKER"),
    SettingSpec("auto_merge", "Combinación automática", bool, True, "AUTO_MERGE"),
    SettingSpec(
        "conference_call_timeout",
        "Segundos para contestar",
        float,
        15,
        "CONFERENCE_CALL_TIMEOUT",
        5,
        120,
    ),
    SettingSpec(
        "conference_merge_timeout",
        "Segundos para combinar",
        float,
        30,
        "CONFERENCE_MERGE_TIMEOUT",
        5,
        60,
    ),
    SettingSpec(
        "max_conference_calls",
        "Máximo de números por conferencia",
        int,
        4,
        "MAX_CONFERENCE_CALLS",
        2,
        4,
    ),
    SettingSpec("merge_attempts", "Intentos para combinar", int, 5, "MERGE_ATTEMPTS", 1, 10),
    SettingSpec("skip_failed_numbers", "Omitir fallidos y continuar", bool, True, "SKIP_FAILED_NUMBERS"),
)


class RuntimeSettings:
    def __init__(self, database: Database, bootstrap: dict[str, Any]) -> None:
        self.database = database
        self.specs = {spec.key: spec for spec in SETTING_SPECS}
        self.values = {
            key: bootstrap.get(key, spec.default) for key, spec in self.specs.items()
        }
        self.locked = {
            spec.key for spec in SETTING_SPECS if spec.env_name in os.environ
        }

    async def initialize(self) -> None:
        stored = await self.database.get_settings()
        missing: dict[str, str] = {}
        for key, spec in self.specs.items():
            self.values[key] = self._parse(spec, self.values[key])
            if key in self.locked:
                continue
            if key in stored:
                self.values[key] = self._parse(spec, stored[key])
            else:
                missing[key] = self._serialize(self.values[key])
        if missing:
            await self.database.set_settings(missing)

    async def update(self, updates: dict[str, Any]) -> None:
        parsed: dict[str, Any] = {}
        for key, value in updates.items():
            if key not in self.specs:
                raise ValueError(f"Ajuste desconocido: {key}")
            if key in self.locked:
                raise ValueError(f"{key} está bloqueado por variable de entorno")
            parsed[key] = self._parse(self.specs[key], value)
        old = {key: self.values[key] for key in parsed}
        await self.database.set_settings(
            {key: self._serialize(value) for key, value in parsed.items()}, old=old
        )
        self.values.update(parsed)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "key": spec.key,
                "label": spec.label,
                "kind": spec.kind.__name__,
                "value": self.values[spec.key],
                "minimum": spec.minimum,
                "maximum": spec.maximum,
                "locked": spec.key in self.locked,
            }
            for spec in SETTING_SPECS
        ]

    def _parse(self, spec: SettingSpec, value: Any) -> Any:
        if spec.kind is bool:
            if isinstance(value, bool):
                parsed = value
            else:
                normalized = str(value).casefold()
                if normalized in {"1", "true", "yes", "on"}:
                    parsed = True
                elif normalized in {"0", "false", "no", "off"}:
                    parsed = False
                else:
                    raise ValueError(f"Valor inválido para {spec.label}")
        else:
            try:
                parsed = spec.kind(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Valor inválido para {spec.label}") from exc
        if spec.minimum is not None and parsed < spec.minimum:
            raise ValueError(f"{spec.label} debe ser al menos {spec.minimum:g}")
        if spec.maximum is not None and parsed > spec.maximum:
            raise ValueError(f"{spec.label} no puede superar {spec.maximum:g}")
        return parsed

    @staticmethod
    def _serialize(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
