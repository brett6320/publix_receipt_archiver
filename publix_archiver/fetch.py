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
    seen: set[str] = {f.stem for f in raw_dir.glob("*.json")}
    saved = 0
    done = 0
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
        "total_receipts_on_disk": len(list(raw_dir.glob("*.json"))),
        "retention_note": (
            f"Publix keeps ~{config.RETENTION_DAYS} days of history; run regularly."),
        "raw_dir": str(raw_dir),
    }
    (config.DATA_DIR / "fetch_summary.json").write_text(json.dumps(summary, indent=2))
    return summary
