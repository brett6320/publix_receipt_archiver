"""Mix-and-match BOGO across two different items must net correctly.

A free BOGO item has NetAmount 0 but a printed ItemAmount; the register
deduction is ItemAmount - NetAmount (not SavingAmount, which is savings vs the
regular price). The item shows its printed price with a discount that nets it to
$0 — never a bare $0.00 row, and never a double-subtracted total.
"""
import csv
import json
import tempfile
from pathlib import Path

from publix_archiver import parse as p


def _bogo_record():
    # Buy Thai, get the (different) Cilantro Lime free.
    thai = {"ItemCode": "1", "ItemQty": 1, "ItemPrice": 11.19, "ItemAmount": 11.19,
            "SavingAmount": 0.0, "NetAmount": 11.19, "ItemTypeDescription": "Normal Sale"}
    cilantro = {"ItemCode": "2", "ItemQty": 1, "ItemPrice": 11.19, "ItemAmount": 11.19,
                "SavingAmount": 11.19, "NetAmount": 0.0, "ItemTypeDescription": "Normal Sale"}
    return {"ReceiptId": "BOGO1", "FacilityId": 1808, "FacilityName": "Gandy Shopping Center",
            "TransactionDate": "2026-05-30T17:13:27", "GrandTotal": 11.19, "TaxAmount": 0.0,
            "Products": [{"ItemName": "Kevin's Thai Coconut Chicken", "UPC": "1"},
                         {"ItemName": "Kevin's Cilantro Lime Chicken", "UPC": "2"}],
            "ReceiptLineItems": [thai, cilantro]}


def test_line_amounts():
    thai, cilantro = _bogo_record()["ReceiptLineItems"]
    assert p.line_amounts(thai) == (11.19, 0.0, 11.19)      # paid in full
    assert p.line_amounts(cilantro) == (11.19, 11.19, 0.0)  # printed 11.19, discount 11.19, paid 0


def test_bogo_pipeline_nets_to_paid():
    rec = _bogo_record()
    rows = list(p._iter_line_items(rec))
    # Two product rows at their printed price + one discount row for the free item.
    prod = [r for r in rows if r["order_type"] != "discount"]
    disc = [r for r in rows if r["order_type"] == "discount"]
    assert [r["amount"] for r in prod] == [11.19, 11.19]     # NOT 0.00 for the free one
    assert len(disc) == 1 and disc[0]["amount"] == -11.19
    # All rows net to the amount actually paid (== GrandTotal).
    assert round(sum(r["amount"] for r in rows), 2) == 11.19

    # Aggregation: Cilantro counts as one purchase, $0 spent, regular unit price.
    tmp = Path(tempfile.mkdtemp())
    raw, out = tmp / "raw", tmp / "out"
    raw.mkdir(); out.mkdir()
    (raw / "BOGO1.json").write_text(json.dumps(rec))
    p.parse_all(raw_dir=raw, output_dir=out)
    agg = {r["item_number"]: r for r in csv.DictReader(open(out / "items_deduped.csv"))}
    assert agg["2"]["times_purchased"] == "1"
    assert float(agg["2"]["total_spent"]) == 0.0
    assert float(agg["2"]["last_price"]) == 11.19
    # Receipt savings == Publix "Your Savings" == sum of SavingAmount.
    assert p.receipt_totals(rec)["instant_savings"] == 11.19


if __name__ == "__main__":
    test_line_amounts()
    test_bogo_pipeline_nets_to_paid()
    print("bogo tests OK")
