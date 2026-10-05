"""
Several saved credential profiles per file (SAP logon, SAP for Me).

File format: {"active": <index into profiles>, "profiles": [{...}, ...]}. The older format -- one
flat credentials object -- is read as a single active profile and rewritten in the new format on
the next save. A profile is identified by *key_fields* (e.g. system + client + username), so
saving the same identity again updates it instead of adding a duplicate.
"""

import json
from pathlib import Path


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _identity(profile: dict, key_fields: tuple[str, ...]) -> tuple:
    return tuple(_norm(profile.get(f)) for f in key_fields)


def load(path: Path) -> dict:
    """Return {"active": int, "profiles": [...]}; empty profiles if the file is missing/unreadable."""
    empty = {"active": 0, "profiles": []}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty
    if isinstance(data, dict) and isinstance(data.get("profiles"), list):
        profiles = [p for p in data["profiles"] if isinstance(p, dict)]
        active = data.get("active", 0)
        active = active if isinstance(active, int) and 0 <= active < len(profiles) else 0
        return {"active": active, "profiles": profiles}
    if isinstance(data, dict) and data:  # legacy single profile
        return {"active": 0, "profiles": [data]}
    return empty


def _write(path: Path, store: dict) -> None:
    path.write_text(json.dumps(store, indent=2), encoding="utf-8")


def active_profile(path: Path) -> dict:
    store = load(path)
    return store["profiles"][store["active"]] if store["profiles"] else {}


def save(path: Path, profile: dict, key_fields: tuple[str, ...]) -> None:
    """Add *profile*, or update the existing one with the same identity, and make it active."""
    store = load(path)
    ident = _identity(profile, key_fields)
    for i, existing in enumerate(store["profiles"]):
        if _identity(existing, key_fields) == ident:
            store["profiles"][i] = profile
            store["active"] = i
            break
    else:
        store["profiles"].append(profile)
        store["active"] = len(store["profiles"]) - 1
    _write(path, store)


def select(path: Path, identity: dict, key_fields: tuple[str, ...]) -> bool:
    """Make the profile matching *identity* active. Returns False if there is none."""
    store = load(path)
    ident = _identity(identity, key_fields)
    for i, existing in enumerate(store["profiles"]):
        if _identity(existing, key_fields) == ident:
            store["active"] = i
            _write(path, store)
            return True
    return False


def delete(path: Path, identity: dict, key_fields: tuple[str, ...]) -> bool:
    """Remove the profile matching *identity*. Returns False if there is none."""
    store = load(path)
    ident = _identity(identity, key_fields)
    kept = [p for p in store["profiles"] if _identity(p, key_fields) != ident]
    if len(kept) == len(store["profiles"]):
        return False
    current = store["profiles"][store["active"]] if store["profiles"] else None
    store["profiles"] = kept
    store["active"] = kept.index(current) if current in kept else 0
    _write(path, store)
    return True
