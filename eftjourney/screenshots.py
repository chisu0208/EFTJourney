"""Position samples from EFT in-game screenshot filenames.

The game names screenshots after the moment and the camera pose, e.g.
    2026-09-18[01-14]_616.67, 1.05, -103.50_0.04218, -0.71487, 0.04329, 0.69664_14.65 (0).png
    local date[hour-minute] _ camera x, y(up), z _ quaternion x, y, z, w _ in-raid time of day (counter)
Unity is Y-up and left-handed, so the top-down map plane is (x, z); y is eye height.
Menus and post-raid screens have no camera, so those names carry no position; the
moment of death can carry a sentinel position far below the map (y about -9997).
"""
from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

_NUM = r"-?\d+(?:\.\d+)?"
SHOT_RE = re.compile(
    rf"^(?P<date>\d{{4}}-\d{{2}}-\d{{2}})\[(?P<hour>\d{{2}})-(?P<minute>\d{{2}})\]"
    rf"_(?P<x>{_NUM}), ?(?P<y>{_NUM}), ?(?P<z>{_NUM})"
    rf"_(?P<qx>{_NUM}), ?(?P<qy>{_NUM}), ?(?P<qz>{_NUM}), ?(?P<qw>{_NUM})"
    r"(?:_(?P<tod>\d+(?:[.,]\d+)?))?"
    r"(?: ?\((?P<n>\d+)\))?\.(?P<ext>png|jpe?g)$",
    re.IGNORECASE,
)
SHOT_EXTS = (".png", ".jpg", ".jpeg")
TIME_SOURCES = ("birthtime", "mtime", "name")
# The name has minutes only; a file time belongs to [minute, minute + 60 s) plus a little
# write delay. Outside that (copied/edited files) the name's minute is used instead.
TIME_TOLERANCE = timedelta(seconds=90)
# How far a journaled time can be from the minute its name gives: a file time (above), or one
# restored from a stored route, where a shot from just before GameStarted sits at the start
JOURNAL_SLACK = TIME_TOLERANCE + timedelta(seconds=11)
MAX_HORIZONTAL = 5000.0  # metres; no map is anywhere near this big
MIN_HEIGHT = -1000.0  # below this is the death sentinel, not a place


@dataclass(frozen=True)
class Shot:
    name: str
    taken: datetime  # local wall-clock time
    x: float
    y: float
    z: float
    qx: float
    qy: float
    qz: float
    qw: float
    yaw: float  # degrees, 0 = facing +Z, 90 = facing +X (clockwise seen from above)
    time_source: str  # "birthtime" | "mtime" | "name"
    tod: float | None = None  # in-raid time of day in hours (14.65 = 14:39)


def yaw_from_quaternion(qx: float, qy: float, qz: float, qw: float) -> float:
    """Heading of Unity's forward vector (q * +Z) on the x/z plane; pitch and roll do not leak in."""
    fx = 2.0 * (qx * qz + qw * qy)
    fz = 1.0 - 2.0 * (qx * qx + qy * qy)
    if abs(fx) < 1e-9 and abs(fz) < 1e-9:
        return 0.0
    return math.degrees(math.atan2(fx, fz)) % 360.0


def plausible(x: float, y: float, z: float) -> bool:
    return abs(x) <= MAX_HORIZONTAL and abs(z) <= MAX_HORIZONTAL and y > MIN_HEIGHT


def parse_name(name: str) -> dict | None:
    """Fields from a screenshot filename, or None if it has no usable position."""
    m = SHOT_RE.match(name)
    if not m:
        return None
    try:
        named = datetime.strptime(f"{m['date']} {m['hour']}:{m['minute']}", "%Y-%m-%d %H:%M")
        named.astimezone()  # Windows cannot place a date before 1970 or in the year 9999: no real shot
    except (ValueError, OverflowError, OSError):
        return None
    values = {k: float(m[k]) for k in ("x", "y", "z", "qx", "qy", "qz", "qw")}
    if not all(math.isfinite(v) for v in values.values()) or not plausible(values["x"], values["y"], values["z"]):
        return None
    tod = float(m["tod"].replace(",", ".")) if m["tod"] else None
    return {"named": named, "tod": tod if tod is not None and 0 <= tod < 24 else None, **values}


@lru_cache(maxsize=None)
def _named(name: str) -> datetime | None:
    """The minute a screenshot name gives (None without a usable position), remembered: every
    scan checks every journal entry against it."""
    info = parse_name(name)
    return info["named"] if info else None


def _file_times(st: os.stat_result) -> list[tuple[datetime, str]]:
    times = []
    birth = getattr(st, "st_birthtime", None)
    if birth is None and sys.platform == "win32" and sys.version_info < (3, 12):
        birth = st.st_ctime  # creation time on Windows before 3.12
    if birth:
        times.append((datetime.fromtimestamp(birth), "birthtime"))
    times.append((datetime.fromtimestamp(st.st_mtime), "mtime"))
    return times


def _shot(name: str, info: dict, taken: datetime, source: str) -> Shot:
    return Shot(
        name=name,
        taken=taken,
        x=info["x"],
        y=info["y"],
        z=info["z"],
        qx=info["qx"],
        qy=info["qy"],
        qz=info["qz"],
        qw=info["qw"],
        yaw=round(yaw_from_quaternion(info["qx"], info["qy"], info["qz"], info["qw"]), 1),
        time_source=source,
        tod=info["tod"],
    )


def shot_from_path(path: Path) -> Shot | None:
    info = parse_name(path.name)
    if info is None:
        return None
    named: datetime = info["named"]
    taken, source = named, "name"
    try:
        candidates = _file_times(path.stat())
    except OSError:
        candidates = []
    for when, kind in candidates:
        if named - TIME_TOLERANCE <= when <= named + timedelta(minutes=1) + TIME_TOLERANCE:
            taken, source = when, kind
            break
    return _shot(path.name, info, taken, source)


def shot_from_name(name: str, taken: datetime | None = None) -> Shot | None:
    """A shot from its file name alone, for a file that is gone: taken at `taken` (a file time
    kept elsewhere; which one is not known, and only "name" changes how a shot is placed) or,
    without it, at the minute the name gives. None if the name carries no position."""
    info = parse_name(name)
    if info is None:
        return None
    return _shot(name, info, taken, "mtime") if taken else _shot(name, info, info["named"], "name")


def matches_name(named: datetime, taken: datetime) -> bool:
    """Whether `taken` can be the moment of a shot whose name gives the minute `named`."""
    return named - JOURNAL_SLACK <= taken <= named + timedelta(minutes=1) + JOURNAL_SLACK


def shot_order(shot: Shot) -> tuple:
    """Sort key: time, then in-raid time of day for shots that only know their minute."""
    return (shot.taken, shot.tod if shot.tod is not None else -1.0, shot.name)


def shot_to_json(shot: Shot) -> dict:
    doc = asdict(shot)
    doc.pop("name")
    doc["taken"] = shot.taken.isoformat(timespec="milliseconds")
    return doc


def _real(value) -> float | None:
    """A finite JSON number as float; anything else (text, null, bool, NaN) is None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:  # an integer too big for a float
        return None
    return value if math.isfinite(value) else None


def shot_from_json(name: str, doc: dict) -> Shot | None:
    """A journal entry back as a Shot, or None if any field is missing or of the wrong kind.

    The journal can be edited by hand or by other tools; one odd entry must not break the
    sorting, window tests and time conversions of every scan.
    """
    if not isinstance(doc, dict) or doc.get("time_source") not in TIME_SOURCES:
        return None
    values = {k: _real(doc.get(k)) for k in ("x", "y", "z", "qx", "qy", "qz", "qw", "yaw")}
    if None in values.values():
        return None
    tod = doc.get("tod")
    if tod is not None:
        tod = _real(tod)
        if tod is None or not 0 <= tod < 24:
            return None
    if not isinstance(doc.get("taken"), str):
        return None
    try:
        taken = datetime.fromisoformat(doc["taken"])
        if taken.tzinfo is not None:  # everything else here is local wall-clock time
            taken = taken.astimezone().replace(tzinfo=None)
        # a name without a minute (made by hand) has nothing to check the time against, and Windows
        # cannot place one before 1970 or in the year 9999: no real shot
        taken.astimezone()
    except (ValueError, OverflowError, OSError):
        return None
    named = _named(name)
    if named is not None and not matches_name(named, taken):
        return None  # far from the minute its name gives: a placeholder date, another time zone
    return Shot(name=name, taken=taken, time_source=doc["time_source"], tod=tod, **values)


def scan_dir(folder: Path | None) -> list[Shot] | None:
    """Positioned screenshots in the folder, oldest first; None when there is no folder or it
    cannot be listed (unlike [] for an empty one), so callers do not take it for "all deleted"."""
    if not folder:
        return None
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return None
    shots = []
    for entry in entries:
        if not entry.is_file() or not entry.name.lower().endswith(SHOT_EXTS):
            continue
        shot = shot_from_path(Path(entry.path))
        if shot is not None:
            shots.append(shot)
    shots.sort(key=shot_order)
    return shots
