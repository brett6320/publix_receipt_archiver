"""Compressed backups of the imported receipt data.

The raw receipts in ``data/raw`` are the source of truth — the CSVs, PDFs and
Markdown are all regenerated from them — so a backup is just a gzip-compressed
tar of that directory. Backups live in ``data/backups`` (git-ignored).

Restore is *additive and idempotent*: each archived receipt is written under its
own receipt key (ReceiptId), and one already on disk is skipped, so restoring the
same backup twice — or a backup that overlaps current data — never creates
duplicates.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import tarfile
from pathlib import Path

from . import config
from .fetch import _safe_key

_ARCHIVE_RE = re.compile(r"^receipts-\d{8}-\d{6}\.tar\.gz$")


def backup_dir() -> Path:
    d = config.DATA_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_path(name: str) -> Path:
    """Resolve a backup name to a path inside backup_dir (no traversal)."""
    p = backup_dir() / Path(name).name
    if p.parent != backup_dir():
        raise ValueError("invalid backup name")
    return p


def create_backup(raw_dir: Path = config.RAW_DIR, stamp: str | None = None) -> dict:
    """Create a .tar.gz of every raw receipt. Returns backup metadata."""
    config.ensure_dirs()
    files = sorted(Path(raw_dir).glob("*.json"))
    stamp = stamp or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = backup_dir() / f"receipts-{stamp}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=f"raw/{f.name}")
    st = path.stat()
    return {"name": path.name, "receipts": len(files), "size": st.st_size,
            "created": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}


def list_backups() -> list[dict]:
    """All backups, newest first, with receipt count and size."""
    out = []
    for p in sorted(backup_dir().glob("receipts-*.tar.gz"), reverse=True):
        try:
            with tarfile.open(p, "r:gz") as tar:
                n = sum(1 for m in tar.getmembers()
                        if m.isfile() and m.name.startswith("raw/")
                        and m.name.endswith(".json"))
        except Exception:
            n = 0
        st = p.stat()
        out.append({"name": p.name, "receipts": n, "size": st.st_size,
                    "created": dt.datetime.fromtimestamp(st.st_mtime)
                    .isoformat(timespec="seconds")})
    return out


def restore_backup(name: str, raw_dir: Path = config.RAW_DIR,
                   overwrite: bool = False) -> dict:
    """Extract a backup into data/raw, skipping receipts already present.

    Dedup is by the receipt's own key (ReceiptId), not the archived filename, so
    the same receipt never lands twice even across keying schemes.
    """
    path = _safe_path(name)
    if not path.exists() or not _ARCHIVE_RE.match(path.name):
        raise FileNotFoundError(name)
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    # Identify receipts already on disk by their own key (not filename), so a
    # receipt present under any name is recognised and never duplicated.
    existing = set()
    for f in raw_dir.glob("*.json"):
        try:
            existing.add(_safe_key(json.loads(f.read_text())))
        except Exception:
            existing.add(f.stem)
    added = skipped = bad = 0
    with tarfile.open(path, "r:gz") as tar:
        for m in tar.getmembers():
            if not (m.isfile() and m.name.startswith("raw/")
                    and m.name.endswith(".json")):
                continue
            fh = tar.extractfile(m)
            if fh is None:
                continue
            data = fh.read()
            try:
                rec = json.loads(data)
            except Exception:
                bad += 1
                continue
            key = _safe_key(rec)  # write under the receipt's identity, not archive path
            if key in existing and not overwrite:
                skipped += 1
                continue
            (raw_dir / f"{key}.json").write_bytes(data)
            existing.add(key)
            added += 1
    return {"name": path.name, "added": added,
            "skipped_existing": skipped, "invalid": bad}


def delete_backup(name: str) -> dict:
    path = _safe_path(name)
    if not path.exists() or not _ARCHIVE_RE.match(path.name):
        raise FileNotFoundError(name)
    path.unlink()
    return {"name": path.name, "deleted": True}
