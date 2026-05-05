#!/usr/bin/env python3
"""Schreibt die Datei VERSION im Projektroot (für Docker ohne .git).

Aufruf aus dem Projektroot: python3 scripts/update-version-file.py
Logik entspricht cloud/versioning.py (Git: YYYY.MM.XX).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "cloud") not in sys.path:
    sys.path.insert(0, str(_ROOT / "cloud"))

from versioning import _REPO_ROOT, _version_from_git  # noqa: E402


def main() -> None:
    v = _version_from_git(_REPO_ROOT)
    if not v:
        print("Kein Git oder keine Commits — VERSION unverändert.", file=sys.stderr)
        sys.exit(1)
    (_REPO_ROOT / "VERSION").write_text(v + "\n", encoding="utf-8")
    print(v)


if __name__ == "__main__":
    main()
