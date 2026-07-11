"""Admin-set display names for items.

Descriptions are normally unified to the most complete name seen for an item
number (see parse._display_descriptions). This module lets an admin pin a custom
display name for an item number that wins over that automatic choice — so a
poorly-abbreviated or wrongly-catalogued item can be renamed by hand.

Stored in data/item_names.json as {item_number: name}. Clearing a name (saving
an empty string) drops the override and the item falls back to the automatic
name on the next parse.
"""
from __future__ import annotations

import json

from . import config


def names() -> dict[str, str]:
    """item_number -> custom display name (only non-empty entries)."""
    try:
        data = json.loads(config.ITEM_NAMES_FILE.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip(): str(v).strip()
            for k, v in data.items() if str(k).strip() and str(v).strip()}


def get(item_number) -> str:
    """The custom name for an item number, or '' if none is set."""
    return names().get(str(item_number or "").strip(), "")


def _save(data: dict[str, str]) -> None:
    config.ensure_dirs()
    config.ITEM_NAMES_FILE.write_text(json.dumps(data, indent=2))


def put(item_number, name) -> dict:
    """Set (or, with an empty name, clear) the custom name for an item number."""
    item_number = str(item_number or "").strip()
    name = str(name or "").strip()
    if not item_number:
        raise ValueError("an item number is required")
    data = names()
    if name:
        data[item_number] = name
    else:
        data.pop(item_number, None)   # empty name resets to the automatic one
    _save(data)
    return {"item_number": item_number, "name": name}


def remove(item_number) -> None:
    data = names()
    if data.pop(str(item_number or "").strip(), None) is not None:
        _save(data)
