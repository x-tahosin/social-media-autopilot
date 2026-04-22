"""
Shared credential loader for diagnostic scripts.

Reads from (in order of precedence):
  1. Environment variables
  2. /opt/autopilot/.creds.json (on the VM)
  3. ./automation/.creds.json (for local dev)

Usage:
    from _creds import get
    API_KEY = get("N8N_API_KEY")
"""
import os, sys, json, pathlib

_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    paths = [
        os.environ.get("AUTOPILOT_CREDS_FILE"),
        "/opt/autopilot/.creds.json",
        str(pathlib.Path(__file__).resolve().parent / ".creds.json"),
    ]
    data = {}
    for p in paths:
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                break
            except Exception:
                pass
    _cache = data
    return data


def get(key, default=None, required=False):
    """Fetch a credential by name. Env var wins over file."""
    v = os.environ.get(key)
    if v:
        return v
    v = _load().get(key, default)
    if required and not v:
        sys.exit(f"Missing required credential: {key}. Set env var or add to .creds.json")
    return v
