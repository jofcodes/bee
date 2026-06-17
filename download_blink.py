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


def _apply_auth_workarounds() -> None:
    """Monkey-patch blinkpy to work around known auth bugs."""

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
    try:
        await blink.start()
    except Exception as exc:
        log.error("Login failed: %s", exc)
        log.error(
            "If you're seeing auth errors, Blink may have changed their API again.\n"
            "Check https://github.com/fronzbot/blinkpy/issues for updates.\n"
            "Fallback: pull clips via USB from your Sync Module 2."
        )
        sys.exit(1)

    # Handle 2FA if needed
    if blink.key_required:
        log.info("2FA required — check your email/phone for a PIN.")
        pin = input("Enter 2FA PIN: ").strip()
        try:
            result = await auth.send_auth_key(blink, pin)
            log.info("2FA result: %s", result)
            await blink.setup_post_verify()
        except Exception as exc:
            log.error("2FA verification failed: %s", exc)
            log.error(
                "This may be the HTTP 202/412 bug (#1233). "
                "Try updating blinkpy or use USB pull."
            )
            sys.exit(1)

    # Save session token to avoid 2FA next time
    if auth.token:
        CREDS_FILE.write_text(json.dumps(auth.token))
        log.info("Saved session token to %s (delete to force re-auth)", CREDS_FILE)

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
    if days:
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
        "--email",
        type=str,
        default=None,
        help="Blink account email (will prompt if not provided).",
    )
    args = parser.parse_args()

    email = args.email or input("Blink email: ").strip()
    password = getpass.getpass("Blink password: ")

    count = asyncio.run(download_clips(email, password, args.output, args.days))

    if count > 0:
        print(f"\nDone! {count} clips saved to {args.output}")
        print(f"Now analyze them:")
        print(f"  python run.py {args.output}")
    else:
        print("\nNo clips downloaded. Try USB pull from your Sync Module 2.")


if __name__ == "__main__":
    main()
