"""Download every in-store purchase, saving one raw JSON file per receipt.

Publix serves purchase history as a paged list; each entry is fetched in full
(items, tenders, barcode, printed text) via its detail endpoint. Each receipt is
saved once, keyed by its ReceiptId, so re-running is idempotent and only new
purchases are fetched. Publix keeps ~180 days of history, so run this regularly
to accumulate an archive that outlives the retention window.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Optional

from . import config
from .api import PublixAPI, PublixAuthError, merge_detail
from .auth import Credentials
from .parse import is_placeholder


def _safe_key(receipt: dict) -> str:
    """A stable, filesystem-safe unique id for a receipt."""
    key = (
        receipt.get("ReceiptId")
        or receipt.get("TransactionKey")
        or "-".join(
            str(receipt.get(k, ""))
            for k in ("TransactionDate", "FacilityId", "SalesTransactionNumber")
        )
    )
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(key)) or "receipt"


def _too_recent(date_str, now: dt.datetime, delay_hours: int) -> bool:
    """True if a transaction is younger than the import-delay window.

    Publix populates itemized detail 24-48h after purchase, so fetching a very
    recent transaction returns nothing useful (or a placeholder). Defer those.
    """
    if not date_str:
        return False
    try:
        t = dt.datetime.fromisoformat(str(date_str)[:19])
    except ValueError:
        return False
    return t > now - dt.timedelta(hours=delay_hours)


def purge_placeholders(raw_dir: Path = config.RAW_DIR) -> int:
    """Delete saved receipts whose detail never fully published (all "Normal
    Sale", no named products). They'll be re-imported once Publix publishes the
    real itemized receipt. Returns the number removed."""
    removed = 0
    for f in list(raw_dir.glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        if is_placeholder(rec):
            f.unlink(missing_ok=True)
            removed += 1
    return removed


def refresh_one_receipt(
    creds: Credentials,
    transaction_key: str,
    receipt_key: str,
    raw_dir: Path = config.RAW_DIR,
) -> dict:
    """Re-fetch a single receipt's detail and overwrite it. If the detail is
    still an unpublished placeholder, delete the stored receipt so it is
    re-imported on a later run. Returns {status, key}."""
    config.ensure_dirs()
    with PublixAPI(creds) as api:
        detail = api.transaction_detail(transaction_key) if transaction_key else {}
    record = merge_detail({"TransactionKey": transaction_key}, detail)
    path = raw_dir / f"{receipt_key}.json"
    if is_placeholder(record):
        path.unlink(missing_ok=True)
        return {"status": "deferred", "key": receipt_key}
    path.write_text(json.dumps(record, indent=2))
    return {"status": "refreshed", "key": receipt_key}


def fetch_all_receipts(
    creds: Credentials,
    page_size: int = 25,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    skip_existing: bool = True,
    raw_dir: Path = config.RAW_DIR,
    progress_cb=None,
) -> dict:
    """Download all purchases, newest first. Returns a run summary.

    progress_cb(done, total, saved, label) is called after each receipt so a UI
    can show live progress. `skip_existing` avoids re-fetching detail for
    receipts already on disk (the common incremental case).
    """
    config.ensure_dirs()
    # Drop any previously-saved placeholders so they get re-imported now that
    # (hopefully) the real receipt has published.
    purged = purge_placeholders(raw_dir)
    if purged:
        print(f"  Purged {purged} placeholder receipt(s) awaiting real detail.")
    seen: set[str] = {f.stem for f in raw_dir.glob("*.json")}
    now = dt.datetime.now()
    delay_hours = config.IMPORT_DELAY_HOURS
    saved = 0
    done = 0
    deferred = 0
    total = None

    with PublixAPI(creds) as api:
        # First page tells us the total count for progress.
        try:
            first = api.purchases_page(1, page_size, from_date, to_date)
            total = int(first.get("TotalCount") or 0)
        except PublixAuthError:
            raise
        except Exception as ex:
            print(f"  ! could not load purchase list: {ex}")
            first = {}
            total = 0

        for txn in api.iter_transactions(page_size, from_date, to_date):
            done += 1
            key = _safe_key(txn)
            label = str(txn.get("TransactionDate") or "")[:10]

            # Too recent: Publix hasn't published the itemized detail yet. Defer.
            if _too_recent(txn.get("TransactionDate"), now, delay_hours):
                deferred += 1
                print(f"  {label}: deferred (< {delay_hours}h old — detail not ready)")
                if progress_cb:
                    try:
                        progress_cb(done, total, saved, label)
                    except Exception:
                        pass
                continue

            if skip_existing and key in seen:
                if progress_cb:
                    try:
                        progress_cb(done, total, saved, label)
                    except Exception:
                        pass
                continue

            tkey = txn.get("TransactionKey")
            try:
                detail = api.transaction_detail(tkey) if tkey else {}
            except PublixAuthError:
                raise
            except Exception as ex:
                print(f"  ! detail failed for {label} ({key}): {ex}")
                detail = {}

            record = merge_detail(txn, detail)
            # Detail came back as an unpublished placeholder (all "Normal Sale",
            # no named products) — don't persist it; retry on a later run.
            if is_placeholder(record):
                deferred += 1
                print(f"  {label}: deferred ({key} detail not published yet)")
                if progress_cb:
                    try:
                        progress_cb(done, total, saved, label)
                    except Exception:
                        pass
                continue

            (raw_dir / f"{key}.json").write_text(json.dumps(record, indent=2))
            seen.add(key)
            saved += 1
            print(f"  {label}: saved {key} "
                  f"({record.get('ItemCount', '?')} items, ${record.get('Amount', '?')})")
            if progress_cb:
                try:
                    progress_cb(done, total, saved, label)
                except Exception:
                    pass

    summary = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "transactions_seen": done,
        "receipts_saved_this_run": saved,
        "deferred_not_ready": deferred,
        "placeholders_purged": purged,
        "total_receipts_on_disk": len(list(raw_dir.glob("*.json"))),
        "retention_note": (
            f"Publix keeps ~{config.RETENTION_DAYS} days of history; run regularly. "
            f"Purchases < {delay_hours}h old are deferred until their detail publishes."),
        "raw_dir": str(raw_dir),
    }
    (config.DATA_DIR / "fetch_summary.json").write_text(json.dumps(summary, indent=2))
    return summary
