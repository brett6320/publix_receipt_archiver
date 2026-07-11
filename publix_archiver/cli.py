"""Command-line interface.

Usage:
  python -m publix_archiver login      # open browser, sign in, cache creds
  python -m publix_archiver fetch      # download all in-store receipts
  python -m publix_archiver parse      # build deduplicated CSVs from raw data
  python -m publix_archiver all        # fetch -> parse -> pdf -> markdown
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from . import config, webauth
from .auth import (
    Credentials,
    creds_from_cookies,
    login_and_get_credentials,
    token_is_expired,
)

CRED_CACHE = config.DATA_DIR / "credentials.json"


def _load_or_login(
    force_login: bool = False, timeout: int = 300, channel: str | None = None
) -> Credentials:
    # Env override (paste credentials manually if the automated path ever fails).
    env_token = os.environ.get("PUBLIX_ACCESS_TOKEN")
    env_ecms = os.environ.get("PUBLIX_ECMS_ID")
    if env_token and env_ecms:
        print(">>> Using PUBLIX_ACCESS_TOKEN / PUBLIX_ECMS_ID from environment.")
        if token_is_expired(env_token):
            print("!!! WARNING: PUBLIX_ACCESS_TOKEN is already EXPIRED "
                  "(these tokens last ~1h). Re-grab a fresh one.")
        return Credentials(id_token=env_token, ecms_id=env_ecms)

    if not force_login and CRED_CACHE.exists():
        data = json.loads(CRED_CACHE.read_text())
        creds = Credentials(**data)
        if token_is_expired(creds.id_token):
            print(">>> Cached token expired (they last ~1h) — re-logging in.")
        else:
            return creds

    config.ensure_dirs()
    return login_and_get_credentials(
        timeout_seconds=timeout, cred_cache=CRED_CACHE, browser_channel=channel
    )


def cmd_login(args) -> None:
    creds = login_and_get_credentials(
        timeout_seconds=args.timeout,
        cred_cache=CRED_CACHE,
        browser_channel=getattr(args, "channel", None),
    )
    print(f"Cached credentials to {CRED_CACHE}")
    _ = asdict(creds)


def _extract_creds_from_blob(text: str) -> Credentials:
    """Parse credentials from a pasted JSON/cookie blob.

    Accepts the browser-console JSON `{"AccessTokenJwt": "...", "EcmsId": "..."}`
    (or `id_token`/`ecms_id`), or a raw `Cookie:` string containing the
    AccessTokenJwt + User cookies.
    """
    text = text.strip()
    token = ecms = None
    try:
        blob = json.loads(text)
        token = (blob.get("AccessTokenJwt") or blob.get("id_token")
                 or blob.get("access_token") or blob.get("idToken"))
        ecms = (blob.get("EcmsId") or blob.get("ecms_id") or blob.get("ecmsId"))
    except (ValueError, AttributeError):
        pass

    # Cookie-string fallback (e.g. a pasted `document.cookie` or Cookie header).
    if not (token and ecms) and "=" in text:
        cookies = {}
        for part in text.replace("Cookie:", "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip()
        creds = creds_from_cookies(cookies)
        if creds:
            return creds

    if not (token and ecms):
        import re
        m_t = re.search(r'(?:AccessTokenJwt|id_token|access_token)"?\s*[:=]\s*"?([A-Za-z0-9._\-]+)', text)
        m_e = re.search(r'(?:EcmsId|ecms_id)"?\s*[:=]\s*"?([A-Za-z0-9._\-]+)', text)
        token = token or (m_t.group(1) if m_t else None)
        ecms = ecms or (m_e.group(1) if m_e else None)

    if not (token and ecms):
        raise SystemExit(
            "Couldn't find both an access token (AccessTokenJwt) and an EcmsId in "
            "the input. Paste the JSON the snippet copies, a cookie blob, or use "
            "`import-curl`.")
    return Credentials(id_token=str(token).replace("Bearer ", "").strip(),
                       ecms_id=str(ecms).strip())


def _read_clipboard() -> str | None:
    """Read the macOS clipboard (pbpaste). Returns None if unavailable/empty."""
    import subprocess
    try:
        out = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
        return out.stdout or None
    except Exception:
        return None


def cmd_paste_token(args) -> None:
    """Supply credentials grabbed from your own logged-in browser.

    Bypasses automated login. The JWT is long, so we read the clipboard (or a
    file you point us at) rather than a terminal prompt (which truncates long
    pastes). Paste the AccessTokenJwt + EcmsId (as JSON or a cookie blob), or use
    `import-curl` with a DevTools 'Copy as cURL'.
    """
    config.ensure_dirs()

    if getattr(args, "file", None):
        creds = _extract_creds_from_blob(Path(args.file).read_text())
    else:
        print("In your normal browser, logged into publix.com, open DevTools →")
        print("Console and copy the AccessTokenJwt + EcmsId (or your cookie blob),")
        print("or run browser/publix_fetch_receipts.js. Alternatively use import-curl.\n")
        clip = _read_clipboard()
        if clip and ("AccessTokenJwt" in clip or "EcmsId" in clip
                     or "id_token" in clip or "ecms_id" in clip):
            print(">>> Read credentials from your clipboard.")
            creds = _extract_creds_from_blob(clip)
        else:
            print("Clipboard didn't contain the token. Alternatives:")
            print("  • copy the AccessTokenJwt + EcmsId, then: python -m publix_archiver paste-token")
            print("  • or save the JSON to a file and pass --file <path>")
            print("  • or use: python -m publix_archiver import-curl\n")
            raise SystemExit("No credentials found on clipboard.")

    CRED_CACHE.write_text(json.dumps(asdict(creds), indent=2))
    print(f"\nSaved to {CRED_CACHE}.")
    print("Token is valid ~1 hour — run this now:")
    print("    python -m publix_archiver fetch && python -m publix_archiver parse")


from .capture import save_from_curl, CaptureError


def cmd_import_curl(args) -> None:
    """Import exact API headers from a DevTools 'Copy as cURL'.

    Log in with your NORMAL browser, open DevTools → Network, load your purchases
    page, right-click the request to
    services.publix.com/api/v1/PurchaseHistory → Copy → Copy as cURL. Then run
    this; it reads the cURL from your clipboard and captures the Bearer token +
    EcmsId verbatim.
    """
    text = None
    if getattr(args, "file", None):
        text = Path(args.file).read_text()
    else:
        print("In your NORMAL browser (logged into publix.com):")
        print("  1. DevTools → Network tab")
        print("  2. Open your Account → Purchases page so it loads")
        print("  3. Right-click the '.../api/v1/PurchaseHistory' request → Copy → Copy as cURL")
        print("  4. Come back here (it reads your clipboard)\n")
        text = _read_clipboard()
    try:
        result = save_from_curl(text or "")
    except CaptureError as ex:
        raise SystemExit(str(ex))

    print(f"\n>>> Imported {result['headers']} headers (Bearer token + EcmsId) from cURL.")
    print(f">>> Token valid ~{result['token_minutes']} more min.")
    if result["expired"]:
        print("!!! That token is ALREADY expired — recopy a fresh cURL and retry.")
    else:
        print(">>> Run NOW:  python -m publix_archiver fetch && python -m publix_archiver parse")


def cmd_fetch(args) -> None:
    from .fetch import fetch_all_receipts

    creds = _load_or_login(timeout=args.timeout, channel=getattr(args, "channel", None))
    summary = fetch_all_receipts(
        creds,
        page_size=args.page_size,
        from_date=getattr(args, "from_date", None),
        to_date=getattr(args, "to_date", None),
        skip_existing=args.skip_existing,
    )
    print("\nFetch summary:")
    print(json.dumps(summary, indent=2))


def cmd_parse(args) -> None:
    from .parse import parse_all

    summary = parse_all()
    print("\nParse summary:")
    print(json.dumps(summary, indent=2))


def cmd_import(args) -> None:
    """Ingest saved receipt JSON (from the browser snippet) into raw receipt JSON."""
    from .ingest import ingest_paths

    if not args.paths:
        raise SystemExit("Provide receipt .json file/dir paths.")
    summary = ingest_paths([Path(p) for p in args.paths])
    print(json.dumps(summary, indent=2))


def cmd_pdf(args) -> None:
    from .pdf import render_all_pdfs

    summary = render_all_pdfs(force=getattr(args, "force", False))
    print(json.dumps(summary, indent=2))


def cmd_web(args) -> None:
    from .web import serve

    serve(host=args.host, port=args.port)


def cmd_markdown(args) -> None:
    from .markdown import generate_markdown

    summary = generate_markdown()
    print(json.dumps(summary, indent=2))


def cmd_refresh(args) -> None:
    """Refresh metadata (PDF, Markdown) for a single receipt."""
    from .markdown import generate_one
    from .pdf import render_one_pdf
    import re

    rid = args.receipt_id
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", rid)
    key = safe if (config.RAW_DIR / f"{safe}.json").exists() else None
    if key is None:  # fall back to matching by ReceiptId
        for f in config.RAW_DIR.glob("*.json"):
            try:
                if str(json.loads(f.read_text()).get("ReceiptId") or "") == rid:
                    key = f.stem
                    break
            except Exception:
                continue
    if key is None:
        raise SystemExit(f"Receipt {rid} not found in {config.RAW_DIR}")

    # An unpublished placeholder (all "Normal Sale", no named products) is dropped
    # so it re-imports once Publix publishes the real itemized receipt.
    from .parse import is_placeholder
    try:
        record = json.loads((config.RAW_DIR / f"{key}.json").read_text())
    except Exception:
        record = {}
    if is_placeholder(record):
        (config.RAW_DIR / f"{key}.json").unlink(missing_ok=True)
        (config.PDF_DIR / f"{key}.pdf").unlink(missing_ok=True)
        (config.OUTPUT_DIR / "receipts" / f"{key}.md").unlink(missing_ok=True)
        print(json.dumps({"receipt": rid, "key": key, "status": "deferred",
                          "message": "Detail not published yet — removed; re-import next day."},
                         indent=2))
        return

    md = generate_one(key)
    pdf = render_one_pdf(key) if not args.no_pdf else False
    print(json.dumps({"receipt": rid, "key": key, "markdown": md, "pdf": pdf}, indent=2))


def cmd_backup_create(args) -> None:
    """Create a compressed backup of all raw receipts."""
    from . import backup
    print(json.dumps(backup.create_backup(), indent=2))


def cmd_backup_list(args) -> None:
    """List existing backups."""
    from . import backup
    print(json.dumps(backup.list_backups(), indent=2))


def cmd_backup_restore(args) -> None:
    """Restore a backup, skipping receipts already on disk, then rebuild outputs."""
    from . import backup
    result = backup.restore_backup(args.name, overwrite=args.overwrite)
    if not args.no_parse and result["added"]:
        from .parse import parse_all
        from .markdown import generate_markdown
        parse_all()
        generate_markdown()
        result["reprocessed"] = True
    print(json.dumps(result, indent=2))


def cmd_backup_delete(args) -> None:
    """Delete a backup."""
    from . import backup
    print(json.dumps(backup.delete_backup(args.name), indent=2))


def _prompt_new_password(username: str) -> str:
    import getpass
    while True:
        pw = getpass.getpass(f"New password for {username!r}: ")
        if len(pw) < 8:
            print("  Password must be at least 8 characters.")
            continue
        if pw != getpass.getpass("Confirm password: "):
            print("  Passwords didn't match — try again.")
            continue
        return pw


def _print_totp_enrollment(username: str, secret: str) -> None:
    from .webauth import provisioning_uri
    print("\nScan this in your authenticator app (Google Authenticator, 1Password, "
          "Authy, …),\nor enter the secret manually:\n")
    print(f"  Account : {config.AUTH_ISSUER}:{username}")
    print(f"  Secret  : {secret}")
    print(f"  otpauth : {provisioning_uri(username, secret)}\n")
    print("Then sign in with your password + the 6-digit code it shows.")


def cmd_auth_adduser(args) -> None:
    pw = _prompt_new_password(args.username)
    try:
        secret = webauth.add_user(args.username, pw, role=getattr(args, "role", None)
                                  or webauth.DEFAULT_ROLE)
    except ValueError as ex:
        raise SystemExit(str(ex))
    role = webauth.get_role(args.username)
    print(f"\n✓ Created {role} {args.username!r}.")
    _print_totp_enrollment(args.username, secret)


def cmd_auth_setrole(args) -> None:
    try:
        webauth.set_role(args.username, args.role)
    except ValueError as ex:
        raise SystemExit(str(ex))
    print(f"✓ {args.username!r} is now {args.role}.")


def cmd_auth_passwd(args) -> None:
    pw = _prompt_new_password(args.username)
    try:
        webauth.set_password(args.username, pw)
    except ValueError as ex:
        raise SystemExit(str(ex))
    print(f"✓ Password updated for {args.username!r}.")


def cmd_auth_reset_mfa(args) -> None:
    try:
        secret = webauth.reset_totp(args.username)
    except ValueError as ex:
        raise SystemExit(str(ex))
    print(f"✓ New TOTP secret for {args.username!r} (old codes no longer work).")
    _print_totp_enrollment(args.username, secret)


def cmd_auth_deluser(args) -> None:
    try:
        webauth.delete_user(args.username)
    except ValueError as ex:
        raise SystemExit(str(ex))
    print(f"✓ Deleted user {args.username!r}.")


def cmd_auth_users(args) -> None:
    users = webauth.users_overview()
    if not users:
        print("No web accounts configured. Add one (first user becomes admin): "
              "python -m publix_archiver auth adduser <name>")
        return
    print("Web accounts:")
    for u in users:
        print(f"  • {u['username']:<20} {u['role']}")


def cmd_all(args) -> None:
    cmd_fetch(args)
    cmd_parse(args)
    if not getattr(args, "skip_pdf", False):
        cmd_pdf(args)
    cmd_markdown(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="publix_archiver", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--timeout", type=int, default=300,
                        help="seconds to wait for interactive login")
        sp.add_argument("--channel", default=None,
                        choices=["chrome", "msedge", "chromium"],
                        help="browser to drive (default: real Chrome, then Edge, "
                             "then bundled Chromium)")

    sp = sub.add_parser("login", help="interactive browser login")
    add_common(sp)
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("paste-token",
                        help="manually supply the AccessTokenJwt + EcmsId from your browser")
    sp.add_argument("--file", default=None,
                    help="path to a file containing the JSON/cookie blob (instead of clipboard)")
    sp.set_defaults(func=cmd_paste_token)

    sp = sub.add_parser("import-curl",
                        help="import exact headers from DevTools 'Copy as cURL' of "
                             "the PurchaseHistory request")
    sp.add_argument("--file", default=None,
                    help="path to a file with the cURL command (instead of clipboard)")
    sp.set_defaults(func=cmd_import_curl)

    sp = sub.add_parser("fetch", help="download all in-store receipts")
    add_common(sp)
    sp.add_argument("--page-size", type=int, default=25,
                    help="purchase-list page size (default 25)")
    sp.add_argument("--from", dest="from_date", default=None,
                    help="earliest date to fetch, YYYY-MM-DD (optional)")
    sp.add_argument("--to", dest="to_date", default=None,
                    help="latest date to fetch, YYYY-MM-DD (optional)")
    sp.add_argument("--all", dest="skip_existing", action="store_false",
                    help="re-fetch every receipt, even those already on disk")
    sp.add_argument("--no-skip-existing", dest="skip_existing", action="store_false",
                    help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_fetch, skip_existing=True)

    sp = sub.add_parser("parse", help="build deduplicated CSVs")
    add_common(sp)
    sp.set_defaults(func=cmd_parse)

    sp = sub.add_parser("import",
                        help="ingest saved receipt JSON (from the browser snippet)")
    sp.add_argument("paths", nargs="*", help="receipt .json files or directories")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("pdf", help="render each receipt to a PDF archive (data/pdfs)")
    sp.add_argument("--force", action="store_true",
                    help="rewrite every PDF even if unchanged (default: only "
                         "overwrite when the re-render differs)")
    sp.set_defaults(func=cmd_pdf)

    sp = sub.add_parser("web", help="launch the local receipt search UI")
    sp.add_argument("--host", default=config.WEB_HOST,
                    help="bind host (env: PUBLIX_WEB_HOST; default 127.0.0.1)")
    sp.add_argument("--port", type=int, default=config.WEB_PORT,
                    help="bind port (env: PUBLIX_WEB_PORT or PORT; default 8000)")
    sp.set_defaults(func=cmd_web)

    sp = sub.add_parser("auth", help="manage web-UI accounts (roles + password + TOTP MFA)")
    asub = sp.add_subparsers(dest="auth_command", required=True)
    a = asub.add_parser("adduser",
                        help="create an account (prompts for password; first user "
                             "is always admin)")
    a.add_argument("username")
    a.add_argument("--role", choices=list(webauth.VALID_ROLES), default=None,
                   help="account role (default: operator; the first account is "
                        "forced to admin)")
    a.set_defaults(func=cmd_auth_adduser)
    a = asub.add_parser("setrole", help="change an account's role (admin/operator)")
    a.add_argument("username")
    a.add_argument("role", choices=list(webauth.VALID_ROLES))
    a.set_defaults(func=cmd_auth_setrole)
    a = asub.add_parser("passwd", help="change an account's password")
    a.add_argument("username")
    a.set_defaults(func=cmd_auth_passwd)
    a = asub.add_parser("reset-mfa", help="regenerate an account's TOTP secret")
    a.add_argument("username")
    a.set_defaults(func=cmd_auth_reset_mfa)
    a = asub.add_parser("deluser", help="delete an account")
    a.add_argument("username")
    a.set_defaults(func=cmd_auth_deluser)
    a = asub.add_parser("users", help="list accounts and their roles")
    a.set_defaults(func=cmd_auth_users)

    sp = sub.add_parser("markdown",
                        help="generate a Markdown archive (index + per-receipt pages)")
    sp.set_defaults(func=cmd_markdown)

    sp = sub.add_parser("refresh",
                        help="refresh metadata (PDF, Markdown) for one receipt")
    sp.add_argument("receipt_id", help="receipt id / key")
    sp.add_argument("--no-pdf", action="store_true", help="skip PDF re-render")
    sp.set_defaults(func=cmd_refresh)

    sp = sub.add_parser("all", help="fetch -> parse -> pdf -> markdown")
    add_common(sp)
    sp.add_argument("--page-size", type=int, default=25)
    sp.add_argument("--from", dest="from_date", default=None)
    sp.add_argument("--to", dest="to_date", default=None)
    sp.add_argument("--all", dest="skip_existing", action="store_false")
    sp.add_argument("--skip-pdf", action="store_true")
    sp.set_defaults(func=cmd_all, skip_existing=True)

    sp = sub.add_parser("backup", help="create/restore compressed backups of data/raw")
    bsub = sp.add_subparsers(dest="backup_command", required=True)
    b = bsub.add_parser("create", help="create a compressed backup of all raw receipts")
    b.set_defaults(func=cmd_backup_create)
    b = bsub.add_parser("list", help="list existing backups")
    b.set_defaults(func=cmd_backup_list)
    b = bsub.add_parser("restore", help="restore a backup (skips receipts already on disk)")
    b.add_argument("name", help="backup filename (see `backup list`)")
    b.add_argument("--overwrite", action="store_true",
                   help="overwrite receipts already on disk instead of skipping")
    b.add_argument("--no-parse", action="store_true",
                   help="don't rebuild CSVs/Markdown after restoring")
    b.set_defaults(func=cmd_backup_restore)
    b = bsub.add_parser("delete", help="delete a backup")
    b.add_argument("name", help="backup filename")
    b.set_defaults(func=cmd_backup_delete)

    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
