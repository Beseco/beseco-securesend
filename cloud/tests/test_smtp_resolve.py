"""Unit tests for SMTP fallback resolution."""

from __future__ import annotations

import os

import pytest

from services.smtp_resolve import is_usable_smtp, normalize_smtp, resolve_smtp_with_fallback


def test_normalize_fills_from_addr_from_user() -> None:
    n = normalize_smtp(
        {"host": "smtp.example.com", "port": "587", "user": "a@example.com"}
    )
    assert n["port"] == 587
    assert n["from_addr"] == "a@example.com"


def test_is_usable_requires_host_and_from() -> None:
    assert is_usable_smtp({"host": "h", "from_addr": "a@b.c"})
    assert not is_usable_smtp({"host": "", "from_addr": "a@b.c"})
    assert not is_usable_smtp({"host": "h", "from_addr": ""})


def test_use_own_smtp_false_skips_org_even_if_smtp_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    org = {"use_own_smtp": False, "smtp": {"host": "org-smtp", "from_addr": "o@x.de"}}
    cfg, src = resolve_smtp_with_fallback(org, None)
    assert cfg is None
    assert src == "none"


def test_reseller_fallback_when_org_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    org = {"use_own_smtp": False, "smtp": None}
    reseller = {
        "smtp": {
            "host": "r.example.com",
            "port": 587,
            "from_addr": "r@example.com",
        }
    }
    cfg, src = resolve_smtp_with_fallback(org, reseller)
    assert src == "reseller"
    assert cfg is not None
    assert cfg["host"] == "r.example.com"


def test_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "env.example.com")
    monkeypatch.setenv("MAIL_FROM", "env@example.com")
    org = {"use_own_smtp": False}
    cfg, src = resolve_smtp_with_fallback(org, None)
    assert src == "env"
    assert cfg is not None
    assert cfg["host"] == "env.example.com"
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("MAIL_FROM", raising=False)
