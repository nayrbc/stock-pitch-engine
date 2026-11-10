"""Tiny JSON file cache keyed by name + date, stored under cache/."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

CACHE_DIR = Path(os.environ.get("PITCH_CACHE_DIR", Path(__file__).resolve().parents[2] / "cache"))


def cache_path(namespace: str, key: str, day: date | None = None) -> Path:
    day = day or date.today()
    safe_key = key.upper().replace("/", "_")
    return CACHE_DIR / namespace / f"{safe_key}_{day.isoformat()}.json"


def cached_json(namespace: str, key: str, fetch: Callable[[], Any], verbose: bool = True) -> Any:
    """Return today's cached JSON for (namespace, key), or call fetch() and cache it."""
    path = cache_path(namespace, key)
    if path.exists():
        if verbose:
            print(f"[cache hit] {path.relative_to(CACHE_DIR.parent)}", file=sys.stderr)
        return json.loads(path.read_text())
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)
    if verbose:
        print(f"[cache miss] fetched -> {path.relative_to(CACHE_DIR.parent)}", file=sys.stderr)
    return data
