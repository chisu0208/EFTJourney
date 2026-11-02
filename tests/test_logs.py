"""Log segmentation tests.

tests/fixtures/logs holds four real sessions with every id, address and token replaced
by placeholders (see the research notes). The expected values were cross-checked
against an independent implementation that also read the huge output_*.log files.
"""
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from eftjourney import logs
from eftjourney.logs import LogRaid, LogScanner, normalize_mode, raids_from_session, scene_location

FIXTURES = Path(__file__).parent / "fixtures" / "logs"
SESSION_A = FIXTURES / "log_2026.10.27_22-32-44_1.2.5.0.48146"  # cancelled online search, then a local Customs raid
SESSION_B = FIXTURES / "log_2026.10.18_14-27-00_1.2.5.0.48146"  # local transit Factory -> Labs, client closed in Labs
SESSION_C = FIXTURES / "log_2026.08.12_20-40-54_1.1.0.1.46699"  # PvE local raid, PvpSeason online raid, cancelled search
SESSION_D = FIXTURES / "log_2026.10.22_21-19-37_1.2.5.0.48146"  # online raids, online transit, insurance message, positions


KST = timezone(timedelta(hours=9))  # the zone the fixtures were recorded in


def t(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f")


@mock.patch.object(logs, "LOCAL_TZ", KST)
class FixtureSessionsTest(unittest.TestCase):
    def test_local_raid_after_cancelled_search(self):
        (raid,) = raids_from_session(SESSION_A)
        self.assertEqual(raid.map_id, "bigmap")
        self.assertEqual((raid.mode, raid.kind, raid.end_reason, raid.map_source), ("pve", "local", "raid-end", "transit"))
        self.assertEqual(raid.raid_id, "6ae0ab5e00660118a0035ff8")
        self.assertEqual(raid.start, t("2026-10-27 22:48:01.792"))
        self.assertEqual(raid.end, t("2026-10-27 22:57:46.599"))  # request /client/match/local/end
        self.assertFalse(raid.exit_via_transit or raid.transit_from_prev or raid.insurance_lost)
        self.assertEqual(raid.fixes, [])

    def test_local_transit_chain_then_client_closed(self):
        factory, labs = raids_from_session(SESSION_B)
        self.assertEqual((factory.map_id, factory.end_reason), ("factory4_day", "raid-end"))
        self.assertTrue(factory.exit_via_transit)
        self.assertEqual((labs.map_id, labs.end_reason), ("laboratory", "open"))  # no end marker: client closed in raid
        self.assertTrue(labs.transit_from_prev)
        self.assertEqual(factory.raid_id, labs.raid_id)  # one RaidId per transit chain
        self.assertEqual(labs.end, t("2026-10-18 14:43:31.460"))  # last log line of the session

    def test_pve_local_and_pvpseason_online(self):
        local, online = raids_from_session(SESSION_C)
        self.assertEqual((local.map_id, local.mode, local.kind, local.end_reason), ("sandbox_high", "pve", "local", "raid-end"))
        self.assertEqual((online.map_id, online.mode, online.kind, online.end_reason), ("sandbox", "pvp", "online", "online-end"))
        self.assertIsNone(online.raid_id)
        self.assertEqual(online.map_source, "confirmed")
        self.assertEqual(online.end, t("2026-08-12 21:33:28.587"))

    def test_online_raids_transit_insurance_positions(self):
        first, customs, second = raids_from_session(SESSION_D)
        self.assertEqual([r.map_id for r in (first, customs, second)], ["shoreline", "bigmap", "shoreline"])
        self.assertTrue(all(r.kind == "online" and r.end_reason == "online-end" for r in (first, customs, second)))
        self.assertTrue(first.insurance_lost)
        self.assertFalse(customs.insurance_lost or second.insurance_lost)
        self.assertTrue(customs.exit_via_transit)
        self.assertTrue(second.transit_from_prev)
        self.assertEqual([len(r.fixes) for r in (first, customs, second)], [2, 1, 10])
        fix = customs.fixes[0]
        self.assertEqual((fix["x"], fix["y"], fix["z"], fix["reason"]), (-148.302, 1.608, -143.646, "PacketsQueue"))
        self.assertEqual(customs.start, t("2026-10-22 22:04:30.088"))
        self.assertEqual(customs.end, t("2026-10-22 22:13:23.272"))  # GC switch on the way back to the menu

    def test_nothing_personal_is_kept(self):
        dump = json.dumps([r.to_json() for r in LogScanner(FIXTURES).scan()])
        for marker in ("aaaaaaaaaaaaaaaaaaaa", "9999001", "203.0.113", "Sid", "ProfileId", "AccountId"):
            self.assertNotIn(marker, dump)

    def test_next_leg_announced_before_the_end_marker(self):
        # Online transit: the next leg's UserConfirmed (push log) can be stamped a
        # millisecond before the running leg's end marker (application log).
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / SESSION_D.name
            shutil.copytree(SESSION_D, session)
            push = next(session.glob("* push-notifications_000.log"))
            with push.open("a", encoding="utf-8") as fh:
                fh.write("2026-10-22 22:13:23.271|1.2.5.0.48146|Info|push-notifications|Got notification | UserConfirmed\n"
                         '{\n  "type": "userConfirmed",\n  "location": "Shoreline"\n}\n')
            first, customs, second = raids_from_session(session)
        self.assertEqual((customs.map_id, customs.map_source), ("bigmap", "confirmed"))
        self.assertEqual(customs.end, t("2026-10-22 22:13:23.272"))
        self.assertEqual((second.map_id, second.map_source), ("shoreline", "confirmed"))
        self.assertTrue(customs.exit_via_transit and second.transit_from_prev)


class HelpersTest(unittest.TestCase):
    def test_scene_location(self):
        self.assertEqual(scene_location("bigmap.scenespreset.asset"), "bigmap")
        self.assertEqual(scene_location("Shopping_Mall.ScenesPreset.asset"), "Interchange")
        self.assertEqual(scene_location("city.scenespreset.asset"), "TarkovStreets")
        self.assertEqual(scene_location("Sandbox_SL.ScenesPreset.asset"), "Sandbox_start")
        self.assertEqual(scene_location("NewMap.ScenesPreset.asset"), "newmap")  # unknown maps keep their key
        self.assertEqual(scene_location("bigmap.scenepreset.asset"), "bigmap")  # suffix variants
        self.assertEqual(scene_location("Terminal-Port.asset"), "terminal-port")  # the raid id sanitizes the rest
        self.assertIsNone(scene_location(None))

    def test_modes(self):
        self.assertEqual(normalize_mode("Pve"), "pve")
        self.assertEqual(normalize_mode("PvpSeason"), "pvp")
        self.assertEqual(normalize_mode("Regular"), "pvp")
        self.assertEqual(normalize_mode(None), "unknown")

    def test_raid_json_roundtrip(self):
        (raid,) = raids_from_session(SESSION_A)
        self.assertEqual(LogRaid.from_json(json.loads(json.dumps(raid.to_json()))), raid)

    def test_raid_json_of_another_shape_is_refused(self):
        # a hand-edited cache: an aware start would break sorting, a number for a map id the builder
        doc = raids_from_session(SESSION_D)[1].to_json()  # Customs, with one server position
        fix = doc["fixes"][0]
        for bad in ({"start": doc["start"] + "+09:00"}, {"end": doc["end"] + "Z"}, {"map_id": 5}, {"session": ["x"]},
                    {"exit_via_transit": "no"}, {"fixes": {"time": doc["start"]}}, {"fixes": [{**fix, "x": "1"}]},
                    {"fixes": [{**fix, "time": fix["time"] + "+03:00"}]}, {"fixes": [{**fix, "reason": None}]},
                    {"start": "0001-01-01T00:00:00"}, {"end": "9999-12-31T23:59:59"}):
            with self.subTest(bad), self.assertRaises((KeyError, TypeError, ValueError)):
                LogRaid.from_json({**doc, **bad})

    def test_folder_name_that_is_not_a_date(self):
        self.assertEqual(logs.session_info(Path("log_2026.13.45_25-61-61_1.2.5.0.48146")), (None, "1.2.5.0.48146"))


@mock.patch.object(logs, "LOCAL_TZ", KST)
class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name) / "Logs"
        shutil.copytree(FIXTURES, self.logs)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_all_sessions_sorted(self):
        raids = LogScanner(self.logs).scan()
        self.assertEqual(len(raids), 8)
        self.assertEqual(raids, sorted(raids, key=lambda r: r.start))

    def test_cache_reused_until_files_change(self):
        first_scanner = LogScanner(self.logs)
        first = first_scanner.scan()
        cache = json.loads(json.dumps(first_scanner.cache))  # persisted and reloaded
        scanner = LogScanner(self.logs, cache)
        self.assertEqual(scanner.cache["sessions"].keys(), {p.name for p in self.logs.iterdir()})
        self.assertEqual(scanner.scan(), first)

        # appending a raid to one session invalidates only that session
        app = next(SESSION_A.glob("* application_000.log")).name
        with (self.logs / SESSION_A.name / app).open("a", encoding="utf-8") as fh:
            fh.write("2026-10-27 23:30:00.000|1.2.5.0.48146|Info|application|scene preset path:maps/woods_preset.bundle rcid:woods.scenespreset.asset\n")
            fh.write("2026-10-27 23:31:00.000|1.2.5.0.48146|Info|application|GameStarted:1(0) real:1(0) diff:0\n")
        again = LogScanner(self.logs, cache).scan()
        self.assertEqual(len(again), 9)
        self.assertEqual((again[-1].map_id, again[-1].end_reason, again[-1].map_source), ("woods", "open", "scene"))

    def test_old_cache_format_is_discarded(self):
        scanner = LogScanner(self.logs, {"log_x": {"sig": [], "raids": []}})
        self.assertEqual(scanner.cache["sessions"], {})
        self.assertEqual(len(scanner.scan()), 8)

    def test_removed_sessions_keep_their_raids(self):
        # old session folders deleted to free space: the cache is the only record left
        scanner = LogScanner(self.logs)
        scanner.scan()
        shutil.rmtree(self.logs / SESSION_C.name)
        self.assertEqual(len(scanner.scan()), 8)
        self.assertIn(SESSION_C.name, scanner.cache["sessions"])
        self.assertNotIn(SESSION_C.name, scanner.visible)
        shutil.rmtree(self.logs)
        self.assertEqual(len(scanner.scan()), 8)
        self.assertEqual(scanner.visible, [])

    def test_unreadable_session_is_retried(self):
        real = logs.raids_from_session

        def locked(session_dir, *args):
            if Path(session_dir).name == SESSION_D.name:
                raise PermissionError("locked by another program")
            return real(session_dir, *args)

        scanner = LogScanner(self.logs)
        with mock.patch.object(logs, "raids_from_session", side_effect=locked):
            self.assertEqual(len(scanner.scan()), 5)
        self.assertEqual(scanner.failed, {SESSION_D.name})
        self.assertNotIn(SESSION_D.name, scanner.cache["sessions"])  # nothing partial was cached
        self.assertEqual(len(scanner.scan()), 8)
        self.assertEqual(scanner.failed, set())

    def test_missing_folder(self):
        self.assertEqual(LogScanner(self.logs / "nope").scan(), [])
        self.assertEqual(LogScanner(None).scan(), [])

    # ---------------------------------------------------- folders changing under the scan
    def entry(self, scanner, session):
        return json.loads(json.dumps(scanner.cache["sessions"][session.name]))

    def after_reading(self, session, change):
        """Runs change() once the last file of the session (its push log) was read."""
        read_push = logs.parse_push

        def read_then_change(path, events):
            last = read_push(path, events)
            if Path(path).parent == session:
                change()
            return last

        return mock.patch.object(logs, "parse_push", side_effect=read_then_change)

    def test_folder_being_deleted_keeps_its_raids(self):
        # Deleting a folder takes a while and goes in name order (application log first): a
        # scan in between must not cache what is left as a session without raids.
        scanner = LogScanner(self.logs)
        scanner.scan()
        session = self.logs / SESSION_C.name
        before = self.entry(scanner, SESSION_C)
        for kind in ("application", "backend"):
            next(session.glob(f"* {kind}_000.log")).unlink()
            self.assertEqual(len(scanner.scan()), 8)
            self.assertEqual(scanner.failed, set())  # freeing space is nothing to warn about
            self.assertEqual(self.entry(scanner, SESSION_C), before)
        shutil.rmtree(session)
        self.assertEqual(len(scanner.scan()), 8)
        self.assertEqual(self.entry(scanner, SESSION_C), before)

    def test_files_gone_or_shorter_keep_the_cached_raids(self):
        # logs only grow: a cached file that is gone or shorter now was deleted, not rewritten
        scanner = LogScanner(self.logs)
        scanner.scan()
        session = self.logs / SESSION_D.name
        before = self.entry(scanner, SESSION_D)
        backend = next(session.glob("* backend_000.log"))
        backend.write_bytes(backend.read_bytes()[:1000])
        self.assertEqual(len(scanner.scan()), 8)
        self.assertEqual(self.entry(scanner, SESSION_D), before)
        next(session.glob("* push-notifications_000.log")).unlink()  # it holds the insurance message
        raids = scanner.scan()
        self.assertEqual(scanner.failed, set())
        self.assertEqual(self.entry(scanner, SESSION_D), before)
        self.assertTrue(next(r for r in raids if r.session == SESSION_D.name).insurance_lost)

    def test_folder_gone_right_after_the_listing_keeps_its_raids(self):
        scanner = LogScanner(self.logs)
        scanner.scan()
        listed = LogScanner.sessions

        def listing_then_gone(this):
            dirs = listed(this)
            shutil.rmtree(self.logs / SESSION_C.name, ignore_errors=True)  # or moved to the Recycle Bin
            return dirs

        with mock.patch.object(LogScanner, "sessions", listing_then_gone):
            self.assertEqual(len(scanner.scan()), 8)
        self.assertIn(SESSION_C.name, scanner.visible)
        self.assertEqual(scanner.failed, set())
        self.assertEqual(len(scanner.cache["sessions"][SESSION_C.name]["raids"]), 2)

    def test_folder_without_application_log_waits_for_it(self):
        # the game creates the folder (and other logs) before the application log
        stamp = "2026.10.29_20-00-00_1.3.0.0.50000"
        session = self.logs / f"log_{stamp}"
        session.mkdir()
        (session / f"{stamp} backend_000.log").write_text("", encoding="utf-8")
        scanner = LogScanner(self.logs)
        self.assertEqual(len(scanner.scan()), 8)
        self.assertNotIn(session.name, scanner.cache["sessions"])
        self.assertEqual(scanner.failed, set())
        (session / f"{stamp} application_000.log").write_text(
            "2026-10-29 20:05:00.000|1.3.0.0.50000|Info|application|scene preset path:maps/woods_preset.bundle rcid:woods.scenespreset.asset\n"
            "2026-10-29 20:06:00.000|1.3.0.0.50000|Info|application|GameStarted:1(0) real:1(0) diff:0\n", encoding="utf-8")
        self.assertEqual(len(scanner.scan()), 9)
        self.assertIn(session.name, scanner.cache["sessions"])

    def test_file_gone_between_measuring_and_reading(self):
        # exactly the measured files are read: one deleted in between fails the session instead
        # of caching it without that file (here: without its insurance message)
        session = self.logs / SESSION_D.name
        measure = logs.session_signature

        def measure_then_delete(session_dir):
            sig = measure(session_dir)
            if Path(session_dir) == session:
                for p in session.glob("* push-notifications_000.log"):
                    p.unlink()
            return sig

        scanner = LogScanner(self.logs)
        with mock.patch.object(logs, "session_signature", side_effect=measure_then_delete):
            self.assertEqual(len(scanner.scan()), 5)
        self.assertEqual(scanner.failed, {SESSION_D.name})
        self.assertNotIn(SESSION_D.name, scanner.cache["sessions"])

    def test_files_changing_while_they_are_read(self):
        session = self.logs / SESSION_D.name
        app = next(session.glob("* application_000.log"))

        def grow():
            with app.open("a", encoding="utf-8") as fh:
                fh.write("2026-10-22 23:49:00.000|1.2.5.0.48146|Info|application|scene preset path:maps/woods_preset.bundle rcid:woods.scenespreset.asset\n"
                         "2026-10-22 23:50:00.000|1.2.5.0.48146|Info|application|GameStarted:1(0) real:1(0) diff:0\n")

        # the session being played grows while it is read: the parse counts, stored with the
        # signature taken before it, so the next scan reads the rest
        scanner = LogScanner(self.logs)
        measured = logs.session_signature(session)
        with self.after_reading(session, grow):
            self.assertEqual(len(scanner.scan()), 8)
        self.assertEqual(scanner.failed, set())
        self.assertEqual(scanner.cache["sessions"][SESSION_D.name]["sig"], measured)
        self.assertEqual(len(scanner.scan()), 9)  # the raid appended meanwhile

        # files shorter or gone after they were read (deleted, the drive dropped out): nothing
        # is stored, the cached raids stay and the session is retried
        before = self.entry(scanner, SESSION_D)
        with app.open("a", encoding="utf-8") as fh:
            fh.write("2026-10-22 23:59:00.000|1.2.5.0.48146|Info|application|GC mode switched to Enabled\n")
        with self.after_reading(session, lambda: app.write_bytes(app.read_bytes()[:1000])):
            self.assertEqual(len(scanner.scan()), 9)
        self.assertEqual(scanner.failed, {SESSION_D.name})
        self.assertEqual(self.entry(scanner, SESSION_D), before)

    def test_parser_change_keeps_the_old_signature(self):
        scanner = LogScanner(self.logs)
        scanner.scan()
        old = json.loads(json.dumps(scanner.cache))
        old["version"] = logs.CACHE_VERSION - 1  # written by the previous parser
        carried = LogScanner(self.logs, old)
        for session in (SESSION_C, SESSION_D):
            entry = carried.cache["sessions"][session.name]
            self.assertEqual((entry["sig"], entry["prev"]), (None, old["sessions"][session.name]["sig"]))
        c_before, d_before = self.entry(carried, SESSION_C), self.entry(carried, SESSION_D)

        # folders caught half-deleted on the first scan after the update keep their raids ...
        push = next((self.logs / SESSION_D.name).glob("* push-notifications_000.log"))
        push.unlink()
        next((self.logs / SESSION_C.name).glob("* application_000.log")).unlink()
        raids = carried.scan()
        self.assertEqual((len(raids), carried.failed), (8, set()))
        self.assertTrue(next(r for r in raids if r.session == SESSION_D.name).insurance_lost)
        self.assertEqual((self.entry(carried, SESSION_C), self.entry(carried, SESSION_D)), (c_before, d_before))
        self.assertIsNotNone(carried.cache["sessions"][SESSION_A.name]["sig"])  # the others were parsed again

        # ... and their re-parse stays due across restarts until it succeeds
        reloaded = LogScanner(self.logs, json.loads(json.dumps(carried.cache)))
        self.assertEqual(self.entry(reloaded, SESSION_D), d_before)
        shutil.copy2(SESSION_D / push.name, push)  # restored from the Recycle Bin
        self.assertEqual(len(reloaded.scan()), 8)
        self.assertEqual(self.entry(reloaded, SESSION_D), {"sig": logs.session_signature(push.parent),
                                                           "raids": d_before["raids"]})

    # --------------------------------------------------------------- odd log contents
    def test_payload_fields_of_another_type_count_as_missing(self):
        # UserConfirmed.location and the insurance message fields come from JSON bodies
        session = self.logs / SESSION_D.name
        with next(session.glob("* push-notifications_000.log")).open("a", encoding="utf-8") as fh:
            fh.write("2026-10-22 23:40:00.000|1.2.5.0.48146|Info|push-notifications|Got notification | UserConfirmed\n"
                     '{"location": 5}\n'
                     "2026-10-22 23:52:00.000|1.2.5.0.48146|Info|push-notifications|Got notification | ChatMessageReceived\n"
                     '{"message": {"type": 2, "systemData": {"location": ["Woods"], "date": 22, "time": null}}}\n')
        with next(session.glob("* application_000.log")).open("a", encoding="utf-8") as fh:
            fh.write("2026-10-22 23:41:00.000|1.2.5.0.48146|Info|application|GameStarted:1(0) real:1(0) diff:0\n"
                     "2026-10-22 23:51:00.000|1.2.5.0.48146|Info|application|GC mode switched to Enabled\n")
        scanner = LogScanner(self.logs)
        raids = scanner.scan()
        self.assertEqual((len(raids), scanner.failed), (9, set()))
        late = next(r for r in raids if r.start == t("2026-10-22 23:41:00.000"))
        self.assertEqual((late.map_id, late.kind, late.end_reason, late.insurance_lost), (None, "online", "online-end", False))

    def test_one_odd_session_does_not_stop_every_scan(self):
        real = logs.parse_application
        for error in (ValueError, TypeError, AttributeError, KeyError, IndexError):
            def odd(path, events):
                if Path(path).parent.name == SESSION_B.name:
                    raise error("a log this parser does not understand")
                return real(path, events)

            with self.subTest(error.__name__):
                scanner = LogScanner(self.logs)
                with mock.patch.object(logs, "parse_application", side_effect=odd):
                    self.assertEqual(len(scanner.scan()), 6)
                self.assertEqual(scanner.failed, {SESSION_B.name})
                self.assertNotIn(SESSION_B.name, scanner.cache["sessions"])
                self.assertEqual(len(scanner.scan()), 8)
                self.assertEqual(scanner.failed, set())

    def test_folder_not_named_after_a_date(self):
        odd = self.logs / "log_2026.13.45_25-61-61_1.2.5.0.48146"
        shutil.copytree(SESSION_A, odd)
        scanner = LogScanner(self.logs)
        self.assertEqual(len(scanner.scan()), 9)
        self.assertEqual(scanner.visible[0], odd.name)  # sorted first


class CacheShapeTest(unittest.TestCase):
    """data/cache/logs.json may hold anything JSON can: an older version, or hand edits."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name) / "Logs"
        shutil.copytree(FIXTURES, self.logs)

    def tearDown(self):
        self.tmp.cleanup()

    def test_any_json_is_accepted(self):
        gone = "log_2025.12.31_20-00-00_1.0.0.0.40000"  # a session folder that is not on disk
        kept = {**raids_from_session(SESSION_A)[0].to_json(), "session": gone}
        aware = {**kept, "start": "2026-10-27T13:48:01.792+00:00"}
        cur, older, a = logs.CACHE_VERSION, logs.CACHE_VERSION - 1, SESSION_A.name
        cases = {  # case: (cache, raids scan() returns)
            "null": (None, 8), "list": ([], 8), "text": ("x", 8), "number": (5, 8), "true": (True, 8),
            "no sessions": ({"version": cur}, 8),
            "older, sessions list": ({"version": older, "sessions": [[gone, {"sig": [], "raids": []}]]}, 8),
            "older, sessions text": ({"version": older, "sessions": "x"}, 8),
            "older, raids not a list": ({"version": older, "sessions": {gone: {"sig": [], "raids": 5}}}, 8),
            "older, sig of another shape": ({"version": older, "sessions": {a: {"sig": {"x": 1}, "raids": []}}}, 8),
            "sessions list": ({"version": cur, "sessions": [[gone, {}]]}, 8),
            "entry not a dict": ({"version": cur, "sessions": {gone: "x"}}, 8),
            "raids not a list": ({"version": cur, "sessions": {gone: {"sig": [], "raids": 5}}}, 8),
            "raids of other types": ({"version": cur, "sessions": {gone: {"sig": [], "raids": [5, "x", None]}}}, 8),
            "sig of another shape": ({"version": cur, "sessions": {a: {"sig": "abc", "raids": []}}}, 8),
            "sig items of another shape": ({"version": cur, "sessions": {a: {"sig": [[1, 2, 3], ["x", "1", 2]], "raids": []}}}, 8),
            "prev of another shape": ({"version": cur, "sessions": {a: {"sig": None, "prev": [["x"]], "raids": []}}}, 8),
            "aware and naive starts": ({"version": cur, "sessions": {gone: {"sig": [], "raids": [aware, kept]}}}, 9),
            "field of another type": ({"version": cur, "sessions": {gone: {"sig": [], "raids": [{**kept, "map_id": 5}, kept]}}}, 9),
        }
        for case, (cache, count) in cases.items():
            with self.subTest(case):
                scanner = LogScanner(self.logs, json.loads(json.dumps(cache)))
                self.assertEqual(len(scanner.scan()), count)
                self.assertEqual(len(scanner.scan()), count)
                json.dumps(scanner.cache)
                for name, entry in scanner.cache["sessions"].items():
                    self.assertIsInstance(entry["raids"], list)
                    self.assertIsInstance(entry["sig"], list if (self.logs / name).is_dir() else (list, type(None)))


if __name__ == "__main__":
    unittest.main()
