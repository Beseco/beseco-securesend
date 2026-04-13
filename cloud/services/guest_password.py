"""Passwortregeln für Gastkonto (Registrierung / Reset)."""

from __future__ import annotations

import re

_SPECIAL_RE = re.compile(r'[!@#$%^&*()_+\-=\[\]{}|;:,.<>/?\\`~]')


def validate_guest_password(password: str) -> tuple[bool, str]:
    """Mindestens 10 Zeichen und Komplexität (Groß-, Kleinbuchstabe, Ziffer, Sonderzeichen)."""
    pw = password or ""
    if len(pw) < 10:
        return False, "Passwort muss mindestens 10 Zeichen lang sein."
    if not re.search(r"[A-Z]", pw):
        return False, "Passwort muss mindestens einen Großbuchstaben enthalten."
    if not re.search(r"[a-z]", pw):
        return False, "Passwort muss mindestens einen Kleinbuchstaben enthalten."
    if not re.search(r"\d", pw):
        return False, "Passwort muss mindestens eine Ziffer enthalten."
    if not _SPECIAL_RE.search(pw):
        return (
            False,
            "Passwort muss mindestens ein Sonderzeichen enthalten (z. B. !@#$%&*).",
        )
    return True, ""
