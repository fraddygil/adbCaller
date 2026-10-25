from __future__ import annotations

import re


VALID_NUMBER = re.compile(r"^\+?[0-9]{3,20}$")


def normalize_number(number: str) -> str:
    normalized = re.sub(r"[\s().-]", "", number)
    if not VALID_NUMBER.fullmatch(normalized):
        raise ValueError("Número inválido: use entre 3 y 20 dígitos, con + opcional")
    return normalized
