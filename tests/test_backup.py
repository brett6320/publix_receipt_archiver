"""Compressed backups: create, list, and restore without creating duplicates."""
import json
import tempfile
from pathlib import Path

from publix_archiver import backup, config


def _rec(rid):
    return {"ReceiptId": rid, "ReceiptLineItems": [{}], "Products": [{"ItemName": "x"}]}


def _setup(tmp):
    src = tmp / "raw"
    src.mkdir()
    for rid in ("A", "B"):
        (src / f"{rid}.json").write_text(json.dumps(_rec(rid)))
    return src


def test_create_list_restore_dedup():
    tmp = Path(tempfile.mkdtemp())
    orig = (config.DATA_DIR, config.RAW_DIR)
    config.DATA_DIR, config.RAW_DIR = tmp, tmp / "raw"
    try:
        src = _setup(tmp)
        meta = backup.create_backup(raw_dir=src, stamp="20200101-000000")
        assert meta["receipts"] == 2
        assert meta["name"] == "receipts-20200101-000000.tar.gz"
        assert backup.list_backups()[0]["receipts"] == 2

        # Restore into an empty dir -> both added.
        dest = tmp / "dest"
        r1 = backup.restore_backup(meta["name"], raw_dir=dest)
        assert (r1["added"], r1["skipped_existing"]) == (2, 0)

        # Restore again -> everything already present, nothing added (no dupes).
        r2 = backup.restore_backup(meta["name"], raw_dir=dest)
        assert (r2["added"], r2["skipped_existing"]) == (0, 2)
        assert len(list(dest.glob("*.json"))) == 2

        # Dedup by identity even when a receipt is on disk under a different name.
        dest2 = tmp / "dest2"
        dest2.mkdir()
        (dest2 / "legacy-name.json").write_text(json.dumps(_rec("A")))
        r3 = backup.restore_backup(meta["name"], raw_dir=dest2)
        assert (r3["added"], r3["skipped_existing"]) == (1, 1)  # A skipped, B added

        backup.delete_backup(meta["name"])
        assert backup.list_backups() == []
    finally:
        config.DATA_DIR, config.RAW_DIR = orig


def test_restore_rejects_bad_name():
    import pytest
    tmp = Path(tempfile.mkdtemp())
    orig = config.DATA_DIR
    config.DATA_DIR = tmp
    try:
        with pytest.raises((FileNotFoundError, ValueError)):
            backup.restore_backup("../../etc/passwd")
        with pytest.raises(FileNotFoundError):
            backup.restore_backup("receipts-20990101-000000.tar.gz")
    finally:
        config.DATA_DIR = orig


if __name__ == "__main__":
    test_create_list_restore_dedup()
    test_restore_rejects_bad_name()
    print("backup tests OK")
