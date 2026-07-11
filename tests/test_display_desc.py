"""Description unification: once an item number is known, every line adopts the
most complete description for that number, while keeping its own register text
in original_description (and still searchable)."""
import csv
import json
import tempfile
from pathlib import Path

from publix_archiver import parse as P
from publix_archiver import web


def test_desc_score_prefers_fuller_name():
    full = "Jennie-O 99%/1% Fresh Ground Turkey Breast (16 oz (1 lb))"
    abbr = "J/O 99% Gr Turkey Breas"
    assert P._desc_score(full) > P._desc_score(abbr)


def test_display_descriptions_picks_most_complete():
    items = [
        {"item_number": "42", "description": "J/O 99% Gr Turkey Breas", "order_type": "store"},
        {"item_number": "42", "description": "Jennie-O 99%/1% Fresh Ground Turkey Breast (16 oz (1 lb))", "order_type": "store"},
        {"item_number": "42", "description": "Savings", "order_type": "discount"},  # ignored
    ]
    disp = P._display_descriptions(items)
    assert disp["42"] == "Jennie-O 99%/1% Fresh Ground Turkey Breast (16 oz (1 lb))"


def _api_rec():
    return {"ReceiptId": "APITURK", "Source": "publix", "FacilityId": 1808,
            "FacilityName": "Sample", "TransactionDate": "2026-07-02T10:00:00",
            "GrandTotal": 6.29, "TaxAmount": 0.0,
            "Products": [{"ItemName": "Jennie-O 99%/1% Fresh Ground Turkey Breast",
                          "SizeDescription": "16 oz (1 lb)", "UPC": "4222230204"}],
            "ReceiptLineItems": [{"ItemCode": "00004222230204", "ItemQty": 1, "ItemPrice": 6.29,
                                  "ItemAmount": 6.29, "SavingAmount": 0.0, "NetAmount": 6.29}]}


def _email_rec():
    return {"ReceiptId": "EMAILTURK", "Source": "email", "FacilityId": 1808,
            "FacilityName": "Sample", "TransactionDate": "2026-07-10T10:00:00",
            "GrandTotal": 6.29, "TaxAmount": 0.0, "Products": [],
            "ReceiptLineItems": [{"ItemCode": "00004222230204", "ItemTypeDescription": "J/O 99% Gr Turkey Breas",
                                  "TaxCode": "F", "ItemQty": 1, "ItemPrice": 6.29, "ItemAmount": 6.29,
                                  "SavingAmount": 0.0, "NetAmount": 6.29}]}


def test_parse_all_rewrites_and_preserves_original():
    tmp = Path(tempfile.mkdtemp()); raw, out = tmp / "raw", tmp / "out"
    raw.mkdir(); out.mkdir()
    (raw / "api.json").write_text(json.dumps(_api_rec()))
    (raw / "email.json").write_text(json.dumps(_email_rec()))
    P.parse_all(raw_dir=raw, output_dir=out)
    rows = [r for r in csv.DictReader(open(out / "line_items.csv"))
            if r["item_number"] == "4222230204" and r["order_type"] != "discount"]
    full = "Jennie-O 99%/1% Fresh Ground Turkey Breast (16 oz (1 lb))"
    assert rows and all(r["description"] == full for r in rows)          # all show the full name
    originals = {r["original_description"] for r in rows}
    assert "J/O 99% Gr Turkey Breas" in originals                        # register text kept
    # the abbreviated line is still findable by its ORIGINAL text
    for r in rows:                       # coerce numerics as _load_rows() does
        r["amount"] = float(r.get("amount") or 0)
    hit = web._search(rows, {"q": ["gr turkey breas"]})
    assert hit["count"] >= 1


if __name__ == "__main__":
    for fn in list(globals()):
        if fn.startswith("test_"):
            globals()[fn]()
    print("display-desc tests OK")
