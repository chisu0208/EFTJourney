import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from eftjourney import store
from eftjourney.store import _atomic_write_json


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.target = self.dir / "20261027-224801-bigmap.edits.json"

    def read(self):
        return json.loads(self.target.read_text(encoding="utf-8"))

    def test_retries_while_the_file_is_held(self):
        real_replace = os.replace
        calls = []

        def busy_twice(src, dst):
            calls.append(dst)
            if len(calls) <= 2:
                raise PermissionError(13, "in use")  # what Windows raises while a reader has it open
            real_replace(src, dst)

        with mock.patch.object(store.os, "replace", busy_twice), mock.patch.object(store.time, "sleep"):
            _atomic_write_json(self.target, {"a": 1})
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.read(), {"a": 1})
        self.assertEqual(os.listdir(self.dir), [self.target.name])

    def test_gives_up_and_leaves_no_temp_file(self):
        with mock.patch.object(store.os, "replace", side_effect=PermissionError(13, "in use")) as replace, \
                mock.patch.object(store.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                _atomic_write_json(self.target, {"a": 1})
        self.assertGreater(replace.call_count, 1)
        self.assertEqual(sleep.call_count, replace.call_count - 1)
        self.assertEqual(os.listdir(self.dir), [])

    def test_written_to_disk_before_the_rename(self):
        # without the fsync a power cut right after the rename can leave a zero-filled file
        events = []
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(fd):
            events.append(("fsync", os.fstat(fd).st_size))
            real_fsync(fd)

        def replace(src, dst):
            events.append(("replace", Path(dst).name))
            real_replace(src, dst)

        with mock.patch.object(store.os, "fsync", fsync), mock.patch.object(store.os, "replace", replace):
            _atomic_write_json(self.target, {"a": 1})
        self.assertEqual([kind for kind, _ in events], ["fsync", "replace"])
        self.assertEqual(events[0][1], self.target.stat().st_size)  # the whole document, not a part of it
        self.assertEqual(self.read(), {"a": 1})

    def test_other_errors_are_not_retried(self):
        with mock.patch.object(store.os, "replace", side_effect=FileNotFoundError(2, "gone")) as replace:
            with self.assertRaises(FileNotFoundError):
                _atomic_write_json(self.target, {"a": 1})
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(os.listdir(self.dir), [])

    @unittest.skipUnless(sys.platform == "win32", "Windows refuses to replace an open file")
    def test_reader_holding_the_file_briefly(self):
        _atomic_write_json(self.target, {"a": 1})
        reader = self.target.open(encoding="utf-8")
        timer = threading.Timer(0.15, reader.close)
        timer.start()
        try:
            _atomic_write_json(self.target, {"a": 2})
        finally:
            timer.cancel()
            reader.close()
        self.assertEqual(self.read(), {"a": 2})


if __name__ == "__main__":
    unittest.main()
