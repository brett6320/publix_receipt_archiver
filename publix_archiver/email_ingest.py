"""Ingest Publix receipt emails into the same raw-receipt store as the API path.

Publix can email an itemized e-receipt (enable it in Receipt Preferences). The
email is an HTML body that renders the printed register receipt — store, items
with tax/benefit letters, per-item "You saved" lines, and totals — plus a
Receipt ID that matches the API's ReceiptId format, so an emailed receipt
deduplicates against the same receipt fetched from the API.

Publix uses more than one email template (e.g. the tax letter before vs after
the amount, ``Total`` vs ``Grand Total``, a labelled vs bare Receipt ID); the
parser handles both. Only genuine Publix receipt emails are ingested; anything
else is ignored.
"""
from __future__ import annotations

import email
import re
from email import policy
from email.message import EmailMessage
from html import unescape
from pathlib import Path

from . import config

# A Publix receipt email comes from a publix.com sender with this subject.
_SENDER_RE = re.compile(r"@(?:[\w-]+\.)*publix\.com", re.I)
_SUBJECT_RE = re.compile(r"publix receipt", re.I)

_TAXLET = set("tTMLFPH")
_AMT_RE = re.compile(r"-?\d+\.\d{2}")  # amounts may be negative (e.g. coupon lines)
# Receipt id: a leading 4-digit group then 3-4 more 3-4 char groups. Covers
# "1808 B5Q 710 114" and the older all-numeric "5417 9660 5160 3902 038".
_RID_PAT = r"\d{4}(?:\s+[0-9A-Za-z]{3,4}){3,4}"
_RID_LABELLED = re.compile(r"Receipt ID:\s*(" + _RID_PAT + r")")
_RID_BARE = re.compile(r"^(" + _RID_PAT + r")\s*$")
_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)?", re.I)
_STORE_NUM = re.compile(r"(?:\bS|store\s+)(\d{3,5})", re.I)
_SAVED = re.compile(r"You saved:?\s*\$?(\d+\.\d{2})", re.I)
_ADDRESS = re.compile(r"^\d+\s+\S", )  # a street address starts with a number


def _num(v) -> float:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


# ---- receipt-text sanitizing ----------------------------------------------
# Some receipts (especially ones imported from a *forwarded* email before the
# attachment handling was fixed) captured raw email transport headers, DKIM/auth
# results, MIME encoded-words and base64 blobs into their ReceiptText. Strip that
# cruft so the printed-receipt facsimile stays a clean receipt.
_EMAIL_HEADER_RE = re.compile(
    r"(?i)^\s*(authentication-results|arc-[\w-]+|dkim-signature|domainkey-signature|"
    r"received|return-path|delivered-to|message-id|mime-version|reply-to|references|"
    r"in-reply-to|precedence|auto-submitted|feedback-id|list-[\w-]+|subject|from|to|"
    r"content-(?:type|transfer-encoding|disposition|id|language)|x-[\w-]+)\b\s*:")
_AUTH_CONT_RE = re.compile(
    r"(?i)^\s*(?:d?kim|spf|dmarc|arc)=|header\.[dib]=|smtp\.(?:mailfrom|remote-ip)")
_ENCWORD_RE = re.compile(r"=\?[^?]*\?[bqBQ]\?[^?]*\?=")
_B64_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=_-]{40,}$")
# The marketing e-mail footer that follows the printed receipt — not receipt
# text. Everything from the first of these phrases onward is dropped. None occur
# in a printed receipt, so we match anywhere (the footer often wraps mid-line).
_FOOTER_RE = re.compile(
    r"(?i)(please do not reply to this email|this email was sent to|"
    r"unsubscribe from all|view this email in your browser|"
    r"update your receipt preferences|view the publix super markets privacy)")
_MAX_RECEIPT_TEXT = 12000  # backstop: no receipt facsimile should exceed this


def strip_email_cruft(text: str) -> str:
    """Clean receipt text for the printed-receipt facsimile: drop leaked email
    headers / DKIM / encoded-words / base64 blobs, and cut the trailing marketing
    footer. A no-op on already-clean receipts; keeps genuine receipt lines."""
    text = text or ""
    m = _FOOTER_RE.search(text)          # cut the marketing footer, if present
    if m:
        text = text[:m.start()]
    out: list[str] = []
    for raw in text.splitlines():
        line = _ENCWORD_RE.sub("", raw).rstrip()   # remove =?utf-8?q?…?= inline
        s = line.strip()
        if s and (_EMAIL_HEADER_RE.match(s) or _AUTH_CONT_RE.search(s)
                  or _B64_BLOB_RE.match(s)
                  or s.startswith("=?") or s.endswith("?=")):
            continue                                # transport cruft — drop
        out.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    if len(cleaned) > _MAX_RECEIPT_TEXT:
        cleaned = cleaned[:_MAX_RECEIPT_TEXT].rstrip() + "\n…(truncated)"
    return cleaned


def repair_receipt_text(raw_dir: Path = config.RAW_DIR) -> dict:
    """Overwrite stored receipts whose ReceiptText still carries leaked email
    cruft/footer with a cleaned version. Idempotent (clean records are left
    untouched); returns how many records were rewritten."""
    import json as _json
    repaired = 0
    for f in sorted(Path(raw_dir).glob("*.json")):
        try:
            rec = _json.loads(f.read_text())
        except Exception:
            continue
        rt = rec.get("ReceiptText")
        if not isinstance(rt, str) or not rt:
            continue
        cleaned = strip_email_cruft(rt)
        if cleaned != rt:
            rec["ReceiptText"] = cleaned
            f.write_text(_json.dumps(rec, indent=2))
            repaired += 1
    return {"repaired": repaired}


# ---- email → text ---------------------------------------------------------

def _html_to_text(html: str) -> str:
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</(tr|div|p|td|table|li)>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = unescape(t).replace("‌", " ").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _text_of(msg: EmailMessage) -> str:
    """Flatten THIS message's own receipt text — without descending into an
    attached email (message/rfc822). Attached emails are evaluated separately so
    a forward carrying several receipts yields several receipts."""
    htmls: list[str] = []
    texts: list[str] = []

    def walk(part) -> None:
        if part.get_content_type() == "message/rfc822":
            return  # an attached email — handled as its own candidate
        if part.is_multipart():
            for p in part.get_payload():
                walk(p)
            return
        payload = part.get_payload(decode=True)
        if not payload:
            return
        body = payload.decode(part.get_content_charset() or "utf-8", "replace")
        ct = part.get_content_type()
        if ct == "text/html":
            htmls.append(body)
        elif ct == "text/plain":
            texts.append(body)

    walk(msg)
    if htmls:
        return "\n".join(_html_to_text(h) for h in htmls)
    return "\n".join(texts)


def _attached_email(part):
    """If a part is an email attachment (message/rfc822, or a *.eml file), return
    it parsed as a Message; else None. Non-email attachments (.asc/.zip/.exe/.jpg,
    etc.) are ignored."""
    ct = part.get_content_type()
    if ct == "message/rfc822":
        try:
            payload = part.get_payload()
            if isinstance(payload, list) and payload:
                return payload[0]
            if isinstance(payload, EmailMessage):
                return payload
        except Exception:
            return None
    fn = (part.get_filename() or "").lower()
    if fn.endswith(".eml"):
        try:
            data = part.get_payload(decode=True)
            if data:
                return email.message_from_bytes(data, policy=policy.default)
        except Exception:
            return None
    return None


def _iter_candidate_emails(msg: EmailMessage):
    """Yield this message plus every email attached to it (recursively) — each an
    independent candidate. Non-email attachments are skipped."""
    yield msg
    for part in msg.walk():
        if part is msg:
            continue
        sub = _attached_email(part)
        if sub is not None:
            yield from _iter_candidate_emails(sub)


def is_publix_receipt(msg: EmailMessage) -> bool:
    """True only for a genuine Publix receipt — content-based, so it still works
    when the receipt was *forwarded* (From/Subject become the forwarder's).

    Requires a Publix marker plus the two hard signals of a real receipt: a
    Receipt ID and a grand total.
    """
    text = _text_of(msg)
    haystack = "\n".join([str(msg.get("From", "")), str(msg.get("Subject", "")), text])
    has_publix = bool(re.search(r"publix", haystack, re.I))
    return bool(has_publix and _find_receipt_id(text) and _find_total(text) is not None)


# ---- receipt text → record ------------------------------------------------

def _find_receipt_id(text: str):
    m = _RID_LABELLED.search(text)
    if m:
        return re.sub(r"\s+", "", m.group(1))
    for ln in text.splitlines():
        m = _RID_BARE.match(ln.strip())
        if m:
            return re.sub(r"\s+", "", m.group(1))
    return None


def _find_total(text: str):
    """Grand total = a line starting with 'Total' or 'Grand Total' (not Sub/Order)."""
    for ln in text.splitlines():
        s = ln.strip()
        if re.match(r"(?i)^(grand\s+total|total)\b", s):
            amts = _AMT_RE.findall(s)
            if amts:
                return _num(amts[-1])
    return None


def _find_labelled_amount(text: str, label_re: str):
    for ln in text.splitlines():
        if re.match(label_re, ln.strip(), re.I):
            amts = _AMT_RE.findall(ln)
            if amts:
                return _num(amts[-1])
    return None


def _store_name(text: str) -> str:
    lines = [l.strip() for l in text.splitlines()]
    for i, ln in enumerate(lines):
        if _ADDRESS.match(ln) and i > 0:
            for j in range(i - 1, -1, -1):
                cand = lines[j]
                if cand and "publix" not in cand.lower() and "thank you" not in cand.lower():
                    return cand
            break
    return ""


def _split_amount_code(line: str):
    """Return (item_amount, tax_code, text_without_them) for an item line.

    The item total is the LAST amount on the line (qty/weight lines carry a unit
    price first). The tax letter is a lone t/T/M/L/F/P/H immediately before or
    after that amount (templates differ on which side)."""
    amts = list(_AMT_RE.finditer(line))
    if not amts:
        return None, "", line
    last = amts[-1]
    amount = _num(last.group(0))
    before, after = line[:last.start()], line[last.end():]
    code = ""
    a_tokens = after.split()
    if a_tokens and a_tokens[0] in _TAXLET and len(a_tokens[0]) == 1:
        code = a_tokens[0]
        after = after.replace(a_tokens[0], "", 1)
    else:
        b_tokens = before.split()
        if b_tokens and b_tokens[-1] in _TAXLET and len(b_tokens[-1]) == 1:
            code = b_tokens[-1]
            before = before[:before.rstrip().rfind(b_tokens[-1])]
    return amount, code, (before + " " + after)


def _trailing_code(line: str) -> str:
    toks = line.split()
    return toks[-1] if toks and toks[-1] in _TAXLET and len(toks[-1]) == 1 else ""


def _parse_items(text: str) -> list[dict]:
    lines = text.splitlines()
    start = 0
    for i, ln in enumerate(lines):
        if re.search(r"Store Manager|\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}", ln):
            start = i + 1
    end = len(lines)
    for i, ln in enumerate(lines):
        if re.match(r"(?i)^(order total|subtotal)\b", ln.strip()):
            end = i
            break

    items: list[dict] = []
    pending_desc, pending_code = "", ""
    for ln in lines[start:end]:
        s = ln.strip()
        if not s:
            continue
        msaved = _SAVED.search(s)
        if msaved:
            if items:
                items[-1]["SavingAmount"] = _num(msaved.group(1))
            continue
        if not _AMT_RE.search(s):  # a description line (maybe with a trailing code)
            pending_code = _trailing_code(s)
            pending_desc = s[:s.rstrip().rfind(pending_code)].strip() if pending_code else s
            continue

        amount, code, rest = _split_amount_code(s)
        if amount is None:
            continue
        cont = "@" in s
        qty, weight, unit = 1, 0.0, 0.0
        if cont:
            desc = pending_desc or re.sub(r"\d.*$", "", rest).strip()
            code = code or pending_code
            mw = re.search(r"([\d.]+)\s*lb\s*@\s*\$?(\d+\.\d{2})", s, re.I)
            mq = re.search(r"^\s*(\d+)\s*@", s)
            if mw:
                weight, unit = _num(mw.group(1)), _num(mw.group(2))
            elif mq:
                qty = int(mq.group(1))
        else:
            desc = re.sub(r"\s{2,}", " ", rest).strip()
        pending_desc, pending_code = "", ""
        if not desc:
            continue
        items.append({
            "ItemCode": "",
            "ItemTypeDescription": desc,   # register description (no catalog in email)
            "TaxCode": code,
            "ItemQty": 0 if weight else qty,
            "ItemWeight": weight,
            "ItemPrice": unit or amount,
            "ItemAmount": amount,
            "SavingAmount": 0.0,
            "NetAmount": amount,           # email amounts are what was paid
        })
    return items


def parse_receipt_text(text: str) -> dict | None:
    # Clean leaked email headers/footer up front so store-name and item parsing
    # (not just the stored ReceiptText) are immune to transport cruft in the body.
    text = strip_email_cruft(text)
    receipt_id = _find_receipt_id(text)
    grand = _find_total(text)
    if not receipt_id or grand is None:
        return None
    tax = _find_labelled_amount(text, r"sales tax") or 0.0
    order_total = (_find_labelled_amount(text, r"order total")
                   or _find_labelled_amount(text, r"subtotal") or grand)

    # Prefer the store number from the footer (S#### / "store ####"); the receipt
    # id starts with the store only on some templates, so it's a weak fallback.
    m_store = _STORE_NUM.search(text)
    if m_store:
        facility_id = int(m_store.group(1))
    elif receipt_id[:4].isdigit():
        facility_id = int(receipt_id[:4])
    else:
        facility_id = 0

    txn_date = ""
    m_date = _DATE.search(text)
    if m_date:
        mm, dd, yyyy, hh, mi, ampm = m_date.groups()
        hour = int(hh)
        if ampm:
            ampm = ampm.upper()
            if ampm == "PM" and hour != 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0
        txn_date = f"{yyyy}-{mm}-{dd}T{hour:02d}:{mi}:00"

    items = _parse_items(text)
    return {
        "ReceiptId": receipt_id,
        "Source": "email",
        "FacilityId": facility_id,
        "FacilityName": _store_name(text),
        "TransactionDate": txn_date,
        "GrandTotal": grand,
        "OrderTotal": order_total,
        "TaxAmount": tax,
        "ReceiptText": text,   # already cleaned at the top of this function
        "Products": [],
        "ReceiptLineItems": items,
        "ItemCount": len(items),
    }


def parse_receipts(raw: bytes | str) -> list[dict]:
    """Every Publix receipt in a raw email: its own body if it's a receipt, plus
    each attached email (.eml / message/rfc822), evaluated independently. Handles
    forward-as-attachment and multiple attached receipts. Deduped by ReceiptId."""
    if isinstance(raw, bytes):
        msg = email.message_from_bytes(raw, policy=policy.default)
    else:
        msg = email.message_from_string(raw, policy=policy.default)
    out: list[dict] = []
    seen: set[str] = set()
    for cand in _iter_candidate_emails(msg):
        if not is_publix_receipt(cand):
            continue
        rec = parse_receipt_text(_text_of(cand))
        if rec and rec["ReceiptId"] not in seen:
            seen.add(rec["ReceiptId"])
            out.append(rec)
    return out


def parse_eml(raw: bytes | str) -> dict | None:
    """Parse a raw .eml into a single record, or None. (First receipt found;
    use parse_receipts() to get all of them.)"""
    recs = parse_receipts(raw)
    return recs[0] if recs else None


def ingest_eml_paths(paths, raw_dir: Path = config.RAW_DIR) -> dict:
    """Ingest .eml files/dirs. Every receipt in each file (including attached
    emails) is saved; non-receipt attachments are ignored. Deduped by key."""
    import json
    from .fetch import _safe_key
    config.ensure_dirs()
    files: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.eml")))
        elif p.suffix.lower() == ".eml":
            files.append(p)
    existing = {f.stem for f in raw_dir.glob("*.json")}
    saved = skipped = ignored = 0
    for f in files:
        recs = parse_receipts(f.read_bytes())
        if not recs:
            ignored += 1
            continue
        for rec in recs:
            key = _safe_key(rec)
            if key in existing:
                skipped += 1
                continue
            (raw_dir / f"{key}.json").write_text(json.dumps(rec, indent=2))
            existing.add(key)
            saved += 1
    return {"files": len(files), "saved": saved,
            "skipped_existing": skipped, "ignored_non_receipt": ignored}


# ---- Cloudflare Queue pull consumer + R2 payload store ---------------------

def _r2_client(s: dict):
    """S3-compatible client for the Cloudflare R2 bucket (needs boto3)."""
    try:
        import boto3
    except ImportError as ex:  # pragma: no cover
        raise RuntimeError(
            "boto3 is required for R2/queue email ingestion (pip install boto3).") from ex
    from botocore.config import Config as _Cfg
    return boto3.client(
        "s3", endpoint_url=s["r2_endpoint"],
        aws_access_key_id=s["r2_access_key_id"],
        aws_secret_access_key=s["r2_secret_access_key"],
        region_name="auto", config=_Cfg(signature_version="s3v4"))


def _queue_url(s: dict, action: str) -> str:
    return (f"https://api.cloudflare.com/client/v4/accounts/{s['cf_account_id']}"
            f"/queues/{s['cf_queue_id']}/messages/{action}")


def _queue_headers(s: dict) -> dict:
    return {"Authorization": f"Bearer {s['cf_api_token']}",
            "Content-Type": "application/json"}


def _msg_r2_key(body) -> str | None:
    """Extract the R2 object key from a queue message body ({key,bucket} or str)."""
    if isinstance(body, dict):
        return body.get("key") or body.get("object") or body.get("Key")
    if isinstance(body, str):
        return body or None
    return None


def _drain_bucket(r2, bucket: str, prefix: str, raw_dir: Path, existing: set,
                  delete: bool) -> dict:
    """Ingest and (optionally) delete EVERY object under the prefix in the
    bucket. Deduped by ReceiptId. Returns per-object counters."""
    import json
    objects = saved = skipped = ignored = deleted = failed = 0
    token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        lst = r2.list_objects_v2(**kw)
        for obj in lst.get("Contents", []) or []:
            key = obj["Key"]
            if key.endswith("/"):
                continue
            objects += 1
            try:
                body = r2.get_object(Bucket=bucket, Key=key)["Body"].read()
                recs = parse_receipts(body)   # inline + each attached .eml
                if not recs:
                    ignored += 1
                for rec in recs:
                    rkey = _safe_key_for(rec)
                    if rkey in existing:
                        skipped += 1
                    else:
                        (raw_dir / f"{rkey}.json").write_text(json.dumps(rec, indent=2))
                        existing.add(rkey)
                        saved += 1
                if delete:
                    r2.delete_object(Bucket=bucket, Key=key)
                    deleted += 1
            except Exception as ex:  # leave the object in place; retried next time
                failed += 1
                print(f"  ! object {key} failed: {ex}")
        if not lst.get("IsTruncated"):
            break
        token = lst.get("NextContinuationToken")
    return {"objects_seen": objects, "saved": saved, "skipped_existing": skipped,
            "ignored_non_receipt": ignored, "deleted": deleted, "failed": failed}


def pull_from_queue(raw_dir: Path = config.RAW_DIR, delete: bool = True,
                    max_batches: int = 20, http=None, r2=None) -> dict:
    """On a queue event, drain the WHOLE R2 bucket — not just the object named in
    the message. Pull messages as triggers, then list every object under the
    prefix, ingest it, and delete it, and finally ack the messages. This way a
    lost/duplicate message, multiple objects, or leftovers from a prior failure
    are all swept up. Deduped by ReceiptId.

    `http` (an httpx.Client) and `r2` (an S3 client) can be injected for tests.
    """
    s = config.email_settings()
    bucket, prefix = s["r2_bucket"], s["r2_prefix"]
    if http is None:
        if not config.email_ingest_configured():
            raise RuntimeError("Email ingestion is not configured "
                               "(set R2 + Cloudflare Queue settings in the admin UI or env).")
        import httpx
        http = httpx.Client(timeout=30.0)
    if r2 is None:
        r2 = _r2_client(s)
    config.ensure_dirs()
    existing = {f.stem for f in raw_dir.glob("*.json")}

    # 1) Pull messages — these are just wake-up triggers; the body is ignored.
    leases = []
    for _ in range(max_batches):
        resp = http.post(_queue_url(s, "pull"), headers=_queue_headers(s),
                         json={"visibility_timeout_ms": 60000, "batch_size": 100})
        resp.raise_for_status()
        msgs = (resp.json().get("result") or {}).get("messages") or []
        if not msgs:
            break
        leases += [m["lease_id"] for m in msgs if m.get("lease_id")]

    # 2) On any trigger, drain the whole bucket.
    counts = {"objects_seen": 0, "saved": 0, "skipped_existing": 0,
              "ignored_non_receipt": 0, "deleted": 0, "failed": 0}
    if leases:
        counts = _drain_bucket(r2, bucket, prefix, raw_dir, existing, delete)

    # 3) Ack the triggers once drained (skip when --keep, so they redeliver).
    if leases and delete:
        http.post(_queue_url(s, "ack"), headers=_queue_headers(s),
                  json={"acks": [{"lease_id": l} for l in leases]}).raise_for_status()

    return {"messages_seen": len(leases), **counts}


def _safe_key_for(rec: dict) -> str:
    from .fetch import _safe_key
    return _safe_key(rec)
