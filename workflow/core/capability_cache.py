"""Capability cache — per-(host, owner) record of how to encode issue types.

The tool needs to know, for each tracker org, whether to encode issue type
via the backend's native field (e.g., GitHub Issue Types) or as a `type:<id>`
label. The decision is determined by a one-time probe and cached to avoid
re-checking on every operation.

Cache file: `$XDG_CONFIG_HOME/blemees-workflow/capabilities.json` (defaults
to `~/.config/blemees-workflow/capabilities.json`).

Schema:

```json
{
  "github.com/blemees": {
    "encoding": "native",
    "checked_at": "2026-05-15T10:30:00+00:00",
    "manual": false
  },
  "ghe.acme.com/engineering": {
    "encoding": "label",
    "checked_at": "2026-05-15T10:35:00+00:00",
    "manual": true
  }
}
```

`encoding` is `"native"` or `"label"`. `manual` indicates the entry was set
explicitly via `workflow capabilities --set-encoding` and should not be
overwritten by automatic probes (TTL refreshes skip manual entries; only
`--clear` or another explicit `--set-encoding` overrides them).

TTL: entries older than 30 days are considered stale and re-probed on next
access unless `manual=True`.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


_TTL_DAYS = 30
ENCODINGS = ("native", "label")
Encoding = Literal["native", "label"]


def _cache_dir() -> Path:
    """Resolve the cache directory, honoring XDG_CONFIG_HOME when set."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg) / "blemees-workflow"
    return Path.home() / ".config" / "blemees-workflow"


def _cache_file() -> Path:
    return _cache_dir() / "capabilities.json"


@dataclass
class CacheEntry:
    encoding: Encoding
    checked_at: str  # ISO-8601 with timezone
    manual: bool = False

    def is_expired(self, ttl_days: int = _TTL_DAYS) -> bool:
        """Per-entry expiry. Manual entries are never expired by this check."""
        if self.manual:
            return False
        try:
            when = datetime.fromisoformat(self.checked_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return True  # corrupt → treat as expired
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - when > timedelta(days=ttl_days)


@dataclass
class CapabilityCache:
    """In-memory + on-disk cache of tracker capabilities."""

    entries: dict[str, CacheEntry] = field(default_factory=dict)
    path: Path = field(default_factory=_cache_file)

    @classmethod
    def load(cls) -> CapabilityCache:
        """Load the cache from disk, returning an empty cache on first use."""
        path = _cache_file()
        cache = cls(path=path)
        if not path.exists():
            return cache
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load capability cache from %s: %s", path, exc)
            return cache
        if not isinstance(data, dict):
            logger.warning("Capability cache at %s is malformed; ignoring.", path)
            return cache
        for key, raw in data.items():
            if not isinstance(raw, dict):
                continue
            encoding = raw.get("encoding")
            if encoding not in ENCODINGS:
                continue
            checked_at = raw.get("checked_at", "")
            manual = bool(raw.get("manual", False))
            cache.entries[key] = CacheEntry(
                encoding=encoding,
                checked_at=str(checked_at),
                manual=manual,
            )
        return cache

    def save(self) -> None:
        """Atomically write the cache to disk, creating parents as needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: {
                "encoding": entry.encoding,
                "checked_at": entry.checked_at,
                "manual": entry.manual,
            }
            for key, entry in self.entries.items()
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def key(host: str, owner: str) -> str:
        return f"{host}/{owner}"

    def get(self, host: str, owner: str) -> CacheEntry | None:
        return self.entries.get(self.key(host, owner))

    def set(
        self,
        host: str,
        owner: str,
        encoding: Encoding,
        *,
        manual: bool = False,
    ) -> CacheEntry:
        if encoding not in ENCODINGS:
            raise ValueError(f"encoding must be one of {ENCODINGS}; got {encoding!r}")
        entry = CacheEntry(
            encoding=encoding,
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            manual=manual,
        )
        self.entries[self.key(host, owner)] = entry
        return entry

    def clear(self) -> None:
        self.entries.clear()

    def clear_entry(self, host: str, owner: str) -> bool:
        key = self.key(host, owner)
        if key in self.entries:
            del self.entries[key]
            return True
        return False
