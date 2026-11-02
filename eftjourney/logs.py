"""Raid segmentation from EFT's own log files (application, backend, push-notifications).

Nothing is logged while a local raid runs, so a raid is the span between the
GameStarted line and an end marker:
  local (client-hosted) raids   backend request /client/match/local/end
  online (server-hosted) raids  earliest of push UserMatchOver, backend /client/putMetrics,
                                the first profile-selection line or "GC mode switched to
                                Enabled" after GameStarted (all within ~20 s of the real end)
A raid without an end marker when the session's logs stop is "open": the client was
closed in the raid, or the raid is still running. Its end is the session's last log line.

Map id, in priority order: the local "[Transit] ... Locations:a -> b ->" chain (last
element), push UserConfirmed.location, "TRACE-NetworkGameCreate profileStatus ...
Location:", the "PVE transit hop" / "Transit reconnect" lines, the scene preset rcid.

Layout: <Logs>/log_2026.10.27_22-32-44_1.2.5.0.48146/
            "2026.10.27_22-32-44_1.2.5.0.48146 application_000.log", "... backend_000.log", ...
Line:   2026-10-27 22:47:12.816|1.2.5.0.48146|Info|application|<message>
        (lines without that header continue the previous entry, e.g. JSON bodies)
Timestamps are local wall-clock time without an offset.

Privacy: these logs contain profile/account/group ids, server addresses and session ids.
They are matched only as far as needed and never stored.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2}\.\d{3})\|([^|]*)\|([^|]*)\|([^|]*)\|(.*)$")
SESSION_DIR_RE = re.compile(r"^log_(\d{4})\.(\d{2})\.(\d{2})_(\d{1,2})-(\d{2})-(\d{2})_(.+)$")
TS_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
MAX_BODY_LINES = 400

APP_RULES = (
    ("mode", re.compile(r"^Session mode: (?P<mode>\w+)")),
    ("profile", re.compile(r"^(?:PrepareSelectedProfileLocally|SelectProfile) ProfileId:")),
    ("match_group", re.compile(r"^Matching with group id")),
    ("scene", re.compile(r"^scene preset path:\S+ rcid:(?P<rcid>\S+)")),
    ("ngc_status", re.compile(r"^TRACE-NetworkGameCreate profileStatus: .*?\bLocation: (?P<loc>[A-Za-z0-9_]+)")),
    ("location_loaded", re.compile(r"^LocationLoaded:")),
    ("matching_completed", re.compile(r"^MatchingCompleted:(?P<t>[\d.]+) ")),
    ("transit_local", re.compile(r"^\[Transit\] Flag:(?P<flag>\w+), RaidId:(?P<raid_id>[0-9a-fA-F]{24}), Count:(?P<count>\d+), Locations:(?P<locs>.*)$")),
    ("transit_online", re.compile(r"^\[Transit\] `[0-9a-fA-F]{24}` Count:(?P<count>\d+)")),
    ("pve_hop", re.compile(r"^PVE transit hop .*?\blocation:(?P<loc>[A-Za-z0-9_]+)")),
    ("transit_reconnect", re.compile(r"^Transit reconnect .*?\blocation:(?P<loc>[A-Za-z0-9_]+)")),
    ("spawn", re.compile(r"^PlayerSpawnEvent:")),
    ("started", re.compile(r"^GameStarted:")),
    ("net_cancel", re.compile(r"^Network game matching cancelled")),
    ("local_cancel", re.compile(r"^Local game matching cancelled")),
    ("gc_enabled", re.compile(r"^GC mode switched to Enabled")),
    ("position", re.compile(r"^Reason:(?P<reason>\w+), Position:\((?P<x>-?[\d.]+), (?P<y>-?[\d.]+), (?P<z>-?[\d.]+)\)")),
)
URL_RE = re.compile(r"^(?:--->|<---) (?P<kind>Request|Response) \w+, id \[\d+\]: URL: (?P<url>\S+?)(?:\. |, |\.$|$)")
URL_PARTS_RE = re.compile(r"^\w+://(?P<host>[^/]+)(?P<path>/[^?]*)")
BACKEND_EVENTS = {
    ("Response", "/client/match/local/start"): "local_start",
    ("Request", "/client/match/local/end"): "local_end",
    ("Response", "/client/match/local/end"): "local_end_resp",
    ("Response", "/client/match/join"): "match_join",
    ("Response", "/client/match/exit"): "match_exit",
    ("Request", "/client/putMetrics"): "put_metrics",
}
NOTIFICATION_RE = re.compile(r"^Got notification \| (?P<type>\w+)")
SOURCE_ORDER = {"application": 0, "backend": 1, "push-notifications": 2}

# scene preset rcid (suffix ".scenespreset.asset" stripped, lowercased) -> location id
SCENE_TO_LOCATION = {
    "bigmap": "bigmap",
    "woods": "Woods",
    "rezerv_base": "RezervBase",
    "laboratory": "laboratory",
    "laboratory_dark": "laboratory_dark",
    "factory_day": "factory4_day",
    "factory_night": "factory4_night",
    "shoreline": "Shoreline",
    "lighthouse": "Lighthouse",
    "icebreaker": "Icebreaker",
    "shopping_mall": "Interchange",
    "city": "TarkovStreets",
    "sandbox": "Sandbox",
    "sandbox_high": "Sandbox_high",
    "sandbox_sl": "Sandbox_start",
    "labyrinth": "Labyrinth",
}
MODE_NAMES = {"pve": "pve", "regular": "pvp", "pvp": "pvp", "pvpseason": "pvp"}
TRANSIT_GAP = timedelta(minutes=15)
MSK = timezone(timedelta(hours=3))  # insurance message dates are Moscow time
LOCAL_TZ = None  # zone of the log timestamps; None = this PC's zone (tests pin it)
CACHE_VERSION = 3


@dataclass
class LogEvent:
    time: datetime
    kind: str
    data: dict = field(default_factory=dict)
    order: int = 0


# what LogRaid.to_json() writes besides the timestamps
RAID_FIELD_TYPES = {
    "map_id": (str, type(None)), "raid_id": (str, type(None)), "map_source": (str, type(None)),
    "mode": str, "session": str, "version": str, "end_reason": str, "kind": str,
    "transit_from_prev": bool, "exit_via_transit": bool, "insurance_lost": bool, "fixes": list,
}


@dataclass
class LogRaid:
    start: datetime  # GameStarted
    end: datetime | None
    map_id: str | None  # lowercase location id, e.g. "bigmap"
    raid_id: str | None  # EFT RaidId (local raids only)
    mode: str  # pve | pvp | unknown
    session: str
    version: str
    end_reason: str  # raid-end | online-end | open | unknown
    kind: str = "unknown"  # local | online | unknown
    map_source: str | None = None
    transit_from_prev: bool = False
    exit_via_transit: bool = False
    insurance_lost: bool = False
    fixes: list = field(default_factory=list)  # [{"time": iso, "x", "y", "z", "reason"}]

    def to_json(self) -> dict:
        doc = asdict(self)
        doc["start"] = self.start.isoformat(timespec="milliseconds")
        doc["end"] = self.end.isoformat(timespec="milliseconds") if self.end else None
        return doc

    @classmethod
    def from_json(cls, doc: dict) -> "LogRaid":
        """Raises KeyError, TypeError or ValueError for anything to_json() did not write: a
        hand-edited cache must not break sorting or the raid builder."""
        doc = dict(doc)
        doc["start"] = datetime.fromisoformat(doc["start"])
        doc["end"] = datetime.fromisoformat(doc["end"]) if doc.get("end") else None
        known = cls.__dataclass_fields__
        raid = cls(**{k: v for k, v in doc.items() if k in known})
        for name, types in RAID_FIELD_TYPES.items():
            if not isinstance(getattr(raid, name), types):
                raise TypeError(f"{name}: {type(getattr(raid, name)).__name__}")
        for fix in raid.fixes:
            if (datetime.fromisoformat(fix["time"]).tzinfo is not None or not isinstance(fix["reason"], str)
                    or not all(isinstance(fix[k], (int, float)) for k in "xyz")):
                raise ValueError("fix")
        if raid.start.tzinfo is not None or (raid.end is not None and raid.end.tzinfo is not None):
            raise ValueError("log times are local wall-clock time without an offset")
        if not all(2000 <= t.year < 2100 for t in (raid.start, raid.end) if t is not None):
            raise ValueError("log time out of range")  # e.g. year 1: the raid windows' date math overflows
        return raid


# ----------------------------------------------------------------------------- reading
def _ts(text: str) -> datetime:
    return datetime.strptime(text, TS_FORMAT)


def iter_entries(path: Path):
    """(timestamp, message, continuation lines) per log entry.

    Read errors propagate: a half-read session must not be cached as if it were complete.
    """
    current = None
    with Path(path).open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            m = LINE_RE.match(line)
            if m:
                if current is not None:
                    yield current
                current = (m.group(1), m.group(5), [])
            elif current is not None and len(current[2]) < MAX_BODY_LINES:
                current[2].append(line)
    if current is not None:
        yield current


def _log_glob(kind: str) -> str:
    return f"* {kind}_*.log"


def session_files(session_dir: Path, kind: str) -> list[Path]:
    return sorted(p for p in Path(session_dir).glob(_log_glob(kind)) if p.is_file())


def _text(value) -> str | None:
    """A JSON payload field that is used as text; any other type counts as missing."""
    return value if isinstance(value, str) else None


def parse_application(path: Path, events: list[LogEvent]) -> str | None:
    last = None
    for ts, msg, _ in iter_entries(path):
        last = ts
        for kind, rx in APP_RULES:
            m = rx.match(msg)
            if m:
                events.append(LogEvent(_ts(ts), kind, m.groupdict(), SOURCE_ORDER["application"]))
                break
    return last


def parse_backend(path: Path, events: list[LogEvent]) -> str | None:
    last = None
    for ts, msg, _ in iter_entries(path):
        last = ts
        m = URL_RE.match(msg)
        if not m:
            continue
        parts = URL_PARTS_RE.match(m.group("url"))
        if not parts:
            continue
        kind = BACKEND_EVENTS.get((m.group("kind"), parts.group("path").rstrip(".")))
        if kind:
            events.append(LogEvent(_ts(ts), kind, {"host": parts.group("host")}, SOURCE_ORDER["backend"]))
    return last


def parse_push(path: Path, events: list[LogEvent]) -> str | None:
    last = None
    for ts, msg, body in iter_entries(path):
        last = ts
        m = NOTIFICATION_RE.match(msg)
        if not m or m.group("type") not in ("UserConfirmed", "UserMatchOver", "ChatMessageReceived"):
            continue
        try:
            doc = json.loads("\n".join(body)) if body else {}
        except ValueError:
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        note, when = m.group("type"), _ts(ts)
        order = SOURCE_ORDER["push-notifications"]
        if note == "UserConfirmed":
            events.append(LogEvent(when, "user_confirmed", {"loc": _text(doc.get("location"))}, order))
        elif note == "UserMatchOver":
            events.append(LogEvent(when, "user_match_over", {"loc": _text(doc.get("location"))}, order))
        else:
            message = doc.get("message") if isinstance(doc.get("message"), dict) else {}
            system = message.get("systemData") if isinstance(message.get("systemData"), dict) else {}
            if message.get("type") == 2 and _text(system.get("location")):
                events.append(LogEvent(when, "insurance", {
                    "loc": system["location"], "date": _text(system.get("date")), "time": _text(system.get("time")),
                }, order))
    return last


def session_events(session_dir: Path, names: list[str] | None = None) -> tuple[list[LogEvent], datetime | None]:
    """All lifecycle events of one session, time-ordered, plus the time of its last log line.

    names: read exactly these files of the folder (the ones session_signature measured), so a
    file that is gone by now raises OSError instead of being skipped. Default: every log file.
    """
    events: list[LogEvent] = []
    last: list[str] = []
    for kind, parser in (("application", parse_application), ("backend", parse_backend), ("push-notifications", parse_push)):
        if names is None:
            files = session_files(session_dir, kind)
        else:
            files = [Path(session_dir) / n for n in dict.fromkeys(names) if fnmatch(n, _log_glob(kind))]
        for p in files:
            if (ts := parser(p, events)) is not None:
                last.append(ts)
    events.sort(key=lambda e: (e.time, e.order))
    return events, (_ts(max(last)) if last else None)


# -------------------------------------------------------------------------- segmenting
def scene_location(rcid: str | None) -> str | None:
    if not rcid:
        return None
    key = re.sub(r"(?:\.scenes?preset)?\.asset$", "", rcid, flags=re.IGNORECASE).lower()
    return SCENE_TO_LOCATION.get(key, key or None)


def normalize_mode(mode: str | None) -> str:
    return MODE_NAMES.get((mode or "").lower(), (mode or "unknown").lower())


class _Attempt:
    """One map load: queued -> loading -> started -> ended (or cancelled)."""

    def __init__(self, mode: str | None, pre: list[LogEvent], t: datetime):
        self.mode = mode
        self.t_queue = pre[0].time if pre else t
        self.hop_loc = next((p.data["loc"] for p in pre if p.kind == "pve_hop"), None)
        self.reconnect_loc = next((p.data["loc"] for p in pre if p.kind == "transit_reconnect"), None)
        self.scene: str | None = None
        self.host: str | None = None
        # announcements of this leg that were logged while the previous leg was still open
        self.uc_loc: str | None = next((p.data.get("loc") for p in reversed(pre) if p.kind == "user_confirmed"), None)
        self.ngc_loc: str | None = next((p.data.get("loc") for p in reversed(pre) if p.kind == "ngc_status"), None)
        self.kind: str | None = "online" if any(p.kind in ("user_confirmed", "ngc_status") for p in pre) else None
        self.raid_id: str | None = None
        self.transit_count: int | None = None
        self.chain: list[str] | None = None
        self.online_transit_count: int | None = None
        self.spawned: datetime | None = None
        self.started: datetime | None = None
        self.t_match_over = self.t_metrics = self.t_profile = self.t_gc = None
        self.local_end_requested = False
        self.end: datetime | None = None
        self.status = "open"
        self.closed = False
        self.fixes: list[dict] = []
        self.transit_from_prev = False
        self.exit_via_transit = False
        self.insurance_lost = False

    def close(self, t: datetime, status: str) -> None:
        if not self.closed:
            self.end, self.status, self.closed = t, status, True

    def finish_online(self) -> None:
        candidates = [t for t in (self.t_match_over, self.t_metrics, self.t_profile, self.t_gc) if t]
        if candidates:
            self.close(min(candidates), "online-end")

    def location(self) -> tuple[str | None, str | None]:
        for value, source in (
            ((self.chain or [None])[-1], "transit"),
            (self.uc_loc, "confirmed"),
            (self.ngc_loc, "trace"),
            (self.hop_loc, "hop"),
            (self.reconnect_loc, "reconnect"),
            (scene_location(self.scene), "scene"),
        ):
            if value:
                return value.lower(), source
        return None, None

    def resolved_mode(self) -> str:
        host = self.host or ""
        if "pvp-season" in host:
            return "pvp"
        if "gw-pve" in host:
            return "pve"
        return normalize_mode(self.mode)


def _apply(a: _Attempt, ev: LogEvent) -> None:
    k, d, t = ev.kind, ev.data, ev.time
    if k == "local_start":
        a.kind, a.host = "local", d.get("host")
    elif k == "match_join":
        a.kind = a.kind or "online"
        a.host = a.host or d.get("host")
    elif k == "user_confirmed":
        a.kind, a.uc_loc = "online", d.get("loc")
    elif k == "ngc_status":
        a.kind, a.ngc_loc = "online", d.get("loc")
    elif k == "transit_local":
        a.raid_id = d["raid_id"].lower()
        a.transit_count = int(d["count"])
        a.chain = [x.strip() for x in d["locs"].split("->") if x.strip()]
    elif k == "transit_online":
        a.online_transit_count = int(d["count"])
    elif k == "spawn":
        a.spawned = a.spawned or t
    elif k == "started":
        a.started = a.started or t
    elif k == "position":
        if a.started or a.spawned:
            a.fixes.append({"time": t.isoformat(timespec="milliseconds"), "x": float(d["x"]), "y": float(d["y"]),
                            "z": float(d["z"]), "reason": d["reason"]})
    elif k in ("local_cancel", "net_cancel") and not a.started:
        a.close(t, "cancelled")
    elif k == "match_exit" and not a.started:
        a.close(t, "cancelled")
    elif k == "user_match_over" and a.started and not a.closed:
        a.t_match_over = t
    elif k == "local_end":
        a.local_end_requested = True
        if a.started:
            a.close(t, "raid-end")
    elif k == "local_end_resp" and a.started:
        a.close(t, "raid-end")
    elif k == "put_metrics" and a.started and not a.t_metrics:
        a.t_metrics = t
    elif k == "profile" and a.started and not a.t_profile:
        a.t_profile = t
    elif k == "gc_enabled" and a.started and not a.t_gc:
        a.t_gc = t
    # once the client is back in the menu the raid is over
    if a.started and not a.closed:
        if a.kind == "online" and (a.t_profile or a.t_gc):
            a.finish_online()
        elif a.kind == "local" and a.t_profile and not a.local_end_requested:
            a.close(a.t_profile, "unknown")


def segment(events: list[LogEvent], last_line: datetime | None) -> list[_Attempt]:
    attempts: list[_Attempt] = []
    cur: _Attempt | None = None
    pre: list[LogEvent] = []
    mode: str | None = None

    def start_new(ev: LogEvent) -> None:
        nonlocal cur, pre
        if cur is not None and not cur.closed:
            if cur.started:
                if cur.kind == "online":
                    cur.finish_online()
                cur.close(ev.time, "unknown")
            else:
                cur.close(ev.time, "abandoned")
        cur = _Attempt(mode, pre, ev.time)
        attempts.append(cur)
        pre = []

    for ev in events:
        k = ev.kind
        if k == "mode":
            mode = ev.data["mode"]
            continue
        if k in ("match_group", "pve_hop", "transit_reconnect") or (k == "matching_completed" and ev.data.get("t") == "0"):
            if cur is None or cur.closed:
                pre.append(ev)
                continue
            if k in ("pve_hop", "transit_reconnect") and cur.started:
                # a transit hop while the previous leg is formally open ends that leg
                if cur.kind == "online":
                    cur.finish_online()
                cur.close(ev.time, "unknown")
                pre.append(ev)
                continue
        if k == "scene":
            # a transit leg can be announced (UserConfirmed/TRACE) before its scene line
            if not (cur is not None and not cur.closed and cur.scene is None and not cur.started):
                start_new(ev)
            cur.scene = ev.data["rcid"]
            continue
        if k in ("user_confirmed", "ngc_status") and cur is not None and cur.started and not cur.closed:
            # Online transit: the next leg's announcement (push log) can land a few ms before
            # the running leg's end marker (application log). It belongs to the next leg.
            pre.append(ev)
            continue
        if k in ("location_loaded", "local_start", "user_confirmed", "ngc_status", "transit_local") and (cur is None or cur.closed):
            start_new(ev)
        if cur is not None:
            _apply(cur, ev)

    if cur is not None and not cur.closed:
        if cur.started:
            if cur.kind == "online":
                cur.finish_online()
            cur.close(last_line or cur.started, "open")
        else:
            cur.close(last_line or cur.t_queue, "abandoned")

    _link_transits(attempts)
    _mark_insurance(attempts, [e for e in events if e.kind == "insurance"])
    return attempts


def _link_transits(attempts: list[_Attempt]) -> None:
    for i in range(1, len(attempts)):
        a = attempts[i]
        prev = next((attempts[j] for j in range(i - 1, -1, -1) if attempts[j].started), None)
        if prev is None:
            continue
        hop = bool(a.hop_loc or a.reconnect_loc)
        same_chain = bool(a.raid_id and a.raid_id == prev.raid_id and (a.transit_count or 0) > (prev.transit_count or 0))
        online_leg = (a.online_transit_count or 0) > 0 or (a.transit_count or 0) > 0
        gap_ok = prev.end is None or a.t_queue - prev.end < TRANSIT_GAP
        if (hop or same_chain or online_leg) and gap_ok:
            a.transit_from_prev = True
            prev.exit_via_transit = True


def _mark_insurance(attempts: list[_Attempt], notes: list[LogEvent]) -> None:
    """Online raids: an insurance message right after the raid hints that insured gear was lost."""
    for a in attempts:
        if not (a.started and a.end) or not notes:
            continue
        loc, _ = a.location()
        end_msk = a.end.replace(tzinfo=LOCAL_TZ).astimezone(MSK).replace(tzinfo=None, second=0, microsecond=0)
        for note in notes:
            if not -30 <= (note.time - a.end).total_seconds() <= 600:
                continue
            if (note.data.get("loc") or "").lower() != loc:
                continue
            try:
                sent = datetime.strptime(f"{note.data.get('date')} {note.data.get('time')}", "%d.%m.%Y %H:%M")
            except (TypeError, ValueError):
                continue
            if abs((sent - end_msk).total_seconds()) <= 180:
                a.insurance_lost = True


def session_info(session_dir: Path) -> tuple[datetime | None, str]:
    m = SESSION_DIR_RE.match(Path(session_dir).name)
    if not m:
        return None, ""
    y, mo, d, h, mi, s, version = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s)), version
    except ValueError:  # e.g. "log_2026.13.45_...", not named by the game
        return None, version


def raids_from_session(session_dir: Path, names: list[str] | None = None) -> list[LogRaid]:
    """names: the log files to read (see session_events)."""
    session_dir = Path(session_dir)
    _, version = session_info(session_dir)
    events, last_line = session_events(session_dir, names)
    raids = []
    for a in segment(events, last_line):
        if not a.started:
            continue
        map_id, map_source = a.location()
        raids.append(LogRaid(
            start=a.started,
            end=a.end,
            map_id=map_id,
            raid_id=a.raid_id,
            mode=a.resolved_mode(),
            session=session_dir.name,
            version=version,
            end_reason=a.status,
            kind=a.kind or "unknown",
            map_source=map_source,
            transit_from_prev=a.transit_from_prev,
            exit_via_transit=a.exit_via_transit,
            insurance_lost=a.insurance_lost,
            fixes=a.fixes,
        ))
    return raids


def session_signature(session_dir: Path) -> list:
    """[[file name, size, mtime], ...] of the log files the parser reads, in parse order."""
    sig = []
    for kind in ("application", "backend", "push-notifications"):
        for p in session_files(session_dir, kind):
            try:
                st = p.stat()
            except OSError:
                continue
            sig.append([p.name, st.st_size, int(st.st_mtime)])
    return sig


def _valid_sig(sig) -> list | None:
    """A cached signature in the shape session_signature() returns, else None."""
    ok = isinstance(sig, list) and all(
        isinstance(f, list) and len(f) == 3 and isinstance(f[0], str) and type(f[1]) is int and type(f[2]) is int
        for f in sig)
    return sig if ok else None


def _shrunk(before: list | None, after: list) -> bool:
    """A file of signature `before` is gone or shorter in `after`. Logs only grow (the game
    never rotates or renames them), so that means files are being deleted or went away."""
    sizes = {name: size for name, size, _ in after}
    return any(sizes.get(name, -1) < size for name, size, _ in before or ())


class LogScanner:
    """Incremental scan of every session folder; unchanged folders come from the cache.

    The cache is a plain dict so the caller can persist it as JSON:
    {"version": CACHE_VERSION, "sessions": {folder: {"sig": [...], "raids": [...]}}}
    After a parser change "sig" is None (the folder is parsed again) and "prev" keeps the
    old signature until that parse succeeds.

    Sessions whose folder is gone (deleted to free space, game reinstalled, drive not
    mounted) keep their cached raids: the logs were the only record of those raids. So do
    folders caught while they are being deleted.
    """

    def __init__(self, logs_dir: Path | None, cache: dict | None = None):
        self.logs_dir = Path(logs_dir) if logs_dir else None
        # any JSON may come in (an older version, a hand-edited file): keep what has the
        # shape this class writes, so scan() can rely on it
        current = isinstance(cache, dict) and cache.get("version") == CACHE_VERSION
        old = cache.get("sessions") if isinstance(cache, dict) else None
        sessions = {}
        for name, entry in (old if isinstance(old, dict) else {}).items():
            if not (isinstance(name, str) and isinstance(entry, dict) and isinstance(entry.get("raids"), list)):
                continue
            sig, prev = _valid_sig(entry.get("sig")), _valid_sig(entry.get("prev"))
            if not current:
                # A parser change re-parses every session folder that still exists; the raids
                # of folders that are gone are carried over. The old sig still tells a folder
                # that is being deleted from one that grew.
                sig, prev = None, (sig if sig is not None else prev)
            sessions[name] = {"sig": sig, "raids": entry["raids"]}
            if sig is None and prev is not None:
                sessions[name]["prev"] = prev
        self.cache = {"version": CACHE_VERSION, "sessions": sessions}
        self.failed: set[str] = set()  # sessions that could not be read in the last scan
        self.visible: list[str] = []  # session folders found in the last scan, oldest first

    def sessions(self) -> list[Path]:
        if not self.logs_dir or not self.logs_dir.is_dir():
            return []
        dirs = [p for p in self.logs_dir.iterdir() if p.is_dir() and SESSION_DIR_RE.match(p.name)]
        return sorted(dirs, key=lambda p: session_info(p)[0] or datetime.min)

    def scan(self) -> list[LogRaid]:
        stored: dict = self.cache["sessions"]
        raids: list[LogRaid] = []
        self.failed = set()
        dirs = self.sessions()
        self.visible = [p.name for p in dirs]
        for session_dir in dirs:
            name = session_dir.name
            sig = session_signature(session_dir)
            cached = stored.get(name)
            if cached is not None and cached["sig"] == sig:
                continue
            if not any(fnmatch(f, _log_glob("application")) for f, _, _ in sig):
                # the game is still creating the folder, or it is being deleted: nothing to
                # read now, and what was cached stays
                continue
            if cached is not None and _shrunk(cached["sig"] or cached.get("prev"), sig):
                # files deleted (to free space) while the folder is still there: its cached
                # raids stay, as for a folder that is gone
                continue
            try:
                found = raids_from_session(session_dir, [f for f, _, _ in sig])
            except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError):
                # locked by another program, the drive dropped out, or a log this parser does
                # not understand: keep what was cached and retry on the next scan instead of
                # caching a partial parse (one odd session must not stop every scan)
                self.failed.add(name)
                continue
            if _shrunk(sig, session_signature(session_dir)):
                # files got shorter or went away while they were read (deleted, drive dropped out)
                self.failed.add(name)
                continue
            # stored with the signature taken before the parse: a file that grew meanwhile (the
            # session being played) is parsed again on the next scan
            stored[name] = {"sig": sig, "raids": [r.to_json() for r in found]}
        for entry in stored.values():
            for doc in entry["raids"]:
                try:
                    raids.append(LogRaid.from_json(doc))
                except (KeyError, TypeError, ValueError):  # carried over from an older format, or edited by hand
                    continue
        raids.sort(key=lambda r: r.start)
        return raids
