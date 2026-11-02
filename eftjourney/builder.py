"""Turn log raids + screenshot positions into raid documents in the store.

Every screenshot position is journaled (data/cache/shots.json) the first time it is
seen, so routes survive the PNG files being deleted later. The stored raids hold every
position their routes use, so a lost or damaged journal is filled again from them.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .logs import LogRaid, LogScanner, session_info
from .mapdata import contains, entry_for, guess_map
from .screenshots import Shot, matches_name, scan_dir, shot_from_json, shot_from_name, shot_order, shot_to_json
from .store import RaidStore, _atomic_write_json, _atomic_write_text, clean_edits

SCHEMA = 1
GENERATOR = "eftjourney"
PRE_START = timedelta(seconds=10)  # screenshots a moment before GameStarted still belong to the raid
POST_END = timedelta(seconds=20)
# A raid that ended with the client being closed only has the session's last log line as
# its end. Local raids log a backend keepalive every ~140 s, so the real end can be later.
POST_OPEN = timedelta(seconds=150)
OPEN_RAID = timedelta(hours=3)  # longest a raid without an end marker may count as running
ORPHAN_GAP = timedelta(minutes=15)  # without logs, screenshots are grouped into raids by this gap
NAME_MINUTE = timedelta(minutes=1)  # a shot timed by its filename was taken within this minute
# Positions farther than this outside the raid's map bounds are stale (e.g. a menu screenshot)
# or broken. Real extracts sit up to ~16 m outside tarkov.dev's bounds.
BOUNDS_PAD = 75.0
GAME_EXE = "EscapeFromTarkov.exe"
RUNNING_KEEP = 60.0  # seconds a definite process check still answers for one that failed
JOURNAL_VERSION = 1
REDETECT_EVERY = 30.0  # seconds between looks for a Logs folder that had no sessions yet


@dataclass
class ScanReport:
    sessions: int = 0
    raids_total: int = 0
    samples: int = 0  # journaled positions plus new files: also those of deleted screenshots
    files: int | None = None  # positioned screenshot files in the folder; None if it could not be listed
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    orphan_samples: int = 0  # screenshots outside every raid window
    outside_map: int = 0  # screenshots in a raid window but far outside that raid's map
    in_raid: dict | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.written or self.removed)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone().isoformat(timespec="milliseconds") if dt else None


def _when(text) -> datetime | None:
    """An ISO time read back from one of our JSON files, as aware local time; None if unusable."""
    try:
        return datetime.fromisoformat(text).astimezone()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _wall_clock(text) -> datetime | None:
    """The naive local time an ISO time from _iso() was written for, whatever time zone this
    program runs in now: log raids and screenshot times are wall-clock times too."""
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _sample(shot: Shot, start: datetime, present: set[str] | None = None) -> dict:
    sample = {
        "t": round(max(0.0, (shot.taken - start).total_seconds()), 1),
        "x": round(shot.x, 2),
        "y": round(shot.y, 2),
        "z": round(shot.z, 2),
        "yaw": shot.yaw,
        "shot": shot.name,
    }
    if shot.tod is not None:
        sample["tod"] = shot.tod
    if shot.time_source == "name":
        sample["approx"] = True  # only minute precision from the filename
    if present is not None and shot.name not in present:
        sample["gone"] = True  # the file was deleted; the journal kept its position
    return sample


def _in_window(shot: Shot, lo: datetime, hi: datetime) -> bool:
    if shot.time_source == "name":
        # only the minute is known: the shot was taken somewhere in [named, named + 60 s)
        return shot.taken <= hi and shot.taken + NAME_MINUTE > lo
    return lo <= shot.taken <= hi


def raid_id_for(start: datetime, map_id: str | None) -> str:
    # map ids come from free-text log fields, while ids must stay [a-z0-9_]
    key = re.sub(r"[^a-z0-9_]+", "_", (map_id or "unknown").lower()).strip("_") or "unknown"
    return f"{start:%Y%m%d-%H%M%S}-{key}"


def _log_fixes(lr: LogRaid, end: datetime | None, entry: dict | None = None) -> list[dict]:
    """Server movement-correction positions (online raids only), seconds since start.

    entry: the raid's map; positions far outside its bounds are dropped like screenshots.
    """
    fixes = []
    for fix in lr.fixes:
        when = datetime.fromisoformat(fix["time"])
        t = (when - lr.start).total_seconds()
        if t < -PRE_START.total_seconds() or (end and when > end + POST_END):
            continue
        if entry and entry.get("bounds") and not contains(entry, fix["x"], fix["z"], BOUNDS_PAD):
            continue
        fixes.append({"t": round(max(0.0, t), 1), "x": round(fix["x"], 2), "y": round(fix["y"], 2),
                      "z": round(fix["z"], 2), "reason": fix["reason"]})
    return fixes


def _log_doc(lr: LogRaid, picked: list[Shot], maps: dict, present: set[str] | None = None) -> dict:
    track = [_sample(s, lr.start, present) for s in picked]
    end = lr.end
    if picked and (end is None or picked[-1].taken > end):
        end = picked[-1].taken  # screenshots prove the raid lasted at least this long
    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        # the id only uses what the logs say, so it stays stable while screenshots arrive
        "id": raid_id_for(lr.start, lr.map_id),
        "map": lr.map_id or guess_map(maps, track),
        "mode": lr.mode,
        "kind": lr.kind,
        "raidId": lr.raid_id,
        "startedAt": _iso(lr.start),
        "endedAt": _iso(end),
        "endReason": lr.end_reason,
        "duration": round((end - lr.start).total_seconds(), 1) if end else None,
        "durationApprox": lr.end_reason in ("open", "unknown"),
        "mapSource": lr.map_source,
        "transitFromPrev": lr.transit_from_prev,
        "exitViaTransit": lr.exit_via_transit,
        "insuranceHint": lr.insurance_lost,
        "source": {"logSession": lr.session, "gameVersion": lr.version},
        "track": track,
        "logFixes": _log_fixes(lr, end, entry_for(maps, lr.map_id)),
        "kills": [],
        # the logs never say survived/killed, but a transit to the next map is certain
        "outcome": {"status": "transit", "source": "log"} if lr.exit_via_transit else None,
    }


def assemble(
    log_raids: list[LogRaid], shots: list[Shot], maps: dict, keep_orphans: bool = False,
    present: set[str] | None = None,
) -> tuple[list[dict], int, int]:
    """Raid documents for every log raid.

    Returns (docs, unassigned, outside_map). Screenshots outside every raid window are
    dropped: menus and the hideout can carry stale coordinates. Only when there are no
    logs at all (keep_orphans) are they grouped into "screenshots-only" raids instead, and
    of those only the ones taken after the last raid the (cached) logs know: older ones were
    already judged by those logs.
    present: names of the screenshot files still on disk (the others get "gone").
    """
    docs: list[dict] = []
    used: set[str] = set()
    outside = 0
    raids = sorted(log_raids, key=lambda r: r.start)
    # the shots in time order: each raid window bisects to the few near it instead of testing
    # all of them (hundreds of raids x tens of thousands of journaled shots on every scan)
    by_time = sorted(range(len(shots)), key=lambda k: shots[k].taken)
    times = [shots[k].taken for k in by_time]
    for i, lr in enumerate(raids):
        nxt = raids[i + 1].start if i + 1 < len(raids) else None
        end = lr.end or min(t for t in (nxt, lr.start + OPEN_RAID) if t is not None)
        lo = lr.start - PRE_START
        hi = end + (POST_OPEN if lr.end_reason == "open" else POST_END)
        if nxt is not None:
            hi = min(hi, nxt - PRE_START)
        # a shot timed by its filename may be named up to a minute before lo (see _in_window);
        # the candidates are taken in the given order, as if every shot had been tested
        near = sorted(by_time[bisect_left(times, lo - NAME_MINUTE):bisect_right(times, hi)])
        picked = [shots[k] for k in near if _in_window(shots[k], lo, hi) and shots[k].name not in used]
        used.update(s.name for s in picked)
        entry = entry_for(maps, lr.map_id)
        if entry and entry.get("bounds"):
            kept = [s for s in picked if contains(entry, s.x, s.z, BOUNDS_PAD)]
            outside += len(picked) - len(kept)
            picked = kept
        docs.append(_log_doc(lr, picked, maps, present))

    orphans = [s for s in shots if s.name not in used]
    if not keep_orphans:
        return docs, len(orphans), outside
    unassigned = 0
    if raids:
        cutoff = max(r.end or r.start for r in raids) + POST_OPEN
        unassigned = sum(1 for s in orphans if s.taken <= cutoff)
        orphans = [s for s in orphans if s.taken > cutoff]
    cluster: list[Shot] = []
    for shot in orphans + [None]:
        if cluster and (shot is None or shot.taken - cluster[-1].taken > ORPHAN_GAP):
            start = cluster[0].taken
            track = [_sample(s, start, present) for s in cluster]
            docs.append({
                "schema": SCHEMA,
                "generator": GENERATOR,
                "id": raid_id_for(start, None),
                "map": guess_map(maps, track),
                "mode": "unknown",
                "kind": "unknown",
                "raidId": None,
                "startedAt": _iso(start),
                "endedAt": None,
                "endReason": "screenshots-only",
                "duration": None,
                "source": {"logSession": None, "gameVersion": None},
                "track": track,
                "logFixes": [],
                "kills": [],
                "outcome": None,
            })
            cluster = []
        if shot is not None:
            cluster.append(shot)
    return docs, unassigned, outside


def game_running() -> bool | None:
    """Is the game process running? None when that cannot be told (not Windows, tasklist failed)."""
    if sys.platform != "win32":
        return None
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH", "/FO", "CSV"],
            capture_output=True,  # bytes: the "no tasks" line is in the console code page
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return GAME_EXE.lower().encode() in (proc.stdout or b"").lower()


def _stem(name: str) -> str:
    """What identifies a screenshot in the journal: its name without extension, any case."""
    return os.path.splitext(name)[0].lower()


def _track(doc: dict) -> list[dict]:
    """A stored raid's samples that name their screenshot (stored files can be edited by hand)."""
    track = doc.get("track")
    if not isinstance(track, list):
        return []
    return [s for s in track if isinstance(s, dict) and isinstance(s.get("shot"), str)]


def _log_session(doc: dict) -> str | None:
    source = doc.get("source")
    session = source.get("logSession") if isinstance(source, dict) else None
    return session if isinstance(session, str) else None


def _starts(text) -> list[datetime]:
    """The naive local times a stored raid's start can mean: the wall clock it was written for
    (see _wall_clock), then the moment it names, in local time (another tool's start in UTC)."""
    out = []
    for when in (_wall_clock(text), _when(text)):
        if when is not None and when.replace(tzinfo=None) not in out:
            out.append(when.replace(tzinfo=None))
    return out


def _restored_shot(sample: dict, starts: list[datetime]) -> Shot | None:
    """The screenshot a stored route sample was made from: the position from its file name, the
    time from the raid's start (see _starts, the first that fits) plus the sample's t, which is
    rounded to 0.1 s (a shot from just before the start is at 0). Only the minute its name
    gives (time_source "name") for a minute-only sample, or when no start gives a time that can
    be the shot's: a start changed by hand or by another tool (a placeholder date), a damaged t.
    None when the name carries no position."""
    by_name = shot_from_name(sample["shot"])
    t = sample.get("t")
    if (by_name is None or sample.get("approx")
            or isinstance(t, bool) or not isinstance(t, (int, float)) or not t >= 0):
        return by_name
    for start in starts:
        try:
            taken = start + timedelta(seconds=t)
        except OverflowError:  # a placeholder date at the very edge of the calendar
            continue
        if matches_name(by_name.taken, taken):
            return replace(by_name, taken=taken, time_source="mtime")
    return by_name


class ShotJournal:
    """Every screenshot position seen so far, keyed by file name (data/cache/shots.json).

    Screenshots are several MB each and some companion tools delete them on purpose, so
    routes are built from this journal plus the files on disk, never from the disk alone.
    The first sighting wins: a file copied back later has lost its original file times.
    A file with the same name but another extension (a PNG converted to JPG) is the same shot.

    Nothing is saved before load() worked: an empty journal written over one that could not
    be read would lose every deleted screenshot's position.
    """

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict] = {}
        self.loaded = False
        self.newer = False  # written by a newer eftjourney: left as it is
        self.skipped = 0  # entries the last merge could not read (they stay in the file)
        self.fallback: dict[str, Shot] = {}  # stem -> a stored route's shot, for unreadable entries
        self.standing_in = 0  # of those, how many the last merge used
        self._dump = ""

    def load(self) -> str | None:
        """Read the journal unless that already worked; a warning when it cannot be used now.

        Only a missing file is an empty journal. A read error (locked by another program,
        drive not ready) leaves it unloaded and the next scan tries again. A damaged file is
        moved aside, never deleted, and a new journal starts (refilled from the stored routes).
        """
        if self.loaded:
            return None
        warning = None
        if not self.newer:
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                raw = None  # first run
            except OSError:
                return f"스크린샷 기록을 읽지 못해 이번 스캔은 레이드를 갱신하지 않았습니다 (다음 스캔에서 다시 시도): {self.path}"
            entries: dict[str, dict] = {}
            if raw is not None:
                try:
                    doc = json.loads(raw.decode("utf-8-sig"))
                except ValueError:
                    doc = None
                version = doc.get("version") if isinstance(doc, dict) else None
                if not isinstance(version, (int, float)) or isinstance(version, bool):
                    version = None
                if version is not None and version > JOURNAL_VERSION:
                    self.newer = True
                elif version is not None and version >= 1 and isinstance(doc.get("shots"), dict):
                    # an older version is read as it is (convert its entries here once their
                    # format changes): moved aside, it would look like a lost journal
                    entries = doc["shots"]
                else:
                    moved = self._move_aside()
                    if moved is None:
                        return f"손상된 스크린샷 기록을 옮기지 못해 이번 스캔은 레이드를 갱신하지 않았습니다 (다음 스캔에서 다시 시도): {self.path}"
                    warning = f"스크린샷 기록이 손상되어 {moved.name} 로 옮겨 두고 새로 기록합니다: {self.path}"
        if self.newer:
            return f"스크린샷 기록을 더 새 버전의 eftjourney가 만들어 그대로 두었습니다. 새 버전으로 실행해 주세요 (그때까지 레이드를 갱신하지 않음): {self.path}"
        self.entries = entries
        self.loaded = True
        self._dump = json.dumps(entries, sort_keys=True)
        return warning

    def _move_aside(self) -> Path | None:
        """Keep a damaged journal for the user under a new name; None if it cannot be moved."""
        stamp = f"{self.path.name}.bad-{datetime.now():%Y%m%d-%H%M%S}"
        target, n = self.path.with_name(stamp), 1
        while target.exists():
            target, n = self.path.with_name(f"{stamp}-{n}"), n + 1
        try:
            self.path.rename(target)
        except OSError:
            return None
        return target

    def merge(self, on_disk: list[Shot]) -> list[Shot]:
        """All known shots, one per name without extension: journaled ones (files may be
        gone) plus new files. Entries that cannot be read are skipped but kept."""
        valid = {name: s for name, doc in self.entries.items() if (s := shot_from_json(name, doc))}
        bad = self.entries.keys() - valid.keys()
        self.skipped = len(bad)
        known: dict[str, str] = {}  # stem -> the entry that stands for that shot
        for name in valid:
            known.setdefault(_stem(name), name)
        listed = {s.name for s in on_disk}
        unjournaled, used = [], set()  # used: the stems an unjournaled file stands for
        # exact file times first: when a PNG and its JPG copy are both new, the original wins
        for shot in sorted(on_disk, key=lambda s: s.time_source == "name"):
            key = _stem(shot.name)
            stand = self.fallback.get(key)
            if stand is not None and shot.time_source == "name" and stand.time_source != "name":
                # a copy that lost its file times, of a shot whose entry cannot be read: the
                # moment its stored route kept is the better one
                shot = replace(stand, name=shot.name)
            old = known.get(key)
            if old is None and key in used:
                continue  # another file of this shot stands for it already
            if old is None and shot.name in bad:
                unjournaled.append(shot)  # its entry cannot be read: leave that alone, use the file
                used.add(key)
            elif old is None:
                self.entries[shot.name] = shot_to_json(shot)
                valid[shot.name] = shot
                known[key] = shot.name
            elif old != shot.name and old not in listed and shot.name not in bad:
                # the journaled file is gone and this is the same shot under another name
                # (converted): keep the first sighting, follow the file that exists
                self.entries[shot.name] = self.entries.pop(old)
                valid[shot.name] = replace(valid.pop(old), name=shot.name)
                known[key] = shot.name
        shots = [valid[name] for name in known.values()] + unjournaled
        # a file whose entry cannot be read and that is gone: its stored route's position
        gone = {_stem(name) for name in bad} - {_stem(s.name) for s in on_disk} - known.keys()
        stand_in = [self.fallback[stem] for stem in sorted(gone) if stem in self.fallback]
        self.standing_in = len(stand_in)
        shots += stand_in
        shots.sort(key=shot_order)
        return shots

    def restore(self, restored: list[Shot], on_disk: list[Shot]) -> set[str]:
        """Journal the shots rebuilt from stored routes that no entry stands for yet, one per
        name without extension: the first with a time of its own (stored raids holding the same
        shot can disagree, one with a start that cannot be right), or else the first; returns
        the names journaled.

        A file still there is left to merge() when it has an exact time (the moment it was
        taken, more precise than a stored route's 0.1 s), and for a shot of which only the
        minute is known here, in any case. Entries that are there are left as they are; for
        one that cannot be read, the stored shot stands in (see merge).
        """
        readable = {_stem(name) for name, doc in self.entries.items() if shot_from_json(name, doc)}
        unreadable = {_stem(name) for name in self.entries} - readable
        listed = {_stem(shot.name) for shot in on_disk}
        exact = {_stem(shot.name) for shot in on_disk if shot.time_source != "name"}
        best: dict[str, Shot] = {}
        for shot in restored:
            stem = _stem(shot.name)
            if stem not in best or (best[stem].time_source == "name" and shot.time_source != "name"):
                best[stem] = shot
        added = set()
        for stem, shot in best.items():
            if stem in readable:
                continue
            if stem in unreadable:
                self.fallback.setdefault(stem, shot)
                continue
            if stem in exact or (shot.time_source == "name" and stem in listed):
                continue
            self.entries[shot.name] = shot_to_json(shot)
            added.add(shot.name)
        return added

    def save(self) -> None:
        if not self.loaded:
            return
        dump = json.dumps(self.entries, sort_keys=True)
        if dump == self._dump:
            return
        _atomic_write_json(self.path, {"version": JOURNAL_VERSION, "shots": self.entries})
        self._dump = dump


class Builder:
    def __init__(
        self, store: RaidStore, maps: dict, logs_dir: Path | None, screenshots_dir: Path | None,
        find_logs: Callable[[], Path | None] | None = None,
    ):
        """find_logs: looks for the Logs folder again while none with sessions is known
        (the game may create its first session after this program started)."""
        self.store = store
        self.maps = maps
        self.screenshots_dir = screenshots_dir
        self.cache_path = store.root / "cache" / "logs.json"
        self.logs = LogScanner(logs_dir, self._load_cache())
        self.journal = ShotJournal(store.root / "cache" / "shots.json")
        self._cache_dump = json.dumps(self.logs.cache, sort_keys=True)
        self._running_checked = 0.0
        self._running: bool | None = None
        self._definite: bool | None = None  # the last answer of a process check that worked
        self._definite_at = float("-inf")
        # When the game was last seen closed (data/cache/state.json): a raid that ended before
        # that is over for good, also after this program restarts.
        self.state_path = store.root / "cache" / "state.json"
        self._state = self._load_state()
        self._closed_seen = _when(self._state.get("gameClosedSeen"))
        self._present: set[str] | None = None  # screenshot files of the last listing that worked
        self._listed = False  # the screenshots folder could be listed in this run
        self._restored = False  # the journal was filled from the stored routes in this run
        self._notes: list[str] = []  # what that restore found, for the first scan that gets to the end
        self.find_logs = find_logs
        self._find_checked = time.monotonic()

    @property
    def logs_dir(self) -> Path | None:
        return self.logs.logs_dir

    def _load_cache(self) -> dict | None:
        try:
            doc = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) else None

    def _save_cache(self, report: ScanReport) -> None:
        dump = json.dumps(self.logs.cache, sort_keys=True)
        if dump == self._cache_dump:
            return
        try:
            _atomic_write_text(self.cache_path, dump)  # no indent: it grows with every log session
        except OSError as exc:
            # A warning, not an error: the raids are written by now, and a failed scan would never
            # tell the viewer or the console about them (the next scan finds nothing new).
            report.warnings.append(f"로그 기록을 쓰지 못했습니다 (다음 스캔에서 다시 시도): {self.cache_path} ({exc.strerror or type(exc).__name__})")
            return
        self._cache_dump = dump

    def _load_state(self) -> dict:
        try:
            doc = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    def _seen_closed(self) -> None:
        self._closed_seen = datetime.now().astimezone()
        self._state["gameClosedSeen"] = _iso(self._closed_seen)
        try:
            _atomic_write_json(self.state_path, self._state)
        except OSError:
            pass  # still known in this run; written again the next time the game is seen closed

    def _game_running(self) -> bool | None:
        now = time.monotonic()
        if now - self._running_checked > 15:
            running = game_running()
            self._running_checked = now
            if running is False:
                # Checks only run while a raid might be in progress, so the game may have run
                # since an earlier False: always take the latest moment. Every raid that ended
                # before it is over, so no check is needed for those again.
                self._seen_closed()
            self._running = running
            if running is not None:
                self._definite, self._definite_at = running, now
        if self._running is None and now - self._definite_at < RUNNING_KEEP:
            return self._definite  # one failed check: the answer from a moment ago still holds
        return self._running

    def _over_for_good(self, end: datetime) -> bool:
        """Was the game seen closed after `end` (naive = local time)? Then that raid has ended."""
        seen = self._closed_seen
        if seen is None or seen > datetime.now().astimezone():  # a clock set back proves nothing
            return False
        return end.astimezone() < seen

    def _in_progress(self, log_raids: list[LogRaid]) -> LogRaid | None:
        """The raid being played right now: the newest one, still open, unless the game was
        seen closed after its last log line or the client was restarted since it began."""
        latest = max(log_raids, key=lambda r: r.start, default=None)
        if latest is None or latest.end_reason != "open" or datetime.now() - latest.start > OPEN_RAID:
            return None
        if self._over_for_good(latest.end or latest.start):
            return None
        visible = self.logs.visible
        if latest.session in visible and any(
                name != latest.session and (session_info(name)[0] or datetime.min) > latest.start
                for name in visible):
            return None  # a newer client session started after it
        # Fail closed: when its session folder is out of sight (Logs folder moved, unplugged,
        # another one chosen) or the process check is unavailable, assume the game still runs.
        return latest if self._game_running() is not False else None

    def _cluster_in_progress(self, doc: dict) -> bool:
        """Without logs, the newest screenshot group may be the raid being played."""
        track = doc.get("track") or []
        last = datetime.fromisoformat(doc["startedAt"]) + timedelta(seconds=track[-1]["t"] if track else 0)
        if self._over_for_good(last):
            return False
        age = datetime.now().astimezone() - last
        return age < ORPHAN_GAP or (age < OPEN_RAID and self._game_running() is not False)

    def _redetect_logs(self) -> None:
        if self.find_logs is None or self.logs.visible:
            return
        if time.monotonic() - self._find_checked < REDETECT_EVERY:
            return
        self._find_checked = time.monotonic()
        found = self.find_logs()
        if found:
            self.logs.logs_dir = Path(found)

    def _edits_target(self, old: dict, docs: list[dict]) -> dict | None:
        """The produced raid that now shows what an edited, no-longer-produced raid showed:
        the same GameStarted in the same session (the map id in the raid id changed), or
        failing that the raid holding most of its screenshots."""
        session = _log_session(old)
        if session and old.get("startedAt"):
            same = [d for d in docs if d.get("startedAt") == old["startedAt"] and _log_session(d) == session]
            if len(same) == 1:
                return same[0]
        names = {s["shot"] for s in _track(old)}
        if not names:
            return None
        overlap = lambda d: len(names & {s["shot"] for s in _track(d)})
        best = max(docs, key=overlap, default=None)
        return best if best is not None and overlap(best) else None

    def _stored_raids(self):
        """The stored raids this program made, as (id, doc)."""
        for raid_id in self.store.list_ids():
            doc = self.store.load_raid(raid_id)
            if doc is not None and doc.get("generator") == GENERATOR:
                yield raid_id, doc

    def _stored_present(self) -> set[str]:
        """Journaled files the stored raids do not mark as deleted: the last listing's view."""
        names = set(self.journal.entries)
        if not names:
            return names
        for _, doc in self._stored_raids():
            names.difference_update(s["shot"] for s in _track(doc) if s.get("gone"))
        return names

    def _stored_shots(self) -> tuple[list[Shot], dict[str, set[str]], dict[str, set[str]]]:
        """The screenshots the stored routes were drawn from, newest raid first (see
        _restored_shot); per raid, the names without extension of samples whose name carries no
        position; and per name without extension, the raids where its sample had a time of its
        own but got only the named minute."""
        out, unreadable, minute_only = [], {}, {}
        for raid_id, doc in self._stored_raids():
            starts = _starts(doc.get("startedAt"))
            same_t: dict[datetime, int] = {}
            for sample in _track(doc):
                shot = _restored_shot(sample, starts)
                if shot is None:
                    unreadable.setdefault(raid_id, set()).add(_stem(sample["shot"]))
                    continue
                if shot.time_source == "name":
                    if not sample.get("approx"):
                        minute_only.setdefault(_stem(shot.name), set()).add(raid_id)
                else:
                    # samples with the same t (the shots of the last moments before the start are
                    # all at 0) keep their stored order: 1 ms apart, which rounds back to that t
                    n = same_t.get(shot.taken, 0)
                    same_t[shot.taken] = n + 1
                    if n:
                        shot = replace(shot, taken=shot.taken + timedelta(milliseconds=n))
                out.append(shot)
        return out, unreadable, minute_only

    def scan(self) -> ScanReport:
        report = ScanReport()
        self._redetect_logs()
        log_raids = self.logs.scan()
        report.sessions = len(self.logs.cache["sessions"])
        for name in sorted(self.logs.failed):
            report.warnings.append(f"로그 세션을 읽지 못했습니다 (다음 스캔에서 다시 시도): {name}")
        problem = self.journal.load()  # tried again on every scan until it works
        if problem:
            report.warnings.append(problem)
        on_disk = scan_dir(self.screenshots_dir)
        report.files = len(on_disk) if on_disk is not None else None
        if self.journal.loaded and not self._restored:
            # Whatever the journal lost (deleted, damaged, an older copy put back) comes back
            # from the stored routes: they hold every screenshot they were drawn from.
            stored_shots, unreadable, minute_only = self._stored_shots()
            added = self.journal.restore(stored_shots, on_disk or [])
            self._restored = True  # only now: a scan that failed before this point tries again
            if added:
                # journaled at the named minute although a stored sample had a time of its own (and
                # no raid holding the shot gave one that fits)
                minutes = [name for name in added
                           if self.journal.entries[name]["time_source"] == "name" and _stem(name) in minute_only]
                raids = sorted({raid_id for name in minutes for raid_id in minute_only[_stem(name)]}, reverse=True)
                part = (f" (그중 {len(minutes)}개는 레이드 시작 시각이나 지점의 시각이 맞지 않아 파일 이름의 분 단위로: "
                        f"{', '.join(raids)})" if minutes else "")
                self._notes.append(f"스크린샷 기록에 없던 위치 {len(added)}개를 저장된 레이드 경로에서 되살렸습니다{part}: {self.journal.path}")
            known = {_stem(name) for name in self.journal.entries} | {_stem(s.name) for s in on_disk or []}
            lost = sorted(raid_id for raid_id, stems in unreadable.items() if stems - known)
            if lost:
                self._notes.append(f"저장된 레이드의 지점 중 파일 이름에 위치가 없어 되살리지 못한 것이 있습니다: {', '.join(lost)}")
        report.warnings += self._notes  # until a scan that tells them gets to the end
        if on_disk is not None:
            self._listed = True
            self._present = {s.name for s in on_disk}
        elif self.screenshots_dir and (self._listed or self.journal.entries):
            # Renamed, moved, drive or sync folder offline: use the journaled positions with the
            # last known file states, so routes neither flip to "deleted" nor get rewritten.
            # (No Screenshots folder before the first screenshot is normal: no warning then.)
            report.warnings.append(f"스크린샷 폴더를 읽을 수 없어 기록해 둔 위치만 썼습니다: {self.screenshots_dir}")
        if self.journal.loaded:
            shots = self.journal.merge(on_disk or [])
            self.journal.save()  # before any raid uses new files: their positions must outlive them
            if self._present is None:  # no listing worked yet in this run
                self._present = self._stored_present()
            present = self._present
            if self.journal.skipped:
                instead = f", 그중 {self.journal.standing_in}개는 저장된 경로의 위치를 대신 씀" if self.journal.standing_in else ""
                report.warnings.append(f"스크린샷 기록에서 읽을 수 없는 항목 {self.journal.skipped}개는 건너뛰었습니다 (파일에는 그대로 둠{instead}): {self.journal.path}")
        else:
            shots, present = sorted(on_disk or [], key=shot_order), None
        report.samples = len(shots)
        no_logs = not self.logs.visible
        docs, report.orphan_samples, report.outside_map = assemble(
            log_raids, shots, self.maps, keep_orphans=no_logs, present=present)
        if no_logs and shots:
            report.warnings.append(
                "게임 로그 폴더를 찾지 못해 마지막으로 기록된 레이드 뒤의 스크린샷만 시각으로 레이드를 나눴습니다 (--logs 로 지정 가능)"
                if log_raids else "게임 로그 폴더를 찾지 못해 스크린샷 시각만으로 레이드를 나눴습니다 (--logs 로 지정 가능)")

        # Post-raid only: the map of a raid still being played is not published (the
        # tarkov.dev map art license excludes in-raid overlays). It appears once it ends.
        hidden: set[str] = set()
        running = self._in_progress(log_raids)
        if running is not None:
            hidden.add(raid_id_for(running.start, running.map_id))
            docs = [d for d in docs if d["id"] not in hidden]
            report.in_raid = {"map": running.map_id, "since": _iso(running.start)}
        if no_logs:
            # Without logs the newest screenshot group may be the raid being played. A group dated
            # later than now is not taken for the newest (a wrong clock when it was taken must not
            # keep the group being played from being checked), but is checked as well: the clock
            # may have been set back during the raid. Further ahead than a raid lasts, it is neither.
            now = datetime.now().astimezone()
            groups = [(d, _when(d["startedAt"])) for d in docs if d["endReason"] == "screenshots-only"]
            newest = max((d for d, when in groups if when is None or when <= now + NAME_MINUTE),
                         key=lambda d: d["startedAt"], default=None)
            checked = [newest] if newest is not None else []
            checked += [d for d, when in groups if when is not None and now + NAME_MINUTE < when <= now + OPEN_RAID]
            playing = [d for d in checked if self._cluster_in_progress(d)]
            if playing:
                hidden.update(d["id"] for d in playing)
                docs = [d for d in docs if d["id"] not in hidden]
                report.in_raid = {"map": playing[0].get("map"), "since": playing[0]["startedAt"]}
        report.raids_total = len(docs)
        if not self.journal.loaded:
            # without the journal every route would lose its deleted screenshots: write and
            # delete nothing until it can be read
            self._save_cache(report)
            return report

        produced, stored, unsaved = set(), [], []
        for doc in docs:
            if doc["id"] in produced:
                report.warnings.append(f"duplicate raid id {doc['id']}")
                continue
            produced.add(doc["id"])
            try:
                if self.store.load_raid(doc["id"]) != doc:
                    self.store.save_raid(doc)
                    report.written.append(doc["id"])
            except KeyError:  # an id the store refuses; skip it instead of aborting the scan
                report.warnings.append(f"레이드를 저장하지 못했습니다: {doc['id']}")
                continue
            except OSError as exc:  # held by another program, disk full: the others still count
                report.warnings.append(f"레이드를 저장하지 못했습니다 (다음 스캔에서 다시 시도): {doc['id']} ({exc.strerror or type(exc).__name__})")
                unsaved.append(doc)
                continue
            stored.append(doc)

        visible = set(self.logs.visible)
        for raid_id in self.store.list_ids():
            if raid_id in produced:
                continue
            old = self.store.load_raid(raid_id) or {}
            if old.get("generator") != GENERATOR or old.get("demo"):
                continue
            try:
                self._retire(raid_id, old, hidden, visible, stored, unsaved, report)
            except OSError as exc:  # a file held by another program: the next scan tries again
                report.warnings.append(f"오래된 레이드를 정리하지 못했습니다 (다음 스캔에서 다시 시도): {raid_id} ({exc.strerror or type(exc).__name__})")

        self._save_cache(report)
        self._notes = []  # told
        return report

    def _retire(self, raid_id: str, old: dict, hidden: set[str], visible: set[str], stored: list[dict],
                unsaved: list[dict], report: ScanReport) -> None:
        """Remove a stored raid this scan no longer makes, moving its edits to the raid that
        replaced it; or leave it, when it still has to stand for something. unsaved: raids
        of this scan that could not be written."""
        if raid_id in hidden:
            # the raid being played, listed by a scan that took it for ended (say a client
            # restart, and its Logs folder is out of sight now): off the list wherever its
            # log folder is, as it is built again from the log cache and the journal once
            # it has ended. Edits are never dropped or moved, so with edits it stays (with an
            # edits file that cannot be read, too).
            if self.store.read_edits(raid_id) == {}:
                self.store.delete_raid(raid_id)
                report.removed.append(raid_id)
            return
        session = _log_session(old)
        if session and (session in self.logs.failed or session not in visible):
            # unreadable this time, or its log folder is gone and the stored raid is
            # the only record left
            return
        edits = self.store.read_edits(raid_id)
        if edits is None:
            # what the user typed, unreadable now (a hand edit gone wrong): neither moved nor dropped
            report.warnings.append(f"편집 파일을 읽을 수 없어 {raid_id} 를 정리하지 않았습니다. "
                                   f"파일을 고치거나, 필요 없으면 지우세요: {self.store.edits_file(raid_id)}")
            return
        target = self._edits_target(old, stored + unsaved) if edits or unsaved else None
        if target is not None and any(target is doc for doc in unsaved):
            return  # what replaces it could not be written this time: it stays until that is
        if edits:
            # never drop what the user typed: move it to the raid that replaced this one
            if target is None:
                return
            moved = self.store.read_edits(target["id"])
            if moved is None:
                # an edits file there that cannot be read may hold what the user typed: never written over
                report.warnings.append(f"편집 내용을 {raid_id} 에서 {target['id']} 로 옮기지 못했습니다 ({target['id']} 의 편집 파일을 읽을 수 없음). "
                                       f"그 파일을 고치거나 지우면 다음 스캔에서 옮깁니다: {self.store.edits_file(target['id'])}")
                return
            if not moved:
                self.store.save_edits(target["id"], edits)
            elif moved != clean_edits(edits):
                # edits of its own there: never overwrite them; the user picks which to keep
                report.warnings.append(f"편집 내용을 {raid_id} 에서 {target['id']} 로 옮기지 못했습니다 ({target['id']} 에 다른 편집이 있음). "
                                       f"{raid_id} 쪽 편집이 필요 없으면 이 파일을 지우세요: {self.store.edits_file(raid_id)}")
                return
            # the edits first, then the raid: a scan that stops in between finishes the move
            # next time (the same edits at the target) or deletes a raid without edits
            self.store.delete_edits(raid_id)
            self.store.delete_raid(raid_id)
            report.removed.append(raid_id)
            if target["id"] not in report.written:
                report.written.append(target["id"])
            report.warnings.append(f"편집 내용을 {raid_id} 에서 {target['id']} 로 옮겼습니다")
            return
        self.store.delete_raid(raid_id)
        report.removed.append(raid_id)
