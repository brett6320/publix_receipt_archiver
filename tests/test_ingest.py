"""Tests for the Publix JSON importer (ingest_paths)."""
import json
import shutil
import tempfile
from pathlib import Path

from publix_archiver import ingest as ingest_mod

FIX = Path(__file__).resolve().parent / "fixtures"


def _detail():
    return json.loads((FIX / "publix_detail.json").read_text())


def run(tmp: Path):
    raw = tmp / "raw"
    src = tmp / "src"
    for d in (raw, src):
        d.mkdir(parents=True, exist_ok=True)

    rec = _detail()

    # 1) A single detail record in a file.
    single = src / "one.json"
    single.write_text(json.dumps(rec))
    s = ingest_mod.ingest_paths([single], raw_dir=raw)
    assert s["ingested"] == 1, s
    assert len(list(raw.glob("*.json"))) == 1, list(raw.glob("*.json"))

    # 2) An envelope {"receipts": [...]} with two DIFFERENT receipts.
    rec2 = dict(rec)
    rec2["ReceiptId"] = "999999R000000"
    env = src / "bundle.json"
    env.write_text(json.dumps({"receipts": [rec, rec2]}))
    s = ingest_mod.ingest_paths([env], raw_dir=raw)
    assert s["ingested"] == 2, s
    # rec (same ReceiptId) overwrites; rec2 is new -> 2 files total on disk.
    assert len(list(raw.glob("*.json"))) == 2, list(raw.glob("*.json"))

    # 3) A directory of JSON, list-shaped file.
    raw2 = tmp / "raw2"; raw2.mkdir()
    listfile = src / "list.json"
    listfile.write_text(json.dumps([rec, rec2]))
    s = ingest_mod.ingest_paths([src], raw_dir=raw2)
    # one.json(1) + bundle.json(2) + list.json(2) = 5 ingested; 2 unique on disk.
    assert s["ingested"] == 5, s
    assert len(list(raw2.glob("*.json"))) == 2, list(raw2.glob("*.json"))

    print("ingest OK: single / envelope / list / directory all handled")
    print("\nALL INGEST TESTS PASSED")


def test_ingest():
    d = Path(tempfile.mkdtemp())
    try:
        run(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_ingest()
