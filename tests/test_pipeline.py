"""End-to-end test: merge fixture detail+list -> raw -> parse -> assert CSVs.

Uses the real Publix fixtures (tests/fixtures/publix_detail.json + publix_list.json)
so we exercise the actual field names and reconcile against GrandTotal.
"""
import csv
import json
import shutil
import tempfile
from pathlib import Path

from publix_archiver import api as api_mod
from publix_archiver import parse as parse_mod

FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text())


def run(tmp: Path):
    raw = tmp / "raw"; out = tmp / "out"
    for d in (raw, out):
        d.mkdir(parents=True, exist_ok=True)

    detail = _load("publix_detail.json")
    listing = _load("publix_list.json")
    # The list summary that matches the detail record (same ReceiptId).
    txn = next(t for t in listing["CurrentTransactions"]
               if t.get("SalesTransactionNumber") == detail.get("SalesTransactionNumber"))

    record = api_mod.merge_detail(txn, detail)
    key = str(record["ReceiptId"])
    (raw / f"{key}.json").write_text(json.dumps(record))

    summary = parse_mod.parse_all(raw_dir=raw, output_dir=out)
    print(json.dumps(summary, indent=2))

    assert summary["receipts_parsed"] == 1, summary
    # 2 line items, one of which carries a SavingAmount -> +1 discount row = 3.
    assert summary["line_items"] == 3, summary

    with (out / "line_items.csv").open() as fh:
        li = list(csv.DictReader(fh))
    assert len(li) == 3, li
    # Store name (renamed CSV column: `store`, not `warehouse`).
    assert all(row["store"] == "Sample Plaza" for row in li), li
    assert all(row["store_number"] == "9999" for row in li), li

    # Totals reconcile to the receipt's GrandTotal.
    with (out / "receipts.csv").open() as fh:
        rec = list(csv.DictReader(fh))
    assert len(rec) == 1, rec
    assert float(rec[0]["total"]) == 4.17, rec[0]
    assert rec[0]["store"] == "Sample Plaza", rec[0]
    assert int(rec[0]["items"]) == 2, rec[0]

    # All line amounts (printed items minus discount rows) net to the total.
    net = round(sum(float(r["amount"]) for r in li), 2)
    assert net == 4.17, net

    print("\nALL PIPELINE ASSERTIONS PASSED")


def test_pipeline():
    d = Path(tempfile.mkdtemp())
    try:
        run(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_pipeline()
