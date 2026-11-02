import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from eftjourney import paths


class DataDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / "site-packages"
        self.project.mkdir()
        self.home = self.root / "home"

    def default(self, platform, localappdata=None):
        with mock.patch.object(paths, "PROJECT_ROOT", self.project), \
                mock.patch.object(sys, "platform", platform), \
                mock.patch.object(Path, "home", return_value=self.home), \
                mock.patch.dict(os.environ):
            os.environ.pop("LOCALAPPDATA", None)
            if localappdata:
                os.environ["LOCALAPPDATA"] = localappdata
            return paths.default_data_dir()

    def test_installed_copy_uses_a_per_user_folder(self):
        # pip puts the package in site-packages; its parent must not hold raids
        local = str(self.root / "Local")
        self.assertEqual(self.default("win32", local), Path(local) / "eftjourney")
        self.assertEqual(self.default("win32"), self.home / ".eftjourney")
        self.assertEqual(self.default("linux", local), self.home / ".eftjourney")

    def test_source_checkout_keeps_the_project_data_folder(self):
        (self.project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        self.assertEqual(self.default("win32", str(self.root / "Local")), self.project / "data")
        self.assertEqual(self.default("linux"), self.project / "data")
        # and this test runs from one
        self.assertEqual(paths.DEFAULT_DATA_DIR, paths.PROJECT_ROOT / "data")

    def test_overrides_win(self):
        default = self.root / "default"
        with mock.patch.object(paths, "DEFAULT_DATA_DIR", default), mock.patch.dict(os.environ):
            os.environ.pop("EFTJOURNEY_DATA", None)
            self.assertEqual(paths.data_dir(), default)
            os.environ["EFTJOURNEY_DATA"] = str(self.root / "env")
            self.assertEqual(paths.data_dir(), self.root / "env")
            self.assertEqual(paths.data_dir(self.root / "arg"), self.root / "arg")


if __name__ == "__main__":
    unittest.main()
