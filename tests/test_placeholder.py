"""Placeholder / import-delay behavior.

Publix serves very recent receipts as unpublished placeholders (line items but
no named products, every line "Normal Sale"). Those must not be kept: fetch
defers purchases younger than the delay window, and purge/refresh delete any
placeholder already on disk so it re-imports once the real detail publishes.
"""
import datetime as dt
import json
import tempfile
from pathlib import Path

from publix_archiver import fetch as fetch_mod
from publix_archiver import parse as parse_mod


def _placeholder_record():
    return {
        "ReceiptId": "PLACEHOLDER1",
        "TransactionKey": "K1",
        "FacilityId": 9999,
        "FacilityName": "Sample Plaza",
        "TransactionDate": "2020-01-15T12:00:00",
        "Products": [],
        "ReceiptLineItems": [
            {"ItemCode": "00000000001111", "ItemSeqNo": 1, "ItemQty": 1,
             "ItemPrice": 9.59, "ItemAmount": 9.59, "SavingAmount": 0.0,
             "NetAmount": 9.59, "ItemTypeDescription": "Normal Sale"},
        ],
    }


def _real_record():
    r = _placeholder_record()
    r["ReceiptId"] = "REAL1"
    r["Products"] = [{"ItemName": "Bananas", "UPC": "1111"}]
    return r


def test_is_placeholder():
    assert parse_mod.is_placeholder(_placeholder_record()) is True
    assert parse_mod.is_placeholder(_real_record()) is False
    assert parse_mod.is_placeholder({"ReceiptLineItems": []}) is False


def test_too_recent():
    now = dt.datetime(2026, 7, 10, 12, 0, 0)
    assert fetch_mod._too_recent("2026-07-10T09:00:00", now, 24) is True   # 3h old
    assert fetch_mod._too_recent("2026-07-08T09:00:00", now, 24) is False  # >2 days
    assert fetch_mod._too_recent("", now, 24) is False


def test_purge_placeholders():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "PLACEHOLDER1.json").write_text(json.dumps(_placeholder_record()))
    (tmp / "REAL1.json").write_text(json.dumps(_real_record()))
    removed = fetch_mod.purge_placeholders(tmp)
    assert removed == 1
    remaining = {f.stem for f in tmp.glob("*.json")}
    assert remaining == {"REAL1"}


if __name__ == "__main__":
    test_is_placeholder()
    test_too_recent()
    test_purge_placeholders()
    print("placeholder tests OK")
