"""Admin item-name overrides: a pinned name wins over the auto-derived one and
is applied to every line for that item number at parse time."""
import csv
import json
import tempfile
from pathlib import Path

import publix_archiver.config as config
from publix_archiver import item_names, parse as P


def _isolate(tmp: Path):
    """Point the persistent stores at a temp dir for the duration of a test."""
    config.ITEM_NAMES_FILE = tmp / "item_names.json"
    config.ITEM_MAP_FILE = tmp / "item_number_map.json"


def test_put_get_names_remove_roundtrip():
    tmp = Path(tempfile.mkdtemp()); _isolate(tmp)
    assert item_names.names() == {}
    item_names.put("4222230204", "My Turkey")
    assert item_names.get("4222230204") == "My Turkey"
    assert item_names.names() == {"4222230204": "My Turkey"}
    # empty name clears the override
    item_names.put("4222230204", "")
    assert item_names.get("4222230204") == ""
    # remove() on a missing key is a no-op
    item_names.put("99", "Nine"); item_names.remove("99")
    assert item_names.names() == {}


def test_put_requires_number():
    tmp = Path(tempfile.mkdtemp()); _isolate(tmp)
    try:
        item_names.put("", "Name")
        assert False, "expected ValueError"
    except ValueError:
        pass


def _rec():
    return {"ReceiptId": "R1", "Source": "publix", "FacilityId": 1808, "FacilityName": "S",
            "TransactionDate": "2026-07-02T10:00:00", "GrandTotal": 6.29, "TaxAmount": 0.0,
            "Products": [{"ItemName": "Jennie-O 99%/1% Fresh Ground Turkey Breast",
                          "SizeDescription": "16 oz (1 lb)", "UPC": "4222230204"}],
            "ReceiptLineItems": [{"ItemCode": "00004222230204", "ItemQty": 1, "ItemPrice": 6.29,
                                  "ItemAmount": 6.29, "SavingAmount": 0.0, "NetAmount": 6.29}]}


def test_override_wins_in_parse():
    tmp = Path(tempfile.mkdtemp()); _isolate(tmp)
    raw, out = tmp / "raw", tmp / "out"; raw.mkdir(); out.mkdir()
    (raw / "r1.json").write_text(json.dumps(_rec()))

    # Without an override, the auto (catalog) name is used.
    P.parse_all(raw_dir=raw, output_dir=out)
    row = [r for r in csv.DictReader(open(out / "line_items.csv"))
           if r["item_number"] == "4222230204"][0]
    assert row["description"].startswith("Jennie-O")
    assert row["original_description"].startswith("Jennie-O")

    # With an override, every line shows the pinned name; original is untouched.
    item_names.put("4222230204", "Ground Turkey (mine)")
    P.parse_all(raw_dir=raw, output_dir=out)
    row = [r for r in csv.DictReader(open(out / "line_items.csv"))
           if r["item_number"] == "4222230204"][0]
    assert row["description"] == "Ground Turkey (mine)"
    assert row["original_description"].startswith("Jennie-O")   # register text preserved


if __name__ == "__main__":
    for fn in list(globals()):
        if fn.startswith("test_"):
            globals()[fn]()
    print("item-name tests OK")
