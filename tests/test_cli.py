import argparse
import contextlib
import http.server
import io
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from eftjourney import cli
from eftjourney.builder import ScanReport
from eftjourney.server import AppContext, make_server
from eftjourney.store import RaidStore

RAID_ID = "20261027-224801-bigmap"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Another process trying the data folder's lock the way eftjourney takes it. Prints "locked"
# and keeps it until its stdin is closed, or prints "busy" and ends.
TRY_LOCK = r"""
import os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)
try:
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print("busy", flush=True)
else:
    print("locked", flush=True)
    sys.stdin.read()
"""


def split_command(line):
    """Split a printed command line the way Python receives it as argv (Windows rules there)."""
    if sys.platform != "win32":
        return shlex.split(line)
    import ctypes
    from ctypes import wintypes

    shell32, kernel32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("kernel32")
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    count = ctypes.c_int()
    argv = shell32.CommandLineToArgvW(line, ctypes.byref(count))
    try:
        return [argv[i] for i in range(count.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


def serve_viewer(test, root, status=None):
    """A port a running eftjourney viewer holds until `test` ends; its /api/status says `status` ({} by default)."""
    holder = make_server(AppContext(RaidStore(root / "data"), {"maps": []}, None, root / "cache", lambda: status or {}), port=0)
    threading.Thread(target=holder.serve_forever, daemon=True).start()
    test.addCleanup(holder.server_close)
    test.addCleanup(holder.shutdown)
    return holder.server_address[1]


class FakeRuntime:
    """The parts of cli.Runtime that cmd_run and _serve use, on a temp folder."""

    def __init__(self, root, reports=()):
        self.root = Path(root)
        self.reports = list(reports)
        self.scans = 0
        self.report = None
        self.last_scan = None
        self.watching = False
        self.error = None

    def context(self):
        return AppContext(RaidStore(self.root / "data"), {"maps": []}, None, self.root / "cache")

    def scan(self, quiet=False):
        self.scans += 1
        if not self.reports:
            raise RuntimeError("boom")  # escapes, unlike the errors Runtime.scan keeps
        self.report = self.reports.pop(0)
        self.last_scan = "12:00:00"
        return self.report


class ParserTest(unittest.TestCase):
    def error(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(argv)
        return err.getvalue()

    def test_interval_is_at_least_a_second(self):
        # -1 killed the watcher thread (time.sleep ValueError), 0 made it spin
        for bad in ("-1", "0", "0.5", "nan", "inf", "soon"):
            self.assertIn("감시 주기", self.error(["run", "--interval", bad]), bad)
        self.assertEqual(cli.build_parser().parse_args(["run", "--interval", "1"]).interval, 1.0)
        self.assertEqual(cli.build_parser().parse_args(["run"]).interval, 5.0)

    def test_interval_has_an_upper_bound(self):
        # 1e10 passed, and then time.sleep raised OverflowError in the watcher
        for bad in ("1e10", "3601", "1e300"):
            self.assertIn("감시 주기", self.error(["run", "--interval", bad]), bad)
        self.assertEqual(cli.build_parser().parse_args(["run", "--interval", "3600"]).interval, 3600.0)

    def test_port_range(self):
        for cmd in ("run", "serve"):
            for bad in ("-1", "65536", "http", "80.5"):
                self.assertIn("포트", self.error([cmd, "--port", bad]), bad)
            self.assertEqual(cli.build_parser().parse_args([cmd, "--port", "0"]).port, 0)
            self.assertEqual(cli.build_parser().parse_args([cmd, "--port", "65535"]).port, 65535)
            self.assertEqual(cli.build_parser().parse_args([cmd]).port, cli.DEFAULT_PORT)

    def test_help_renders(self):
        def help_text(argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                cli.build_parser().parse_args(argv + ["--help"])
            return out.getvalue()

        text = " ".join(help_text([]).split())  # as one line: argparse wraps it to the terminal's width
        self.assertIn("%LOCALAPPDATA%", text)  # argparse wants "%%" in help strings
        self.assertIn("pip install -e .", text)  # an editable install keeps the project's data folder
        for cmd in ("run", "serve"):
            self.assertIn(str(cli.DEFAULT_PORT), help_text([cmd]))


class RuntimeTest(unittest.TestCase):
    def runtime(self, tmp, logs=None, env=None, cmd="run", data=None):
        args = argparse.Namespace(cmd=cmd, data=str(data or Path(tmp) / "data"), logs=logs, screenshots=str(Path(tmp) / "shots"))
        with mock.patch.dict("os.environ", env or {}, clear=False), \
                mock.patch.object(cli.paths, "find_logs_dir", return_value=None):
            if not env:
                cli.os.environ.pop("EFT_LOGS_DIR", None)
            return cli.Runtime(args)

    def test_logs_folder_is_looked_for_again_unless_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNotNone(self.runtime(tmp).builder.find_logs)  # auto-detected: keep looking
            self.assertIsNone(self.runtime(tmp, logs=tmp).builder.find_logs)  # the user's choice stays
            self.assertIsNone(self.runtime(tmp, env={"EFT_LOGS_DIR": tmp}).builder.find_logs)

    def test_status_has_the_last_scans_warnings(self):
        # Only the first scan printed them: in watch mode nothing told why raids stopped updating
        with tempfile.TemporaryDirectory() as tmp:
            rt = self.runtime(tmp, logs=tmp)
            self.assertEqual(rt.status()["warnings"], [])
            many = [f"경고 {i}" for i in range(15)]
            with mock.patch.object(rt.builder, "scan", return_value=ScanReport(warnings=many)):
                rt.scan()
            self.assertEqual(json.loads(json.dumps(rt.status()))["warnings"], many[:cli.MAX_WARNINGS])
            with mock.patch.object(rt.builder, "scan", return_value=ScanReport()):
                rt.scan()
            self.assertEqual(rt.status()["warnings"], [])

    def test_status_lists_each_warning_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = self.runtime(tmp, logs=tmp)
            with mock.patch.object(rt.builder, "scan", return_value=ScanReport(warnings=["A", "B", "A"])):
                rt.scan()
            self.assertEqual(rt.status()["warnings"], ["A", "B"])

    def test_status_names_the_data_folder(self):
        # a copy that finds its port taken tells by it whether that viewer shows its own data folder
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            status = json.loads(json.dumps(self.runtime(tmp, logs=tmp).status()))
            self.assertEqual(status["dataDir"], str(data))
            self.assertTrue(cli._same_data_dir(status, data))
            self.assertTrue(cli._same_data_dir({"dataDir": data.as_posix() + "/"}, data))  # the folder, not the text
            self.assertFalse(cli._same_data_dir(status, Path(tmp) / "other"))
            for silent in ({}, {"dataDir": None}, {"dataDir": ""}, {"dataDir": 1}):
                self.assertIsNone(cli._same_data_dir(silent, data), silent)

    def test_status_says_run_or_serve(self):
        # a serve takes no lock: a copy that finds the data folder locked must not name it as the holder
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.runtime(tmp, logs=tmp).status()["mode"], "run")
            self.assertEqual(self.runtime(tmp, logs=tmp, cmd="serve").status()["mode"], "serve")

    def test_same_data_folder_by_its_id(self):
        # other text for the same folder (a junction, an 8.3 short name, a \\?\ prefix, a subst drive)
        # counted as another data folder
        with tempfile.TemporaryDirectory() as tmp:
            data, other = Path(tmp) / "data", Path(tmp) / "other"
            self.assertIsNone(self.runtime(tmp, logs=tmp).status()["dataDirId"])  # no folder there yet
            data.mkdir()
            other.mkdir()
            ours = json.loads(json.dumps(self.runtime(tmp, logs=tmp).status()))["dataDirId"]
            self.assertEqual(ours, [os.stat(data).st_dev, os.stat(data).st_ino])
            theirs = str(Path(tmp) / "link")  # never opened, so it need not be there
            self.assertTrue(cli._same_data_dir({"dataDir": theirs, "dataDirId": ours}, data))
            self.assertFalse(cli._same_data_dir({"dataDir": theirs, "dataDirId": ours}, other))
            self.assertFalse(cli._same_data_dir({"dataDir": theirs, "dataDirId": ours}, Path(tmp) / "missing"))  # no id of ours
            self.assertIsNone(cli._same_data_dir({"dataDirId": ours}, data))  # names no folder: says nothing
            with mock.patch.object(cli, "_folder_id", return_value=[1, 1]):  # what else JSON could pass for it
                for bad in ([True, True], [1.0, 1.0], [1, 1, 0], [1], "1,1", {"1": 1}, None):
                    self.assertFalse(cli._same_data_dir({"dataDir": theirs, "dataDirId": bad}, data), bad)
            # a path in a reply is never opened: a UNC path there would have us connect to that host
            unc = {"dataDir": r"\\192.0.2.1\share\data", "dataDirId": [1, 2]}
            with mock.patch.object(cli.os, "stat", side_effect=lambda p, real=os.stat: real(p) if p == data else self.fail(p)):
                self.assertFalse(cli._same_data_dir(unc, data))

    def test_same_data_folder_through_a_junction(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, link, other = Path(tmp) / "data", Path(tmp) / "link", Path(tmp) / "other"
            data.mkdir()
            other.mkdir()
            if sys.platform != "win32" or subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(data)],
                                                         capture_output=True).returncode != 0:
                self.skipTest("no junction here")
            status = json.loads(json.dumps(self.runtime(tmp, logs=tmp, data=link).status()))  # a copy given --data <link>
            self.assertEqual(status["dataDir"], str(link))
            self.assertTrue(cli._same_data_dir(status, data))
            self.assertFalse(cli._same_data_dir(status, other))


class DoctorTest(unittest.TestCase):
    def test_unreadable_screenshots_folder(self):
        # scan_dir returns None for a folder that is there but cannot be listed
        with tempfile.TemporaryDirectory() as tmp:
            shots = Path(tmp) / "shots"
            shots.mkdir()
            rt = SimpleNamespace(data_dir=Path(tmp) / "data", logs_dir=None, shots_dir=shots, maps={"maps": []},
                                 store=RaidStore(Path(tmp) / "data"))
            out = io.StringIO()
            with mock.patch("eftjourney.screenshots.scan_dir", return_value=None), contextlib.redirect_stdout(out):
                self.assertEqual(cli.cmd_doctor(rt, argparse.Namespace()), 0)
            self.assertIn("위치 스크린샷: 폴더를 읽을 수 없습니다", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.cmd_doctor(rt, argparse.Namespace()), 0)
            self.assertIn("위치 스크린샷: 0장", out.getvalue())

    def test_unreadable_logs_folder(self):
        # A Logs folder that is there but cannot be listed made doctor end in a traceback
        class Unlistable(type(Path())):
            def iterdir(self):
                raise PermissionError(13, "Access is denied")

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "log_2026.10.27_22-40-00_1.0.0.1").mkdir()
            rt = SimpleNamespace(data_dir=Path(tmp) / "data", logs_dir=Unlistable(tmp), shots_dir=Path(tmp) / "shots",
                                 maps={"maps": []}, store=RaidStore(Path(tmp) / "data"))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.cmd_doctor(rt, argparse.Namespace()), 0)
            self.assertIn("로그 세션    : 폴더를 읽을 수 없습니다", out.getvalue())
            rt.logs_dir = Path(tmp)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.cmd_doctor(rt, argparse.Namespace()), 0)
            self.assertIn("로그 세션    : 1개", out.getvalue())


class ScanSummaryTest(unittest.TestCase):
    @staticmethod
    def lines(report):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._print_report(report)
        return out.getvalue().splitlines()

    def test_positions_are_not_counted_as_screenshots(self):
        # 93 screenshots deleted: scan said "위치 스크린샷 93장" (their journaled positions), doctor 0
        self.assertEqual(self.lines(ScanReport(sessions=2, raids_total=3, samples=93, files=0, orphan_samples=5, outside_map=1)),
                         ["  로그 세션 2개, 레이드 3개, 기록된 위치 93개 (폴더의 위치 스크린샷 0장)",
                          "  레이드 시간 밖에서 찍힌 위치 5개는 쓰지 않았습니다 (메뉴·은신처 등).",
                          "  레이드 맵 범위를 크게 벗어난 위치 1개는 제외했습니다."])
        # a folder that could not be listed has no count of its own
        self.assertEqual(self.lines(ScanReport(sessions=2, raids_total=3, samples=93)),
                         ["  로그 세션 2개, 레이드 3개, 기록된 위치 93개"])


class RunTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        probe = mock.patch.object(cli, "PROBE_TIMEOUT", 0.2)  # the silent holders below never answer
        probe.start()
        self.addCleanup(probe.stop)

    def foreign_port(self):
        """A port another program holds; it never answers."""
        holder = socket.socket()
        self.addCleanup(holder.close)
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        return holder.getsockname()[1]

    def other_web_server_port(self):
        """A port some other web server holds."""
        class Other(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        holder = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Other)
        threading.Thread(target=holder.serve_forever, daemon=True).start()
        self.addCleanup(holder.server_close)
        self.addCleanup(holder.shutdown)
        return holder.server_address[1]

    def viewer_port(self, status=None):
        """A port a running eftjourney holds; its /api/status says `status` (nothing by default)."""
        return serve_viewer(self, self.root, status)

    def folders(self):
        # spaces, and a trailing backslash on Windows, must survive the printed command
        return ["--data", str(self.root / "my data"), "--logs", str(self.root / "game logs"),
                "--screenshots", str(self.root / "Screen shots") + os.sep]

    def hint(self, out, nth=0):
        """The nth suggested command, parsed the way `py -m eftjourney` would parse it."""
        commands = re.findall(r"(py -m eftjourney .+)$", out, re.M)
        self.assertGreater(len(commands), nth, out)
        argv = split_command(commands[nth])
        self.assertEqual(argv[:3], ["py", "-m", "eftjourney"])
        return cli.build_parser().parse_args(argv[3:])

    def demo_hint(self, folders):
        """What demo prints after making its raid, given these folder options."""
        out = io.StringIO()
        with mock.patch("eftjourney.demo.create_demo", return_value=RAID_ID), contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_demo(SimpleNamespace(store=None, maps=None), cli.build_parser().parse_args([*folders, "demo"])), 0)
        return out.getvalue()

    @staticmethod
    def new_watchers(before):
        return [t for t in threading.enumerate() if t.name == "watcher" and t not in before]

    def test_taken_port_stops_run_before_it_scans(self):
        port = self.foreign_port()
        rt = FakeRuntime(self.root, [None])
        before = set(threading.enumerate())
        args = cli.build_parser().parse_args([*self.folders(), "run", "--port", str(port), "--interval", "2.5", "--no-open"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.cmd_run(rt, args)
        self.assertEqual(code, 1)
        self.assertEqual(rt.scans, 0)  # a second copy must not write data
        self.assertFalse(rt.watching)
        self.assertEqual(self.new_watchers(before), [])
        # the hint is the user's own command on another port, and works as typed
        hint = self.hint(out.getvalue())
        self.assertEqual((hint.cmd, hint.data, hint.logs, hint.screenshots), ("run", args.data, args.logs, args.screenshots))
        self.assertEqual((hint.interval, hint.no_open), (2.5, True))
        self.assertNotEqual(hint.port, port)

    def test_serve_hint_on_a_taken_port(self):
        port = self.foreign_port()
        args = cli.build_parser().parse_args([*self.folders(), "serve", "--port", str(port), "--open"])
        out = io.StringIO()
        with mock.patch.object(cli.webbrowser, "open") as browser, contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_serve(FakeRuntime(self.root), args), 1)
        browser.assert_not_called()
        hint = self.hint(out.getvalue())
        self.assertEqual((hint.cmd, hint.data, hint.logs, hint.screenshots, hint.open), ("serve", args.data, args.logs, args.screenshots, True))
        self.assertNotEqual(hint.port, port)

    def test_bare_command_hint_works_as_typed(self):
        # "py -m eftjourney --data ..." means run: the hint has to say "run" and keep the folders
        port = self.foreign_port()
        folders = self.folders()
        out = io.StringIO()
        with mock.patch.object(cli, "DEFAULT_PORT", port), mock.patch.object(cli, "Runtime", lambda args: FakeRuntime(self.root)), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(folders), 1)
        hint = self.hint(out.getvalue())
        self.assertEqual((hint.cmd, hint.data, hint.logs, hint.screenshots), ("run", folders[1], folders[3], folders[5]))
        self.assertNotEqual(hint.port, port)

    def test_run_on_a_port_eftjourney_holds(self):
        # Offering another port there started a second watcher on the same data folder
        stopped = {"mode": "run", "watching": False, "dataDir": str(self.root / "my data"),  # not a serve: it keeps the lock
                   "error": "감시가 멈췄습니다 (RuntimeError: boom). 프로그램을 다시 실행해 주세요."}
        for status in (None, stopped):
            port = self.viewer_port(status)
            rt = FakeRuntime(self.root, [None])
            before = set(threading.enumerate())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.cmd_run(rt, cli.build_parser().parse_args([*self.folders(), "run", "--port", str(port), "--no-open"]))
            self.assertEqual(code, 1)
            self.assertEqual(rt.scans, 0)
            self.assertEqual(self.new_watchers(before), [])
            self.assertIn(f"eftjourney가 이미 실행 중입니다: http://127.0.0.1:{port}/", out.getvalue())
            self.assertNotIn("--port", out.getvalue())

    def test_run_on_a_port_a_serve_of_its_data_folder_holds(self):
        # a serve only shows the raids: run said the two would overwrite them, and offered no way forward
        for status in ({"mode": "serve", "watching": False, "dataDir": str(self.root / "my data")},
                       {"watching": False, "dataDir": str(self.root / "my data")}):  # an older copy: no mode, not watching
            port = self.viewer_port(status)
            rt = FakeRuntime(self.root, [None])
            before = set(threading.enumerate())
            args = cli.build_parser().parse_args([*self.folders(), "run", "--port", str(port), "--interval", "2.5", "--no-open"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.cmd_run(rt, args), 1)
            self.assertEqual(rt.scans, 0)
            self.assertEqual(self.new_watchers(before), [])
            self.assertIn(f"eftjourney 뷰어(serve: 보기만 하고 감시는 하지 않음)가 열려 있습니다: http://127.0.0.1:{port}/", out.getvalue())
            # close it and run again, or run on another port ...
            hint = self.hint(out.getvalue())
            self.assertEqual((hint.cmd, hint.data, hint.logs, hint.screenshots), ("run", args.data, args.logs, args.screenshots))
            self.assertEqual((hint.interval, hint.no_open), (2.5, True))
            self.assertNotEqual(hint.port, port)
            # ... or keep that viewer and only update the raids
            scan = self.hint(out.getvalue(), 1)
            self.assertEqual((scan.cmd, scan.data, scan.logs, scan.screenshots), ("scan", args.data, args.logs, args.screenshots))

    def test_run_on_a_port_eftjourney_of_another_folder_holds(self):
        # that copy works on other raids: this folder can run next to it on another port
        theirs = str(self.root / "other data")
        port = self.viewer_port({"watching": True, "dataDir": theirs})
        rt = FakeRuntime(self.root, [None])
        before = set(threading.enumerate())
        args = cli.build_parser().parse_args([*self.folders(), "run", "--port", str(port), "--no-open"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_run(rt, args), 1)
        self.assertEqual(rt.scans, 0)
        self.assertEqual(self.new_watchers(before), [])
        self.assertIn(f"http://127.0.0.1:{port}/", out.getvalue())
        self.assertIn(theirs, out.getvalue())
        hint = self.hint(out.getvalue())
        self.assertEqual((hint.cmd, hint.data, hint.logs, hint.screenshots, hint.no_open),
                         ("run", args.data, args.logs, args.screenshots, True))
        self.assertNotEqual(hint.port, port)

    def test_serve_on_a_port_eftjourney_of_another_folder_holds(self):
        # "already running" sent the user to a viewer of other raids, and opened it
        theirs = str(self.root / "other data")
        port = self.viewer_port({"watching": False, "dataDir": theirs})
        args = cli.build_parser().parse_args([*self.folders(), "serve", "--port", str(port), "--open"])
        out = io.StringIO()
        with mock.patch.object(cli.webbrowser, "open") as browser, contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_serve(FakeRuntime(self.root), args), 1)
        browser.assert_not_called()
        self.assertIn(theirs, out.getvalue())
        hint = self.hint(out.getvalue())
        self.assertEqual((hint.cmd, hint.data, hint.logs, hint.screenshots, hint.open),
                         ("serve", args.data, args.logs, args.screenshots, True))
        self.assertNotEqual(hint.port, port)

    def test_serve_on_a_port_eftjourney_holds_uses_that_viewer(self):
        # one of this data folder, or one that does not say which (an older copy)
        for status in ({}, {"watching": True, "dataDir": str(self.root / "my data")}):
            port = self.viewer_port(status)
            for argv, opened in ((["serve", "--port", str(port), "--open"], True), (["serve", "--port", str(port)], False)):
                out = io.StringIO()
                with mock.patch.object(cli.webbrowser, "open") as browser, mock.patch.object(cli, "_serve") as serve, \
                        contextlib.redirect_stdout(out):
                    args = cli.build_parser().parse_args([*self.folders(), *argv])
                    self.assertEqual(cli.cmd_serve(FakeRuntime(self.root), args), 0)
                serve.assert_not_called()  # no second server
                self.assertEqual(browser.call_args_list, [mock.call(f"http://127.0.0.1:{port}/")] if opened else [])
                self.assertIn(f"http://127.0.0.1:{port}/", out.getvalue())
                self.assertNotIn("--port", out.getvalue())

    def test_probe_tells_eftjourney_from_other_programs(self):
        self.assertTrue(cli._running_viewer(self.viewer_port()))
        self.assertFalse(cli._running_viewer(self.foreign_port()))  # takes the connection, never answers
        self.assertFalse(cli._running_viewer(self.other_web_server_port()))
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        self.assertFalse(cli._running_viewer(port))  # nothing there at all
        # and what a viewer says about itself
        self.assertEqual(cli._viewer_status(self.viewer_port({"watching": False})), {"watching": False})
        self.assertEqual(cli._viewer_status(self.viewer_port(["not", "an", "object"])), {})
        self.assertIsNone(cli._viewer_status(self.other_web_server_port()))  # its "{}" is no viewer's

    def test_demo_hint_keeps_the_data_folder(self):
        args = cli.build_parser().parse_args([*self.folders(), "demo"])
        out = io.StringIO()
        with mock.patch("eftjourney.demo.create_demo", return_value=RAID_ID), contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_demo(SimpleNamespace(store=None, maps=None), args), 0)
        hint = self.hint(out.getvalue())
        self.assertEqual((hint.cmd, hint.data, hint.open), ("serve", args.data, True))

    def test_hint_quotes_folders_a_shell_would_split(self):
        # only folders with spaces were quoted: pasted into PowerShell or cmd, one with ; ( ) ' & ^ { }
        # became another folder or another command
        for special in ";()'&^{}":
            folders = ["--data", str(self.root / f"my{special}data"), "--logs", str(self.root / f"game{special}logs"),
                       "--screenshots", str(self.root / f"shots{special}") + os.sep]
            out = self.demo_hint(folders)
            self.assertIn(f'--data "{folders[1]}"', out)
            hint = self.hint(out)
            self.assertEqual((hint.cmd, hint.data, hint.logs, hint.screenshots), ("serve", folders[1], folders[3], folders[5]))

    def test_hint_keeps_folders_that_start_with_a_dash(self):
        # a relative folder like "-mydata" is only a value as --data=-mydata: printed as "--data -mydata",
        # pasted back it stopped argparse with "argument --data: expected one argument"
        for data in ("-mydata", "-my data"):
            folders = [f"--data={data}", "--logs", str(self.root / "game logs"), "--screenshots=-shots"]
            port = self.viewer_port({"mode": "serve", "watching": False, "dataDir": os.path.abspath(data)})
            args = cli.build_parser().parse_args([*folders, "run", "--port", str(port), "--no-open"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.cmd_run(FakeRuntime(self.root), args), 1)
            out = out.getvalue() + self.demo_hint(folders)
            self.assertIn(f'"--data={data}" --logs "{folders[2]}" "--screenshots=-shots"', out)  # any other folder as before
            # another port, a scan next to that serve, and the demo's serve
            for nth, cmd in enumerate(("run", "scan", "serve")):
                hint = self.hint(out, nth)
                self.assertEqual((hint.cmd, hint.data, hint.logs, hint.screenshots), (cmd, data, folders[2], "-shots"))

    def test_no_command_for_folders_no_quotes_keep(self):
        # PowerShell expands $ and ` even in double quotes, cmd expands %: a printed command would name
        # another folder. The user's own command has them as their shell wants: say what to add to it.
        for special in "$`%":
            folders = ["--data", str(self.root / f"my{special}data")]
            port = self.foreign_port()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.cmd_run(FakeRuntime(self.root), cli.build_parser().parse_args([*folders, "run", "--port", str(port)])), 1)
            out = out.getvalue() + self.demo_hint(folders)
            self.assertNotIn("py -m eftjourney", out)
            self.assertRegex(out, r"다른 포트로 실행해 보세요:  지금 명령에 --port \d+ 을\(를\) 붙여 다시 실행하세요\.")
            self.assertIn("뷰어로 보려면 같은 --data 로 serve --open 을 실행하세요.", out)
        # the bare command has no run for --port to follow
        folders = ["--data", str(self.root / "my$data")]
        port = self.foreign_port()
        out = io.StringIO()
        with mock.patch.object(cli, "DEFAULT_PORT", port), mock.patch.object(cli, "Runtime", lambda args: FakeRuntime(self.root)), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(folders), 1)
        self.assertNotIn("py -m eftjourney", out.getvalue())
        self.assertRegex(out.getvalue(), r"지금 명령에 run --port \d+ 을\(를\) 붙여 다시 실행하세요\.")
        # next to a serve of this folder: another port, or a scan
        port = self.viewer_port({"mode": "serve", "watching": False, "dataDir": folders[1]})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_run(FakeRuntime(self.root), cli.build_parser().parse_args([*folders, "run", "--port", str(port)])), 1)
        self.assertNotIn("py -m eftjourney", out.getvalue())
        self.assertRegex(out.getvalue(), r"다른 포트로 실행하세요:  지금 명령에 --port \d+ 을\(를\) 붙여 다시 실행하세요\.")
        self.assertIn("기록만 갱신하려면 같은 폴더 옵션으로 scan을 실행한 뒤 페이지를 새로고침하세요.", out.getvalue())

    def test_port_zero_prints_the_bound_port(self):
        servers = []

        def no_loop_make_server(ctx, port):
            server = make_server(ctx, port)
            server.serve_forever = lambda poll_interval=0.5: None
            servers.append(server)
            return server

        out = io.StringIO()
        with mock.patch.object(cli, "make_server", no_loop_make_server), contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_serve(FakeRuntime(self.root), cli.build_parser().parse_args(["serve", "--port", "0"])), 0)
        port = servers[0].server_address[1]
        self.assertNotEqual(port, 0)
        self.assertIn(f"http://127.0.0.1:{port}/", out.getvalue())

    def test_watcher_links_the_real_port_and_reports_its_end(self):
        report = ScanReport(written=[RAID_ID])
        rt = FakeRuntime(self.root, [None, report])  # the third scan raises
        before = set(threading.enumerate())
        seen = {}

        def fake_serve(server, open_browser):
            seen["bound"] = server.server_address[1]
            watchers = self.new_watchers(before)
            for t in watchers:
                t.join(5)
            seen["watchers"] = len(watchers)
            seen["alive"] = any(t.is_alive() for t in watchers)
            server.server_close()
            return 0

        out = io.StringIO()
        with mock.patch.object(cli, "_serve", fake_serve), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_run(rt, argparse.Namespace(port=0, interval=0.01, no_open=True)), 0)
        self.assertNotEqual(seen["bound"], 0)
        self.assertIn(f"http://127.0.0.1:{seen['bound']}/#/raid/{RAID_ID}", out.getvalue())
        # an exception Runtime.scan did not keep ends the watcher; /api/status must say so
        self.assertEqual(seen["watchers"], 1)
        self.assertFalse(seen["alive"])
        self.assertFalse(rt.watching)
        self.assertIn("RuntimeError", rt.error)

    def test_watcher_prints_each_warning_once(self):
        # Only the first scan printed its warnings: in watch mode nothing told why raids stopped updating
        many = [f"D{i}" for i in range(12)]
        reports = [ScanReport(warnings=["A"]), ScanReport(warnings=["A", "B"]), None,  # a failed scan
                   ScanReport(warnings=["B"]), ScanReport(warnings=["A", "B", "C", "C"]),
                   ScanReport(warnings=many)]  # then the scan raises
        rt = FakeRuntime(self.root, reports)
        before = set(threading.enumerate())

        def fake_serve(server, open_browser):
            for t in self.new_watchers(before):
                t.join(5)
            server.server_close()
            return 0

        out = io.StringIO()
        with mock.patch.object(cli, "_serve", fake_serve), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_run(rt, argparse.Namespace(port=0, interval=0.01, no_open=True)), 0)
        self.assertEqual(rt.scans, len(reports) + 1)
        # still there: not again; gone and back (a failed scan in between is no "gone"): again;
        # at most 10 new ones per scan, like the first scan's list
        lines = [line for line in out.getvalue().splitlines() if "주의" in line]
        self.assertEqual(lines, ["  주의: A", "[12:00:00] 주의: B", "[12:00:00] 주의: A", "[12:00:00] 주의: C",
                                 *(f"[12:00:00] 주의: {w}" for w in many[:10])])

    def test_watcher_prints_each_scan_error_once(self):
        # an error Runtime.scan kept only reached /api/status: the watching console said nothing
        rt = FakeRuntime(self.root, [ScanReport(), None, None, ScanReport(), None, None])  # then the scan raises
        errors = iter(["ValueError: a", "ValueError: a", "ValueError: b", "ValueError: c"])
        plain = rt.scan

        def scan(quiet=False):
            report = plain(quiet)
            rt.error = None if report is not None else next(errors)
            return report

        rt.scan = scan
        before = set(threading.enumerate())

        def fake_serve(server, open_browser):
            for t in self.new_watchers(before):
                t.join(5)
            server.server_close()
            return 0

        out = io.StringIO()
        with mock.patch.object(cli, "_serve", fake_serve), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_run(rt, argparse.Namespace(port=0, interval=0.01, no_open=True)), 0)
        lines = [line.split("] ", 1)[1] for line in out.getvalue().splitlines() if "스캔 오류" in line]
        self.assertEqual(lines, ["스캔 오류: ValueError: a", "스캔 오류: ValueError: b", "스캔 오류: ValueError: c"])

    def test_watcher_prints_a_write_error_once_whatever_its_temp_file(self):
        # each try of an atomic write has a new temp file, so a file another program kept open
        # printed the same error again on every scan
        held = r"PermissionError: [WinError 5] Access is denied: 'E:\\x\\data\\cache\\{}' -> 'E:\\x\\data\\cache\\{}'"
        errors = [held.format("tmpk3l2j1.tmp", "logs.json"),  # the first scan's, printed with its traceback
                  held.format("tmp8x_q0wz.tmp", "logs.json"),  # the same: not again
                  held.format("state.jsonq1w2e3.tmp", "state.json"),  # another file: printed
                  held.format("state.json9z8y7x.tmp", "state.json"),  # the same: not again
                  held.format("tmp0a1b2c.tmp", "logs.json")]  # the first one back: printed again
        rt = FakeRuntime(self.root, [None] * len(errors))  # then the scan raises
        pending = iter(errors)
        plain = rt.scan

        def scan(quiet=False):
            report = plain(quiet)
            rt.error = next(pending)
            return report

        rt.scan = scan
        before = set(threading.enumerate())

        def fake_serve(server, open_browser):
            for t in self.new_watchers(before):
                t.join(5)
            server.server_close()
            return 0

        out = io.StringIO()
        with mock.patch.object(cli, "_serve", fake_serve), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_run(rt, argparse.Namespace(port=0, interval=0.01, no_open=True)), 0)
        lines = [line.split("] ", 1)[1] for line in out.getvalue().splitlines() if "스캔 오류" in line]
        self.assertEqual(lines, [f"스캔 오류: {errors[2]}", f"스캔 오류: {errors[4]}"])  # with their real temp names


class DataLockTest(unittest.TestCase):
    """Two copies on one data folder overwrote each other's raids: run and scan hold an OS lock."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name in ("logs", "shots"):
            (self.root / name).mkdir()
        self.data = self.root / "data"
        self.folders = ["--data", str(self.data), "--logs", str(self.root / "logs"), "--screenshots", str(self.root / "shots")]
        # the lock message probes the default port for a viewer: never the user's own on 8765
        for name, value in (("DEFAULT_PORT", 0), ("PROBE_TIMEOUT", 0.2)):
            patcher = mock.patch.object(cli, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def try_lock(self):
        """Another process tries the data folder's lock: (it, "locked" or "busy"). It keeps a lock until let go."""
        path = self.data / "cache" / "eftjourney.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen([sys.executable, "-B", "-c", TRY_LOCK, str(path)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.let_go, proc)
        return proc, proc.stdout.readline().strip()

    @staticmethod
    def let_go(proc):
        proc.stdin.close()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()

    def test_another_process_on_the_folder_stops_run_and_scan(self):
        holder, state = self.try_lock()
        self.assertEqual(state, "locked")
        built, bound = [], []
        before = set(threading.enumerate())
        with mock.patch.object(cli, "Runtime", lambda args: built.append(args) or FakeRuntime(self.root, [ScanReport()])), \
                mock.patch.object(cli, "make_server", side_effect=lambda ctx, port: bound.append(port)), \
                mock.patch.object(cli, "DEFAULT_PORT", 0):
            for argv in ([*self.folders, "scan"], [*self.folders, "run", "--port", "0", "--no-open"], self.folders):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(cli.main(argv), 1, argv)
                self.assertIn("다른 eftjourney 창(run 또는 진행 중인 scan)이 이 데이터 폴더를 잠그고 있습니다", out.getvalue())
                self.assertIn(str(self.data), out.getvalue())
        self.assertEqual((built, bound), ([], []))  # nothing was read, scanned or bound
        self.assertEqual(RunTest.new_watchers(before), [])
        # serve, doctor and demo never rewrite raids: they work next to it
        out = io.StringIO()
        with mock.patch.object(cli, "_serve", return_value=0) as serve, \
                mock.patch("eftjourney.demo.create_demo", return_value=RAID_ID), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main([*self.folders, "serve", "--port", "0"]), 0)
            serve.call_args.args[0].server_close()
            self.assertEqual(cli.main([*self.folders, "doctor"]), 0)
            self.assertEqual(cli.main([*self.folders, "demo"]), 0)
        self.assertNotIn("다른 eftjourney", out.getvalue())
        # the lock goes with the process that held it
        self.let_go(holder)
        rt = FakeRuntime(self.root, [ScanReport()])
        with mock.patch.object(cli, "Runtime", lambda args: rt), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([*self.folders, "scan"]), 0)
        self.assertEqual(rt.scans, 1)

    def test_lock_message_names_a_viewer_on_the_port(self):
        self.assertEqual(self.try_lock()[1], "locked")
        for status in ({}, {"watching": True, "dataDir": str(self.data)},  # one that does not say which folder, or this one
                       {"mode": "run", "watching": True, "error": "ValueError: x", "dataDir": str(self.data)}):  # a scan failed
            port = serve_viewer(self, self.root, status)
            out = io.StringIO()
            with mock.patch.object(cli, "Runtime", lambda args: self.fail("started anyway")), contextlib.redirect_stdout(out):
                self.assertEqual(cli.main([*self.folders, "run", "--port", str(port), "--no-open"]), 1)
            self.assertIn(f"eftjourney 뷰어가 열려 있습니다: http://127.0.0.1:{port}/", out.getvalue())

    def test_lock_message_does_not_name_a_serve(self):
        # serve of the folder on one port, run of it on the next (as the port hint says): scan and run then
        # named the serve as the copy holding the lock, and closing it changed nothing
        self.assertEqual(self.try_lock()[1], "locked")
        for status in ({"mode": "serve", "watching": False, "dataDir": str(self.data)},
                       {"watching": False, "dataDir": str(self.data)}):  # an older copy: no mode, not watching, no error
            port = serve_viewer(self, self.root, status)
            for argv in ([*self.folders, "run", "--port", str(port), "--no-open"], [*self.folders, "scan"]):
                out = io.StringIO()
                with mock.patch.object(cli, "DEFAULT_PORT", port), contextlib.redirect_stdout(out), \
                        mock.patch.object(cli, "Runtime", lambda args: self.fail("started anyway")):
                    self.assertEqual(cli.main(argv), 1)
                self.assertIn(f"이 포트({port})의 eftjourney 뷰어는 보기만 하는 serve라 이 데이터 폴더를 잠근 창이 아닙니다.", out.getvalue())
                self.assertIn("다른 포트에서 감시 중인 run이나 진행 중인 scan이 잠그고 있습니다.", out.getvalue())
                self.assertNotIn("뷰어가 열려 있습니다", out.getvalue())

    def test_lock_message_says_a_run_stopped_watching(self):
        # its watcher ended, but it still serves and holds the lock until that window is closed
        self.assertEqual(self.try_lock()[1], "locked")
        error = "감시가 멈췄습니다 (RuntimeError: boom). 프로그램을 다시 실행해 주세요."
        for status in ({"mode": "run", "watching": False, "error": error, "dataDir": str(self.data)},
                       {"watching": False, "error": error, "dataDir": str(self.data)}):  # an older copy's run
            port = serve_viewer(self, self.root, status)
            out = io.StringIO()
            with mock.patch.object(cli, "DEFAULT_PORT", port), contextlib.redirect_stdout(out), \
                    mock.patch.object(cli, "Runtime", lambda args: self.fail("started anyway")):
                self.assertEqual(cli.main([*self.folders, "scan"]), 1)
            # the first line said that copy watched this folder, and the next that it stopped watching
            self.assertIn(f"다른 eftjourney 창(run 또는 진행 중인 scan)이 이 데이터 폴더를 잠그고 있습니다: {self.data}", out.getvalue())
            self.assertIn(f"이 포트에서 eftjourney가 열려 있지만 감시가 멈췄습니다: http://127.0.0.1:{port}/", out.getvalue())
            self.assertIn("그 창을 닫고(Ctrl+C) 다시 실행하세요.", out.getvalue())
            self.assertNotIn("보기만 하는 serve", out.getvalue())

    def test_lock_message_leaves_out_a_viewer_of_another_folder(self):
        # that one is not the copy holding this folder's lock
        self.assertEqual(self.try_lock()[1], "locked")
        port = serve_viewer(self, self.root, {"watching": True, "dataDir": str(self.root / "other data")})
        out = io.StringIO()
        with mock.patch.object(cli, "Runtime", lambda args: self.fail("started anyway")), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main([*self.folders, "run", "--port", str(port), "--no-open"]), 1)
        self.assertIn("다른 eftjourney 창(run 또는 진행 중인 scan)이 이 데이터 폴더를 잠그고 있습니다", out.getvalue())
        self.assertNotIn(f"http://127.0.0.1:{port}/", out.getvalue())

    def test_scan_holds_the_lock_while_it_works(self):
        rt = FakeRuntime(self.root, [ScanReport()])
        real_scan, seen = rt.scan, []

        def scan(quiet=False):
            seen.append(self.try_lock()[1])  # another copy starting meanwhile
            return real_scan(quiet)

        rt.scan = scan
        with mock.patch.object(cli, "Runtime", lambda args: rt), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([*self.folders, "scan"]), 0)
        self.assertEqual(seen, ["busy"])
        self.assertEqual(self.try_lock()[1], "locked")  # given back once it is done

    def test_running_copy_holds_the_lock_and_a_killed_one_leaves_none(self):
        # a real "py -m eftjourney ... run", as in another console window
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.Popen([sys.executable, "-B", "-u", "-m", "eftjourney", *self.folders, "run", "--port", "0", "--no-open"],
                                cwd=PROJECT_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        timer = threading.Timer(30, proc.kill)  # one that never comes up must not hang the tests
        timer.start()
        self.addCleanup(timer.cancel)
        lines = []
        for line in proc.stdout:
            lines.append(line)
            if "뷰어:" in line:
                break
        self.assertIn("뷰어:", "".join(lines))
        self.assertEqual(self.try_lock()[1], "busy")
        out = io.StringIO()
        with mock.patch.object(cli, "Runtime", lambda args: FakeRuntime(self.root, [ScanReport()])), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main([*self.folders, "scan"]), 1)
        proc.kill()  # like closing its console window: it cleans nothing up itself
        proc.wait(10)
        self.assertEqual(self.try_lock()[1], "locked")  # the OS dropped its lock

    def test_folder_that_cannot_be_locked_still_works(self):
        # no lock possible there (here: --data names a file): say so, then work as before
        self.data.write_text("not a folder", encoding="utf-8")
        rt = FakeRuntime(self.root, [ScanReport()])
        out = io.StringIO()
        with mock.patch.object(cli, "Runtime", lambda args: rt), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main([*self.folders, "scan"]), 0)
        self.assertEqual(rt.scans, 1)
        self.assertIn("데이터 폴더를 잠그지 못했습니다", out.getvalue())


if __name__ == "__main__":
    unittest.main()
