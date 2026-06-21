"""
Multi-zone storage.

Holds multiple operator-drawn polygons, each with an id, a list of normalised
[0, 1] points, and a riskLevel ('Low', 'Medium', 'High').

Persisted in restricted_zone.json across server restarts.

Old single-zone format (flat list of {x, y} dicts) is auto-migrated to
the new array-of-zones format on first load.
"""
import json
import os
import uuid

_ZONE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "restricted_zone.json")

# In-memory cache — list of {"id": str, "points": [{x, y}, ...], "riskLevel": str} dicts.
_zones: list = []


def load() -> list:
    """Load persisted zones from disk into the in-memory cache. Call once at startup."""
    global _zones
    if not os.path.exists(_ZONE_FILE):
        print("[INFO] ZoneStore: no zone file on disk — starting empty.")
        return _zones

    try:
        with open(_ZONE_FILE) as f:
            data = json.load(f)

        # Migrate old format: flat list of {x, y} points → single zone object
        if data and isinstance(data, list) and data[0] and "x" in data[0]:
            _zones = [{
                "id":        f"zone_{uuid.uuid4().hex[:8]}",
                "points":    data,
                "riskLevel": "Medium",
            }]
            _persist()
            print("[INFO] ZoneStore: migrated legacy single-zone to multi-zone format.")
        else:
            _zones = data if isinstance(data, list) else []
            print(f"[INFO] ZoneStore: loaded {len(_zones)} zone(s) from disk.")
    except Exception as exc:
        print(f"[WARNING] ZoneStore: could not load zone file — {exc}")
        _zones = []

    return _zones


def save(zones: list) -> None:
    """Replace all zones and persist to disk.

    Each zone is:  {"id": str, "points": [{x, y}], "riskLevel": "Low"|"Medium"|"High"}
    """
    global _zones
    _zones = zones
    _persist()
    print(f"[INFO] ZoneStore: saved {len(zones)} zone(s).")


def get() -> list:
    """Return the current list of zone dicts."""
    return _zones


def add_or_update(zone: dict) -> None:
    """Upsert a single zone by id and persist."""
    global _zones
    zone_id = zone.get("id")
    for i, z in enumerate(_zones):
        if z.get("id") == zone_id:
            _zones[i] = zone
            _persist()
            return
    _zones.append(zone)
    _persist()


def remove(zone_id: str) -> bool:
    """Remove a zone by id. Returns True if found and removed."""
    global _zones
    before = len(_zones)
    _zones = [z for z in _zones if z.get("id") != zone_id]
    if len(_zones) < before:
        _persist()
        return True
    return False


def _persist() -> None:
    try:
        with open(_ZONE_FILE, "w") as f:
            json.dump(_zones, f)
    except Exception as exc:
        print(f"[WARNING] ZoneStore: could not write zone file — {exc}")
