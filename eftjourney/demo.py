"""A made-up Customs raid so the viewer can be tried before any real screenshot exists.

Route: Trailer Park -> Big Red -> Junk Bridge -> Sniper Roadblock -> Dorms (2nd floor)
-> New Gas, where the player dies. Coordinates are tarkov.dev/game coordinates.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from .store import RaidStore

# (seconds since raid start, x, y, z) - y is camera height, 2nd floor of the dorms is y 2.7..6.5
WAYPOINTS = [
    (14, -300.0, 1.6, -224.0),
    (75, -262.0, 1.6, -188.0),
    (150, -228.0, 1.7, -150.0),
    (236, -171.0, 1.6, -96.0),
    (318, -121.0, 1.5, -27.0),
    (392, -75.0, 2.4, 31.0),
    (468, -31.0, 1.4, 94.0),
    (556, 38.0, 1.3, 128.0),
    (648, 109.0, 1.6, 149.0),
    (735, 166.0, 1.7, 163.0),
    (808, 204.0, 4.6, 171.0),
    (846, 214.0, 4.6, 158.0),
    (884, 226.0, 1.7, 147.0),
    (962, 281.0, 1.6, 113.0),
    (1041, 339.0, 1.6, 79.0),
    (1102, 377.0, 1.7, 51.0),
    (1128, 391.0, 1.6, 41.0),
]
KILLS = [
    {"t": 301.0, "name": "Zheka Yuvelir", "level": None, "side": "scav", "weapon": "AKS-74U", "bodyPart": "thorax", "distance": 41.6},
    {"t": 603.0, "name": "Sleepy_Lion", "level": 31, "side": "bear", "weapon": "M4A1", "bodyPart": "head", "distance": 63.5},
    {"t": 841.0, "name": "Foma Znahar", "level": None, "side": "scav", "weapon": "MP-133", "bodyPart": "stomach", "distance": 6.2},
]
DEATH = {"status": "killed", "t": 1131.0, "killer": "Reshala", "killerSide": "boss", "weapon": "TT pistol (Golden)",
         "bodyPart": "head", "distance": 18.4, "source": "manual"}
TOD_START = 14.17  # in-raid clock at spawn; runs 7x real time


def _heading(a: tuple, b: tuple) -> float:
    return round(math.degrees(math.atan2(b[1] - a[1], b[3] - a[3])) % 360.0, 1)


def build_demo(start: datetime) -> dict:
    track = []
    for i, (t, x, y, z) in enumerate(WAYPOINTS):
        nxt = WAYPOINTS[min(i + 1, len(WAYPOINTS) - 1)]
        prev = WAYPOINTS[max(i - 1, 0)]
        yaw = _heading((t, x, y, z), nxt) if nxt[0] != t else _heading(prev, (t, x, y, z))
        track.append({"t": float(t), "x": x, "y": y, "z": z, "yaw": round((yaw + (17 if i % 3 == 1 else -9)) % 360, 1),
                      "tod": round(TOD_START + 7 * t / 3600, 2)})
    end = start + timedelta(seconds=1135)
    return {
        "schema": 1,
        "generator": "demo",
        "demo": True,
        "id": f"{start:%Y%m%d-%H%M%S}-bigmap",
        "title": "데모: 세관 기숙사 런",
        "map": "bigmap",
        "mode": "pve",
        "kind": "local",
        "raidId": None,
        "startedAt": start.astimezone().isoformat(timespec="milliseconds"),
        "endedAt": end.astimezone().isoformat(timespec="milliseconds"),
        "endReason": "raid-end",
        "duration": 1135.0,
        "durationApprox": False,
        "mapSource": "demo",
        "transitFromPrev": False,
        "exitViaTransit": False,
        "insuranceHint": False,
        "source": {"logSession": None, "gameVersion": None},
        "track": track,
        "logFixes": [],
        "kills": [{"id": f"k{i + 1}", "note": "", "source": "manual", **k} for i, k in enumerate(KILLS)],
        "outcome": DEATH,
    }


def create_demo(store: RaidStore, maps: dict | None = None) -> str:
    """Replace earlier untouched demo raids with a fresh one and return its id."""
    for raid_id in store.list_ids():
        raid = store.load_raid(raid_id) or {}
        if raid.get("demo") and not store.load_edits(raid_id):
            store.delete_raid(raid_id)
    doc = build_demo(datetime.now().replace(second=0, microsecond=0))
    store.save_raid(doc)
    return doc["id"]
