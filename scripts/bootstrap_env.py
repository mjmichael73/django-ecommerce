#!/usr/bin/env python3
"""
Ensure a repo-root .env exists and contains a strong SECRET_KEY.

Docker Compose requires SECRET_KEY (no insecure default). Run via: make env
"""
from __future__ import annotations

import re
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
ENV = ROOT / ".env"

WEAK_SUBSTRINGS = (
    "change-me",
    "django-insecure",
    "insecure",
    "replace-me",
    "your-secret",
    "example.com",
    "fixme",
    "todo",
)


def secret_key_needs_replace(value: str) -> bool:
    v = (value or "").strip().strip('"').strip("'")
    if len(v) < 32:
        return True
    low = v.lower()
    return any(s in low for s in WEAK_SUBSTRINGS)


def main() -> int:
    if not ENV_EXAMPLE.is_file():
        print(f"Missing {ENV_EXAMPLE}", file=sys.stderr)
        return 1
    if not ENV.is_file():
        shutil.copy(ENV_EXAMPLE, ENV)
        print(f"Created {ENV} from .env.example")

    text = ENV.read_text(encoding="utf-8")
    m = re.search(r"^SECRET_KEY=(.*)$", text, re.MULTILINE)
    raw_val = m.group(1) if m else ""

    if secret_key_needs_replace(raw_val):
        key = secrets.token_urlsafe(48)
        if m:
            text, n = re.subn(
                r"^SECRET_KEY=.*$",
                f"SECRET_KEY={key}",
                text,
                count=1,
                flags=re.MULTILINE,
            )
            if n != 1:
                print("Could not update SECRET_KEY line in .env", file=sys.stderr)
                return 1
        else:
            sep = "\n" if text and not text.endswith("\n") else ""
            text = f"{text}{sep}SECRET_KEY={key}\n"
        ENV.write_text(text, encoding="utf-8")
        print("Wrote a new SECRET_KEY to .env — keep this file out of version control.")

    print(".env is ready for docker compose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
