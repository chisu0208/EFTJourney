"""Local web server for the viewer. Binds to 127.0.0.1 only.

GET  /                         viewer (eftjourney/viewer/index.html)
GET  /viewer/<file>            viewer assets
GET  /api/status               detected folders, watcher state, data version
GET  /api/maps                 map metadata (eftjourney/data/maps.json)
GET  /api/raids                raid summaries, newest first
GET  /api/raids/<id>           one raid, generated data merged with user edits
PUT  /api/raids/<id>/edits     save user edits (kills, outcome, map override, notes)
GET  /mapsvg/<key>.svg         tarkov.dev map SVG, cached under data/cache/maps
GET  /shots/<file>             an in-game screenshot from the screenshots folder
GET  /thumbs/<file>            a small JPEG of it (needs Pillow, else the original)
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from .store import RaidStore

VIEWER_DIR = Path(__file__).resolve().parent / "viewer"
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}
SHOT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
SVG_HOSTS = ("assets.tarkov.dev",)
SVG_HEAD = 4096  # bytes in which a map file has to show its "<svg" tag
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")
MAX_BODY = 256 * 1024
DRAIN_LIMIT = 4 * 1024 * 1024  # a rejected request's body up to this size is read before the reply
DRAIN_TIMEOUT = 2.0  # seconds a rejected request's body may take to arrive
REQUEST_TIMEOUT = 30.0  # seconds a client may stall mid-request before its connection is dropped
THUMB_WIDTH = 640


def _svg_on_disk(path: Path) -> bool:
    """Whether a cached map is there and starts like an SVG (a power cut can leave zeros)."""
    try:
        with path.open("rb") as fh:
            return b"<svg" in fh.read(SVG_HEAD)
    except OSError:
        return False


def _jpeg_on_disk(path: Path) -> bool:
    """Whether a cached thumbnail is there and whole: a JPEG's start and end markers."""
    try:
        with path.open("rb") as fh:
            head = fh.read(3)
            fh.seek(-2, os.SEEK_END)
            return head == b"\xff\xd8\xff" and fh.read(2) == b"\xff\xd9"
    except OSError:  # missing, or too short to seek in
        return False


class AppContext:
    def __init__(
        self,
        store: RaidStore,
        maps: dict,
        screenshots_dir: Path | None,
        cache_dir: Path,
        status: Callable[[], dict] | None = None,
    ):
        self.store = store
        self.maps = maps
        self.screenshots_dir = Path(screenshots_dir) if screenshots_dir else None
        self.cache_dir = Path(cache_dir)
        self.status = status or (lambda: {})
        self._lock = threading.Lock()

    def map_entry(self, key: str) -> dict | None:
        return next((m for m in self.maps.get("maps", []) if m.get("key") == key), None)

    def map_svg(self, key: str) -> Path | None:
        """Fetch the SVG once from tarkov.dev's asset host, then serve it from disk."""
        entry = self.map_entry(key)
        url = (entry or {}).get("svgUrl") or ""
        if not url or urlsplit(url).hostname not in SVG_HOSTS:
            return None
        target = self.cache_dir / "maps" / f"{key}.svg"
        with self._lock:
            if not _svg_on_disk(target):  # not cached yet, or damaged: download it (again)
                target.parent.mkdir(parents=True, exist_ok=True)
                req = urllib.request.Request(url, headers={"User-Agent": "eftjourney/0.1 (+local viewer)"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                if b"<svg" not in data[:SVG_HEAD]:
                    raise ValueError(f"not an SVG: {url}")
                tmp = target.with_suffix(".svg.tmp")
                with tmp.open("wb") as fh:
                    fh.write(data)
                    # on disk before the rename: a power cut must not leave a zero-filled map behind
                    fh.flush()
                    os.fsync(fh.fileno())
                tmp.replace(target)
        return target

    def screenshot(self, name: str) -> Path | None:
        """Only plain file names directly inside the screenshots folder."""
        if not self.screenshots_dir or not name or name != Path(name).name or name.startswith("."):
            return None
        if Path(name).suffix.lower() not in SHOT_TYPES:
            return None
        path = self.screenshots_dir / name
        return path if path.is_file() else None

    def thumbnail(self, name: str) -> tuple[Path, str] | None:
        src = self.screenshot(name)
        if src is None:
            return None
        try:
            from PIL import Image  # optional dependency
        except ImportError:
            return src, SHOT_TYPES[src.suffix.lower()]
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", src.stem)
        target = self.cache_dir / "thumbs" / f"{safe}.jpg"
        with self._lock:
            if not _jpeg_on_disk(target) or target.stat().st_mtime < src.stat().st_mtime:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".jpg.tmp")
                with Image.open(src) as img:
                    img = img.convert("RGB")
                    img.thumbnail((THUMB_WIDTH, THUMB_WIDTH))
                    with tmp.open("wb") as fh:
                        img.save(fh, "JPEG", quality=82)
                        # on disk before the rename, like the maps: a half-written thumbnail is never served
                        fh.flush()
                        os.fsync(fh.fileno())
                tmp.replace(target)
        return target, "image/jpeg"


class Handler(BaseHTTPRequestHandler):
    server_version = "EFTJourney/0.1"
    timeout = REQUEST_TIMEOUT  # socketserver puts it on each connection; a timeout just closes it
    ctx: AppContext
    verbose = False

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.verbose:
            super().log_message(fmt, *args)

    # -- helpers ---------------------------------------------------------
    def _host_ok(self) -> bool:
        # DNS-rebinding guard: only answer requests addressed to loopback
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        if port == 80 and host in LOOPBACK_NAMES:  # browsers leave the default port out of Host
            return True
        return host in {f"{name}:{port}" for name in LOOPBACK_NAMES}

    def _content_length(self) -> int:
        """Declared body size; -1 when it can't be known (not a number, chunked)."""
        if self.headers.get("Transfer-Encoding"):
            return -1
        try:
            return int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return -1

    def _reject(self, status: int, message: str) -> None:
        """Error reply to a request whose body has not been read.

        Closing a socket with unread data in it makes Windows reset the
        connection, and the client may lose the reply (WinError 10053). So a
        body of sane size is read and dropped first; for any other length the
        reply is followed by a lingering close instead.
        """
        length = self._content_length()
        drained = length == 0 or (0 < length <= DRAIN_LIMIT and self._discard(length))
        try:
            self._error(status, message)
        except OSError:  # the client hung up before the reply; nothing left to linger for
            self.close_connection = True
            return
        if not drained:
            self.close_connection = True
            self._linger()

    def _discard(self, length: int) -> bool:
        """Read and drop `length` body bytes; False if the client stops short or is too slow."""
        try:
            self.connection.settimeout(DRAIN_TIMEOUT)
            deadline = time.monotonic() + DRAIN_TIMEOUT
            while length > 0:
                if time.monotonic() >= deadline:  # a body trickling in must not hold back the reply
                    return False
                chunk = self.rfile.read1(min(length, 64 * 1024))
                if not chunk:
                    return False
                length -= len(chunk)
        except OSError:
            return False
        return True

    def _linger(self) -> None:
        """Half-close after the reply, then drop what the client still sends for a moment.

        The client gets our reply and a FIN rather than a reset.
        """
        try:
            self.connection.shutdown(socket.SHUT_WR)
            self.connection.settimeout(DRAIN_TIMEOUT)
            deadline = time.monotonic() + DRAIN_TIMEOUT
            while time.monotonic() < deadline and self.rfile.read1(64 * 1024):
                pass
        except OSError:
            pass

    def _send(self, status: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, doc: Any, status: int = 200) -> None:
        try:
            body = json.dumps(doc, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, RecursionError) as exc:  # UnicodeEncodeError is a ValueError too
            # NaN or infinity (JSON.parse rejects them), a lone surrogate (UTF-8 can't carry it): an error
            # the viewer can show, rather than a reply it can't parse or a connection closed without one
            self._unsendable(exc)
            return
        self._send(status, body, "application/json; charset=utf-8")

    def _unsendable(self, exc: Exception, files: str = "") -> None:
        """One line on the console and a 500 the viewer can show, for a reply that can't be sent as JSON."""
        about = f"{self.path}, {files}" if files else self.path
        print(f"JSON으로 보낼 수 없는 응답이라 오류로 답했습니다 ({about}): {exc}", file=sys.stderr)
        body = json.dumps({"error": "보낼 수 없는 값(NaN, 깨진 문자 등)이 들어 있어 응답을 만들지 못했습니다"},
                          ensure_ascii=False).encode("utf-8")
        self._send(500, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _file(self, path: Path, ctype: str, cache: str = "no-cache") -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._error(404, "not found")
            return
        self._send(200, data, ctype, cache)

    def _static(self, rel: str) -> None:
        target = (VIEWER_DIR / rel).resolve()
        if VIEWER_DIR.resolve() not in target.parents or target.suffix.lower() not in STATIC_TYPES:
            self._error(404, "not found")
            return
        self._file(target, STATIC_TYPES[target.suffix.lower()])

    # -- routes ----------------------------------------------------------
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._host_ok():
            self._error(403, "forbidden host")
            return
        path = unquote(urlsplit(self.path).path)
        ctx = self.ctx
        if path in ("/", "/index.html"):
            self._static("index.html")
        elif path.startswith("/viewer/"):
            self._static(path[len("/viewer/"):])
        elif path == "/api/status":
            self._json(ctx.status())
        elif path == "/api/maps":
            self._json(ctx.maps)
        elif path == "/api/raids":
            self._json(ctx.store.summaries())
        elif m := re.fullmatch(r"/api/raids/([^/]+)", path):
            try:
                doc = ctx.store.merged(m.group(1))
            except KeyError:
                doc = None
            except RecursionError as exc:  # a raid file changed by hand, nested too deep for merged()'s JSON round trip
                # (never its edits file: clean_edits() keeps nothing deeper than the viewer saves)
                self._unsendable(exc, str(ctx.store.raid_file(m.group(1))))
                return
            if doc is None:
                self._error(404, "raid not found")
            else:
                self._json(doc)
        elif m := re.fullmatch(r"/mapsvg/([a-z0-9-]+)\.svg", path):
            try:
                svg = ctx.map_svg(m.group(1))
            except Exception as exc:  # network trouble shows up in the viewer
                self._error(502, f"map download failed: {exc}")
                return
            if svg is None:
                self._error(404, "unknown map")
            else:
                self._file(svg, "image/svg+xml", "max-age=86400")
        elif m := re.fullmatch(r"/shots/([^/\\]+)", path):
            shot = ctx.screenshot(m.group(1))
            if shot is None:
                self._error(404, "screenshot not found")
            else:
                self._file(shot, SHOT_TYPES[shot.suffix.lower()], "max-age=86400")
        elif m := re.fullmatch(r"/thumbs/([^/\\]+)", path):
            try:
                thumb = ctx.thumbnail(m.group(1))
            except OSError:
                thumb = None
            if thumb is None:
                self._error(404, "screenshot not found")
            else:
                self._file(thumb[0], thumb[1], "max-age=86400")
        else:
            self._error(404, "not found")

    def do_PUT(self) -> None:
        if not self._host_ok():
            self._reject(403, "forbidden host")
            return
        path = unquote(urlsplit(self.path).path)
        m = re.fullmatch(r"/api/raids/([^/]+)/edits", path)
        if not m:
            self._reject(404, "not found")
            return
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            self._reject(415, "expected application/json")
            return
        length = self._content_length()
        if not 0 < length <= MAX_BODY:
            self._reject(413, "body too large or empty")
            return
        body = self.rfile.read(length)
        try:
            raw = json.loads(body.decode("utf-8"))
            edits = self.ctx.store.save_edits(m.group(1), raw)
        except KeyError:
            self._error(404, "raid not found")
            return
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:  # RecursionError: absurdly nested JSON
            self._error(400, f"invalid edits: {exc}")
            return
        except OSError as exc:  # e.g. another program kept the file open past the store's retries
            self._error(503, f"파일에 쓸 수 없습니다 ({exc.strerror or exc}). 잠시 후 다시 저장해 보세요.")
            return
        # saved: the reply is what was saved, cleaned, so it always goes out. Not merged(): a raid file nested
        # too deep for its JSON round trip failed it, and any error now reads as a failed save in the viewer
        self._json(edits)


class ViewerServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind a port that is already
    # listening, without an error: two copies would share the port and both
    # watchers would rewrite the same data. Elsewhere it only skips TIME_WAIT.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # nor may another program bind over this socket with SO_REUSEADDR
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A client that hung up mid-request (a cancelled upload, a closed tab) is not
        # our fault: keep its traceback off the console. Anything else is printed.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def make_server(ctx: AppContext, port: int = 8765, verbose: bool = False) -> ViewerServer:
    handler = type("BoundHandler", (Handler,), {"ctx": ctx, "verbose": verbose})
    server = ViewerServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server
