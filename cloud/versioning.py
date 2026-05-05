"""
Anzeige-Version für die UI: YYYY.MM.XX

- XX = Anzahl der Commits auf der first-parent-Linie bis HEAD, deren Committer-Datum
  in dem Kalendermonat liegt, den HEAD hat (steigt mit jedem Commit im Monat).
- Ohne Git (z. B. Docker-Image ohne .env): Datei VERSION im Projektroot oder
  APP_DISPLAY_VERSION in der Konfiguration.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _version_from_git(repo: Path) -> str | None:
    git_dir = repo / ".git"
    if not git_dir.exists():
        return None
    try:
        ts = subprocess.check_output(
            ["git", "-C", str(repo), "log", "-1", "--format=%ct"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
        if not ts:
            return None
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        y, m = dt.year, dt.month
        if m == 12:
            y_end, m_end = y + 1, 1
        else:
            y_end, m_end = y, m + 1
        since = f"{y}-{m:02d}-01T00:00:00Z"
        until = f"{y_end}-{m_end:02d}-01T00:00:00Z"
        n = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo),
                "rev-list",
                "--count",
                "--first-parent",
                f"--since={since}",
                f"--until={until}",
                "HEAD",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).strip()
        count = max(1, int(n)) if n else 1
        return f"{y}.{m:02d}.{count:02d}"
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError, OSError):
        return None


def _version_from_file(repo: Path) -> str | None:
    vf = repo / "VERSION"
    if not vf.is_file():
        return None
    try:
        line = vf.read_text(encoding="utf-8").strip().splitlines()
        if not line:
            return None
        return line[0].strip() or None
    except OSError:
        return None


def get_ui_version() -> str:
    """Version für Templates (einmal pro Prozess sinnvoll gecacht durch Aufrufer)."""
    try:
        from config import settings

        override = (getattr(settings, "APP_DISPLAY_VERSION", None) or "").strip()
        if override:
            return override
    except Exception:
        pass

    v = _version_from_git(_REPO_ROOT)
    if v:
        return v
    v = _version_from_file(_REPO_ROOT)
    if v:
        return v
    return "0.0.00-dev"


_UI_VERSION_CACHE: str | None = None


def get_ui_version_cached() -> str:
    global _UI_VERSION_CACHE
    if _UI_VERSION_CACHE is None:
        _UI_VERSION_CACHE = get_ui_version()
    return _UI_VERSION_CACHE
