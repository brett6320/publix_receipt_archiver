"""Fill missing item numbers (email receipts) by matching descriptions to known
item numbers from other receipts."""
import csv
import json
import tempfile
from pathlib import Path

from publix_archiver import parse as P


def test_norm_desc_token_set():
    # Word order and case don't matter; size tokens are dropped.
    assert P._norm_desc("TOMATO BEEFSTEAK") == P._norm_desc("Beefsteak Tomato")
    assert P._norm_desc("AB MILK U ORG 96OZ") == P._norm_desc("org u milk ab")  # 96oz dropped
    # Single-word items are kept (BANANAS, MILK)...
    assert P._norm_desc("BANANAS") == "bananas"
    assert P._norm_desc("MILK") == "milk"
    # ...and don't collide with a longer name that contains the word.
    assert P._norm_desc("BANANAS") != P._norm_desc("Organic Bananas")
    assert P._norm_desc("16 OZ") is None           # only size/number -> nothing to match


def test_build_index_unambiguous():
    items = [
        {"item_number": "4799", "description": "Beefsteak Tomato", "order_type": "store"},
        {"item_number": "", "description": "TOMATO BEEFSTEAK", "order_type": "store"},
        {"item_number": "1", "description": "Same Name", "order_type": "store"},
        {"item_number": "2", "description": "Same Name", "order_type": "store"},  # conflict
    ]
    idx = P.build_number_index(items)
    assert idx[P._norm_desc("TOMATO BEEFSTEAK")] == "4799"
    assert P._norm_desc("Same Name") not in idx    # ambiguous → dropped


def _api_rec():
    return {"ReceiptId": "API1", "FacilityId": 1, "FacilityName": "S",
            "TransactionDate": "2026-01-02T10:00:00", "GrandTotal": 2.99, "TaxAmount": 0.0,
            "Products": [{"ItemName": "Beefsteak Tomato", "UPC": "4799"}],
            "ReceiptLineItems": [{"ItemCode": "00000000004799", "ItemQty": 1,
                                  "ItemPrice": 2.99, "ItemAmount": 2.99,
                                  "SavingAmount": 0.0, "NetAmount": 2.99}]}


def _email_rec():
    return {"ReceiptId": "EMAIL1", "Source": "email", "FacilityId": 1, "FacilityName": "S",
            "TransactionDate": "2026-01-01T10:00:00", "GrandTotal": 2.99, "TaxAmount": 0.0,
            "Products": [], "ReceiptLineItems": [
                {"ItemCode": "", "ItemTypeDescription": "TOMATO BEEFSTEAK", "TaxCode": "F",
                 "ItemQty": 1, "ItemWeight": 0.0, "ItemPrice": 2.99, "ItemAmount": 2.99,
                 "SavingAmount": 0.0, "NetAmount": 2.99}]}


def test_parse_all_fills_email_item_number():
    tmp = Path(tempfile.mkdtemp()); raw, out = tmp / "raw", tmp / "out"
    raw.mkdir(); out.mkdir()
    (raw / "API1.json").write_text(json.dumps(_api_rec()))
    (raw / "EMAIL1.json").write_text(json.dumps(_email_rec()))
    P.parse_all(raw_dir=raw, output_dir=out)
    rows = list(csv.DictReader(open(out / "line_items.csv")))
    email_row = [r for r in rows if r["receipt_id"] == "EMAIL1"][0]
    assert email_row["item_number"] == "4799"   # filled from the API item's number


def test_backfill_persists_into_raw():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "API1.json").write_text(json.dumps(_api_rec()))
    (tmp / "EMAIL1.json").write_text(json.dumps(_email_rec()))
    result = P.backfill_item_numbers(tmp)
    assert result["filled"] == 1
    rec = json.loads((tmp / "EMAIL1.json").read_text())
    assert rec["ReceiptLineItems"][0]["ItemCode"] == "4799"   # persisted


if __name__ == "__main__":
    for fn in list(globals()):
        if fn.startswith("test_"):
            globals()[fn]()
    print("item-number tests OK")
