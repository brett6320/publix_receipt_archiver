"""Central admin item-number map: CRUD, backfill/parse use it, and email
receipts get upgraded when a richer API version is collected."""
import csv
import json
import tempfile
from pathlib import Path

from publix_archiver import item_map, parse as P, config
from publix_archiver import fetch as F


def _with_map(tmp):
    config.ITEM_MAP_FILE = tmp / "item_number_map.json"


def test_add_remove_and_index():
    tmp = Path(tempfile.mkdtemp()); _with_map(tmp)
    orig = config.ITEM_MAP_FILE
    try:
        item_map.add("AB MILK U ORG 96OZ", "12345")
        assert len(item_map.entries()) == 1
        idx = item_map.index()
        assert idx[P._norm_desc("org 96oz u ab milk")] == "12345"   # normalized match
        # Adding the same description replaces (case-insensitive).
        item_map.add("ab milk u org 96oz", "99999")
        assert len(item_map.entries()) == 1 and item_map.entries()[0]["item_number"] == "99999"
        item_map.remove("AB MILK U ORG 96OZ")
        assert item_map.entries() == []
    finally:
        config.ITEM_MAP_FILE = orig


def _email_rec():
    return {"ReceiptId": "E1", "Source": "email", "FacilityId": 1, "FacilityName": "S",
            "TransactionDate": "2026-01-01T10:00:00", "GrandTotal": 5.49, "TaxAmount": 0.0,
            "Products": [], "ReceiptLineItems": [
                {"ItemCode": "", "ItemTypeDescription": "AB MILK U ORG 96OZ", "TaxCode": "F",
                 "ItemQty": 1, "ItemWeight": 0.0, "ItemPrice": 5.49, "ItemAmount": 5.49,
                 "SavingAmount": 0.0, "NetAmount": 5.49}]}


def test_parse_all_and_backfill_use_manual_map():
    tmp = Path(tempfile.mkdtemp()); raw, out = tmp / "raw", tmp / "out"
    raw.mkdir(); out.mkdir()
    orig = config.ITEM_MAP_FILE
    config.ITEM_MAP_FILE = tmp / "item_number_map.json"
    try:
        item_map.add("AB MILK U ORG 96OZ", "12345")
        (raw / "E1.json").write_text(json.dumps(_email_rec()))
        # parse_all fills it in the output...
        P.parse_all(raw_dir=raw, output_dir=out)
        row = list(csv.DictReader(open(out / "line_items.csv")))[0]
        assert row["item_number"] == "12345"
        # ...and backfill persists it into the raw record.
        assert P.backfill_item_numbers(raw)["filled"] == 1
        rec = json.loads((raw / "E1.json").read_text())
        assert rec["ReceiptLineItems"][0]["ItemCode"] == "12345"
    finally:
        config.ITEM_MAP_FILE = orig


def test_email_receipt_not_skipped_so_api_can_upgrade():
    # An email record on disk must NOT be in the fetch skip-set, so a richer API
    # detail with the same ReceiptId is fetched and overwrites (upgrades) it.
    tmp = Path(tempfile.mkdtemp())
    (tmp / "E1.json").write_text(json.dumps(_email_rec()))
    api_like = {"ReceiptId": "A1", "Products": [{"ItemName": "x"}],
                "ReceiptLineItems": [{"ItemCode": "1"}]}
    (tmp / "A1.json").write_text(json.dumps(api_like))
    seen = F._index_existing(tmp)
    assert F._safe_key(api_like) in seen      # API receipt is remembered (skipped)
    assert "E1" not in seen                    # email receipt is NOT — re-fetchable


if __name__ == "__main__":
    for fn in list(globals()):
        if fn.startswith("test_"):
            globals()[fn]()
    print("item-map tests OK")
