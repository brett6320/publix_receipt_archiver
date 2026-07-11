"""Duplicate re-import updates a stored receipt when the parsed output differs
(backfilling mangled data), stays idempotent otherwise, preserves enrichments,
and never downgrades a richer API record with an email one."""
import json
import tempfile
from pathlib import Path

from publix_archiver import email_ingest as ei


def _email_rec(store="Gandy Shopping Center", text="clean receipt"):
    return {"ReceiptId": "R1", "Source": "email", "FacilityId": 1808,
            "FacilityName": store, "TransactionDate": "2026-01-04T17:53:00",
            "GrandTotal": 5.0, "TaxAmount": 0.0, "ReceiptText": text, "Products": [],
            "ReceiptLineItems": [{"ItemCode": "", "ItemTypeDescription": "DONUT",
                                  "ItemAmount": 5.0, "NetAmount": 5.0}], "ItemCount": 1}


def _key(rec):
    return ei._safe_key_for(rec)


def _existing(raw):
    return {f.stem for f in raw.glob("*.json")}


def test_new_then_identical_then_changed():
    raw = Path(tempfile.mkdtemp())
    rec = _email_rec()
    assert ei._store_receipt(raw, rec, _existing(raw)) == "saved"
    # identical re-import → skipped (idempotent)
    assert ei._store_receipt(raw, _email_rec(), _existing(raw)) == "skipped"
    # mangled stored → clean re-import updates and refreshes content
    mangled = _email_rec(store="X-Pm-Transfer-Encryption: TLSv1.", text="cruft...")
    (raw / f"{_key(mangled)}.json").write_text(json.dumps(mangled))
    assert ei._store_receipt(raw, _email_rec(), _existing(raw)) == "updated"
    stored = json.loads((raw / f"{_key(rec)}.json").read_text())
    assert stored["FacilityName"] == "Gandy Shopping Center"
    assert stored["ReceiptText"] == "clean receipt"


def test_preserves_backfilled_item_numbers_and_catalog():
    raw = Path(tempfile.mkdtemp())
    enriched = _email_rec()
    enriched["ReceiptLineItems"][0]["ItemCode"] = "4011"        # backfilled number
    enriched["Products"] = [{"ItemName": "Donut", "UPC": "4011"}]  # catalog
    (raw / f"{_key(enriched)}.json").write_text(json.dumps(enriched))
    # re-import the plain parse (no ItemCode/Products) → enrichments preserved,
    # nothing else changed → skipped (idempotent, not a needless rewrite)
    assert ei._store_receipt(raw, _email_rec(), _existing(raw)) == "skipped"
    stored = json.loads((raw / f"{_key(enriched)}.json").read_text())
    assert stored["ReceiptLineItems"][0]["ItemCode"] == "4011"
    assert stored["Products"]


def test_email_does_not_downgrade_api_record():
    raw = Path(tempfile.mkdtemp())
    api = {"ReceiptId": "R1", "Source": "publix", "FacilityName": "Store",
           "GrandTotal": 5.0, "Products": [{"ItemName": "Donut", "UPC": "4011"}],
           "ReceiptLineItems": [{"ItemCode": "00000000004011"}]}
    (raw / f"{_key(api)}.json").write_text(json.dumps(api))
    assert ei._store_receipt(raw, _email_rec(), _existing(raw)) == "skipped"
    stored = json.loads((raw / f"{_key(api)}.json").read_text())
    assert stored["Source"] == "publix" and stored["Products"]   # untouched


if __name__ == "__main__":
    for fn in list(globals()):
        if fn.startswith("test_"):
            globals()[fn]()
    print("ingest-update tests OK")
