from __future__ import annotations

from typing import Iterable

LEVEL_1 = "level1"
LEVEL_2 = "level2"
LEVEL_3 = "level3"
LEVEL_4 = "level4"

SECURITY_LEVELS: tuple[str, ...] = (LEVEL_1, LEVEL_2, LEVEL_3, LEVEL_4)
DEFAULT_SECURITY_LEVEL = LEVEL_2

LEGACY_LEVEL_MAP: dict[str, str] = {
    "normal": LEVEL_1,
    "standard": LEVEL_2,
    "secure": LEVEL_2,
    "extended": LEVEL_2,
    "advanced": LEVEL_3,
    "maximal": LEVEL_4,
}


def normalize_security_level(raw: str | None, default: str = DEFAULT_SECURITY_LEVEL) -> str:
    v = (raw or "").strip().lower()
    if v in SECURITY_LEVELS:
        return v
    if v in LEGACY_LEVEL_MAP:
        return LEGACY_LEVEL_MAP[v]
    return default


def normalize_allowed_security_levels(values: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = normalize_security_level(value, default="")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out or list(SECURITY_LEVELS)


def is_e2e_level(level: str) -> bool:
    return normalize_security_level(level) in (LEVEL_3, LEVEL_4)


def is_text_e2e_level(level: str) -> bool:
    return normalize_security_level(level) == LEVEL_4


def requires_guest_account(level: str) -> bool:
    return normalize_security_level(level) in (LEVEL_2, LEVEL_3, LEVEL_4)
