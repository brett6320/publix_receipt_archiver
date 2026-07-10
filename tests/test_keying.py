"""Receipt file keying: the stored filename must match the CSV receipt_id / PDF
link. Publix returns a different ReceiptId in the list (opaque hash) than in the
detail (printed number); the record — and thus the file — must use the detail id.
"""
import json
import tempfile
from pathlib import Path

from publix_archiver import api as api_mod
from publix_archiver import fetch as fetch_mod
from publix_archiver import parse as parse_mod


def _list_txn():
    # List summary carries a hashed ReceiptId, unlike the detail.
    return {"TransactionKey": "TK%3D", "ReceiptId": "hashID==",
            "TransactionDate": "2020-01-15T12:00:00", "FacilityId": 9999,
            "SalesTransactionNumber": 1234}


def _detail():
    return {"ReceiptId": "180876R772138", "TransactionKey": "TK=",
            "FacilityId": 9999, "FacilityName": "Sample Plaza",
            "TransactionDate": "2020-01-15T12:00:00",
            "Products": [{"ItemName": "Bananas", "UPC": "1111"}],
            "ReceiptLineItems": [{"ItemCode": "00000000001111", "ItemQty": 1,
                                  "ItemPrice": 1.0, "ItemAmount": 1.0,
                                  "NetAmount": 1.0, "SavingAmount": 0.0}]}


def test_record_key_matches_parse_receipt_id():
    record = api_mod.merge_detail(_list_txn(), _detail())
    file_key = fetch_mod._safe_key(record)
    csv_id = parse_mod._receipt_key(record)
    # Both must resolve to the detail ReceiptId so /pdf/<receipt_id> matches.
    assert file_key == "180876R772138", file_key
    assert csv_id == "180876R772138", csv_id
    assert file_key == csv_id


def test_index_existing_migrates_legacy_filename():
    tmp = Path(tempfile.mkdtemp())
    record = api_mod.merge_detail(_list_txn(), _detail())
    # Simulate the old bug: file saved under the LIST key.
    legacy_stem = fetch_mod._safe_key(_list_txn())  # "hashID__"
    assert legacy_stem != "180876R772138"
    (tmp / f"{legacy_stem}.json").write_text(json.dumps(record))

    seen = fetch_mod._index_existing(tmp)
    files = {f.stem for f in tmp.glob("*.json")}
    # Migrated to the detail key; legacy name gone.
    assert files == {"180876R772138"}, files
    # Its list key is remembered for skip (legacy file had none -> detail key).
    assert "180876R772138" in seen


def test_raw_key_for_resolves_legacy_named_file():
    """The /pdf/<ReceiptId> route must find a receipt even when its file is still
    saved under the old list-key name (matched by the ReceiptId inside)."""
    from publix_archiver import web, config
    tmp = Path(tempfile.mkdtemp())
    old = config.RAW_DIR
    config.RAW_DIR = tmp
    try:
        rec = {"ReceiptId": "180876R772138",
               "ReceiptLineItems": [{}], "Products": [{"ItemName": "x"}]}
        (tmp / "nm_hHFzdyeNVYnE8jmDlaQ__.json").write_text(json.dumps(rec))
        assert web._raw_key_for("180876R772138") == "nm_hHFzdyeNVYnE8jmDlaQ__"
        assert web._raw_key_for("nope") is None
    finally:
        config.RAW_DIR = old


if __name__ == "__main__":
    test_record_key_matches_parse_receipt_id()
    test_index_existing_migrates_legacy_filename()
    test_raw_key_for_resolves_legacy_named_file()
    print("keying tests OK")
