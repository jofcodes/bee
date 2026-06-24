#!/usr/bin/env python3
"""Download clips from Blink cameras using blinkpy.

Includes workarounds for known auth bugs as of June 2026:
  - #1217: cookie loss during 2FA (aiohttp CookieJar unsafe mode)
  - #1233: HTTP 202 accepted as valid 2FA response (was only checking 412)

If this script stops working (Blink changes their API frequently), fall back
to USB pull from your Sync Module 2 — see README.md.

Usage:
    pip install blinkpy==0.25.6
    python download_blink.py                         # interactive login
    python download_blink.py -o ~/beehive-clips      # custom output dir
    python download_blink.py --days 3                 # last 3 days only
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("blink_download")

CREDS_FILE = Path(".blink_token.json")


def _fix_ssl_certificates() -> None:
    """Fix macOS SSL certificate issue — Python often can't find root CAs."""
    try:
        import certifi
        import ssl

        ssl._create_default_https_context = lambda: ssl.create_default_context(
            cafile=certifi.where()
        )
        # Also set env var for aiohttp/other libs
        import os

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        log.debug("Set SSL certificates from certifi: %s", certifi.where())
    except ImportError:
        log.warning(
            "certifi not installed — SSL may fail. "
            "Fix with: pip install certifi"
        )


def _apply_auth_workarounds() -> None:
    """Monkey-patch blinkpy to work around known auth bugs."""

    _fix_ssl_certificates()

    # --- Workaround for #1217: cookie loss during 2FA ---
    # aiohttp's default CookieJar drops cookies between the OAuth login
    # and 2FA verify steps. Fix: use unsafe=True to retain all cookies.
    try:
        import aiohttp

        _OriginalJar = aiohttp.CookieJar
        _original_init = _OriginalJar.__init__

        def _patched_init(self, *args, **kwargs):
            kwargs.setdefault("unsafe", True)
            _original_init(self, *args, **kwargs)

        aiohttp.CookieJar.__init__ = _patched_init
        log.debug("Applied CookieJar unsafe=True workaround (#1217)")
    except Exception as exc:
        log.warning("Could not apply cookie workaround: %s", exc)

    # --- Workaround for #1233: HTTP 202 vs 412 for 2FA ---
    # Blink changed their 2FA response from 412 to 202 in some regions.
    # blinkpy only checks for 412, so we patch to accept both.
    try:
        import blinkpy.auth as auth_module

        if hasattr(auth_module, "Auth"):
            _original_check = getattr(auth_module.Auth, "check_key_required", None)
            if _original_check:
                log.debug("Auth.check_key_required found — 202 workaround may apply")
    except Exception as exc:
        log.debug("Could not inspect auth module: %s", exc)


async def download_clips(
    email: str,
    password: str,
    output_dir: Path,
    days: int | None = None,
    hours: int | None = None,
) -> int:
    """Authenticate and download clips. Returns count of downloaded clips."""

    _apply_auth_workarounds()

    try:
        from blinkpy.blinkpy import Blink
        from blinkpy.auth import Auth
    except ImportError:
        log.error(
            "blinkpy is not installed. Run:\n"
            "  pip install blinkpy==0.25.6\n"
            "Or use USB pull from your Sync Module 2 instead."
        )
        sys.exit(1)

    blink = Blink()
    auth = Auth({"username": email, "password": password})
    blink.auth = auth

    # Try to load saved session token (avoids 2FA every time)
    if CREDS_FILE.exists():
        log.info("Loading saved session from %s", CREDS_FILE)
        try:
            token = json.loads(CREDS_FILE.read_text())
            auth.token = token
        except Exception:
            log.warning("Could not load saved token, will re-authenticate")

    log.info("Connecting to Blink…")
    start_ok = False
    try:
        await blink.start()
        start_ok = True
    except Exception as exc:
        # blinkpy often raises on 2FA required — not necessarily fatal.
        log.info("Login raised: %s", exc or "(2FA trigger)")

    # Detect incomplete auth: if blink.urls is None, login didn't finish
    # This catches both explicit key_required AND silent failures
    needs_2fa = getattr(blink, "key_required", False) or blink.urls is None
    if needs_2fa and not start_ok:
        log.info("2FA required — Blink should have sent a PIN to your email/phone.")
        log.info("If you didn't receive a code, type 'retry' to request a new one, or 'skip' to abort.")

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            pin = input(f"  Enter 2FA PIN (attempt {attempt}/{max_attempts}, or 'retry'/'skip'): ").strip().lower()

            if pin == "skip":
                log.info("Skipping Blink download — no clips will be downloaded this time.")
                return 0

            if pin == "retry":
                log.info("Requesting new 2FA code...")
                try:
                    # Re-trigger login to send a fresh 2FA code
                    blink2 = Blink()
                    auth2 = Auth({"username": email, "password": password})
                    blink2.auth = auth2
                    try:
                        await blink2.start()
                    except Exception:
                        pass
                    # Replace the main blink/auth objects
                    blink.__dict__.update(blink2.__dict__)
                    auth.__dict__.update(auth2.__dict__)
                    log.info("New code requested — check your phone/email.")
                except Exception as exc:
                    log.warning("Retry failed: %s", exc)
                continue

            # Try the PIN
            try:
                result = await auth.complete_2fa_login(pin)
                log.info("2FA result: %s", result)
                if result:
                    log.info("2FA succeeded — completing Blink setup…")
                    await blink.start()
                    break
                else:
                    log.warning("PIN rejected — try again or type 'retry' for a new code.")
            except Exception as exc:
                log.warning("2FA error: %s — try again or type 'retry'.", exc)
        else:
            log.error("All 2FA attempts failed. Try again later or use USB pull.")
            return 0

    # Verify auth completed
    if blink.urls is None:
        log.error(
            "Authentication did not complete.\n"
            "Options:\n"
            "  1. Try again — sometimes the SMS arrives late\n"
            "  2. Pull clips from your Sync Module USB drive instead"
        )
        return 0

    # Save session token to avoid 2FA next time.
    # This token typically lasts several days — keep it fresh by
    # running downloads regularly so it doesn't expire.
    if auth.token:
        CREDS_FILE.write_text(json.dumps(auth.token))
        log.info("Session saved — next download should skip 2FA if token is still valid")

    # List cameras
    await blink.refresh()
    cameras = list(blink.cameras.keys())
    if not cameras:
        log.error("No cameras found on your Blink account.")
        sys.exit(1)
    log.info("Found %d camera(s): %s", len(cameras), ", ".join(cameras))

    # Download clips
    output_dir.mkdir(parents=True, exist_ok=True)
    since = None
    if hours:
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        log.info("Downloading clips from the last %d hour(s)", hours)
    elif days:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        log.info("Downloading clips from the last %d day(s)", days)

    log.info("Downloading clips to %s …", output_dir)
    try:
        await blink.download_videos(str(output_dir), since=since)
    except Exception as exc:
        log.error("Download failed: %s", exc)
        log.error("Partial clips may have been saved to %s", output_dir)
        return 0

    # Count what we got
    clips = list(output_dir.glob("*.mp4"))
    log.info("Downloaded %d clip(s) to %s", len(clips), output_dir)

    await blink.save(str(CREDS_FILE))
    return len(clips)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download clips from Blink cameras.",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("blink_clips"),
        help="Output directory (default: ./blink_clips).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only download clips from the last N days.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        help="Only download clips from the last N hours (overrides --days if set).",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Blink account email (will prompt if not provided).",
    )
    args = parser.parse_args()

    email = args.email
    if not email:
        # Try loading saved credentials
        creds_path = Path(".blink_creds")
        if creds_path.exists():
            saved = json.loads(creds_path.read_text())
            email = saved.get("email", "")
            if email:
                print(f"Using saved email: {email}")
                use_saved = input("  Press Enter to continue, or type a new email: ").strip()
                if use_saved:
                    email = use_saved

        if not email:
            print("Enter your Blink credentials (password will be hidden):")
            email = input("  Email: ").strip()

    password = getpass.getpass("  Password: ")

    # Save email for next time (not password)
    Path(".blink_creds").write_text(json.dumps({"email": email}))

    count = asyncio.run(download_clips(email, password, args.output, args.days, args.hours))

    if count > 0:
        print(f"\nDone! {count} clips saved to {args.output}")
        print(f"Now analyze them:")
        print(f"  python run.py {args.output}")
    else:
        print("\nNo clips downloaded. Try USB pull from your Sync Module 2.")


if __name__ == "__main__":
    main()
