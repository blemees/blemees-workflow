"""Capability tier cache + resolution tests (ADR-0005, #70)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from workflow.core.capability_cache import CacheEntry, CapabilityCache


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Point the cache at a throwaway XDG dir so tests never touch the real one."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------- #
# CacheEntry / CapabilityCache


def test_set_get_roundtrip() -> None:
    cache = CapabilityCache()
    cache.set("github.com", "blemees", "native")
    entry = cache.get("github.com", "blemees")
    assert entry is not None
    assert entry.tier == "native"
    assert entry.manual is False


def test_set_rejects_invalid_tier() -> None:
    cache = CapabilityCache()
    with pytest.raises(ValueError, match="tier must be one of"):
        cache.set("github.com", "blemees", "bogus")  # type: ignore[arg-type]


def test_save_load_roundtrip() -> None:
    cache = CapabilityCache()
    cache.set("github.com", "blemees", "native")
    cache.set("ghe.acme.com", "eng", "label", manual=True)
    cache.save()

    reloaded = CapabilityCache.load()
    a = reloaded.get("github.com", "blemees")
    b = reloaded.get("ghe.acme.com", "eng")
    assert a is not None and a.tier == "native" and a.manual is False
    assert b is not None and b.tier == "label" and b.manual is True


def test_save_writes_tier_key(_isolated_cache) -> None:
    cache = CapabilityCache()
    cache.set("github.com", "blemees", "native")
    cache.save()
    data = json.loads((_isolated_cache / "blemees-workflow" / "capabilities.json").read_text())
    assert data["github.com/blemees"]["tier"] == "native"
    assert "encoding" not in data["github.com/blemees"]


def test_load_reads_legacy_encoding_key(_isolated_cache) -> None:
    """A pre-existing cache written with the historical `encoding` key still
    loads (esp. so manual pins survive the rename)."""
    cfg = _isolated_cache / "blemees-workflow"
    cfg.mkdir(parents=True)
    (cfg / "capabilities.json").write_text(
        json.dumps(
            {
                "github.com/blemees": {
                    "encoding": "native",
                    "checked_at": "2026-05-01T00:00:00+00:00",
                    "manual": True,
                }
            }
        )
    )
    entry = CapabilityCache.load().get("github.com", "blemees")
    assert entry is not None
    assert entry.tier == "native"
    assert entry.manual is True


def test_load_skips_invalid_tier(_isolated_cache) -> None:
    cfg = _isolated_cache / "blemees-workflow"
    cfg.mkdir(parents=True)
    (cfg / "capabilities.json").write_text(
        json.dumps({"github.com/blemees": {"tier": "bogus", "checked_at": "x"}})
    )
    assert CapabilityCache.load().get("github.com", "blemees") is None


def test_is_expired() -> None:
    fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
    assert CacheEntry(tier="native", checked_at=fresh).is_expired() is False
    assert CacheEntry(tier="native", checked_at=old).is_expired() is True
    # Manual entries never expire, even when old.
    assert CacheEntry(tier="native", checked_at=old, manual=True).is_expired() is False


def test_clear_and_clear_entry() -> None:
    cache = CapabilityCache()
    cache.set("github.com", "a", "native")
    cache.set("github.com", "b", "label")
    assert cache.clear_entry("github.com", "a") is True
    assert cache.clear_entry("github.com", "a") is False  # already gone
    assert cache.get("github.com", "b") is not None
    cache.clear()
    assert cache.entries == {}


# --------------------------------------------------------------------------- #
# _resolve_tier — probe → cache → fallback, and manual precedence


def _backend(types, *, host=None, repo="blemees/blemees-workflow"):
    """A minimal fake backend: records list_issue_types calls."""
    calls = {"n": 0}

    def list_issue_types(owner):
        calls["n"] += 1
        return types

    return SimpleNamespace(host=host, repo=repo, list_issue_types=list_issue_types, _calls=calls)


def test_resolve_tier_probes_native_when_types_present() -> None:
    from workflow.cli import _resolve_tier

    backend = _backend(["Bug", "Feature"])
    assert _resolve_tier({}, backend) == "native"
    # And the probe result was persisted.
    assert CapabilityCache.load().get("github.com", "blemees").tier == "native"


@pytest.mark.parametrize("types", [None, []])
def test_resolve_tier_falls_back_to_label(types) -> None:
    """No types (feature absent / no permission) or an empty list → label."""
    from workflow.cli import _resolve_tier

    assert _resolve_tier({}, _backend(types)) == "label"


def test_resolve_tier_uses_cache_without_reprobing() -> None:
    from workflow.cli import _resolve_tier

    seed = CapabilityCache()
    seed.set("github.com", "blemees", "native", manual=False)
    seed.save()

    backend = _backend(None)  # would resolve to label if probed
    assert _resolve_tier({}, backend) == "native"  # cache hit wins
    assert backend._calls["n"] == 0  # never probed


def test_resolve_tier_manual_entry_takes_precedence() -> None:
    from workflow.cli import _resolve_tier

    seed = CapabilityCache()
    seed.set("github.com", "blemees", "native", manual=True)
    seed.save()

    backend = _backend(None)
    # Manual pin is honored even though a probe would say label.
    assert _resolve_tier({}, backend) == "native"
    assert backend._calls["n"] == 0
    # force_probe overrides the manual entry.
    assert _resolve_tier({}, backend, force_probe=True) == "label"
    assert backend._calls["n"] == 1


def test_resolve_tier_persist_false_does_not_write_cache() -> None:
    from workflow.cli import _resolve_tier

    backend = _backend(["Bug"])
    assert _resolve_tier({}, backend, persist=False) == "native"
    # Nothing written — a later load sees no entry.
    assert CapabilityCache.load().get("github.com", "blemees") is None
