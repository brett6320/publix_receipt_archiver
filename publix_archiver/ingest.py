"""Import Publix receipt JSON into the raw archive.

This is the API-free bulk path. The browser-console snippet
(browser/publix_fetch_receipts.js) downloads a single JSON file of merged
receipt records; this module reads that file (or a directory of files) and saves
each record as data/raw/<key>.json using the same key rule as `fetch`, so the
data flows through `parse`, the web UI, PDF, and Markdown unchanged.

Accepted JSON shapes:
  - a single detail/merged record (an object with a ReceiptId/TransactionKey)
  - a list of such records
  - an envelope: {"receipts": [...]}
  - Publix's own list envelope: {"CurrentTransactions": [...]}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config


def _safe_key(receipt: dict) -> str:
    """A stable, filesystem-safe unique id for a receipt (matches fetch._safe_key)."""
    key = (
        receipt.get("ReceiptId")
        or receipt.get("TransactionKey")
        or "-".join(
            str(receipt.get(k, ""))
            for k in ("TransactionDate", "FacilityId", "SalesTransactionNumber")
        )
    )
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(key)) or "receipt"


def save_receipt(receipt: dict, raw_dir: Path = config.RAW_DIR) -> Path:
    """Persist one receipt record to data/raw/<key>.json. Returns the path."""
    config.ensure_dirs()
    receipt.setdefault("source", "json-import")
    out = raw_dir / f"{_safe_key(receipt)}.json"
    out.write_text(json.dumps(receipt, indent=2))
    return out


def _looks_like_receipt(obj) -> bool:
    return isinstance(obj, dict) and bool(
        obj.get("ReceiptId") or obj.get("TransactionKey")
        or obj.get("ReceiptLineItems") or obj.get("Products")
    )


def _find_receipts(blob) -> list[dict]:
    """Extract every receipt-like record from an arbitrary JSON blob."""
    if isinstance(blob, list):
        return [r for r in blob if _looks_like_receipt(r)]
    if isinstance(blob, dict):
        for key in ("receipts", "CurrentTransactions", "Receipts"):
            val = blob.get(key)
            if isinstance(val, list):
                return [r for r in val if _looks_like_receipt(r)]
        if _looks_like_receipt(blob):
            return [blob]
    return []


def _ingest_json_file(f: Path, raw_dir: Path) -> int:
    blob = json.loads(f.read_text())
    receipts = _find_receipts(blob)
    saved = 0
    for rec in receipts:
        save_receipt(rec, raw_dir)
        saved += 1
    print(f"  {f.name} → {saved} receipt(s)")
    return saved


def ingest_paths(paths: list[Path], raw_dir: Path = config.RAW_DIR) -> dict:
    """Ingest .json files (or directories of them) into raw receipt JSON."""
    config.ensure_dirs()
    files: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files += sorted(p.glob("*.json"))
        else:
            files.append(p)

    saved = 0
    for f in files:
        if f.suffix.lower() != ".json":
            continue
        try:
            saved += _ingest_json_file(f, raw_dir)
        except Exception as ex:
            print(f"  ! failed to import {f.name}: {ex}")
    return {"ingested": saved, "raw_dir": str(raw_dir)}
