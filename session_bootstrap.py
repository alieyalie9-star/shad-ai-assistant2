# -*- coding: utf-8 -*-
"""Restore the first aioshad .session file from GitHub Actions Secrets."""
from __future__ import annotations
import base64
import os
from pathlib import Path

root = Path(__file__).resolve().parent
folder = root / "sessions"
folder.mkdir(parents=True, exist_ok=True)

filename = os.getenv("SHAD_SESSION_FILENAME", "").strip()
encoded = os.getenv("SHAD_SESSION_B64", "").strip()

if not filename or Path(filename).name != filename or not filename.endswith(".session"):
    raise SystemExit("Missing/invalid SHAD_SESSION_FILENAME secret (must be the original .session filename).")

target = folder / filename
if target.exists() and target.stat().st_size > 0:
    print("Existing cached Shad session found.")
elif encoded:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise SystemExit(f"SHAD_SESSION_B64 is not valid Base64: {type(exc).__name__}")
    if not raw:
        raise SystemExit("Decoded session file is empty.")
    target.write_bytes(raw)
    print("Initial Shad session restored from GitHub Secret.")
else:
    raise SystemExit("No cached .session file and SHAD_SESSION_B64 is empty.")
