"""Command line entry point: py -m eftjourney [run|scan|serve|demo|doctor]."""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import re
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

from . import __version__, paths
from .builder import Builder
from .mapdata import load_maps
from .server import AppContext, ViewerServer, make_server
from .store import RaidStore

DEFAULT_PORT = 8765
DEFAULT_INTERVAL = 5.0
MAX_INTERVAL = 3600.0  # time.sleep overflows on absurd values like 1e10
PROBE_TIMEOUT = 1.0  # seconds to wait for whatever holds a taken port to answer
MAX_WARNINGS = 10  # warnings of one scan shown on the console and in /api/status
LOCK_FILE = "eftjourney.lock"  # in <data>/cache; run and scan hold an OS lock on it while they work
# An atomic write's temp file (tmp<random>.tmp, <file name><random>.tmp): each try has a new name
TEMP_NAME = re.compile(r"[^\\/'\"\s]+\.tmp\b")
# A printed command quotes every value but those of letters (any script), digits and these: pasted
# into PowerShell or cmd, ; & ( ) ' ^ { } and the like end the command or start another
BARE_CHARS = frozenset("_-.:\\/")
# No quotes keep these in both: PowerShell expands $ and ` in double quotes and ends them at “ ” „
# as at ", cmd expands %. A folder with one is named in words instead.
UNQUOTABLE = frozenset('$`%"\u201c\u201d\u201e')


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


class Runtime:
    """Shared state between the watcher thread and the /api/status endpoint."""

    def __init__(self, args: argparse.Namespace):
        self.mode = args.cmd  # "run" or "serve" when it serves the viewer
        self.data_dir = paths.data_dir(args.data)
        self.shots_dir = paths.find_screenshots_dir(args.screenshots)
        self.maps = load_maps()
        self.store = RaidStore(self.data_dir)
        # Without an explicit Logs folder, keep looking while none has sessions: after a
        # fresh install the game creates its first session after this program started.
        explicit = args.logs or os.environ.get("EFT_LOGS_DIR")
        self.builder = Builder(self.store, self.maps, paths.find_logs_dir(args.logs), self.shots_dir,
                               find_logs=None if explicit else paths.find_logs_dir)
        self.version = 0
        self.watching = False
        self.report = None
        self.last_scan: str | None = None
        self.error: str | None = None
        self.lock = threading.Lock()

    @property
    def logs_dir(self):
        return self.builder.logs_dir

    def scan(self, quiet: bool = False):
        with self.lock:
            try:
                report = self.builder.scan()
            except Exception as exc:  # keep the watcher alive; surface it in the viewer
                self.error = f"{type(exc).__name__}: {exc}"
                if not quiet:
                    traceback.print_exc()
                return None
            self.error = None
            self.report = report
            self.last_scan = datetime.now().strftime("%H:%M:%S")
            if report.changed:
                self.version += 1
            return report

    def status(self) -> dict:
        report = self.report
        return {
            "version": self.version,
            "mode": self.mode,  # of the two, only run holds the data folder's lock
            "watching": self.watching,
            "inRaid": report.in_raid if report else None,
            "dataDir": os.path.abspath(self.data_dir),  # absolute: a copy started elsewhere compares it with its own
            "dataDirId": _folder_id(self.data_dir),  # and this when that is other text (a junction, a short name)
            "logsDir": str(self.logs_dir) if self.logs_dir else None,
            "screenshotsDir": str(self.shots_dir) if self.shots_dir else None,
            "screenshotsDirExists": bool(self.shots_dir and self.shots_dir.is_dir()),
            "lastScan": self.last_scan,
            "error": self.error,
            "warnings": list(dict.fromkeys(report.warnings))[:MAX_WARNINGS] if report else [],
            "raidCount": len(self.store.list_ids()),
        }

    def context(self) -> AppContext:
        return AppContext(self.store, self.maps, self.shots_dir, self.data_dir / "cache", self.status)


def _print_report(report) -> None:
    if report is None:
        return
    # the journal keeps the positions of deleted screenshots too: the files are counted on their own
    files = f" (폴더의 위치 스크린샷 {report.files}장)" if report.files is not None else ""
    print(f"  로그 세션 {report.sessions}개, 레이드 {report.raids_total}개, 기록된 위치 {report.samples}개{files}")
    if report.written:
        print(f"  새로 쓰거나 갱신한 레이드: {len(report.written)}개")
    if report.orphan_samples:
        print(f"  레이드 시간 밖에서 찍힌 위치 {report.orphan_samples}개는 쓰지 않았습니다 (메뉴·은신처 등).")
    if report.outside_map:
        print(f"  레이드 맵 범위를 크게 벗어난 위치 {report.outside_map}개는 제외했습니다.")
    for warning in report.warnings[:MAX_WARNINGS]:
        print(f"  주의: {warning}")


def _error_key(error: str | None) -> str | None:
    """A scan error without its temp file names, so that one error retried is still one."""
    return TEMP_NAME.sub("*.tmp", error) if error else error


def _lock_data(data_dir: Path) -> int | None:
    """Lock the data folder for this process: the lock file's descriptor, or None when
    another process holds it. OSError when no lock can be taken there at all.

    Two watchers on one folder overwrite each other's raids (each keeps its own shot
    journal in memory). The lock is the operating system's, not the file's: it goes
    with the process, so a crash or a closed console window leaves no stale lock.
    """
    path = data_dir / "cache" / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # its first byte, without waiting
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if isinstance(exc, (PermissionError, BlockingIOError)):  # how each OS says "taken"
            return None
        raise
    return fd


def _data_taken(cmd: str, data_dir: Path, port: int) -> int:
    """Say that another copy works on this data folder; returns the exit code."""
    print(f"다른 eftjourney 창(run 또는 진행 중인 scan)이 이 데이터 폴더를 잠그고 있습니다: {data_dir}")
    if cmd == "run":
        print("같은 데이터 폴더를 둘이 함께 감시하면 서로의 레이드를 덮어쓰므로 새로 시작하지 않았습니다.")
    else:
        print("같은 데이터 폴더를 둘이 함께 스캔하면 서로의 레이드를 덮어쓰므로 스캔하지 않았습니다.")
    # most likely that copy's viewer (not if it shows another folder); it may be on another port
    status = _viewer_status(port) if port else None
    if status is not None and _same_data_dir(status, data_dir) is not False:
        url = f"http://127.0.0.1:{port}/"
        if status.get("mode") != "serve" and status.get("watching") is False and status.get("error"):
            # a run whose watcher stopped: it still serves, and keeps the lock until it is closed
            print(f"이 포트에서 eftjourney가 열려 있지만 감시가 멈췄습니다: {url}")
            print("그 창을 닫고(Ctrl+C) 다시 실행하세요.")
            return 1
        if _only_shows(status):  # it takes no lock: closing it would change nothing
            print(f"이 포트({port})의 eftjourney 뷰어는 보기만 하는 serve라 이 데이터 폴더를 잠근 창이 아닙니다.")
            print("다른 포트에서 감시 중인 run이나 진행 중인 scan이 잠그고 있습니다.")
        else:
            print(f"이 포트에서 eftjourney 뷰어가 열려 있습니다: {url}")
    print("켜 둔 창을 닫거나(Ctrl+C) --data 로 다른 데이터 폴더를 지정하세요.")
    return 1


def _viewer_status(port: int) -> dict | None:
    """The /api/status of the eftjourney viewer holding `port` ({} when that is no JSON
    object); None when the program there is not an eftjourney viewer."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=PROBE_TIMEOUT)
    try:
        conn.request("GET", "/api/status", headers={"Host": f"127.0.0.1:{port}"})
        res = conn.getresponse()
        body = res.read(64 * 1024)  # a status is far smaller
        if not (res.getheader("Server") or "").startswith("EFTJourney/"):  # server.Handler.server_version
            return None
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()
    try:
        status = json.loads(body)
    except ValueError:
        return {}
    return status if isinstance(status, dict) else {}


def _running_viewer(port: int) -> bool:
    """Whether the program holding `port` answers like an eftjourney viewer."""
    return _viewer_status(port) is not None


def _only_shows(status: dict) -> bool:
    """Whether a viewer's status is a serve's: it only shows the raids and takes no lock.
    An older copy does not say; there one that does not watch is taken for a serve."""
    mode = status.get("mode")
    if mode in ("run", "serve"):
        return mode == "serve"
    return status.get("watching") is False


def _folder_id(path: Path) -> list[int] | None:
    """[st_dev, st_ino] of a folder, the same by every path to it (on Windows the volume serial
    and file index); None when there is none (no folder there yet)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_dev, st.st_ino] if st.st_ino else None  # 0: the file system gave none, and all would match


def _same_data_dir(status: dict, data_dir: Path) -> bool | None:
    """Whether a viewer's status says it shows `data_dir`; None when it does not say (an older copy)."""
    theirs = status.get("dataDir")
    if not (isinstance(theirs, str) and theirs):
        return None
    if os.path.normcase(os.path.abspath(theirs)) == os.path.normcase(os.path.abspath(data_dir)):
        return True
    # Other text can name this folder too: a junction, an 8.3 short name, a \\?\ prefix, a subst drive.
    # The folder ids tell. Only ours is read: a path in a reply is never opened (a UNC path there would
    # have us connect to that host).
    their_id = status.get("dataDirId")
    if isinstance(their_id, list) and len(their_id) == 2 and all(type(n) is int for n in their_id):  # no bools
        ours = _folder_id(data_dir)
        if ours is not None:
            return their_id == ours
    return False


def _quote(value: str) -> str:
    """One argument of a printed command, in double quotes unless it is all letters, digits and BARE_CHARS."""
    if value and all(ch.isalnum() or ch in BARE_CHARS for ch in value):
        return value
    # CommandLineToArgvW: backslashes before the closing quote are doubled (none inside: UNQUOTABLE has ")
    return '"' + re.sub(r"(\\+)\Z", r"\1\1", value) + '"'


def _command(args: argparse.Namespace, *rest: str) -> str | None:
    """A command line that keeps the folders the user gave, followed by `rest`, for PowerShell and
    cmd alike; None when a folder has a character no quoting keeps there (say it in words then)."""
    argv = ["py", "-m", "eftjourney"]
    for option in ("data", "logs", "screenshots"):  # global options, so before the subcommand
        value = getattr(args, option)
        if value and value.startswith("-"):  # argparse reads "-x" as an option, "--data=-x" as --data's value
            argv.append(f"--{option}={value}")
        elif value:
            argv += [f"--{option}", value]
    argv += rest
    if any(ch in UNQUOTABLE for arg in argv for ch in arg):
        return None
    return " ".join(map(_quote, argv))


def _on_port(args: argparse.Namespace, port: int) -> str:
    """The user's run or serve command again, with its options, on `port`; when it cannot be
    printed, what to add to their own."""
    rest = [args.cmd, "--port", str(port)]
    if args.cmd == "run":
        if args.interval != DEFAULT_INTERVAL:
            rest += ["--interval", f"{args.interval:g}"]
        if args.no_open:
            rest.append("--no-open")
    elif args.open:
        rest.append("--open")
    added = f"run --port {port}" if getattr(args, "bare", False) else f"--port {port}"  # bare: no run for --port
    return _command(args, *rest) or f"지금 명령에 {added} 을(를) 붙여 다시 실행하세요."


def _port_taken(args: argparse.Namespace, exc: OSError) -> int:
    """Say what holds the port and what to do instead; returns the exit code."""
    port = args.port
    url = f"http://127.0.0.1:{port}/"
    other = port + 1 if 0 < port < 65535 else DEFAULT_PORT + 1
    status = _viewer_status(port) if port else None
    if status is not None:
        if _same_data_dir(status, paths.data_dir(getattr(args, "data", None))) is False:  # only the port is in the way
            print(f"포트 {port}에서 다른 데이터 폴더({status['dataDir']})의 eftjourney가 실행 중입니다: {url}")
            print(f"이 데이터 폴더는 다른 포트로 실행하세요:  {_on_port(args, other)}")
            return 1
        if args.cmd == "serve":  # nothing to start: that copy already serves the viewer
            print(f"이미 실행 중인 eftjourney 뷰어가 있습니다: {url}")
            if args.open:
                webbrowser.open(url)
            return 0
        if _only_shows(status):  # a serve, which only shows the raids
            print(f"포트 {port}에서 eftjourney 뷰어(serve: 보기만 하고 감시는 하지 않음)가 열려 있습니다: {url}")
            print(f"감시까지 하려면 그 창을 닫고(Ctrl+C) 다시 실행하거나, 다른 포트로 실행하세요:  {_on_port(args, other)}")
            scan = _command(args, "scan")
            if scan:
                print(f"뷰어는 그대로 두고 기록만 갱신하려면 이 명령을 실행한 뒤 페이지를 새로고침하세요:  {scan}")
            else:
                print("뷰어는 그대로 두고 기록만 갱신하려면 같은 폴더 옵션으로 scan을 실행한 뒤 페이지를 새로고침하세요.")
            return 1
        # a run, also one whose watcher stopped: it keeps its port and the data folder's lock
        print(f"포트 {port}에서 eftjourney가 이미 실행 중입니다: {url}")
        print("같은 데이터 폴더를 둘이 함께 감시하면 서로의 레이드를 덮어쓰므로 새로 시작하지 않았습니다.")
        print("설정을 바꿔 다시 시작하려면 켜 둔 창을 닫고(Ctrl+C) 같은 명령을 다시 실행하세요.")
        return 1
    print(f"포트 {port}을(를) 열 수 없습니다 ({exc}).")
    print(f"다른 프로그램이 쓰고 있거나 열 수 없는 포트입니다. 다른 포트로 실행해 보세요:  {_on_port(args, other)}")
    return 1


def _bind(rt: Runtime, args: argparse.Namespace) -> ViewerServer | int:
    """Open the viewer's port; if it is taken, the exit code after saying what to do."""
    try:
        return make_server(rt.context(), args.port)
    except OSError as exc:
        return _port_taken(args, exc)


def _serve(server: ViewerServer, open_browser: bool) -> int:
    """Serve the viewer until Ctrl+C."""
    url = f"http://127.0.0.1:{server.server_address[1]}/"  # the real port, also for --port 0
    print(f"\n뷰어: {url}   (종료: Ctrl+C)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        server.server_close()
    return 0


def cmd_doctor(rt: Runtime, args: argparse.Namespace) -> int:
    print(f"eftjourney {__version__} / Python {sys.version.split()[0]}")
    print(f"데이터 폴더    : {rt.data_dir}")
    print(f"로그 폴더      : {rt.logs_dir or '찾지 못함 (--logs 로 지정)'}")
    if rt.logs_dir and rt.logs_dir.is_dir():
        try:
            sessions = [p for p in rt.logs_dir.iterdir() if p.is_dir() and p.name.startswith("log_")]
        except OSError:  # there, but could not be listed
            print("  로그 세션    : 폴더를 읽을 수 없습니다 (접근 권한이나 드라이브 상태를 확인하세요)")
        else:
            print(f"  로그 세션    : {len(sessions)}개")
    print(f"스크린샷 폴더  : {rt.shots_dir}")
    if rt.shots_dir.is_dir():
        from .screenshots import scan_dir

        shots = scan_dir(rt.shots_dir)
        if shots is None:  # there, but could not be listed
            print("  위치 스크린샷: 폴더를 읽을 수 없습니다 (접근 권한이나 OneDrive 동기화 상태를 확인하세요)")
        else:
            print(f"  위치 스크린샷: {len(shots)}장")
    else:
        print("  (아직 없음 - 게임에서 스크린샷 키로 첫 스크린샷을 찍으면 생깁니다)")
    print(f"맵 정보        : {len(rt.maps.get('maps', []))}개 맵")
    print(f"저장된 레이드  : {len(rt.store.list_ids())}개")
    return 0


def cmd_scan(rt: Runtime, args: argparse.Namespace) -> int:
    print("스캔 중...")
    report = rt.scan()
    _print_report(report)
    return 0 if report is not None else 1


def cmd_serve(rt: Runtime, args: argparse.Namespace) -> int:
    server = _bind(rt, args)
    if isinstance(server, int):
        return server
    return _serve(server, args.open)


def cmd_run(rt: Runtime, args: argparse.Namespace) -> int:
    # Take the port first: when another copy already holds it, this one must
    # neither scan nor watch, or both would keep rewriting the same data.
    # (main() holds the data folder's lock by now; this also stops an older copy without one.)
    server = _bind(rt, args)
    if isinstance(server, int):
        return server
    port = server.server_address[1]
    print("스캔 중...")
    first = rt.scan()
    _print_report(first)
    rt.watching = True

    def watch() -> None:
        shown = set(first.warnings) if first else set()  # the first scan printed its own
        failed = _error_key(rt.error) if first is None else None  # and its traceback
        try:
            while True:
                time.sleep(args.interval)
                before = rt.report.in_raid if rt.report else None
                report = rt.scan(quiet=True)
                if report is None:
                    key = _error_key(rt.error)
                    if key and key != failed:  # each error once, like the warnings below
                        print(f"[{datetime.now():%H:%M:%S}] 스캔 오류: {rt.error}")
                    failed = key
                    continue
                failed = None
                if report.in_raid and not before:
                    print(f"[{rt.last_scan}] 레이드 시작 감지: {report.in_raid.get('map') or '?'} - 스크린샷 키로 위치를 남겨 주세요. 경로는 레이드가 끝난 뒤에 공개됩니다.")
                for raid_id in report.written:
                    print(f"[{rt.last_scan}] 레이드 저장: {raid_id}  http://127.0.0.1:{port}/#/raid/{raid_id}")
                # each warning once, when it appears; one that went away and came back is new again
                for warning in [w for w in dict.fromkeys(report.warnings) if w not in shown][:MAX_WARNINGS]:
                    print(f"[{rt.last_scan}] 주의: {warning}")
                shown = set(report.warnings)
        except Exception as exc:  # Runtime.scan keeps scan errors; anything else ends the watcher
            rt.error = f"감시가 멈췄습니다 ({type(exc).__name__}: {exc}). 프로그램을 다시 실행해 주세요."
            print(f"\n{rt.error}")
            traceback.print_exc()
        finally:
            rt.watching = False

    threading.Thread(target=watch, name="watcher", daemon=True).start()
    return _serve(server, not args.no_open)


def cmd_demo(rt: Runtime, args: argparse.Namespace) -> int:
    from .demo import create_demo

    raid_id = create_demo(rt.store, rt.maps)
    print(f"데모 레이드를 만들었습니다: {raid_id}")
    command = _command(args, "serve", "--open")  # with --data, or it shows another folder
    print(f"뷰어로 보려면:  {command}" if command else "뷰어로 보려면 같은 --data 로 serve --open 을 실행하세요.")
    return 0


def _port(text: str) -> int:
    try:
        port = int(text)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"포트는 0~65535 사이의 정수여야 합니다 (받은 값: {text})")
    return port


def _interval(text: str) -> float:
    try:
        seconds = float(text)
    except ValueError:
        seconds = math.nan
    if not (math.isfinite(seconds) and 1 <= seconds <= MAX_INTERVAL):
        raise argparse.ArgumentTypeError(f"감시 주기는 1~{MAX_INTERVAL:g} 사이의 숫자(초)여야 합니다 (받은 값: {text})")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eftjourney",
        description="타르코프 레이드 여정 뷰어 - 인게임 스크린샷 좌표와 게임 로그로 이동 경로를 지도에 그립니다.",
    )
    parser.add_argument("--logs", help="게임 Logs 폴더 (기본: 자동 감지)")
    parser.add_argument("--screenshots", help="인게임 스크린샷 폴더 (기본: 문서\\Escape from Tarkov\\Screenshots)")
    parser.add_argument(
        "--data",
        help="데이터 저장 폴더 (기본: 소스 폴더에서 실행하거나 pip install -e . 로 설치했다면 프로젝트의 data 폴더, "
        "일반 pip 설치라면 %%LOCALAPPDATA%%\\eftjourney, 그 변수가 없으면 ~/.eftjourney. "
        "환경 변수 EFTJOURNEY_DATA로도 지정, 지금 쓰는 폴더는 doctor로 확인)",
    )
    sub = parser.add_subparsers(dest="cmd")
    port_help = "뷰어 포트 (기본: %(default)s, 0이면 빈 포트를 자동으로 고름)"

    run = sub.add_parser("run", help="스캔 + 감시 + 뷰어 (기본 명령)")
    run.add_argument("--port", type=_port, default=DEFAULT_PORT, help=port_help)
    run.add_argument("--interval", type=_interval, default=DEFAULT_INTERVAL, help=f"감시 주기(초, 1~{MAX_INTERVAL:g})")
    run.add_argument("--no-open", action="store_true", help="브라우저를 자동으로 열지 않음")

    sub.add_parser("scan", help="로그와 스크린샷을 한 번 스캔해 레이드를 만든다")
    serve = sub.add_parser("serve", help="뷰어만 띄운다")
    serve.add_argument("--port", type=_port, default=DEFAULT_PORT, help=port_help)
    serve.add_argument("--open", action="store_true", help="브라우저 열기")
    sub.add_parser("demo", help="실제 기록 없이 볼 수 있는 데모 레이드를 만든다")
    sub.add_parser("doctor", help="감지한 폴더와 파일 수를 보여준다")
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:  # bare "py -m eftjourney [--logs ...]" means run
        args.cmd, args.port, args.interval, args.no_open = "run", DEFAULT_PORT, DEFAULT_INTERVAL, False
        args.bare = True  # a hint in words then adds "run" too
    handler = {"run": cmd_run, "scan": cmd_scan, "serve": cmd_serve, "demo": cmd_demo, "doctor": cmd_doctor}[args.cmd]
    lock = None
    if args.cmd in ("run", "scan"):  # the two that rewrite raids; serve, demo and doctor go without
        data_dir = paths.data_dir(args.data)
        try:
            lock = _lock_data(data_dir)
        except OSError as exc:  # no lock possible there (a read-only folder, a file system without locks)
            print(f"주의: 데이터 폴더를 잠그지 못했습니다 ({exc}). 같은 데이터 폴더로 eftjourney를 둘 이상 켜지 마세요.")
        else:
            if lock is None:  # before anything is read, scanned or bound
                return _data_taken(args.cmd, data_dir, getattr(args, "port", DEFAULT_PORT))
    try:
        return handler(Runtime(args), args)
    finally:
        if lock is not None:
            os.close(lock)  # the OS drops it with the process anyway; this is for callers like the tests
