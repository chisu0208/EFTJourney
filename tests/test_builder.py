import json
import os
import random
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from eftjourney import builder as builder_mod
from eftjourney.builder import Builder, assemble, game_running, raid_id_for
from eftjourney.logs import LogRaid
from eftjourney.screenshots import Shot, shot_order
from eftjourney.store import RaidStore

MAPS = {"maps": [
    {"ids": ["bigmap"], "key": "customs", "name": "Customs", "bounds": [[-400, -300], [800, 300]]},
    {"ids": ["factory4_day", "factory4_night"], "key": "factory", "name": "Factory", "bounds": [[-80, -80], [80, 80]]},
]}

SESSION = "log_2026.10.27_22-32-44_1.2.5.0.48146"
STAMP = "2026.10.27_22-32-44_1.2.5.0.48146"
APP_LOG = """\
2026-10-27 22:33:14.941|1.2.5.0.48146|Info|application|Session mode: Pve
2026-10-27 22:37:02.921|1.2.5.0.48146|Debug|application|Matching with group id:
2026-10-27 22:37:25.202|1.2.5.0.48146|Info|application|LocationLoaded:16.81 real:22.28 diff:5.47
2026-10-27 22:37:26.000|1.2.5.0.48146|Info|application|[Transit] Flag:Common, RaidId:aaaaaaaaaaaaaaaaaaaaaaaa, Count:0, Locations:woods ->
2026-10-27 22:45:54.393|1.2.5.0.48146|Info|application|Network game matching cancelled.
2026-10-27 22:46:37.701|1.2.5.0.48146|Debug|application|TRACE-NetworkGameMatching G
2026-10-27 22:47:09.046|1.2.5.0.48146|Info|application|LocationLoaded:25.68 real:32.3 diff:6.61
2026-10-27 22:47:12.816|1.2.5.0.48146|Info|application|[Transit] Flag:Common, RaidId:6ae0ab5e00660118a0035ff8, Count:0, Locations:bigmap ->
2026-10-27 22:47:19.745|1.2.5.0.48146|Info|application|PlayerSpawnEvent:32.53(32.53) real:43(43) diff:10.47
2026-10-27 22:48:01.792|1.2.5.0.48146|Info|application|GameStarting:64.79(0) real:85.04(0) diff:20.25
2026-10-27 22:48:01.792|1.2.5.0.48146|Info|application|GameStarted:64.79(0) real:85.04(0) diff:20.25
{
  "multi-line": "json noise"
}
2026-10-27 22:59:12.201|1.2.5.0.48146|Info|application|BEClient exit
"""
BACKEND_LOG = """\
2026-10-27 22:47:10.254|1.2.5.0.48146|Info|backend|---> Request HTTPS, id [209]: URL: https://gw-pve.escapefromtarkov.com/client/match/local/start.
2026-10-27 22:57:46.599|1.2.5.0.48146|Info|backend|---> Request HTTPS, id [223]: URL: https://gw-pve.escapefromtarkov.com/client/match/local/end.
2026-10-27 22:57:47.791|1.2.5.0.48146|Info|backend|<--- Response HTTPS, id [223]: URL: https://gw-pve.escapefromtarkov.com/client/match/local/end, DownloadSeconds: 1.178, responseText:
"""


def shot(minute, second, x, z, y=1.0):
    taken = datetime(2026, 10, 27, 22, minute, second)
    name = f"2026-10-27[22-{minute:02d}]_{x}, {y}, {z}_0, 0, 0, 1_0.00 ({second}).png"
    return Shot(name, taken, x, y, z, 0, 0, 0, 1, 0.0, "mtime")


def log_raid(start, end, map_id="bigmap", reason="raid-end"):
    return LogRaid(start, end, map_id, "6ae0ab5e00660118a0035ff8", "pve", SESSION, "1.2.5.0.48146", reason)


def assemble_testing_every_shot(log_raids, shots, maps, keep_orphans=False, present=None):
    """assemble() as it was before it bisected the raid windows, every shot tested against
    every window: the faster search must give exactly what this gives."""
    b = builder_mod
    docs, used, outside = [], set(), 0
    raids = sorted(log_raids, key=lambda r: r.start)
    for i, lr in enumerate(raids):
        nxt = raids[i + 1].start if i + 1 < len(raids) else None
        end = lr.end or min(t for t in (nxt, lr.start + b.OPEN_RAID) if t is not None)
        lo = lr.start - b.PRE_START
        hi = end + (b.POST_OPEN if lr.end_reason == "open" else b.POST_END)
        if nxt is not None:
            hi = min(hi, nxt - b.PRE_START)
        picked = [s for s in shots if b._in_window(s, lo, hi) and s.name not in used]
        used.update(s.name for s in picked)
        entry = b.entry_for(maps, lr.map_id)
        if entry and entry.get("bounds"):
            kept = [s for s in picked if b.contains(entry, s.x, s.z, b.BOUNDS_PAD)]
            outside += len(picked) - len(kept)
            picked = kept
        docs.append(b._log_doc(lr, picked, maps, present))

    orphans = [s for s in shots if s.name not in used]
    if not keep_orphans:
        return docs, len(orphans), outside
    unassigned = 0
    if raids:
        cutoff = max(r.end or r.start for r in raids) + b.POST_OPEN
        unassigned = sum(1 for s in orphans if s.taken <= cutoff)
        orphans = [s for s in orphans if s.taken > cutoff]
    cluster = []
    for one in orphans + [None]:
        if cluster and (one is None or one.taken - cluster[-1].taken > b.ORPHAN_GAP):
            start = cluster[0].taken
            track = [b._sample(s, start, present) for s in cluster]
            docs.append({
                "schema": b.SCHEMA,
                "generator": b.GENERATOR,
                "id": b.raid_id_for(start, None),
                "map": b.guess_map(maps, track),
                "mode": "unknown",
                "kind": "unknown",
                "raidId": None,
                "startedAt": b._iso(start),
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
        if one is not None:
            cluster.append(one)
    return docs, unassigned, outside


def random_case(rnd):
    """Log raids and shots crowding the window edges: equal starts, overlapping windows, open
    raids, shots named a minute before a window, names and moments shared by several shots."""
    b = builder_mod
    base = datetime(2026, 10, 27, 20, 0, 0)
    raids = []
    for _ in range(rnd.randrange(9)):
        if raids and rnd.random() < 0.25:  # the same start, or one inside the raid before
            start = rnd.choice(raids).start + timedelta(seconds=rnd.choice((0, 1, 5, 30, 600)))
        else:
            start = base + timedelta(milliseconds=rnd.randrange(4 * 3600 * 1000))
        end = None if rnd.random() < 0.3 else start + timedelta(seconds=rnd.uniform(-60, 3600))
        reason = rnd.choice(("raid-end", "online-end", "open", "unknown"))
        map_id = rnd.choice((None, "bigmap", "factory4_day", "woods"))  # woods: no bounds here
        raids.append(LogRaid(start, end, map_id, None, "pve", SESSION, "1.2.5.0.48146", reason))
    edges = [base]
    for r in raids:
        lo = r.start - b.PRE_START
        edges += [lo, lo - b.NAME_MINUTE, r.start + b.OPEN_RAID + b.POST_END, r.start + b.OPEN_RAID + b.POST_OPEN]
        if r.end:
            edges += [r.end + b.POST_END, r.end + b.POST_OPEN]
    shots = []
    for k in range(rnd.randrange(120)):
        roll = rnd.random()
        if roll < 0.5:
            taken = rnd.choice(edges) + timedelta(microseconds=rnd.choice((0, 0, 1, -1, 10**6, -10**6)))
        elif roll < 0.6 and shots:
            taken = rnd.choice(shots).taken
        else:
            taken = base + timedelta(microseconds=rnd.randrange(-1800 * 10**6, 5 * 3600 * 10**6))
        source = rnd.choice(("birthtime", "mtime", "name", "name"))
        if source == "name" and rnd.random() < 0.5:
            taken = taken.replace(second=0, microsecond=0)  # all a filename tells
        name = rnd.choice(shots).name if shots and rnd.random() < 0.05 else f"{k}.png"
        x = rnd.choice((rnd.uniform(-450, 850), rnd.uniform(-90, 90), 2500.0))
        tod = None if rnd.random() < 0.5 else round(rnd.uniform(0, 23.99), 2)
        shots.append(Shot(name, taken, x, 1.0, rnd.uniform(-350, 350), 0, 0, 0, 1,
                          round(rnd.uniform(0, 360), 1), source, tod))
    if rnd.random() < 0.7:
        shots.sort(key=shot_order)  # as the builder passes them
    else:
        rnd.shuffle(shots)
    rnd.shuffle(raids)
    present = None if rnd.random() < 0.5 else {s.name for s in shots if rnd.random() < 0.5}
    return raids, shots, rnd.random() < 0.5, present


class AssembleTest(unittest.TestCase):
    def test_assigns_shots_inside_raid_window(self):
        raid = log_raid(datetime(2026, 10, 27, 22, 48, 1), datetime(2026, 10, 27, 22, 57, 46))
        shots = [shot(47, 0, 1, 1), shot(48, 30, 10, 20), shot(55, 0, 30, 40), shot(58, 30, 50, 50)]
        docs, orphans, outside = assemble([raid], shots, MAPS)
        (main,) = docs  # screenshots outside the raid window are not turned into raids
        self.assertEqual(main["id"], "20261027-224801-bigmap")
        self.assertEqual(main["map"], "bigmap")
        self.assertEqual([s["t"] for s in main["track"]], [29.0, 419.0])
        self.assertEqual(main["duration"], 585.0)
        self.assertEqual((orphans, outside), (2, 0))  # 22:47:00 (before the raid) and 22:58:30 (after)

    def test_without_logs_screenshots_are_grouped(self):
        shots = [shot(47, 0, 1, 1), shot(58, 30, 50, 50), shot(59, 0, 52, 50)]
        docs, orphans, _ = assemble([], shots, MAPS, keep_orphans=True)
        (only,) = docs  # 11.5 min gap < 15 min -> one cluster
        self.assertEqual(orphans, 0)
        self.assertEqual(only["endReason"], "screenshots-only")
        self.assertEqual(only["id"], "20261027-224700-unknown")
        self.assertEqual(len(only["track"]), 3)
        self.assertIsNone(only["map"])  # both test maps contain the points -> ambiguous, user picks

    def test_positions_far_outside_the_map_are_dropped(self):
        raid = log_raid(datetime(2026, 10, 27, 22, 48, 1), datetime(2026, 10, 27, 22, 57, 46))
        near_edge = shot(50, 0, 850, 0)  # 50 m past Customs' x bound: kept
        stale = shot(51, 0, 2500, 0)  # wrong map / stale coordinates
        docs, orphans, outside = assemble([raid], [near_edge, stale], MAPS)
        self.assertEqual([s["x"] for s in docs[0]["track"]], [850])
        self.assertEqual((orphans, outside), (0, 1))

    def test_tod_is_kept(self):
        raid = log_raid(datetime(2026, 10, 27, 22, 48, 1), datetime(2026, 10, 27, 22, 57, 46))
        s = Shot("t.png", datetime(2026, 10, 27, 22, 50), 1, 1, 1, 0, 0, 0, 1, 0, "birthtime", 14.65)
        sample = assemble([raid], [s], MAPS)[0][0]["track"][0]
        self.assertEqual(sample["tod"], 14.65)
        self.assertNotIn("approx", sample)

    def test_open_raid_is_cut_at_next_raid(self):
        first = log_raid(datetime(2026, 10, 27, 20, 0, 0), None, reason="open")
        second = log_raid(datetime(2026, 10, 27, 20, 30, 0), datetime(2026, 10, 27, 20, 50, 0), map_id="woods")
        shots = [
            Shot("a.png", datetime(2026, 10, 27, 20, 10), 1, 1, 1, 0, 0, 0, 1, 0, "mtime"),
            Shot("b.png", datetime(2026, 10, 27, 20, 35), 2, 1, 2, 0, 0, 0, 1, 0, "mtime"),
        ]
        docs, orphans, _ = assemble([first, second], shots, MAPS)
        self.assertEqual(orphans, 0)
        self.assertEqual([s["shot"] for s in docs[0]["track"]], ["a.png"])
        self.assertEqual(docs[0]["duration"], 600.0)  # no end marker: at least until the last screenshot
        self.assertTrue(docs[0]["durationApprox"])
        self.assertEqual([s["shot"] for s in docs[1]["track"]], ["b.png"])

    def test_transit_outcome_and_server_fixes(self):
        raid = log_raid(datetime(2026, 10, 27, 22, 48, 1), datetime(2026, 10, 27, 22, 57, 46))
        raid.exit_via_transit = True
        raid.fixes = [
            {"time": "2026-10-27T22:50:00.250", "x": 1.234, "y": 2.0, "z": -3.456, "reason": "Stuck"},
            {"time": "2026-10-27T23:30:00.000", "x": 9.0, "y": 9.0, "z": 9.0, "reason": "Speed"},  # after the raid
        ]
        doc = assemble([raid], [], MAPS)[0][0]
        self.assertEqual(doc["outcome"], {"status": "transit", "source": "log"})
        self.assertEqual(doc["logFixes"], [{"t": 119.2, "x": 1.23, "y": 2.0, "z": -3.46, "reason": "Stuck"}])
        self.assertFalse(doc["durationApprox"])

    def test_client_closed_raid_keeps_late_screenshots(self):
        # end = last log line; the client may have been closed up to a keepalive later
        raid = log_raid(datetime(2026, 10, 27, 22, 0, 0), datetime(2026, 10, 27, 22, 30, 0), reason="open")
        late = Shot("late.png", datetime(2026, 10, 27, 22, 32, 0), 1, 1, 1, 0, 0, 0, 1, 0, "mtime")
        too_late = Shot("later.png", datetime(2026, 10, 27, 22, 40, 0), 2, 1, 2, 0, 0, 0, 1, 0, "mtime")
        docs, orphans, _ = assemble([raid], [late, too_late], MAPS)
        self.assertEqual([s["shot"] for s in docs[0]["track"]], ["late.png"])
        self.assertEqual(docs[0]["duration"], 1920.0)
        self.assertEqual(orphans, 1)

    def test_unknown_map_guessed_but_id_stable(self):
        raid = log_raid(datetime(2026, 10, 27, 22, 48, 1), datetime(2026, 10, 27, 22, 57, 46), map_id=None)
        docs, *_ = assemble([raid], [shot(50, 0, 500, 0)], MAPS)
        self.assertEqual(docs[0]["id"], "20261027-224801-unknown")
        self.assertEqual(docs[0]["map"], "bigmap")

    def test_approx_flag_for_name_time(self):
        raid = log_raid(datetime(2026, 10, 27, 22, 48, 1), datetime(2026, 10, 27, 22, 57, 46))
        s = Shot("n.png", datetime(2026, 10, 27, 22, 50), 1, 1, 1, 0, 0, 0, 1, 0, "name")
        docs, *_ = assemble([raid], [s], MAPS)
        self.assertTrue(docs[0]["track"][0]["approx"])

    def test_name_time_shot_in_the_start_minute(self):
        # named [22-48] but the raid started at 22:48:41: the shot may be from 22:48:45
        raid = log_raid(datetime(2026, 10, 27, 22, 48, 41), datetime(2026, 10, 27, 22, 57, 46))
        s = Shot("n.png", datetime(2026, 10, 27, 22, 48), 1, 1, 1, 0, 0, 0, 1, 0, "name")
        docs, orphans, _ = assemble([raid], [s], MAPS)
        self.assertEqual([(p["shot"], p["t"]) for p in docs[0]["track"]], [("n.png", 0.0)])
        self.assertEqual(orphans, 0)

    def test_same_minute_name_shots_follow_time_of_day(self):
        minute = datetime(2026, 10, 27, 22, 50)
        shots = [Shot(f"{x}.png", minute, x, 1, 1, 0, 0, 0, 1, 0, "name", tod) for x, tod in
                 ((-100, 11.71), (-200, 11.72), (-300, 11.70))]
        self.assertEqual([s.x for s in sorted(shots, key=shot_order)], [-300, -100, -200])

    def test_deleted_screenshots_are_flagged(self):
        raid = log_raid(datetime(2026, 10, 27, 22, 48, 1), datetime(2026, 10, 27, 22, 57, 46))
        shots = [shot(49, 0, 1, 1), shot(50, 0, 2, 2)]
        docs, *_ = assemble([raid], shots, MAPS, present={shots[1].name})
        self.assertEqual([p.get("gone", False) for p in docs[0]["track"]], [True, False])

    def test_server_fixes_far_outside_the_map_are_dropped(self):
        raid = log_raid(datetime(2026, 10, 27, 22, 48, 1), datetime(2026, 10, 27, 22, 57, 46))
        raid.fixes = [
            {"time": "2026-10-27T22:50:00.000", "x": 850.0, "y": 1.0, "z": 0.0, "reason": "Stuck"},  # near the edge
            {"time": "2026-10-27T22:51:00.000", "x": 2500.0, "y": 1.0, "z": 0.0, "reason": "Speed"},  # not this map
        ]
        docs, orphans, outside = assemble([raid], [], MAPS)
        self.assertEqual([f["x"] for f in docs[0]["logFixes"]], [850.0])
        self.assertEqual(outside, 0)  # that counter is about screenshots

    def test_without_logs_only_screenshots_after_the_known_raids_are_grouped(self):
        raid = log_raid(datetime(2026, 10, 27, 22, 48, 1), datetime(2026, 10, 27, 22, 57, 46))
        late = Shot("late.png", datetime(2026, 10, 27, 23, 10), 120, 1, 60, 0, 0, 0, 1, 0, "mtime")
        docs, orphans, _ = assemble([raid], [shot(40, 0, 3, -5), shot(50, 0, 100, 50), late], MAPS, keep_orphans=True)
        self.assertEqual([d["id"] for d in docs], ["20261027-224801-bigmap", "20261027-231000-unknown"])
        self.assertEqual(orphans, 1)  # 22:40, before the logged raid, stays out

    def test_each_raid_window_tests_only_the_shots_near_it(self):
        # testing every shot against every raid window made a watch scan take seconds with
        # the 600+ raids and tens of thousands of journaled shots of a long-time player
        first = datetime(2026, 10, 1, 12, 0, 0)
        raids = [log_raid(first + timedelta(minutes=30 * i), first + timedelta(minutes=30 * i + 20))
                 for i in range(100)]
        shots = [Shot(f"{k}.png", first + timedelta(seconds=45 * k), 1, 1, 1, 0, 0, 0, 1, 0, ("mtime", "name")[k % 2])
                 for k in range(4000)]
        tested = []
        real = builder_mod._in_window

        def counted(s, lo, hi):
            tested.append(s.name)
            return real(s, lo, hi)

        with mock.patch.object(builder_mod, "_in_window", counted):
            result = assemble(raids, shots, MAPS)
        self.assertLessEqual(len(tested), len(shots))  # each shot is near one raid at most (was 100 x 4000)
        self.assertEqual(result, assemble_testing_every_shot(raids, shots, MAPS))

    def test_window_search_picks_what_testing_every_shot_picks(self):
        rnd = random.Random(20261030)
        seen = [0, 0]
        for case in range(1000):
            raids, shots, keep_orphans, present = random_case(rnd)
            seen = [seen[0] + len(raids), seen[1] + len(shots)]
            self.assertEqual(assemble(raids, shots, MAPS, keep_orphans, present),
                             assemble_testing_every_shot(raids, shots, MAPS, keep_orphans, present), f"case {case}")
        self.assertGreater(min(seen), 3000)  # raids, shots

    def test_raid_ids_stay_storable(self):
        start = datetime(2026, 11, 2, 20, 6, 0)
        self.assertEqual(raid_id_for(start, "Terminal-Port"), "20261102-200600-terminal_port")
        self.assertEqual(raid_id_for(start, "bigmap"), "20261102-200600-bigmap")
        self.assertEqual(raid_id_for(start, "..."), "20261102-200600-unknown")
        self.assertEqual(raid_id_for(start, None), "20261102-200600-unknown")


class GameRunningTest(unittest.TestCase):
    def run_with(self, **kwargs):
        done = subprocess.CompletedProcess(["tasklist"], kwargs.pop("returncode", 0), **kwargs)
        with mock.patch.object(builder_mod.sys, "platform", "win32"), \
                mock.patch.object(builder_mod.subprocess, "run", return_value=done):
            return game_running()

    def test_matches_bytes_in_any_code_page(self):
        self.assertTrue(self.run_with(stdout=b'"EscapeFromTarkov.exe","1234","Console","1","2,345,678 K"\r\n'))
        no_tasks = "정보: 지정된 조건에 맞는 실행 중인 작업이 없습니다.".encode("cp949")
        self.assertIs(self.run_with(stdout=no_tasks), False)

    def test_unknown_when_the_check_fails(self):
        self.assertIsNone(self.run_with(stdout=b"", returncode=1))
        with mock.patch.object(builder_mod.sys, "platform", "win32"), \
                mock.patch.object(builder_mod.subprocess, "run", side_effect=subprocess.TimeoutExpired("tasklist", 5)):
            self.assertIsNone(game_running())
        with mock.patch.object(builder_mod.sys, "platform", "linux"):
            self.assertIsNone(game_running())


class BuilderScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.logs = root / "Logs"
        session = self.logs / SESSION
        session.mkdir(parents=True)
        (session / f"{STAMP} application_000.log").write_text(APP_LOG, encoding="utf-8")
        (session / f"{STAMP} backend_000.log").write_text(BACKEND_LOG, encoding="utf-8")
        self.shots = root / "Screenshots"
        self.shots.mkdir()
        self.store = RaidStore(root / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def add_shot(self, minute, second, x, z, hour=22, ext="png", mtime=None):
        name = f"2026-10-27[{hour:02d}-{minute:02d}]_{x}, 1.0, {z}_0.0, 0.0, 0.0, 1.0_0.00 (0).{ext}"
        path = self.shots / name
        path.write_bytes(b"png")
        ts = (mtime or datetime(2026, 10, 27, hour, minute, second)).timestamp()
        os.utime(path, (ts, ts))
        return path

    def track(self, raid_id="20261027-224801-bigmap"):
        """(t, x, file extension, gone) per point of a stored raid."""
        return [(s["t"], s["x"], os.path.splitext(s["shot"])[1], s.get("gone", False))
                for s in self.store.load_raid(raid_id)["track"]]

    def live_session(self, launched, started=None, last=None, logs=None):
        """A session folder of a client launched at `launched`, with an open Woods raid since
        `started` (if given) and a last log line at `last`. Returns (folder name, raid id)."""
        version = "1.3.0.0.48484"
        name = f"log_{launched:%Y.%m.%d}_{launched.hour}-{launched:%M-%S}_{version}"
        session = (logs or self.logs) / name
        session.mkdir(parents=True)
        line = lambda t, msg: f"{t:%Y-%m-%d %H:%M:%S}.000|{version}|Info|application|{msg}\n"  # noqa: E731
        text = line(launched, "Session mode: Pve")
        if started:
            text += line(started - timedelta(minutes=1), "scene preset path:maps/woods_preset.bundle rcid:woods.scenespreset.asset")
            text += line(started, "GameStarted:1(0) real:1(0) diff:0")
        if last:
            text += line(last, "a line the parser does not care about")
        (session / f"{name[4:]} application_000.log").write_text(text, encoding="utf-8")
        return name, f"{started:%Y%m%d-%H%M%S}-woods" if started else None

    def builder(self):
        return Builder(self.store, MAPS, self.logs, self.shots)

    def test_end_to_end(self):
        self.add_shot(49, 0, 100, 50)
        self.add_shot(52, 30, 140, 80)
        report = self.builder().scan()
        self.assertEqual(report.sessions, 1)
        self.assertEqual(report.written, ["20261027-224801-bigmap"])
        raid = self.store.load_raid("20261027-224801-bigmap")
        self.assertEqual(raid["mode"], "pve")
        self.assertEqual(raid["raidId"], "6ae0ab5e00660118a0035ff8")
        self.assertEqual(raid["endReason"], "raid-end")
        self.assertEqual([s["t"] for s in raid["track"]], [58.2, 268.2])
        self.assertTrue(raid["startedAt"].startswith("2026-10-27T22:48:01.792"))

        again = self.builder().scan()  # fresh builder reads the on-disk log cache
        self.assertEqual(again.written, [])
        self.assertFalse(again.changed)

        self.add_shot(55, 0, 160, 90)
        third = self.builder().scan()
        self.assertEqual(third.written, ["20261027-224801-bigmap"])
        self.assertEqual(len(self.store.load_raid("20261027-224801-bigmap")["track"]), 3)

    def test_screenshot_outside_raids_is_ignored(self):
        self.add_shot(10, 0, 1, 1)  # 22:10, before the logged raid (menu or hideout)
        report = self.builder().scan()
        self.assertEqual(report.orphan_samples, 1)
        self.assertEqual(self.store.list_ids(), ["20261027-224801-bigmap"])

    def test_stale_generated_raids_removed_unless_edited(self):
        no_logs = lambda: Builder(self.store, MAPS, None, self.shots)  # noqa: E731
        self.add_shot(10, 0, 1, 1)  # without logs this becomes a screenshots-only raid
        report = no_logs().scan()
        self.assertTrue(report.warnings)
        self.assertIn("20261027-221000-unknown", self.store.list_ids())
        self.store.save_edits("20261027-221000-unknown", {"notes": "keep me"})
        report = self.builder().scan()  # with logs the shot lies outside every raid
        self.assertNotIn("20261027-221000-unknown", report.removed)
        self.assertIn("20261027-221000-unknown", self.store.list_ids())

        self.store.delete_edits("20261027-221000-unknown")
        report = self.builder().scan()
        self.assertIn("20261027-221000-unknown", report.removed)

    def test_without_the_logs_only_later_screenshots_are_grouped(self):
        self.add_shot(49, 0, 100, 50)  # in the logged raid
        self.add_shot(40, 0, 3, -5)  # in the hideout before it: the logs already ruled it out
        self.builder().scan()
        shutil.rmtree(self.logs / SESSION)  # game reinstalled: Logs is empty, logs.json remembers
        self.add_shot(10, 0, 120, 60, hour=23)  # a raid played since, while the logs are not found
        report = self.builder().scan()
        self.assertEqual(report.written, ["20261027-231000-unknown"])
        self.assertEqual((report.removed, report.orphan_samples), ([], 1))
        self.assertIn("20261027-224801-bigmap", self.store.list_ids())
        self.assertNotIn("20261027-224000-unknown", self.store.list_ids())
        self.assertTrue(any("마지막으로 기록된 레이드 뒤" in w for w in report.warnings), report.warnings)

    def test_deleted_screenshots_keep_the_route(self):
        self.add_shot(49, 0, 100, 50)
        self.add_shot(52, 30, 140, 80)
        self.builder().scan()
        for p in self.shots.iterdir():
            p.unlink()
        report = self.builder().scan()  # a fresh builder reads the journal from disk
        self.assertEqual(report.removed, [])
        track = self.store.load_raid("20261027-224801-bigmap")["track"]
        self.assertEqual([(s["t"], s.get("gone")) for s in track], [(58.2, True), (268.2, True)])

    def test_deleted_log_session_keeps_its_raids(self):
        self.add_shot(49, 0, 100, 50)
        self.builder().scan()
        shutil.rmtree(self.logs / SESSION)
        report = self.builder().scan()
        self.assertEqual((report.removed, report.orphan_samples), ([], 0))
        self.assertEqual(len(self.store.load_raid("20261027-224801-bigmap")["track"]), 1)

        cache = self.store.root / "cache" / "logs.json"  # a cache format change keeps them too
        cache.write_text(cache.read_text(encoding="utf-8").replace('"version": 3', '"version": 2'), encoding="utf-8")
        report = self.builder().scan()
        self.assertEqual((report.removed, report.raids_total), ([], 1))

        cache.unlink()  # without any record the stored raid still stays
        self.assertEqual(self.builder().scan().removed, [])
        self.assertIn("20261027-224801-bigmap", self.store.list_ids())

    def test_unreadable_session_keeps_its_raids(self):
        self.builder().scan()
        (self.store.root / "cache" / "logs.json").unlink()  # e.g. after a cache format change
        with mock.patch("eftjourney.logs.raids_from_session", side_effect=PermissionError("locked")):
            report = self.builder().scan()
        self.assertEqual(report.removed, [])
        self.assertTrue(any(SESSION in w for w in report.warnings))
        self.assertIn("20261027-224801-bigmap", self.store.list_ids())

    def test_edits_follow_the_raid_that_replaced_them(self):
        self.add_shot(49, 0, 100, 50)
        self.add_shot(52, 30, 140, 80)
        Builder(self.store, MAPS, None, self.shots).scan()  # logs not found yet
        self.store.save_edits("20261027-224900-unknown", {"notes": "typed before the logs were found"})
        report = self.builder().scan()
        self.assertIn("20261027-224900-unknown", report.removed)
        self.assertEqual(self.store.load_edits("20261027-224801-bigmap")["notes"], "typed before the logs were found")
        self.assertNotIn("20261027-224900-unknown", self.store.list_ids())
        self.assertFalse((self.store.raids_dir / "20261027-224900-unknown.edits.json").exists())

    def edited_raid_before_the_logs(self, edits=True):
        """A screenshots-only raid (logs not found yet), with edits: the logs' raid replaces it."""
        self.add_shot(49, 0, 100, 50)
        self.add_shot(52, 30, 140, 80)
        Builder(self.store, MAPS, None, self.shots).scan()
        if edits:
            self.store.save_edits("20261027-224900-unknown", {"notes": "typed before the logs were found"})

    def test_raid_whose_replacement_could_not_be_written_stays(self):
        # the edits went to another raid, or a raid without edits was deleted, while what
        # replaced it was not on disk
        for edits in (True, False):
            with self.subTest(edits=edits):
                self.edited_raid_before_the_logs(edits)
                real_replace = os.replace

                def replace(src, dst):
                    if Path(dst).name == "20261027-224801-bigmap.json":
                        raise PermissionError(13, "held by another program")
                    real_replace(src, dst)

                with mock.patch.object(builder_mod.os, "replace", replace), mock.patch("eftjourney.store.time.sleep"):
                    report = self.builder().scan()
                self.assertEqual((report.written, report.removed), ([], []))
                self.assertEqual(self.store.list_ids(), ["20261027-224900-unknown"])
                self.assertEqual(bool(self.store.load_edits("20261027-224900-unknown")), edits)
                report = self.builder().scan()
                self.assertEqual((report.written, report.removed), (["20261027-224801-bigmap"], ["20261027-224900-unknown"]))
                if edits:
                    self.assertEqual(self.store.load_edits("20261027-224801-bigmap")["notes"], "typed before the logs were found")
                for p in self.shots.iterdir():
                    p.unlink()
                shutil.rmtree(self.store.root)

    def test_stale_raid_that_cannot_be_deleted_is_a_warning(self):
        # a file held a moment (the viewer's server reading it, a virus scanner) failed the whole
        # scan: the raids it had written were never announced, and a move whose delete failed
        # left the raid twice for good
        for held in ("20261027-224900-unknown.json", "20261027-224900-unknown.edits.json"):
            with self.subTest(held=held):
                self.edited_raid_before_the_logs()
                real_unlink = Path.unlink

                def unlink(path, missing_ok=False):
                    if path.name == held:
                        raise PermissionError(13, "held by another program")
                    return real_unlink(path, missing_ok=missing_ok)

                with mock.patch.object(Path, "unlink", unlink), mock.patch("eftjourney.store.time.sleep"):
                    report = self.builder().scan()
                self.assertEqual((report.written, report.removed), (["20261027-224801-bigmap"], []))
                self.assertTrue(any("오래된 레이드를 정리하지 못했습니다 (다음 스캔에서 다시 시도): 20261027-224900-unknown" in w
                                    for w in report.warnings), report.warnings)
                report = self.builder().scan()  # let go: the move is finished
                self.assertEqual(report.removed, ["20261027-224900-unknown"])
                self.assertEqual(self.store.list_ids(), ["20261027-224801-bigmap"])
                self.assertEqual(self.store.load_edits("20261027-224801-bigmap")["notes"], "typed before the logs were found")
                self.assertEqual(sorted(p.name for p in self.store.raids_dir.iterdir()),
                                 ["20261027-224801-bigmap.edits.json", "20261027-224801-bigmap.json"])
                for p in self.shots.iterdir():
                    p.unlink()
                shutil.rmtree(self.store.root)

    def test_edits_that_cannot_move_are_named(self):
        # the user edited the new raid after a move the scan could not finish: the old raid
        # stayed in the list for good, with nothing said
        self.edited_raid_before_the_logs()
        real_unlink = Path.unlink

        def unlink(path, missing_ok=False):
            if path.name == "20261027-224900-unknown.edits.json":
                raise PermissionError(13, "held by another program")
            return real_unlink(path, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", unlink), mock.patch("eftjourney.store.time.sleep"):
            self.builder().scan()
        self.store.save_edits("20261027-224801-bigmap", {"notes": "typed on the log raid"})
        report = self.builder().scan()
        old_edits = self.store.raids_dir / "20261027-224900-unknown.edits.json"
        self.assertTrue(any("20261027-224900-unknown 에서 20261027-224801-bigmap 로 옮기지 못했습니다" in w and str(old_edits) in w
                            for w in report.warnings), report.warnings)
        self.assertEqual(sorted(self.store.list_ids()), ["20261027-224801-bigmap", "20261027-224900-unknown"])
        old_edits.unlink()  # what the warning says to do
        report = self.builder().scan()
        self.assertEqual(report.removed, ["20261027-224900-unknown"])
        self.assertEqual(self.store.load_edits("20261027-224801-bigmap")["notes"], "typed on the log raid")

    def test_unreadable_edits_are_neither_moved_nor_dropped(self):
        # read as "no edits": the raid they belonged to was deleted and the file left behind, unnamed
        self.edited_raid_before_the_logs()
        old_edits = self.store.edits_file("20261027-224900-unknown")
        old_edits.write_text('{"notes": "typed before the logs were found",}', encoding="utf-8")  # a hand edit gone wrong
        report = self.builder().scan()
        self.assertEqual(report.removed, [])
        self.assertTrue(any("편집 파일을 읽을 수 없어 20261027-224900-unknown 를 정리하지 않았습니다" in w and str(old_edits) in w
                            for w in report.warnings), report.warnings)
        self.assertEqual(self.store.read_edits("20261027-224801-bigmap"), {})
        old_edits.write_text('{"notes": "typed before the logs were found"}', encoding="utf-8")  # mended
        report = self.builder().scan()
        self.assertEqual(report.removed, ["20261027-224900-unknown"])
        self.assertEqual(self.store.load_edits("20261027-224801-bigmap")["notes"], "typed before the logs were found")

    def test_unreadable_edits_of_the_new_raid_are_not_written_over(self):
        # read as "no edits": what the user had typed for the new raid was replaced by the moved edits
        self.edited_raid_before_the_logs()
        target = self.store.edits_file("20261027-224801-bigmap")
        target.write_text('{"notes": "typed on the log raid",}', encoding="utf-8")
        report = self.builder().scan()
        self.assertTrue(any("20261027-224900-unknown 에서 20261027-224801-bigmap 로 옮기지 못했습니다" in w and str(target) in w
                            for w in report.warnings), report.warnings)
        self.assertEqual(target.read_text(encoding="utf-8"), '{"notes": "typed on the log raid",}')
        self.assertEqual(sorted(self.store.list_ids()), ["20261027-224801-bigmap", "20261027-224900-unknown"])

    def test_edits_saved_with_a_bom_still_move(self):
        # valid JSON saved with a BOM (Notepad's "UTF-8 with BOM") read as no edits: deleted with its raid
        self.edited_raid_before_the_logs()
        old_edits = self.store.edits_file("20261027-224900-unknown")
        old_edits.write_text(old_edits.read_text(encoding="utf-8"), encoding="utf-8-sig")
        report = self.builder().scan()
        self.assertEqual(report.removed, ["20261027-224900-unknown"])
        self.assertEqual(self.store.load_edits("20261027-224801-bigmap")["notes"], "typed before the logs were found")

    def test_screenshots_only_raid_in_progress_is_hidden(self):
        now = datetime.now().replace(microsecond=0)

        def add(minutes_ago, x):
            taken = now - timedelta(minutes=minutes_ago)
            path = self.shots / f"{taken:%Y-%m-%d[%H-%M]}_{x}, 1.0, 1.0_0.0, 0.0, 0.0, 1.0_0.00 ({x}).png"
            path.write_bytes(b"png")
            os.utime(path, (taken.timestamp(), taken.timestamp()))

        no_logs = lambda: Builder(self.store, MAPS, None, self.shots)  # noqa: E731
        add(3, 1)
        add(1, 2)
        with mock.patch("eftjourney.builder.game_running", return_value=False):
            report = no_logs().scan()  # the last screenshot is a minute old
        self.assertIsNotNone(report.in_raid)
        self.assertEqual(self.store.list_ids(), [])

        for p in self.shots.iterdir():
            p.unlink()
        (self.store.root / "cache" / "shots.json").unlink()
        add(40, 3)
        add(30, 4)
        with mock.patch("eftjourney.builder.game_running", return_value=True):
            report = no_logs().scan()  # half an hour ago, but the game is still running
        self.assertIsNotNone(report.in_raid)
        self.assertEqual(self.store.list_ids(), [])
        with mock.patch("eftjourney.builder.game_running", return_value=False):
            report = no_logs().scan()
        self.assertIsNone(report.in_raid)
        self.assertEqual(len(self.store.list_ids()), 1)

    def test_logs_folder_found_later(self):
        empty = self.logs.parent / "EmptyLogs"
        empty.mkdir()
        found = []
        b = Builder(self.store, MAPS, empty, self.shots, find_logs=lambda: found[0] if found else None)
        b._find_checked = 0.0
        b.scan()
        self.assertEqual(self.store.list_ids(), [])
        found.append(self.logs)  # the game created its first session
        b._find_checked = 0.0
        b.scan()
        self.assertEqual(b.logs_dir, self.logs)
        self.assertEqual(self.store.list_ids(), ["20261027-224801-bigmap"])

    def test_raid_in_progress_is_not_published(self):
        now = datetime.now().replace(microsecond=0)
        version = "1.3.0.0.48484"
        name = f"log_{now:%Y.%m.%d}_{now.hour}-{now:%M-%S}_{version}"
        session = self.logs / name
        session.mkdir()
        started = now - timedelta(minutes=5)
        stamp = f"{started:%Y-%m-%d %H:%M:%S}.000|{version}|Info|application|"
        (session / f"{name[4:]} application_000.log").write_text(
            f"{stamp}Session mode: Pve\n"
            f"{stamp}scene preset path:maps/woods_preset.bundle rcid:woods.scenespreset.asset\n"
            f"{stamp}GameStarted:1(0) real:1(0) diff:0\n",
            encoding="utf-8",
        )
        raid_id = f"{started:%Y%m%d-%H%M%S}-woods"
        with mock.patch("eftjourney.builder.game_running", return_value=True):
            report = self.builder().scan()
        self.assertEqual(report.in_raid["map"], "woods")
        self.assertNotIn(raid_id, self.store.list_ids())
        with mock.patch("eftjourney.builder.game_running", return_value=None):
            report = self.builder().scan()  # the process check failed: stay hidden
        self.assertIsNotNone(report.in_raid)
        self.assertNotIn(raid_id, self.store.list_ids())
        with mock.patch("eftjourney.builder.game_running", return_value=False):
            report = self.builder().scan()  # game closed: the raid is history now
        self.assertIsNone(report.in_raid)
        self.assertEqual(self.store.load_raid(raid_id)["endReason"], "open")

    def test_missing_folders(self):
        report = Builder(self.store, MAPS, None, None).scan()
        self.assertEqual(report.raids_total, 0)
        report = Builder(self.store, MAPS, self.logs / "nope", self.shots / "nope").scan()
        self.assertEqual(report.raids_total, 0)
        # no Screenshots folder before the first screenshot is normal: nothing to warn about
        self.assertEqual(Builder(self.store, MAPS, self.logs, self.shots / "nope").scan().warnings, [])

    def test_open_raid_is_judged_by_its_own_session_folder(self):
        now = datetime.now().replace(microsecond=0)
        _, raid_id = self.live_session(now - timedelta(minutes=25), now - timedelta(minutes=6),
                                       last=now - timedelta(seconds=40))
        other = self.logs.parent / "OtherLogs"  # e.g. another install's Logs folder
        shutil.copytree(self.logs / SESSION, other / SESSION)
        empty = self.logs.parent / "EmptyLogs"
        empty.mkdir()
        with mock.patch("eftjourney.builder.game_running", return_value=True):
            self.assertEqual(self.builder().scan().in_raid["map"], "woods")
            for logs_dir in (None, empty, other):
                report = Builder(self.store, MAPS, logs_dir, self.shots).scan()  # only logs.json knows it
                self.assertEqual((report.in_raid or {}).get("map"), "woods", logs_dir)
                self.assertNotIn(raid_id, self.store.list_ids())
            b = self.builder()
            b.scan()
            away = self.logs.rename(self.logs.parent / "LogsAway")  # gone for one scan of the same Builder
            self.assertIsNotNone(b.scan().in_raid)
            self.assertNotIn(raid_id, self.store.list_ids())

            away.rename(self.logs)
            self.live_session(now - timedelta(seconds=20))  # the client was closed and started again
            report = self.builder().scan()
        self.assertIsNone(report.in_raid)
        self.assertEqual(self.store.load_raid(raid_id)["endReason"], "open")

    def test_raid_in_progress_leaves_the_list_wherever_its_logs_are(self):
        now = datetime.now().replace(microsecond=0)
        _, raid_id = self.live_session(now - timedelta(minutes=25), now - timedelta(minutes=6),
                                       last=now - timedelta(minutes=3))
        self.live_session(now - timedelta(minutes=2))  # the client was started again: listed
        away = self.logs.with_name("LogsAway")
        with mock.patch("eftjourney.builder.game_running", return_value=True):
            self.assertIn(raid_id, self.builder().scan().written)
            self.logs.rename(away)  # e.g. the drive dropped out: only logs.json knows the raid now
            report = self.builder().scan()  # so it may be the raid being played
            self.assertEqual((report.in_raid or {}).get("map"), "woods")
            self.assertEqual(report.removed, [raid_id])
            self.assertNotIn(raid_id, self.store.list_ids())

            away.rename(self.logs)
            self.assertIn(raid_id, self.builder().scan().written)  # built again, nothing lost
            self.store.save_edits(raid_id, {"notes": "typed after the raid"})
            self.logs.rename(away)
            report = self.builder().scan()
        self.assertIsNotNone(report.in_raid)
        self.assertEqual(report.removed, [])  # edits are never dropped: they keep it listed
        self.assertEqual(self.store.load_edits(raid_id), {"notes": "typed after the raid"})
        self.assertIn(raid_id, self.store.list_ids())

    def test_edits_of_the_raid_in_progress_never_move(self):
        # Stored under the id of the raid being played, with screenshots another raid holds
        # now (other rules back then, say): the edits are not handed to that raid.
        self.add_shot(49, 0, 100, 50)  # in the logged raid, or a screenshots-only one without logs
        now = datetime.now().replace(microsecond=0)
        self.live_session(now - timedelta(minutes=25), now - timedelta(minutes=6), last=now - timedelta(seconds=40))
        taken = now - timedelta(minutes=1)  # without logs, the newest group is being played
        path = self.shots / f"{taken:%Y-%m-%d[%H-%M]}_2, 1.0, 1.0_0.0, 0.0, 0.0, 1.0_0.00 (2).png"
        path.write_bytes(b"png")
        os.utime(path, (taken.timestamp(), taken.timestamp()))
        cases = (("logs", self.store, self.logs, "woods", "20261027-224801-bigmap"),
                 ("screenshots only", RaidStore(Path(self.tmp.name) / "data-nologs"), None, None, "20261027-224900-unknown"))
        for label, store, logs, map_id, other in cases:
            with self.subTest(label), mock.patch("eftjourney.builder.game_running", return_value=True):
                b = Builder(store, MAPS, logs, self.shots)
                played = raid_id_for(datetime.fromisoformat(b.scan().in_raid["since"]), map_id)
                store.save_raid(dict(store.load_raid(other), id=played))
                store.save_edits(played, {"notes": "keep me here"})
                report = b.scan()
                self.assertIsNotNone(report.in_raid)
                self.assertEqual(report.removed, [])
                self.assertEqual((store.load_edits(played), store.load_edits(other)), ({"notes": "keep me here"}, {}))

    def test_raid_seen_ended_is_not_hidden_again(self):
        now = datetime.now().replace(microsecond=0)
        _, raid_id = self.live_session(now - timedelta(minutes=50), now - timedelta(minutes=40),
                                       last=now - timedelta(minutes=28))  # client closed in the raid
        for running in (False, None, True):  # closed; a failed check after a restart; started again
            with mock.patch("eftjourney.builder.game_running", return_value=running):
                report = self.builder().scan()  # a fresh builder: the moment is kept in state.json
            self.assertEqual((report.in_raid, report.removed), (None, []), running)
            self.assertIn(raid_id, self.store.list_ids())
        (self.store.root / "cache" / "state.json").write_text("{broken", encoding="utf-8")
        with mock.patch("eftjourney.builder.game_running", return_value=None):
            report = self.builder().scan()  # never seen closed and cannot tell: fail closed
        self.assertIsNotNone(report.in_raid)

    def test_game_seen_closed_is_remembered_at_the_latest_check(self):
        now = datetime.now().replace(microsecond=0)
        _, raid_id = self.live_session(now - timedelta(minutes=50), now - timedelta(minutes=40),
                                       last=now - timedelta(minutes=28))  # client closed in the raid
        b = self.builder()
        # the previous check, long before that raid, had also found the game closed
        b._running, b._closed_seen = False, datetime.now().astimezone() - timedelta(hours=2)
        with mock.patch("eftjourney.builder.game_running", return_value=False):
            self.assertIn(raid_id, b.scan().written)
        b._running_checked = 0.0
        with mock.patch("eftjourney.builder.game_running", return_value=True):
            report = b.scan()  # started again, its new session folder not there yet
        self.assertEqual((report.in_raid, report.removed), (None, []))

    def test_screenshots_only_raid_seen_ended_is_not_hidden_again(self):
        now = datetime.now().replace(microsecond=0)
        for minutes_ago, x in ((50, 1), (40, 2)):
            taken = now - timedelta(minutes=minutes_ago)
            path = self.shots / f"{taken:%Y-%m-%d[%H-%M]}_{x}, 1.0, 1.0_0.0, 0.0, 0.0, 1.0_0.00 ({x}).png"
            path.write_bytes(b"png")
            os.utime(path, (taken.timestamp(), taken.timestamp()))
        b = Builder(self.store, MAPS, None, self.shots)
        with mock.patch("eftjourney.builder.game_running", return_value=False):
            raid_id, = b.scan().written
        for running in (True, None):  # the game was started again, no new screenshot yet
            b._running_checked = 0.0
            with mock.patch("eftjourney.builder.game_running", return_value=running):
                report = b.scan()
            self.assertEqual((report.in_raid, report.removed), (None, []), running)
        self.assertEqual(self.store.list_ids(), [raid_id])

    def test_failed_process_check_reuses_a_recent_answer(self):
        b = self.builder()
        for definite in (True, False):
            b._running_checked = 0.0
            with mock.patch("eftjourney.builder.game_running", return_value=definite):
                self.assertIs(b._game_running(), definite)
            b._running_checked = 0.0
            with mock.patch("eftjourney.builder.game_running", return_value=None):
                self.assertIs(b._game_running(), definite)  # tasklist failed once
                b._running_checked = 0.0
                b._definite_at -= builder_mod.RUNNING_KEEP
                self.assertIsNone(b._game_running())  # nothing recent to go by: fail closed

    def journaled_and_one_deleted(self):
        """Two shots in the raid, the first one deleted since: only the journal has it now."""
        first = self.add_shot(49, 0, 100, 50)
        self.add_shot(52, 30, 140, 80)
        self.builder().scan()
        first.unlink()
        self.builder().scan()
        self.assertEqual(self.track(), [(58.2, 100.0, ".png", True), (268.2, 140.0, ".png", False)])
        return self.store.root / "cache" / "shots.json"

    def test_damaged_journal_is_kept_aside(self):
        for damage in (lambda raw: b"\0" * len(raw), lambda raw: raw[: len(raw) // 2], lambda raw: b"[]"):
            with self.subTest(damage=damage):
                journal = self.journaled_and_one_deleted()
                bad = damage(journal.read_bytes())
                journal.write_bytes(bad)
                report = self.builder().scan()
                kept = list(journal.parent.glob("shots.json.bad-*"))
                self.assertEqual([p.read_bytes() for p in kept], [bad])  # never deleted
                self.assertTrue(any(kept[0].name in w for w in report.warnings), report.warnings)
                # the deleted screenshot's position comes back from the stored route; the other
                # one is journaled from its file, which still has the exact moment
                self.assertEqual(len(json.loads(journal.read_text(encoding="utf-8"))["shots"]), 2)
                self.assertTrue(any("위치 1개를 저장된 레이드 경로에서 되살렸습니다" in w for w in report.warnings), report.warnings)
                self.assertEqual(report.written, [])
                self.assertEqual(self.track(), [(58.2, 100.0, ".png", True), (268.2, 140.0, ".png", False)])
                for p in kept + [journal]:
                    p.unlink()

    def test_unreadable_journal_changes_nothing(self):
        journal = self.journaled_and_one_deleted()
        before = journal.read_bytes()
        stale = dict(self.store.load_raid("20261027-224801-bigmap"), id="20261027-100000-woods")
        self.store.save_raid(stale)  # a generated raid the next good scan removes
        self.add_shot(55, 0, 160, 90)
        real_read = Path.read_bytes

        def locked(path):  # another program holds the file without read sharing
            if path.name == "shots.json":
                raise PermissionError(13, "The process cannot access the file")
            return real_read(path)

        b = self.builder()
        with mock.patch.object(Path, "read_bytes", locked):
            report = b.scan()
        self.assertEqual((report.written, report.removed), ([], []))
        self.assertTrue(any("스크린샷 기록을 읽지 못해" in w for w in report.warnings), report.warnings)
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual(len(self.track()), 2)
        report = b.scan()  # let go: read again, nothing was lost
        self.assertEqual((report.written, report.removed), (["20261027-224801-bigmap"], ["20261027-100000-woods"]))
        self.assertEqual([p[1:] for p in self.track()], [(100.0, ".png", True), (140.0, ".png", False), (160.0, ".png", False)])

    def test_journal_of_a_newer_version_is_left_alone(self):
        journal = self.journaled_and_one_deleted()
        doc = json.loads(journal.read_text(encoding="utf-8"))
        doc["version"] = builder_mod.JOURNAL_VERSION + 1
        journal.write_text(json.dumps(doc), encoding="utf-8")
        before = journal.read_bytes()
        self.add_shot(55, 0, 160, 90)
        b = self.builder()
        for _ in range(2):
            report = b.scan()
            self.assertEqual((report.written, report.removed), ([], []))
            self.assertTrue(any("새 버전" in w for w in report.warnings), report.warnings)
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual(list(journal.parent.glob("shots.json.bad-*")), [])
        self.assertEqual(len(self.track()), 2)

    def test_journal_of_an_older_version_is_read(self):
        # moved aside as damaged, it would look like a lost journal after a format change
        journal = self.journaled_and_one_deleted()
        with mock.patch.object(builder_mod, "JOURNAL_VERSION", builder_mod.JOURNAL_VERSION + 1):
            self.add_shot(55, 0, 160, 90)
            report = self.builder().scan()
        self.assertEqual(list(journal.parent.glob("shots.json.bad-*")), [])
        self.assertFalse(any("되살렸습니다" in w for w in report.warnings), report.warnings)
        doc = json.loads(journal.read_text(encoding="utf-8"))
        self.assertEqual((doc["version"], len(doc["shots"])), (builder_mod.JOURNAL_VERSION + 1, 3))
        self.assertEqual([p[1:] for p in self.track()], [(100.0, ".png", True), (140.0, ".png", False), (160.0, ".png", False)])

    def test_lost_journal_is_filled_again_from_the_stored_routes(self):
        # With the screenshots deleted the journal was the only record of their positions:
        # a deleted journal, or an older copy put back, emptied those routes on the next scan
        journal = self.journaled_and_one_deleted()
        before = self.store.load_raid("20261027-224801-bigmap")
        journal.unlink()
        report = self.builder().scan()
        self.assertEqual((report.written, report.removed), ([], []))
        self.assertEqual(self.store.load_raid("20261027-224801-bigmap"), before)
        self.assertTrue(any("위치 1개를 저장된 레이드 경로에서 되살렸습니다" in w for w in report.warnings), report.warnings)
        restored = json.loads(journal.read_text(encoding="utf-8"))["shots"]
        self.assertEqual(len(restored), 2)
        self.assertEqual(sorted(e["taken"] for e in restored.values()),  # the file there gave its own time
                         ["2026-10-27T22:48:59.992", "2026-10-27T22:52:30.000"])

        for p in self.shots.iterdir():
            p.unlink()
        second = next(name for name in restored if "[22-52]" in name)
        older = {"version": 1, "shots": {name: e for name, e in restored.items() if name != second}}
        journal.write_text(json.dumps(older), encoding="utf-8")
        report = self.builder().scan()
        self.assertEqual(self.track(), [(58.2, 100.0, ".png", True), (268.2, 140.0, ".png", True)])
        self.assertTrue(any("위치 1개를" in w for w in report.warnings), report.warnings)
        self.assertIn(second, json.loads(journal.read_text(encoding="utf-8"))["shots"])
        self.assertFalse(any("되살렸습니다" in w for w in self.builder().scan().warnings))  # whole again

    def test_restored_positions_stay_where_they_were(self):
        self.add_shot(47, 55, 90, 40)  # a moment before GameStarted: its t is 0
        self.add_shot(50, 0, 120, 60, mtime=datetime(2026, 10, 28, 9, 0))  # a copy: only the named minute
        self.add_shot(52, 30, 140, 80)
        self.builder().scan()
        before = self.store.load_raid("20261027-224801-bigmap")["track"]
        self.assertEqual([(s["t"], s.get("approx", False)) for s in before], [(0.0, False), (118.2, True), (268.2, False)])
        for p in self.shots.iterdir():
            p.unlink()
        journal = self.store.root / "cache" / "shots.json"
        journal.unlink()
        self.builder().scan()
        self.assertEqual(self.store.load_raid("20261027-224801-bigmap")["track"], [dict(s, gone=True) for s in before])
        entries = json.loads(journal.read_text(encoding="utf-8"))["shots"]
        self.assertEqual(sorted(e["time_source"] for e in entries.values()), ["mtime", "mtime", "name"])

    def test_restore_reads_the_wall_clock_the_raid_was_written_for(self):
        # startedAt carries the offset of when it was written: converted to this PC's zone, a raid
        # written in another one (travel, another PC) was restored hours away from its window
        self.add_shot(49, 0, 100, 50)
        self.builder().scan()
        before = self.store.load_raid("20261027-224801-bigmap")
        journal = self.store.root / "cache" / "shots.json"
        for p in self.shots.iterdir():
            p.unlink()
        for offset in ("+00:00", "-05:00", "+14:00"):  # at least two are not this PC's
            with self.subTest(offset=offset):
                self.store.save_raid(dict(before, startedAt=before["startedAt"][:-6] + offset))
                journal.unlink()
                self.builder().scan()
                self.assertEqual(self.track(), [(58.2, 100.0, ".png", True)])
                self.assertEqual(next(iter(json.loads(journal.read_text(encoding="utf-8"))["shots"].values()))["taken"],
                                 "2026-10-27T22:48:59.992")

    def test_restored_shots_from_before_the_start_keep_their_order(self):
        # both a moment before GameStarted, so both at t 0: restored at the same moment, they
        # were put in name order instead
        self.add_shot(47, 53, 90, 40)
        self.add_shot(47, 58, 70, 40)
        self.builder().scan()
        self.assertEqual([p[:2] for p in self.track()], [(0.0, 90.0), (0.0, 70.0)])
        for p in self.shots.iterdir():
            p.unlink()
        (self.store.root / "cache" / "shots.json").unlink()
        report = self.builder().scan()
        self.assertEqual([p[:2] for p in self.track()], [(0.0, 90.0), (0.0, 70.0)])
        self.assertEqual(report.written, ["20261027-224801-bigmap"])  # only the gone flags changed
        self.assertEqual([p[3] for p in self.track()], [True, True])

    def test_unreadable_entry_of_a_deleted_screenshot_keeps_its_stored_position(self):
        # an entry changed by hand or by another tool while its file was gone: the scan rewrote
        # the raid without that point, so the stored route lost it too
        journal = self.journaled_and_one_deleted()
        doc = json.loads(journal.read_text(encoding="utf-8"))
        first = next(name for name in doc["shots"] if "[22-49]" in name)
        doc["shots"][first]["taken"] = "2026/10/27 22:49:00"  # another tool's time format
        journal.write_text(json.dumps(doc), encoding="utf-8")
        report = self.builder().scan()
        self.assertEqual(report.written, [])
        self.assertEqual(self.track(), [(58.2, 100.0, ".png", True), (268.2, 140.0, ".png", False)])
        self.assertTrue(any("항목 1개는 건너뛰었습니다 (파일에는 그대로 둠, 그중 1개는 저장된 경로의 위치를 대신 씀)" in w
                            for w in report.warnings), report.warnings)
        self.assertEqual(json.loads(journal.read_text(encoding="utf-8")), doc)  # left as it was

    def test_copy_of_a_shot_whose_entry_cannot_be_read_keeps_the_stored_moment(self):
        # Put back from a backup (new file times, so only its named minute) or converted to JPG,
        # the screenshot of an unreadable entry moved to that minute for good.
        original = self.add_shot(49, 30, 100, 50)
        self.builder().scan()
        self.assertEqual(self.track(), [(88.2, 100.0, ".png", False)])
        journal = self.store.root / "cache" / "shots.json"
        doc = json.loads(journal.read_text(encoding="utf-8"))
        doc["shots"][original.name]["taken"] = "2026/10/27 22:49:30"  # another tool's time format
        journal.write_text(json.dumps(doc), encoding="utf-8")
        original.unlink()
        for ext in ("png", "jpg"):
            with self.subTest(ext=ext):
                copy = self.add_shot(49, 30, 100, 50, ext=ext, mtime=datetime(2026, 10, 28, 9, 0))
                self.builder().scan()
                self.assertEqual(self.track(), [(88.2, 100.0, f".{ext}", False)])
                self.assertNotIn("approx", self.store.load_raid("20261027-224801-bigmap")["track"][0])
                copy.unlink()
                self.builder().scan()
                self.assertEqual(self.track(), [(88.2, 100.0, f".{ext}", True)])
        self.assertEqual(json.loads(journal.read_text(encoding="utf-8"))["shots"][original.name], doc["shots"][original.name])

    def test_unreadable_entry_and_a_copy_are_one_point(self):
        # a PNG whose entry cannot be read next to its JPG copy was drawn twice
        original = self.add_shot(49, 30, 100, 50)
        self.builder().scan()
        journal = self.store.root / "cache" / "shots.json"
        doc = json.loads(journal.read_text(encoding="utf-8"))
        doc["shots"][original.name]["x"] = "100.0"
        journal.write_text(json.dumps(doc), encoding="utf-8")
        self.add_shot(49, 30, 100, 50, ext="jpg", mtime=datetime(2026, 10, 28, 9, 0))
        self.builder().scan()
        self.assertEqual(self.track(), [(88.2, 100.0, ".png", False)])

    def test_stored_raid_with_a_wrong_start_gives_back_the_named_minutes(self):
        # A start changed by hand or by another tool made every scan fail (a placeholder date at
        # the edge of the calendar), or restored its points at that date: off every raid window,
        # the route emptied and the journal kept the wrong times for good.
        journal = self.journaled_and_one_deleted()
        good = self.store.load_raid("20261027-224801-bigmap")
        utc = datetime.fromisoformat(good["startedAt"]).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        starts = ("0001-01-01T00:00:00", "9999-12-31T23:59:59.990+09:00", "1970-01-01T00:00:00+09:00",
                  utc)  # the last: the same moment rewritten in UTC by another tool
        for n, started in enumerate(starts, 5):
            sample = dict(good["track"][0], t=58.2 if n % 2 else 0.0, shot=good["track"][0]["shot"].replace("(0)", f"({n})"))
            self.store.save_raid(dict(good, id=f"20261027-10{n:02d}00-woods", startedAt=started, track=[sample]))
        journal.unlink()
        report = self.builder().scan()
        # three come back at the minute their names give (22:49), so in the raid of that minute; the
        # one in UTC at its own moment (its t 0 is the raid's start)
        self.assertEqual(self.track(), [(0.0, 100.0, ".png", True)] + [(58.2, 100.0, ".png", True)] * 4
                         + [(268.2, 140.0, ".png", False)])
        self.assertEqual(sum(1 for s in self.store.load_raid("20261027-224801-bigmap")["track"] if s.get("approx")), 3)
        self.assertTrue(any("위치 5개를 저장된 레이드 경로에서 되살렸습니다 (그중 3개는 레이드 시작 시각이나 지점의 시각이 맞지 않아 "
                            "파일 이름의 분 단위로: 20261027-100700-woods, 20261027-100600-woods, 20261027-100500-woods)" in w
                            for w in report.warnings), report.warnings)
        entries = json.loads(journal.read_text(encoding="utf-8"))["shots"]
        self.assertTrue(all(e["taken"].startswith("2026-10-27T22:") for e in entries.values()), entries)

    def test_restore_takes_the_time_from_a_raid_whose_start_fits(self):
        # Two stored raids held the shot, the newer one with a start that cannot be right: the first
        # one read decided, so the shot came back at its named minute, and that for good.
        journal = self.journaled_and_one_deleted()
        good = self.store.load_raid("20261027-224801-bigmap")
        self.store.save_raid(dict(good, id="20261028-100000-woods", startedAt="0001-01-01T00:00:00"))
        journal.unlink()
        report = self.builder().scan()
        self.assertEqual(self.track(), [(58.2, 100.0, ".png", True), (268.2, 140.0, ".png", False)])
        self.assertFalse(any(s.get("approx") for s in self.store.load_raid("20261027-224801-bigmap")["track"]))
        entries = json.loads(journal.read_text(encoding="utf-8"))["shots"]
        self.assertTrue(all(e["time_source"] != "name" for e in entries.values()), entries)
        # and nothing counted as minute-only for it
        self.assertTrue(any("위치 1개를 저장된 레이드 경로에서 되살렸습니다:" in w for w in report.warnings), report.warnings)

    def test_restore_note_names_the_raids_whose_times_did_not_fit(self):
        # a damaged t, not the start, was blamed on the raid's start, and nothing said which raid
        journal = self.journaled_and_one_deleted()
        raid = self.store.load_raid("20261027-224801-bigmap")
        raid["track"][0]["t"] = "58.2"
        self.store.save_raid(raid)
        journal.unlink()
        report = self.builder().scan()
        self.assertTrue(any("위치 1개를 저장된 레이드 경로에서 되살렸습니다 (그중 1개는 레이드 시작 시각이나 지점의 시각이 맞지 않아 "
                            "파일 이름의 분 단위로: 20261027-224801-bigmap)" in w for w in report.warnings), report.warnings)

    def test_journal_entry_at_a_time_windows_cannot_place(self):
        # a name without a minute to check it against (made by hand) and a placeholder time: without
        # logs every scan failed (OSError), as the entry began a screenshots-only raid then
        self.add_shot(49, 0, 100, 50)
        Builder(self.store, MAPS, None, self.shots).scan()
        journal = self.store.root / "cache" / "shots.json"
        doc = json.loads(journal.read_text(encoding="utf-8"))
        doc["shots"]["hand-made.png"] = dict(next(iter(doc["shots"].values())), taken="0001-01-01T00:00:00")
        journal.write_text(json.dumps(doc), encoding="utf-8")
        report = Builder(self.store, MAPS, None, self.shots).scan()
        self.assertTrue(any("읽을 수 없는 항목 1개는 건너뛰었습니다" in w for w in report.warnings), report.warnings)
        self.assertEqual(self.store.list_ids(), ["20261027-224900-unknown"])

    def test_restored_minute_leaves_a_file_still_there_to_its_own_time(self):
        # a start that cannot be right: the file still in the folder has the moment it was taken
        self.add_shot(49, 0, 100, 50)
        second = self.add_shot(52, 30, 140, 80)
        self.builder().scan()
        doc = self.store.load_raid("20261027-224801-bigmap")
        self.store.save_raid(dict(doc, startedAt="0001-01-01T00:00:00"))
        for p in self.shots.iterdir():
            if p != second:
                p.unlink()
        (self.store.root / "cache" / "shots.json").unlink()
        self.builder().scan()
        self.assertEqual(self.track(), [(58.2, 100.0, ".png", True), (268.2, 140.0, ".png", False)])
        entries = json.loads((self.store.root / "cache" / "shots.json").read_text(encoding="utf-8"))["shots"]
        self.assertEqual((entries[second.name]["taken"], entries[second.name]["time_source"]), ("2026-10-27T22:52:30.000", "mtime"))

    def test_restore_notes_wait_for_a_scan_that_gets_to_the_end(self):
        # the restoring scan failed later (the journal held by another program): its warning was
        # lost, and the next scan of that run said nothing
        journal = self.journaled_and_one_deleted()
        journal.unlink()
        b = self.builder()
        real_replace = os.replace

        def replace(src, dst):
            if Path(dst).name == "shots.json":
                raise PermissionError(13, "held by another program")
            real_replace(src, dst)

        with mock.patch.object(builder_mod.os, "replace", replace), mock.patch("eftjourney.store.time.sleep"), \
                self.assertRaises(PermissionError):
            b.scan()
        report = b.scan()
        self.assertTrue(any("위치 1개를 저장된 레이드 경로에서 되살렸습니다" in w for w in report.warnings), report.warnings)
        self.assertFalse(any("되살렸습니다" in w for w in b.scan().warnings))  # told once

    def test_future_group_does_not_hide_the_raid_being_played(self):
        # Without logs, only the newest screenshot group was checked for being played: one dated
        # in the future (a wrong clock when it was taken) always looked "in progress", so the
        # group really being played was published while the raid went on.
        now = datetime.now().replace(microsecond=0)

        def add(taken, x):
            path = self.shots / f"{taken:%Y-%m-%d[%H-%M]}_{x}, 1.0, 1.0_0.0, 0.0, 0.0, 1.0_0.00 ({x}).png"
            path.write_bytes(b"png")
            os.utime(path, (taken.timestamp(), taken.timestamp()))

        add(now + timedelta(days=400), 1)
        add(now - timedelta(minutes=3), 2)
        add(now - timedelta(minutes=1), 3)
        with mock.patch("eftjourney.builder.game_running", return_value=True):
            report = Builder(self.store, MAPS, None, self.shots).scan()
        self.assertIsNotNone(report.in_raid)
        self.assertEqual(report.in_raid["since"][:10], f"{now - timedelta(minutes=3):%Y-%m-%d}")
        self.assertEqual(len(self.store.list_ids()), 1)  # only the future one: the played one waits

    def test_raid_dated_ahead_after_the_clock_was_set_back_stays_hidden(self):
        # Without logs, groups dated later than now were left out when looking for the raid being
        # played: one whose clock was set back during it (5 minutes fast until a minute ago) was published.
        now = datetime.now().replace(microsecond=0)
        for minutes, x in ((4, 1), (4.5, 2)):
            taken = now + timedelta(minutes=minutes)
            path = self.shots / f"{taken:%Y-%m-%d[%H-%M]}_{x}, 1.0, 1.0_0.0, 0.0, 0.0, 1.0_0.00 ({x}).png"
            path.write_bytes(b"png")
            os.utime(path, (taken.timestamp(), taken.timestamp()))
        with mock.patch("eftjourney.builder.game_running", return_value=True):
            report = Builder(self.store, MAPS, None, self.shots).scan()
        self.assertIsNotNone(report.in_raid)
        self.assertEqual(self.store.list_ids(), [])

    def test_restore_that_failed_is_tried_again(self):
        journal = self.journaled_and_one_deleted()
        journal.unlink()
        b = self.builder()
        with mock.patch.object(b, "_stored_shots", side_effect=OSError(5, "drive not ready")), \
                self.assertRaises(OSError):
            b.scan()
        self.assertEqual(self.track(), [(58.2, 100.0, ".png", True), (268.2, 140.0, ".png", False)])  # nothing written
        report = b.scan()  # the same run: the restore is tried again before anything is written
        self.assertTrue(any("위치 1개를 저장된 레이드 경로에서 되살렸습니다" in w for w in report.warnings), report.warnings)
        self.assertEqual(self.track(), [(58.2, 100.0, ".png", True), (268.2, 140.0, ".png", False)])

    def test_report_tells_positions_from_files(self):
        # with the screenshots deleted, the scan said "93 screenshots" while the folder had none
        self.journaled_and_one_deleted()
        report = self.builder().scan()
        self.assertEqual((report.samples, report.files), (2, 1))
        self.shots.rename(self.shots.with_name("ScreenshotsAway"))
        report = self.builder().scan()
        self.assertEqual((report.samples, report.files), (2, None))

    def test_restore_leaves_entries_that_are_there_alone(self):
        self.add_shot(49, 0, 100, 50)
        self.builder().scan()
        journal = self.store.root / "cache" / "shots.json"
        doc = json.loads(journal.read_text(encoding="utf-8"))
        (name,) = doc["shots"]
        doc["shots"][name]["x"] = "100.0"  # unreadable now, while the file is still there
        journal.write_text(json.dumps(doc), encoding="utf-8")
        converted = dict(self.store.load_raid("20261027-224801-bigmap"), id="20261027-100000-woods")
        converted["track"] = [dict(s, shot=os.path.splitext(s["shot"])[0] + ".jpg") for s in converted["track"]]
        self.store.save_raid(converted)  # the same shot under another extension
        report = self.builder().scan()
        self.assertFalse(any("되살렸습니다" in w for w in report.warnings), report.warnings)
        self.assertEqual(json.loads(journal.read_text(encoding="utf-8"))["shots"], doc["shots"])

    def test_odd_stored_raids_do_not_stop_the_scan(self):
        # a raid file that is not an object, or one with a track or source of another shape
        # (edited by hand, damaged), made every scan fail
        self.add_shot(49, 0, 100, 50)
        self.builder().scan()
        good = self.store.load_raid("20261027-224801-bigmap")
        shot_name = good["track"][0]["shot"]
        odd = {
            "20261027-100000-woods": [1, 2],
            "20261027-100100-woods": "text",
            "20261027-100200-woods": dict(good, id="20261027-100200-woods", track={"shot": shot_name}, source="log"),
            "20261027-100300-woods": dict(good, id="20261027-100300-woods", source={"logSession": ["x"]}, track=[
                1, None, {"shot": 5, "gone": True}, {"t": "1", "shot": shot_name.replace("(0)", "(9)")},
                {"t": float("inf"), "shot": shot_name.replace("(0)", "(8)")}]),
        }
        for raid_id, doc in odd.items():
            (self.store.raids_dir / f"{raid_id}.json").write_text(json.dumps(doc), encoding="utf-8")
        (self.store.root / "cache" / "shots.json").unlink()  # the restore reads every stored raid
        self.shots.rename(self.shots.with_name("ScreenshotsAway"))  # no listing: the stored raids tell what is gone
        report = self.builder().scan()
        # the samples with a damaged t: their names still give the minute (22:49), so that raid has them
        self.assertEqual(self.track(), [(58.2, 100.0, ".png", False)] * 3)
        self.assertEqual((report.written, report.removed),
                         (["20261027-224801-bigmap"], ["20261027-100300-woods", "20261027-100200-woods"]))
        self.assertEqual(set(self.store.list_ids()), {"20261027-224801-bigmap", "20261027-100000-woods", "20261027-100100-woods"})
        entries = json.loads((self.store.root / "cache" / "shots.json").read_text(encoding="utf-8"))["shots"]
        self.assertEqual(sorted(entries), sorted(shot_name.replace("(0)", f"({n})") for n in (0, 8, 9)))

    def test_log_cache_that_cannot_be_written_leaves_no_temp_file(self):
        # The log cache had its own write: a failed rename left a tmp*.tmp behind on every scan.
        # And as an error it hid the raids that scan had written: the next one found nothing new.
        self.add_shot(49, 0, 100, 50)
        real_replace = os.replace

        def replace(src, dst):
            if Path(dst).name in held:
                raise PermissionError(13, "held by another program")
            real_replace(src, dst)

        b = self.builder()
        held = {"logs.json"}
        with mock.patch.object(builder_mod.os, "replace", replace), mock.patch("eftjourney.store.time.sleep"):
            report = b.scan()
            self.assertEqual(report.written, ["20261027-224801-bigmap"])
            self.assertTrue(any("로그 기록을 쓰지 못했습니다" in w for w in report.warnings), report.warnings)
            cache = self.store.root / "cache"
            self.assertEqual([p.name for p in cache.iterdir()], ["shots.json"])  # no temp file left
            again = b.scan()  # still held: the same warning, word for word (the watcher prints it once)
            self.assertEqual([w for w in again.warnings if "로그 기록" in w], [w for w in report.warnings if "로그 기록" in w])
            held = {"20261027-224801-bigmap.json"}  # a raid that cannot be written is a warning too
            self.add_shot(52, 30, 140, 80)
            report = b.scan()
            self.assertEqual(report.written, [])
            self.assertTrue(any("레이드를 저장하지 못했습니다 (다음 스캔에서 다시 시도): 20261027-224801-bigmap" in w
                                for w in report.warnings), report.warnings)
        report = b.scan()
        self.assertEqual((report.written, [w for w in report.warnings if "저장하지" in w or "쓰지" in w]),
                         (["20261027-224801-bigmap"], []))
        self.assertEqual(sorted(p.name for p in cache.iterdir()), ["logs.json", "shots.json"])
        self.assertEqual(len(self.track()), 2)

    def test_unreadable_journal_entries_are_skipped(self):
        self.add_shot(49, 0, 100, 50)
        self.builder().scan()
        journal = self.store.root / "cache" / "shots.json"
        doc = json.loads(journal.read_text(encoding="utf-8"))
        (entry,) = doc["shots"].values()
        doc["shots"].update({  # edited by hand or written by another tool
            "x-text.png": {**entry, "x": "120.0"},
            "tod-text.png": {**entry, "tod": "12.5"},
            "offset.png": {**entry, "taken": "2026-10-27T13:50:00.000+00:00", "x": 110.0},
        })
        journal.write_text(json.dumps(doc), encoding="utf-8")
        self.add_shot(55, 0, 160, 90)
        report = self.builder().scan()
        self.assertTrue(any("항목 2개" in w for w in report.warnings), report.warnings)
        local = datetime(2026, 10, 27, 13, 50, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        expected = sorted([(datetime(2026, 10, 27, 22, 49), 100.0), (local, 110.0), (datetime(2026, 10, 27, 22, 55), 160.0)])
        in_raid = [x for when, x in expected if datetime(2026, 10, 27, 22, 47, 51) <= when <= datetime(2026, 10, 27, 22, 58, 6)]
        self.assertEqual([p[1] for p in self.track()], in_raid)
        self.assertIn("x-text.png", json.loads(journal.read_text(encoding="utf-8"))["shots"])  # left as it was

    def test_converted_screenshots_stay_one_point(self):
        # the copy's file times are not kept, so alone it would only know the named minute (58.2)
        for first, second in (("png", "jpg"), ("jpg", "PNG")):
            with self.subTest(first=first, second=second):
                original = self.add_shot(49, 20, 100, 50, ext=first)
                self.builder().scan()
                copy = self.add_shot(49, 0, 100, 50, ext=second, mtime=datetime(2026, 10, 28, 9, 0))
                self.builder().scan()  # both files there: one point, under the journaled name
                self.assertEqual(self.track(), [(78.2, 100.0, original.suffix, False)])
                original.unlink()
                self.builder().scan()  # first sighting kept, now under the file that exists
                self.assertEqual(self.track(), [(78.2, 100.0, copy.suffix, False)])
                journal = self.store.root / "cache" / "shots.json"
                self.assertEqual(list(json.loads(journal.read_text(encoding="utf-8"))["shots"]), [copy.name])
                copy.unlink()
                journal.unlink()

    def test_unavailable_screenshots_folder_changes_nothing(self):
        self.journaled_and_one_deleted()
        before = self.store.load_raid("20261027-224801-bigmap")
        b = self.builder()
        b.scan()
        away = self.shots.with_name("ScreenshotsAway")
        self.shots.rename(away)  # renamed, or the drive / sync folder is offline
        report = b.scan()
        self.assertEqual(report.written, [])
        self.assertTrue(any("스크린샷 폴더를 읽을 수 없어" in w for w in report.warnings), report.warnings)
        report = self.builder().scan()  # started while the folder is away
        self.assertEqual(report.written, [])
        self.assertEqual(self.store.load_raid("20261027-224801-bigmap"), before)
        away.rename(self.shots)
        self.assertEqual(b.scan().written, [])

    def test_files_reach_the_disk_before_they_replace_the_old_ones(self):
        # a power cut right after the rename could leave a zero-filled cache, journal or raid
        self.add_shot(49, 0, 100, 50)
        events = []
        real_replace = os.replace

        def replace(src, dst):
            events.append(Path(dst).name)
            real_replace(src, dst)

        with mock.patch.object(builder_mod.os, "fsync", side_effect=lambda fd: events.append("fsync")), \
                mock.patch.object(builder_mod.os, "replace", replace):
            self.builder().scan()
        self.assertEqual(sorted(e for e in events if e != "fsync"), ["20261027-224801-bigmap.json", "logs.json", "shots.json"])
        self.assertTrue(all(events[i - 1] == "fsync" for i, e in enumerate(events) if e != "fsync"), events)


if __name__ == "__main__":
    unittest.main()
