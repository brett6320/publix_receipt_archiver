"""Central, admin-curated description → item-number map.

Receipts that don't carry item numbers (email receipts) can be filled from this
map by matching a line's description. Stored in data/item_number_map.json as a
list of {description, item_number} entries; matching is by the same normalized
token-set key the parser uses, so word order / size tokens don't matter.
"""
from __future__ import annotations

import json

from . import config


def entries() -> list[dict]:
    """All map entries, newest first."""
    try:
        data = json.loads(config.ITEM_MAP_FILE.read_text())
    except Exception:
        return []
    if isinstance(data, dict):  # tolerate a legacy {description: number} shape
        data = [{"description": k, "item_number": v} for k, v in data.items()]
    return [e for e in data if isinstance(e, dict)]


def _save(data: list[dict]) -> None:
    config.ensure_dirs()
    config.ITEM_MAP_FILE.write_text(json.dumps(data, indent=2))


def add(description, item_number) -> dict:
    """Add or replace a mapping (case-insensitive by description)."""
    description = str(description or "").strip()
    item_number = str(item_number or "").strip()
    if not description or not item_number:
        raise ValueError("both a description and an item number are required")
    data = [e for e in entries()
            if str(e.get("description", "")).strip().lower() != description.lower()]
    data.insert(0, {"description": description, "item_number": item_number})
    _save(data)
    return {"description": description, "item_number": item_number}


def remove(description) -> None:
    key = str(description or "").strip().lower()
    _save([e for e in entries()
           if str(e.get("description", "")).strip().lower() != key])


def index() -> dict[str, str]:
    """Normalized-description → item_number, for backfill/matching."""
    from .parse import _norm_desc  # lazy to avoid an import cycle
    out: dict[str, str] = {}
    for e in entries():
        key = _norm_desc(e.get("description"))
        num = str(e.get("item_number") or "").strip()
        if key and num:
            out[key] = num
    return out
