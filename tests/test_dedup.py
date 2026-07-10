"""Dedup: the same ReceiptId ingested twice collapses to a single receipt.

'Duplicate' means the exact same receipt saved more than once (e.g. fetched via
API and again via the browser snippet) — NOT multiple purchases of the same item.
"""
import csv
import json
import shutil
import tempfile
from pathlib import Path

from publix_archiver import parse as parse_mod

FIX = Path(__file__).resolve().parent / "fixtures"


def run(tmp: Path):
    raw = tmp / "raw"; out = tmp / "out"
    for d in (raw, out):
        d.mkdir(parents=True, exist_ok=True)

    record = json.loads((FIX / "publix_detail.json").read_text())
    rid = str(record["ReceiptId"])

    # Save the SAME receipt twice under different filenames.
    (raw / f"{rid}.json").write_text(json.dumps(record))
    (raw / f"{rid}_again.json").write_text(json.dumps(record))

    summary = parse_mod.parse_all(raw_dir=raw, output_dir=out)

    # Dedup by ReceiptId -> one receipt, not two.
    assert summary["receipts_parsed"] == 1, summary

    with (out / "receipts.csv").open() as fh:
        rec = list(csv.DictReader(fh))
    assert len(rec) == 1, rec

    with (out / "line_items.csv").open() as fh:
        li = list(csv.DictReader(fh))
    # 2 items + 1 discount row = 3 (not doubled).
    assert len(li) == 3, li

    print("dedup OK: identical ReceiptId ingested twice -> one receipt")
    print("\nALL DEDUP TESTS PASSED")


def test_dedup():
    d = Path(tempfile.mkdtemp())
    try:
        run(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_dedup()
