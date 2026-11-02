import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from eftjourney import screenshots
from eftjourney.screenshots import (Shot, parse_name, scan_dir, shot_from_json, shot_from_name, shot_from_path,
                                    shot_to_json, yaw_from_quaternion)

SAMPLE = "2026-10-27[22-51]_-146.32, 1.06, -154.11_0.00000, 0.70711, 0.00000, 0.70711_14.45 (0).png"


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def yaw_pitch_quat(yaw_deg, pitch_deg):
    """Unity order: yaw about world +Y, then pitch about the local +X axis."""
    y = math.radians(yaw_deg) / 2
    p = math.radians(pitch_deg) / 2
    return quat_mul((0.0, math.sin(y), 0.0, math.cos(y)), (math.sin(p), 0.0, 0.0, math.cos(p)))


class ParseNameTest(unittest.TestCase):
    def test_sample(self):
        info = parse_name(SAMPLE)
        self.assertEqual(info["named"], datetime(2026, 10, 27, 22, 51))
        self.assertEqual((info["x"], info["y"], info["z"]), (-146.32, 1.06, -154.11))
        self.assertAlmostEqual(info["qy"], 0.70711)

    def test_variants(self):
        for name in (
            "2026-10-27[22-51]_-146.32, 1.06, -154.11_0.00000, 0.70711, 0.00000, 0.70711_14.45 (12).png",
            "2026-10-27[22-51]_-146.32, 1.06, -154.11_0.00000, 0.70711, 0.00000, 0.70711_14.45.png",
            "2026-10-27[22-51]_-146.32, 1.06, -154.11_0.00000, 0.70711, 0.00000, 0.70711 (0).jpg",
            "2026-10-27[22-51]_-146, 1, -154_0, 0.70711, 0, 0.70711_0.00 (0).PNG",
        ):
            self.assertIsNotNone(parse_name(name), name)

    def test_tod(self):
        self.assertEqual(parse_name(SAMPLE)["tod"], 14.45)
        self.assertIsNone(parse_name(SAMPLE.replace("_14.45", ""))["tod"])
        self.assertIsNone(parse_name(SAMPLE.replace("_14.45", "_31.00"))["tod"])  # not a time of day

    def test_real_names_from_other_projects(self):
        # verbatim names collected during research (0.16 to 1.1.5 clients)
        for name, heading in (
            ("2025-04-14[20-42]_14.31, 2.55, 57.88_0.17434, 0.81716, -0.31801, 0.44802_15.47 (0).png", 122.53),
            ("2026-09-18[01-14]_616.67, 1.05, -103.50_0.04218, -0.71487, 0.04329, 0.69664_14.65 (0).png", 268.52),
            ("2024-10-01[22-46]_92.9, 15.6, -0.5_0.0, 0.0, 0.0, 1.0_9.40 (0).png", 0.0),  # older one-decimal build
            ("2026-09-18[01-14]_616.67, 1.05, -103.50_0.04218, -0.71487, 0.04329, 0.69664_14.65 (482913).PNG", 268.52),
        ):
            info = parse_name(name)
            self.assertIsNotNone(info, name)
            self.assertAlmostEqual(yaw_from_quaternion(info["qx"], info["qy"], info["qz"], info["qw"]), heading, delta=0.01)

    def test_death_sentinel_and_absurd_positions_rejected(self):
        self.assertIsNone(parse_name("2026-08-29[12-51]_-0.10, -9997.50, -0.10_0.00000, 0.00000, 0.00000, 1.00000_19.10 (0).png"))
        self.assertIsNone(parse_name("2026-08-29[12-51]_99999.00, 1.00, 0.00_0.00000, 0.00000, 0.00000, 1.00000_19.10 (0).png"))
        self.assertIsNone(parse_name("2026-08-29[12-52]_19.12 (0).png"))  # after-action screen: no camera

    def test_rejects_files_without_position(self):
        for name in ("2026-10-27[22-51].png", "Screenshot 2026-10-27.png", "2026-10-27[22-51]_ (0).png", "2026-13-45[25-99]_1, 2, 3_0, 0, 0, 1_0.00 (0).png", SAMPLE.replace(".png", ".txt")):
            self.assertIsNone(parse_name(name), name)


class YawTest(unittest.TestCase):
    def test_cardinal_directions(self):
        self.assertAlmostEqual(yaw_from_quaternion(0, 0, 0, 1), 0.0)
        s = math.sqrt(0.5)
        self.assertAlmostEqual(yaw_from_quaternion(0, s, 0, s), 90.0, places=3)  # +90 about Y faces +X
        self.assertAlmostEqual(yaw_from_quaternion(0, 1, 0, 0), 180.0, places=3)
        self.assertAlmostEqual(yaw_from_quaternion(0, -s, 0, s), 270.0, places=3)

    def test_pitch_does_not_change_heading(self):
        for yaw in (0, 37, 135, 250, 359):
            for pitch in (-60, -10, 25, 70):
                q = yaw_pitch_quat(yaw, pitch)
                self.assertAlmostEqual(yaw_from_quaternion(*q), yaw % 360, places=3, msg=(yaw, pitch))

    def test_sample_name_faces_east(self):
        info = parse_name(SAMPLE)
        self.assertAlmostEqual(yaw_from_quaternion(info["qx"], info["qy"], info["qz"], info["qw"]), 90.0, places=2)


class ShotTimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, name, mtime):
        path = self.dir / name
        path.write_bytes(b"png")
        ts = mtime.timestamp()
        os.utime(path, (ts, ts))
        return path

    def test_mtime_used_when_consistent(self):
        shot = shot_from_path(self.make(SAMPLE, datetime(2026, 10, 27, 22, 51, 37, 250000)))
        self.assertEqual(shot.time_source, "mtime")
        self.assertEqual(shot.taken, datetime(2026, 10, 27, 22, 51, 37, 250000))
        self.assertAlmostEqual(shot.yaw, 90.0)

    @unittest.skipUnless(hasattr(os.stat_result, "st_birthtime") or os.name == "nt", "no file creation time here")
    def test_creation_time_preferred(self):
        now = datetime.now()
        name = f"{now:%Y-%m-%d[%H-%M]}_1.00, 2.00, 3.00_0.00000, 0.00000, 0.00000, 1.00000_12.00 (0).png"
        path = self.dir / name
        path.write_bytes(b"png")  # created just now, i.e. inside the named minute (+ tolerance)
        modified = now.replace(second=0, microsecond=0) + timedelta(seconds=59)
        os.utime(path, (modified.timestamp(), modified.timestamp()))
        shot = shot_from_path(path)
        self.assertEqual(shot.time_source, "birthtime")
        self.assertLess(abs((shot.taken - now).total_seconds()), 5)
        self.assertEqual(shot.tod, 12.0)

    def test_name_used_when_file_was_modified_later(self):
        shot = shot_from_path(self.make(SAMPLE, datetime(2026, 10, 28, 9, 0, 0)))
        self.assertEqual(shot.time_source, "name")
        self.assertEqual(shot.taken, datetime(2026, 10, 27, 22, 51))

    def test_scan_dir_filters_and_sorts(self):
        self.make("2026-10-27[22-55]_1, 2, 3_0, 0, 0, 1_0.00 (0).png", datetime(2026, 10, 27, 22, 55, 10))
        self.make(SAMPLE, datetime(2026, 10, 27, 22, 51, 5))
        self.make("notes.txt", datetime(2026, 10, 27, 22, 51, 5))
        self.make("2026-10-27[22-56].png", datetime(2026, 10, 27, 22, 56, 5))
        (self.dir / "sub.png").mkdir()
        shots = scan_dir(self.dir)
        self.assertEqual([s.taken.minute for s in shots], [51, 55])

    def test_scan_dir_tells_missing_from_empty(self):
        # a folder that cannot be listed must not look like "every screenshot was deleted"
        self.assertIsNone(scan_dir(self.dir / "missing"))
        self.assertIsNone(scan_dir(None))
        self.assertIsNone(scan_dir(self.make("2026-10-27[22-55].png", datetime(2026, 10, 27, 22, 55))))  # a file
        (self.dir / "empty").mkdir()
        self.assertEqual(scan_dir(self.dir / "empty"), [])


class ShotFromJsonTest(unittest.TestCase):
    def entry(self, **changes):
        shot = shot_from_path(Path(SAMPLE))  # no such file: timed by its name
        return {**shot_to_json(shot), **changes}

    def test_roundtrip(self):
        shot = shot_from_json(SAMPLE, self.entry())
        self.assertEqual((shot.taken, shot.x, shot.z, shot.tod, shot.time_source),
                         (datetime(2026, 10, 27, 22, 51), -146.32, -154.11, 14.45, "name"))
        whole = shot_from_json(SAMPLE, self.entry(x=100, tod=None))  # JSON integers are numbers too
        self.assertEqual((whole.x, type(whole.x), whole.tod), (100.0, float, None))

    def test_wrong_kinds_are_rejected(self):
        # hand-edited or written by another tool: one such entry broke every scan's sorting
        for changes in ({"x": "120.0"}, {"x": None}, {"z": [1]}, {"yaw": "90"}, {"qw": True},
                        {"y": float("nan")}, {"qx": float("inf")}, {"x": 10 ** 400}, {"tod": "12.5"},
                        {"tod": 24.0}, {"tod": -1}, {"time_source": 1}, {"time_source": "exif"},
                        {"taken": 1761572460}, {"taken": "yesterday"}):
            self.assertIsNone(shot_from_json(SAMPLE, self.entry(**changes)), changes)
        missing = self.entry()
        del missing["qx"]
        self.assertIsNone(shot_from_json(SAMPLE, missing))
        self.assertIsNone(shot_from_json(SAMPLE, ["not", "a", "dict"]))

    def test_offset_times_become_local_wall_clock(self):
        # the moment SAMPLE's name gives (22:51 here), written in UTC by another tool
        utc = datetime(2026, 10, 27, 22, 51, 20).astimezone().astimezone(timezone.utc)
        for text in (utc.isoformat(), utc.strftime("%Y-%m-%dT%H:%M:%SZ")):
            shot = shot_from_json(SAMPLE, self.entry(taken=text))
            self.assertIsNone(shot.taken.tzinfo)
            self.assertEqual(shot.taken, datetime(2026, 10, 27, 22, 51, 20))

    def test_time_far_from_the_named_minute_is_unreadable(self):
        # a placeholder date or a time zone mix-up: such a time sent the shot out of every raid
        # window, or (without logs) made every scan fail
        for taken in ("2026-10-27T22:51:30.000", "2026-10-27T22:49:31.000", "2026-10-27T22:53:40.000"):
            self.assertIsNotNone(shot_from_json(SAMPLE, self.entry(taken=taken)), taken)  # file times, a restored start
        for taken in ("0001-01-01T00:00:00", "1970-01-01T00:00:00", "9999-12-31T00:00:00", "2026-10-27T13:51:00",
                      "2026-10-27T22:45:00.000", "2026-10-28T22:51:00"):
            self.assertIsNone(shot_from_json(SAMPLE, self.entry(taken=taken)), taken)
        self.assertIsNotNone(shot_from_json("hand-made.png", self.entry()))  # no minute to go by
        for taken in ("0001-01-01T00:00:00", "1969-12-31T00:00:00", "9999-12-31T00:00:00"):
            # nor a time Windows can place: without logs such an entry made every scan fail
            self.assertIsNone(shot_from_json("hand-made.png", self.entry(taken=taken)), taken)

    def test_names_are_parsed_once(self):
        # every scan checks every journal entry against its name: tens of thousands each time
        names = [SAMPLE.replace("(0)", f"({n})") for n in range(3)]
        entry = self.entry()
        screenshots._named.cache_clear()
        with mock.patch.object(screenshots, "parse_name", wraps=screenshots.parse_name) as parse:
            for _ in range(2):
                for name in names:
                    self.assertIsNotNone(shot_from_json(name, entry))
        self.assertEqual(parse.call_count, len(names))

    def test_names_dated_where_no_clock_can_be_are_no_position(self):
        # Windows cannot convert such dates: the scan failed on every run
        for date in ("1970-01-01", "1969-12-31", "9999-12-31"):
            self.assertIsNone(parse_name(SAMPLE.replace("2026-10-27", date)), date)
        self.assertIsNotNone(parse_name(SAMPLE.replace("2026-10-27", "2017-12-28")))


class ShotFromNameTest(unittest.TestCase):
    def test_a_gone_file_from_its_name(self):
        # the whole position from the name, not the 2-decimal one a route sample keeps
        timed = shot_from_name(SAMPLE, datetime(2026, 10, 27, 22, 51, 7, 300000))
        self.assertEqual(timed, Shot(SAMPLE, datetime(2026, 10, 27, 22, 51, 7, 300000), -146.32, 1.06, -154.11,
                                     0.0, 0.70711, 0.0, 0.70711, 90.0, "mtime", 14.45))
        named = shot_from_name(SAMPLE)
        self.assertEqual((named.taken, named.time_source), (datetime(2026, 10, 27, 22, 51), "name"))
        self.assertEqual(shot_to_json(named), shot_to_json(shot_from_path(Path(SAMPLE))))  # no such file: by name
        self.assertIsNone(shot_from_name("2026-10-27[22-51]_14.45 (0).png"))  # a menu screenshot


if __name__ == "__main__":
    unittest.main()
