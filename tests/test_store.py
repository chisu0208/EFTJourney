import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from eftjourney.demo import build_demo
from eftjourney.store import RaidStore, clean_edits, track_distance

ODD_ID = "20261028-100000-woods"  # a file left by a hand edit or another tool; listed before make_raid()'s
TOO_DEEP = 100_000  # arrays nested past what json.load can follow (Python 3.14 here stops near 16900)


def make_raid(raid_id="20261027-224801-bigmap", **extra):
    raid = {
        "schema": 1,
        "id": raid_id,
        "map": "bigmap",
        "mode": "pve",
        "startedAt": "2026-10-27T22:48:01.792+09:00",
        "endedAt": "2026-10-27T22:57:46.599+09:00",
        "duration": 584.8,
        "track": [
            {"t": 10.0, "x": 0.0, "y": 1.0, "z": 0.0, "yaw": 0.0},
            {"t": 40.0, "x": 3.0, "y": 1.0, "z": 4.0, "yaw": 90.0},
        ],
        "kills": [],
        "outcome": None,
    }
    raid.update(extra)
    return raid


# the summaries() row of make_raid(): no other file in the store may change it
NORMAL_ROW = {
    "id": "20261027-224801-bigmap", "map": "bigmap", "mode": "pve", "title": "",
    "startedAt": "2026-10-27T22:48:01.792+09:00", "endedAt": "2026-10-27T22:57:46.599+09:00",
    "duration": 584.8, "durationApprox": False, "samples": 2, "fixes": 0, "distance": 5.0,
    "kills": 0, "outcome": "unknown", "demo": False,
}
NORMAL_DOC = dict(make_raid(), detectedMap="bigmap", edited=False)  # and its merged(), likewise


def generated_raid(raid_id="20261027-200000-bigmap", **extra):
    """As builder._log_doc() writes one: samples with tod, approx and gone, server fixes, a transit."""
    raid = {
        "schema": 1, "generator": "eftjourney", "id": raid_id, "map": "bigmap", "mode": "pve", "kind": "online",
        "raidId": "a1b2", "startedAt": "2026-10-27T20:00:00.000+09:00", "endedAt": "2026-10-27T20:21:58.200+09:00",
        "endReason": "raid-end", "duration": 1318.2, "durationApprox": False, "mapSource": "log",
        "transitFromPrev": False, "exitViaTransit": True, "insuranceHint": False,
        "source": {"logSession": "log_2026.10.27_19-50-00_1.0.0", "gameVersion": "1.0.0"},
        "track": [
            {"t": 12.3, "x": -300.12, "y": 1.6, "z": -224.5, "yaw": 37.1, "shot": "a.png", "tod": 14.65},
            {"t": 60.0, "x": -280.0, "y": 1.61, "z": -200.0, "yaw": 0.0, "shot": "b.png", "approx": True},
            {"t": 95.4, "x": -250.5, "y": 2.0, "z": -180.25, "yaw": 359.9, "shot": "c.png", "gone": True},
        ],
        "logFixes": [{"t": 0.0, "x": -301.0, "y": 1.5, "z": -225.0, "reason": "spawn"}],
        "kills": [],
        "outcome": {"status": "transit", "source": "log"},
    }
    raid.update(extra)
    return raid


def saved_kill(**fields):
    """A kill as the viewer saves it (clean_edits)."""
    kill = {"id": "k1", "t": 5.0, "name": "", "level": None, "side": "", "weapon": "", "bodyPart": "",
            "distance": None, "note": "", "source": "manual"}
    kill.update(fields)
    return kill


def sendable(doc):
    """What the server sends: browsers' JSON.parse takes no NaN or Infinity, UTF-8 no lone surrogate."""
    return json.dumps(doc, ensure_ascii=False, allow_nan=False).encode("utf-8")


@contextlib.contextmanager
def odd_store(raid, edits=None):
    """A store of its own: make_raid() next to a raid file (and edits file) of another shape."""
    with tempfile.TemporaryDirectory() as tmp:
        store = RaidStore(Path(tmp))
        store.save_raid(make_raid())
        # written as they are (NaN and Infinity too): save_raid() files a raid under its "id"
        (store.raids_dir / f"{ODD_ID}.json").write_text(json.dumps(raid), encoding="utf-8")
        if edits is not None:
            (store.raids_dir / f"{ODD_ID}.edits.json").write_text(json.dumps(edits), encoding="utf-8")
        yield store


class CleanEditsTest(unittest.TestCase):
    def test_whitelists_and_coerces(self):
        edits = clean_edits({
            "map": "BigMap",
            "evil": "x",
            "outcome": {"status": "Killed", "distance": "35.5", "bodyPart": "Head", "killer": "  Tagilla  ", "t": "581"},
            "kills": [
                {"t": "312.44", "name": "Scav", "level": "12", "side": "SCAV", "bodyPart": "THORAX", "distance": 1e9},
                {"t": None, "name": "no time -> dropped"},
                "not a dict",
                {"t": 5, "side": "martian"},
            ],
        })
        self.assertNotIn("evil", edits)
        self.assertEqual(edits["map"], "bigmap")
        self.assertEqual(edits["outcome"]["status"], "killed")
        self.assertEqual(edits["outcome"]["killer"], "Tagilla")
        self.assertEqual(edits["outcome"]["distance"], 35.5)
        self.assertEqual(edits["outcome"]["bodyPart"], "head")
        self.assertEqual(edits["outcome"]["t"], 581.0)
        self.assertEqual(len(edits["kills"]), 2)
        first, second = edits["kills"]  # sorted by time
        self.assertEqual(first["t"], 5.0)
        self.assertEqual(first["side"], "other")
        self.assertEqual(second["t"], 312.4)
        self.assertEqual(second["level"], 12)
        self.assertEqual(second["side"], "scav")
        self.assertEqual(second["bodyPart"], "thorax")
        self.assertEqual(second["distance"], 5000)  # clamped

    def test_rejects_non_object(self):
        with self.assertRaises(ValueError):
            clean_edits(["kills"])

    def test_bad_map_override_ignored(self):
        self.assertNotIn("map", clean_edits({"map": "../../etc"}))

    def test_nan_and_bool_are_not_numbers(self):
        edits = clean_edits({"kills": [{"t": float("nan")}, {"t": True}, {"t": 3, "distance": float("inf")}]})
        self.assertEqual(len(edits["kills"]), 1)
        self.assertIsNone(edits["kills"][0]["distance"])

    def test_long_text_truncated(self):
        edits = clean_edits({"notes": "a" * 10000, "title": "b" * 999})
        self.assertEqual(len(edits["notes"]), 4000)
        self.assertEqual(len(edits["title"]), 200)

    def test_int_too_big_for_a_float_is_no_number(self):
        # json reads a 400-digit number as an int, and float() of it raised OverflowError
        huge = 10 ** 400
        edits = clean_edits({"kills": [{"t": huge}, {"t": 5, "level": huge, "distance": -huge}], "outcome": {"t": huge}})
        self.assertEqual(len(edits["kills"]), 1)
        self.assertIsNone(edits["kills"][0]["level"])
        self.assertIsNone(edits["kills"][0]["distance"])
        self.assertIsNone(edits["outcome"]["t"])

    def test_cleaning_clean_edits_changes_nothing(self):
        # merged() cleans the stored edits again for display: what the viewer saved must come back as it was
        cut = "a" * 199 + " b"  # cut at 200 characters, it ended in a space
        edits = clean_edits({
            "map": "Woods", "title": cut, "notes": "  메모\n",
            "outcome": {"status": "Killed", "t": "581", "killer": " Tagilla ", "bodyPart": "HEAD", "distance": 1e9},
            "kills": [{"t": "312.44", "name": cut, "side": "PMC", "level": "12"}, {"t": -3, "distance": -1}, {"t": 20, "side": "martian"}],
        })
        self.assertEqual(edits["title"], "a" * 199)
        stored = json.loads(json.dumps(edits, ensure_ascii=False))  # as save_edits() leaves it on disk
        self.assertEqual(clean_edits(stored), edits)

    def test_lone_surrogate_becomes_a_question_mark(self):
        # "\ud800" in the JSON reads as a str UTF-8 can't carry: saving failed (PUT answered 400) and
        # showing an edits file with one closed the connection without a reply
        edits = clean_edits(json.loads(
            '{"title": "\\ud800", "notes": " a\\udfffb ", "outcome": {"status": "killed", "killer": "x\\ud83d"},'
            ' "kills": [{"t": 1, "name": "\\ude00", "note": "\\ud83d\\ude00 ok"}]}'))
        self.assertEqual((edits["title"], edits["notes"], edits["outcome"]["killer"]), ("?", "a?b", "x?"))
        self.assertEqual((edits["kills"][0]["name"], edits["kills"][0]["note"]), ("?", "\U0001f600 ok"))  # a pair stays
        sendable(edits)
        self.assertEqual(clean_edits(json.loads(json.dumps(edits, ensure_ascii=False))), edits)


class RaidStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RaidStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_and_merge(self):
        self.store.save_raid(make_raid())
        self.assertEqual(self.store.list_ids(), ["20261027-224801-bigmap"])
        self.store.save_edits("20261027-224801-bigmap", {"kills": [{"t": 20, "name": "A"}], "outcome": {"status": "killed"}, "map": "woods"})
        doc = self.store.merged("20261027-224801-bigmap")
        self.assertEqual(doc["map"], "woods")
        self.assertEqual(doc["detectedMap"], "bigmap")
        self.assertEqual(doc["kills"][0]["name"], "A")
        self.assertEqual(doc["outcome"]["status"], "killed")
        self.assertTrue(doc["edited"])

    def test_rescan_does_not_touch_edits(self):
        self.store.save_raid(make_raid())
        self.store.save_edits("20261027-224801-bigmap", {"kills": [{"t": 20, "name": "A"}]})
        self.store.save_raid(make_raid(track=[]))  # a rebuild overwrites the generated file only
        self.assertEqual(self.store.merged("20261027-224801-bigmap")["kills"][0]["name"], "A")

    def test_edits_for_missing_raid(self):
        with self.assertRaises(KeyError):
            self.store.save_edits("20261027-224801-bigmap", {})

    def test_raid_file_that_is_no_object_is_no_raid(self):
        # a damaged or hand-made file: callers read raids with .get and failed on every scan
        self.store.save_raid(make_raid("20261027-230000-woods"))
        path = self.store.raids_dir / "20261027-224801-bigmap.json"
        for text in ("[1, 2]", '"text"', "5", "null"):
            path.write_text(text, encoding="utf-8")
            self.assertIsNone(self.store.load_raid("20261027-224801-bigmap"))
            self.assertIsNone(self.store.merged("20261027-224801-bigmap"))
            self.assertEqual([s["id"] for s in self.store.summaries()], ["20261027-230000-woods"])

    def test_invalid_ids_rejected(self):
        for bad in ("../x", "20261027-224801-BIGMAP", "", "20261027-224801-big map", "x.edits"):
            with self.assertRaises(KeyError):
                self.store.load_raid(bad)

    def test_edits_files_not_listed_as_raids(self):
        self.store.save_raid(make_raid())
        self.store.save_edits("20261027-224801-bigmap", {"notes": "hi"})
        self.assertEqual(self.store.list_ids(), ["20261027-224801-bigmap"])

    def test_summaries(self):
        self.store.save_raid(make_raid())
        self.store.save_raid(make_raid("20261028-100000-woods", map="woods", track=[]))
        self.store.save_edits("20261027-224801-bigmap", {"kills": [{"t": 1}, {"t": 2}], "outcome": {"status": "survived"}})
        rows = {r["id"]: r for r in self.store.summaries()}
        self.assertEqual(list(rows), ["20261028-100000-woods", "20261027-224801-bigmap"])  # newest first
        self.assertEqual(rows["20261027-224801-bigmap"]["kills"], 2)
        self.assertEqual(rows["20261027-224801-bigmap"]["outcome"], "survived")
        self.assertEqual(rows["20261027-224801-bigmap"]["distance"], 5.0)
        self.assertEqual(rows["20261028-100000-woods"]["samples"], 0)
        self.assertEqual(rows["20261028-100000-woods"]["outcome"], "unknown")

    def test_corrupt_file_is_skipped(self):
        self.store.save_raid(make_raid())
        (self.store.raids_dir / "20261029-000000-woods.json").write_text("{not json", encoding="utf-8")
        ids = [r["id"] for r in self.store.summaries()]
        self.assertEqual(ids, ["20261027-224801-bigmap"])

    def test_raid_files_of_another_shape_stay_listed(self):
        # One such file broke GET /api/raids for the whole list (KeyError on "id", TypeError on len()
        # or on "x" - "x"). The program never writes them: hand edits and other tools only.
        a, b = {"t": 1.0, "x": 0.0, "z": 0.0}, {"t": 2.0, "x": 3.0, "z": 4.0}  # 5 m apart
        nan, inf = float("nan"), float("inf")
        whole = make_raid(ODD_ID, map="woods", track=[a, b])
        cases = [  # (raid file: a whole raid but for one thing, what its row says)
            ({}, {"samples": 0, "distance": 0.0, "fixes": 0, "kills": 0, "outcome": "unknown", "title": "", "map": None}),
            ({key: value for key, value in whole.items() if key != "id"}, {"samples": 2, "distance": 5.0, "map": "woods"}),
            (dict(whole, id="20261027-224801-bigmap"), {"samples": 2}),  # names another raid
            (dict(whole, id=5), {}),
            (dict(whole, track={"x": 1.0, "z": 2.0}), {"samples": 0, "distance": 0.0}),
            (dict(whole, track="0 0 3 4"), {"samples": 0}),
            (dict(whole, track=7), {"samples": 0}),
            (dict(whole, track=[1, 2]), {"samples": 0}),
            (dict(whole, track=[a, {"t": 1.5, "z": 9.0}, {"x": "-300.0", "z": 9.0}, {"x": True, "z": 9.0}, None, b]),
             {"samples": 2, "distance": 5.0}),
            (dict(whole, track=[a, {"x": nan, "z": 9.0}, {"x": 9.0, "z": inf}, {"x": 10 ** 400, "z": 9.0}, b]),
             {"samples": 2, "distance": 5.0}),
            (dict(whole, track=[{"t": 1.0, "x": 1e308, "z": 0.0}, {"t": 2.0, "x": -1e308, "z": 0.0}]),
             {"samples": 2, "distance": 0.0}),  # overflows
            (dict(whole, track=[{"t": 1.0, "x": 10 ** 308, "z": 0.0}, {"t": 2.0, "x": -10 ** 308, "z": 0.0}]),
             {"samples": 2, "distance": 0.0}),  # ints: each fits a float, their difference doesn't
            (dict(whole, outcome="killed"), {"outcome": "unknown"}),
            (dict(whole, outcome=["killed"]), {"outcome": "unknown"}),
            (dict(whole, outcome={"status": nan}), {"outcome": "unknown"}),
            (dict(whole, kills=3, logFixes=3), {"kills": 0, "fixes": 0}),
            (dict(whole, kills="abc", logFixes={"t": 1.0}), {"kills": 0, "fixes": 0}),
            (dict(whole, title=5), {"title": ""}),
            (dict(whole, map=nan, mode=inf, title=nan, startedAt=nan, endedAt=-inf, duration=nan),
             {"map": None, "mode": None, "title": "", "startedAt": None, "endedAt": None, "duration": None}),
            (dict(whole, map=["woods"], duration=inf), {"map": None, "duration": None}),
        ]
        for raid, expected in cases:
            with self.subTest(raid=raid), odd_store(raid) as store:
                rows = store.summaries()
                json.dumps(rows, allow_nan=False)  # what browsers accept: no NaN or Infinity
                self.assertEqual([r["id"] for r in rows], [ODD_ID, NORMAL_ROW["id"]])  # ids from the file names
                self.assertEqual(rows[1], NORMAL_ROW)
                self.assertEqual({key: rows[0][key] for key in expected}, expected)
                doc = store.merged(ODD_ID)
                sendable(doc)  # and the raid opens: GET /api/raids/<id> sends merged()
                self.assertEqual(len(doc["track"]), rows[0]["samples"])  # what the viewer draws

    def test_edits_files_of_another_shape(self):
        # merged() applied them as they were: {"kills": 3} broke len(), {"outcome": "x"} broke .get()
        nan, inf = float("nan"), float("inf")
        raid = make_raid(ODD_ID, map="woods", kills=[saved_kill(name="Scav")],  # one the viewer saved: merged() cleans kills
                         outcome={"status": "survived", "source": "log"})
        cases = [  # (edits file, what the raid's row says)
            ({"kills": 3}, {"kills": 1}),
            ({"outcome": "x"}, {"outcome": "survived"}),
            ({"kills": "abc", "outcome": ["killed"]}, {"kills": 1, "outcome": "survived"}),
            ({"kills": [{"t": nan}, 5, None, {"t": 10 ** 400}, {"t": 12, "name": 7, "distance": inf}]}, {"kills": 1}),
            ({"outcome": {"status": nan, "t": inf}}, {"outcome": "unknown"}),
            ({"title": 5, "notes": ["x"], "map": "Interchange"}, {"title": "5", "map": "interchange"}),
            ({"junk": 1}, {"kills": 1, "outcome": "survived", "title": "", "map": "woods"}),
        ]
        for edits, expected in cases:
            with self.subTest(edits=edits), odd_store(raid, edits) as store:
                rows = store.summaries()
                json.dumps(rows, allow_nan=False)
                self.assertEqual([r["id"] for r in rows], [ODD_ID, NORMAL_ROW["id"]])
                self.assertEqual({key: rows[0][key] for key in expected}, expected)
                doc, shown = store.merged(ODD_ID), clean_edits(edits)
                sendable(doc)
                for key in ("map", "title", "notes", "outcome", "kills"):
                    self.assertEqual(doc.get(key), shown[key] if key in shown else raid.get(key), key)
                self.assertNotIn("junk", doc)
                self.assertTrue(doc["edited"])
                # load_edits stays raw: with odd keys the file still counts as edits, so no scan moves or overwrites it
                self.assertEqual(json.dumps(store.load_edits(ODD_ID)), json.dumps(edits))

    def test_merged_draws_only_samples_with_a_position(self):
        a = {"t": 1.0, "x": 0.0, "y": 1.0, "z": 0.0, "yaw": 0.0, "shot": "a.png"}
        b = {"t": 2.0, "x": 3.0, "y": 1.0, "z": 4.0, "yaw": 90.0, "gone": True}
        track = [a, {"t": 1.5, "x": "1", "z": 2.0}, None, {"t": 1.7, "x": float("nan"), "z": 0.0}, b]
        with odd_store(make_raid(ODD_ID, id="20261027-224801-bigmap", track=track)) as store:
            doc = store.merged(ODD_ID)
            self.assertEqual(doc["track"], [a, b])
            self.assertEqual(doc["id"], ODD_ID)  # the file's name, not the "id" inside: the viewer saves edits to it
            self.assertEqual(store.merged(NORMAL_ROW["id"])["track"], make_raid()["track"])

    def test_every_raid_the_list_shows_opens(self):
        # summaries() corrected these for its row, but GET /api/raids/<id> sent merged() with them: NaN or
        # Infinity (Python's json reads both) failed the browser's res.json() ("레이드를 불러오지 못했습니다"),
        # and "kills": "abc" or "logFixes": {...} broke app.js's .map() and left the previous raid's map
        nan, inf = float("nan"), float("inf")
        a = {"t": 1.0, "x": 0.0, "y": 1.0, "z": 0.0, "yaw": 0.0, "shot": "a.png", "tod": 14.5}
        b = {"t": 2.0, "x": 3.0, "y": 1.0, "z": 4.0, "yaw": 90.0, "shot": "b.png", "approx": True, "gone": True}
        whole = make_raid(ODD_ID, map="woods", track=[a, b])
        fix = {"t": 1.5, "x": 2.0, "y": 0.5, "z": 3.0, "reason": "spawn"}
        killed = {"status": "killed", "t": None, "killer": "?", "killerSide": "", "weapon": "", "bodyPart": "",
                  "distance": None, "exit": "", "source": "manual"}
        cases = [  # (raid file, edits file, what merged() says)
            (dict(whole, duration=nan), None, {"duration": None}),
            (dict(whole, duration=inf), None, {"duration": None}),
            (dict(whole, duration=-inf), None, {"duration": None}),
            (dict(whole, duration="584.8"), None, {"duration": None}),
            (dict(whole, track=[dict(a, y=nan, yaw=inf, tod=-inf), dict(b, y="1", yaw=None, tod=[14])]), None,
             {"track": [dict(a, y=None, yaw=None, tod=None), dict(b, y=None, yaw=None, tod=None)]}),
            # no time as a real number: the list counted them, the viewer drops them
            (dict(whole, track=[a, {"x": 1.0, "z": 1.0}, dict(a, t=nan), dict(a, t=-inf), dict(a, t=None),
                                dict(a, t="1.5"), dict(a, t=True), dict(a, t=10 ** 400), b]), None, {"track": [a, b]}),
            (dict(whole, kills="abc", logFixes={"t": 1.0, "x": 0.0, "z": 0.0}), None, {"kills": [], "logFixes": []}),
            (dict(whole, kills=[{"t": 5.0, "name": "Scav"}, {"t": nan}, "x", None],
                  logFixes=[fix, dict(fix, y=nan), dict(fix, t=inf), dict(fix, x="2"), None, 5]), None,
             {"kills": [saved_kill(name="Scav")], "logFixes": [fix, dict(fix, y=None)]}),
            (dict(whole, outcome="killed"), None, {"outcome": None}),
            (dict(whole, outcome={"status": "killed", "t": nan, "distance": inf}), None,
             {"outcome": {"status": "killed", "t": None, "distance": None}}),
            (dict(whole, outcome={"status": "killed", "t": "abc"}), None, {"outcome": {"status": "killed", "t": None}}),
            # a lone surrogate in the edits failed the server's encode("utf-8"): no reply, a traceback per request
            (whole, {"notes": "a\ud800b", "kills": [{"t": 5, "name": "\udfffScav"}],
                     "outcome": {"status": "killed", "killer": "\ud800"}},
             {"notes": "a?b", "kills": [saved_kill(name="?Scav")], "outcome": killed}),
            (dict(whole, source={"logSession": nan, "gameVersion": [inf, "\ud800"]}), None,
             {"source": {"logSession": None, "gameVersion": [None, "?"]}}),
        ]
        for raid, edits, expected in cases:
            with self.subTest(raid=raid, edits=edits), odd_store(raid, edits) as store:
                doc = store.merged(ODD_ID)
                sendable(doc)
                self.assertEqual({key: doc[key] for key in expected}, expected)
                rows = store.summaries()
                self.assertEqual([r["id"] for r in rows], [ODD_ID, NORMAL_ROW["id"]])
                # what the list says is what the viewer draws: "기록 N", "서버 기록 N", the kills
                self.assertEqual((rows[0]["samples"], rows[0]["fixes"], rows[0]["kills"], rows[0]["duration"]),
                                 (len(doc["track"]), len(doc.get("logFixes") or []), len(doc["kills"]), doc["duration"]))
                self.assertEqual((rows[1], store.merged(NORMAL_ROW["id"])), (NORMAL_ROW, NORMAL_DOC))

    def test_lone_surrogate_in_a_raid_file_is_shown_as_a_question_mark(self):
        # "\ud800" in the file passes json.load, but not the UTF-8 the server sends: the row was left out,
        # and in a value the row doesn't show (a sample's file name, a key) the raid listed but didn't open
        raid = make_raid(ODD_ID, title="\ud800", track=[dict(make_raid()["track"][0], shot="\udc00.png")], **{"\ud800": 1})
        err = io.StringIO()
        with odd_store(raid) as store, contextlib.redirect_stderr(err):
            rows = store.summaries()
            doc = store.merged(ODD_ID)
        self.assertEqual(rows, [dict(NORMAL_ROW, id=ODD_ID, title="?", samples=1, distance=0.0), NORMAL_ROW])
        self.assertEqual((doc["title"], doc["track"][0]["shot"], doc["?"]), ("?", "?.png", 1))
        sendable(doc)
        self.assertEqual(err.getvalue(), "")

    def test_normal_raids_come_out_as_they_are(self):
        # what the scan, the demo and the viewer's editor write: none of the checks above may change it
        generated = generated_raid()
        demo = build_demo(datetime(2026, 10, 20, 12, 0))
        shots_only = generated_raid("20261026-100000-woods", map="woods", endedAt=None, endReason="screenshots-only",
                                    duration=None, logFixes=[], outcome=None)
        edited = generated_raid("20261025-100000-bigmap")
        for raid in (generated, demo, shots_only, edited):
            self.store.save_raid(raid)
        edits = self.store.save_edits(edited["id"], {
            "map": "woods", "title": "첫 런", "notes": "메모\n둘째 줄",
            "outcome": {"status": "killed", "t": 300, "killer": "Tagilla"},
            "kills": [{"t": 200, "name": "Scav", "side": "scav"}, {"t": 100.04, "name": "PMC", "level": "40"}]})
        for raid in (generated, demo, shots_only):
            self.assertEqual(self.store.merged(raid["id"]), dict(raid, detectedMap=raid["map"], edited=False), raid["id"])
        self.assertEqual(self.store.merged(edited["id"]), dict(edited, **edits, detectedMap="bigmap", edited=True))
        rows = [(r["id"], r["samples"], r["fixes"], r["kills"], r["duration"], r["outcome"]) for r in self.store.summaries()]
        self.assertEqual(rows, [(generated["id"], 3, 1, 0, 1318.2, "transit"), (shots_only["id"], 3, 0, 0, None, "unknown"),
                                (edited["id"], 3, 1, 2, 1318.2, "killed"), (demo["id"], 17, 0, 3, 1135.0, "killed")])

    def test_lone_surrogate_in_edits_is_saved(self):
        # PUT /api/raids/<id>/edits with one answered 400, and nothing was saved
        self.store.save_raid(make_raid())
        raw = json.loads('{"notes": "메모 \\ud800 끝", "kills": [{"t": 3, "name": "\\udc00"}]}')  # as the request body reads
        edits = self.store.save_edits(NORMAL_ROW["id"], raw)
        self.assertEqual((edits["notes"], edits["kills"][0]["name"]), ("메모 ? 끝", "?"))
        self.assertEqual(self.store.load_edits(NORMAL_ROW["id"]), edits)
        self.assertEqual(self.store.merged(NORMAL_ROW["id"])["notes"], "메모 ? 끝")

    def test_delete_waits_while_the_file_is_held(self):
        # Windows: while the viewer's server (or a virus scanner) reads the file, unlink raises
        # PermissionError (WinError 32), and that failed the whole scan
        raid_id = NORMAL_ROW["id"]
        self.store.save_raid(make_raid())
        self.store.save_edits(raid_id, {"notes": "x"})
        real_unlink, calls = Path.unlink, []

        def unlink(path, missing_ok=False):
            calls.append(path.name)
            if calls.count(path.name) <= 2:
                raise PermissionError(13, "in use")
            return real_unlink(path, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", unlink), mock.patch("eftjourney.store.time.sleep") as sleep:
            self.store.delete_edits(raid_id)
            self.store.delete_raid(raid_id)
        self.assertEqual(calls, [f"{raid_id}.edits.json"] * 3 + [f"{raid_id}.json"] * 3)
        self.assertEqual(sleep.call_args_list, [mock.call(0.05)] * 4)
        self.assertEqual(list(self.store.raids_dir.iterdir()), [])

    def test_delete_gives_up_on_a_file_held_too_long(self):
        raid_id = NORMAL_ROW["id"]
        self.store.save_raid(make_raid())
        calls = []

        def unlink(path, missing_ok=False):
            calls.append(path.name)
            raise PermissionError(13, "in use")

        with mock.patch.object(Path, "unlink", unlink), mock.patch("eftjourney.store.time.sleep") as sleep:
            with self.assertRaises(PermissionError):  # the scan reports it and tries again next time
                self.store.delete_raid(raid_id)
        self.assertEqual((len(calls), sleep.call_count), (10, 9))
        self.assertEqual(self.store.list_ids(), [raid_id])

    def test_deleting_what_is_not_there(self):
        with mock.patch("eftjourney.store.time.sleep") as sleep:
            self.store.delete_raid(NORMAL_ROW["id"])
            self.store.delete_edits(NORMAL_ROW["id"])
        sleep.assert_not_called()

    def test_row_that_still_fails_is_left_out(self):
        # the last resort: whatever still breaks one row is named on stderr, and the rest is listed
        self.store.save_raid(make_raid())
        self.store.save_raid(make_raid(ODD_ID, map="woods"))
        real = self.store.merged

        def merged(raid_id):
            if raid_id == ODD_ID:
                raise RecursionError("maximum recursion depth exceeded")  # say a raid nested too deep for _sendable()
            return real(raid_id)

        err = io.StringIO()
        with mock.patch.object(self.store, "merged", merged), contextlib.redirect_stderr(err):
            self.assertEqual(self.store.summaries(), [NORMAL_ROW])
        # merged() reads the raid file and its edits file: the line names both when both are there, either
        # may be at fault, but not an edits file that does not exist
        raid_file, edits_file = (self.store.raids_dir / f"{ODD_ID}{suffix}" for suffix in (".json", ".edits.json"))
        self.assertEqual(err.getvalue(), f"레이드 목록에서 뺐습니다 (읽을 수 없는 모양): {raid_file}\n")
        self.store.save_edits(ODD_ID, {"notes": "x"})
        err = io.StringIO()
        with mock.patch.object(self.store, "merged", merged), contextlib.redirect_stderr(err):
            self.assertEqual(self.store.summaries(), [NORMAL_ROW])
        self.assertEqual(err.getvalue(), f"레이드 목록에서 뺐습니다 (읽을 수 없는 모양): {raid_file} 또는 {edits_file}\n")

    def test_files_nested_too_deep_for_json_load(self):
        # json.load raised RecursionError on them (hand-made files only): every scan failed, as it reads all
        # stored raids, and GET /api/raids/<id> closed the connection without a reply
        deep = "[" * TOO_DEEP + "]" * TOO_DEEP
        whole = json.dumps(make_raid(ODD_ID, map="woods"))
        self.store.save_raid(make_raid())
        raid_file, edits_file = (self.store.raids_dir / f"{ODD_ID}{suffix}" for suffix in (".json", ".edits.json"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            raid_file.write_text(whole[:-1] + ', "deep": ' + deep + "}", encoding="utf-8")  # a raid but for that
            self.assertIsNone(self.store.load_raid(ODD_ID))  # a damaged file, as invalid JSON is
            self.assertIsNone(self.store.merged(ODD_ID))
            self.assertEqual(self.store.summaries(), [NORMAL_ROW])
            raid_file.write_text(whole, encoding="utf-8")
            edits_file.write_text('{"title": "x", "deep": ' + deep + "}", encoding="utf-8")
            self.assertEqual(self.store.load_edits(ODD_ID), {})  # nothing to show, as with invalid JSON
            self.assertIsNone(self.store.read_edits(ODD_ID))  # but a file is there: no scan moves or writes over it
            self.assertTrue(self.store.merged(ODD_ID)["edited"])
            self.assertEqual(self.store.summaries(), [dict(NORMAL_ROW, id=ODD_ID, map="woods"), NORMAL_ROW])
        self.assertEqual(err.getvalue(), "")

    def test_files_saved_with_a_bom(self):
        # Notepad's "UTF-8 with BOM" and PowerShell 5.1 write one: such edits read as none at all, and a
        # raid file as damaged
        self.store.save_raid(make_raid())
        raid_id = NORMAL_ROW["id"]
        self.store.edits_file(raid_id).write_text('{"notes": "메모"}', encoding="utf-8-sig")
        self.assertEqual(self.store.read_edits(raid_id), {"notes": "메모"})
        self.assertEqual(self.store.merged(raid_id)["notes"], "메모")
        raid_file = self.store.raid_file(raid_id)
        raid_file.write_text(raid_file.read_text(encoding="utf-8"), encoding="utf-8-sig")
        self.assertEqual(self.store.summaries(), [NORMAL_ROW])

    def test_read_edits_tells_a_missing_file_from_an_unreadable_one(self):
        self.store.save_raid(make_raid())
        raid_id = NORMAL_ROW["id"]
        self.assertEqual(self.store.read_edits(raid_id), {})
        for text in ('{"notes": "x",}', "[1]", ""):
            self.store.edits_file(raid_id).write_text(text, encoding="utf-8")
            self.assertIsNone(self.store.read_edits(raid_id), text)
            self.assertEqual(self.store.load_edits(raid_id), {}, text)  # for showing: no edits

    def test_written_json_is_utf8(self):
        self.store.save_raid(make_raid(title="첫 기숙사 런"))
        text = (self.store.raids_dir / "20261027-224801-bigmap.json").read_text(encoding="utf-8")
        self.assertIn("첫 기숙사 런", text)
        self.assertEqual(json.loads(text)["title"], "첫 기숙사 런")


class DistanceTest(unittest.TestCase):
    def test_top_down_distance_ignores_height(self):
        track = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 50, "z": 4}, {"x": 3, "y": 0, "z": 10}]
        self.assertEqual(track_distance(track), 11.0)


if __name__ == "__main__":
    unittest.main()
