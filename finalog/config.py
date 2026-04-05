"""Configuration management for finalog.

Stores user config in ~/.finalog/config.json so credentials
persist across sessions without relying on .env files.
"""

import json
import os
import shutil

CONFIG_DIR = os.path.expanduser("~/.finalog")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "gemini_api_key": "",
    "google_sheet_id": "",
    "google_sheets_creds": "",  # path to service-account JSON
}


def _ensure_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load() -> dict:
    """Load config from disk, returning defaults for missing keys."""
    _ensure_dir()
    if not os.path.exists(CONFIG_FILE):
        return dict(DEFAULTS)
    with open(CONFIG_FILE) as f:
        stored = json.load(f)
    merged = dict(DEFAULTS)
    merged.update(stored)
    return merged


def save(cfg: dict):
    """Write config to disk."""
    _ensure_dir()
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"Config saved to {CONFIG_FILE}")


def is_configured() -> bool:
    """Return True if the minimum required keys are set."""
    cfg = load()
    return bool(cfg.get("gemini_api_key") and cfg.get("google_sheet_id") and cfg.get("google_sheets_creds"))


def run_setup():
    """Interactive first-time configuration wizard."""
    print("=" * 50)
    print("  finalog — first-time setup")
    print("=" * 50)
    print()

    cfg = load()

    def _prompt(key: str, label: str):
        current = cfg.get(key, "")
        hint = f" [{current}]" if current else ""
        val = input(f"{label}{hint}: ").strip()
        if val:
            cfg[key] = val

    _prompt("gemini_api_key", "Gemini API key")
    _prompt("google_sheet_id", "Google Sheet ID (from the URL)")

    # Sheets credentials file — copy into ~/.finalog for portability
    current_creds = cfg.get("google_sheets_creds", "")
    hint = f" [{current_creds}]" if current_creds else ""
    creds_input = input(f"Path to Google service-account JSON{hint}: ").strip()
    if creds_input:
        src = os.path.expanduser(creds_input)
        if not os.path.isfile(src):
            print(f"  Warning: '{src}' not found — saving path anyway.")
            cfg["google_sheets_creds"] = src
        else:
            dest = os.path.join(CONFIG_DIR, "sheets_creds.json")
            shutil.copy2(src, dest)
            cfg["google_sheets_creds"] = dest
            print(f"  Copied credentials to {dest}")

    save(cfg)
    print()
    print("Setup complete! Run `finalog start` to launch.")


def apply_to_env(cfg: dict | None = None):
    """Push config values into environment variables so existing code works."""
    if cfg is None:
        cfg = load()
    os.environ.setdefault("GEMINI_API_KEY", cfg.get("gemini_api_key", ""))
    os.environ.setdefault("GOOGLE_SHEET_ID", cfg.get("google_sheet_id", ""))
    os.environ.setdefault("GOOGLE_SHEETS_CREDS", cfg.get("google_sheets_creds", ""))
