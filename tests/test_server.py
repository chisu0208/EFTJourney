import contextlib
import http.client
import io
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from eftjourney.server import DRAIN_LIMIT, VIEWER_DIR, AppContext, Handler, make_server
from eftjourney.store import RaidStore
from tests.test_store import TOO_DEEP, make_raid

MAPS ={"maps": [{"ids": ["bigmap"], "key": "customs", "name": "Customs", "svgUrl": "https://evil.example.com/x.svg", "transform": [1, 0, 1, 0], "coordinateRotation": 0, "bounds": [[0, 0], [1, 1]]}]}
EDITS_URL = "/api/raids/20261027-224801-bigmap/edits"
SHOT_NAME = "2026-10-27[22-50]_1.0, 2.0, 3.0_0.0, 0.0, 0.0, 1.0_0.00 (0).png"
RESET_ON_CLOSE = struct.pack("hh" if sys.platform == "win32" else "ii", 1, 0)  # SO_LINGER on, 0 s: close() sends RST
# objects nested this deep pass json.load but not merged()'s JSON round trip (Python 3.14 here, in a handler
# thread too: json.dumps stops near 9300 levels, json.loads near 14300)
SEND_DEEP = 12_000


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.shots = root / "shots"
        cls.shots.mkdir()
        (cls.shots / SHOT_NAME).write_bytes(b"\x89PNG fake")
        (cls.shots / ".hidden.png").write_bytes(b"\x89PNG hidden")
        (root / "secret.txt").write_text("secret", encoding="utf-8")
        cls.store = RaidStore(root / "data")
        cls.store.save_raid(make_raid())
        cls.ctx = AppContext(cls.store, MAPS, cls.shots, root / "data" / "cache", lambda: {"version": 7})
        cls.server = make_server(cls.ctx, port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            res = conn.getresponse()
            return res.status, res.getheader("Content-Type") or "", res.read()
        finally:
            conn.close()

    def raw_request(self, method, path, headers, body=b""):
        # http.client fills in Host and Content-Length itself; this sends them as given
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in headers.items():
                conn.putheader(name, value)
            conn.endheaders(body or None)
            res = conn.getresponse()
            return res.status, res.read()
        finally:
            conn.close()

    def put_head(self, length, ctype="application/json", host=None, path=EDITS_URL):
        return (f"PUT {path} HTTP/1.1\r\nHost: {host or f'127.0.0.1:{self.port}'}\r\n"
                f"Content-Type: {ctype}\r\nContent-Length: {length}\r\n\r\n").encode()

    @contextlib.contextmanager
    def handlers_tracked(self):
        """Yields wait(client_port): True once the server is done with that client."""
        done, cond = set(), threading.Condition()
        real = self.server.process_request_thread

        def tracked(request, client_address):
            try:
                real(request, client_address)
            finally:
                with cond:
                    done.add(client_address[1])
                    cond.notify_all()

        def wait(port):
            with cond:
                return cond.wait_for(lambda: port in done, 10)

        with mock.patch.object(self.server, "process_request_thread", tracked):
            yield wait

    def test_index_and_assets(self):
        status, ctype, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"MY EFT", body)
        self.assertEqual(self.request("GET", "/viewer/app.js")[0], 200)

    def test_api(self):
        status, _, body = self.request("GET", "/api/status")
        self.assertEqual(json.loads(body), {"version": 7})
        status, _, body = self.request("GET", "/api/raids")
        self.assertEqual(json.loads(body)[0]["id"], "20261027-224801-bigmap")
        status, _, body = self.request("GET", "/api/raids/20261027-224801-bigmap")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["map"], "bigmap")
        self.assertEqual(self.request("GET", "/api/raids/nope")[0], 404)

    def test_raid_list_with_files_of_another_shape(self):
        # One such raid file closed the connection without a reply: the viewer showed an empty list,
        # then "서버 연결 끊김" every 4 s. Hand edits and other tools only; the scan never writes them.
        odd, edits, surrogate = (self.store.raids_dir / name for name in (
            "20261026-120000-woods.json", "20261026-120000-woods.edits.json", "20261025-120000-woods.json"))
        odd.write_text(json.dumps({"track": {"x": 1}, "outcome": "x", "logFixes": 3, "duration": float("nan")}), encoding="utf-8")
        edits.write_text(json.dumps({"kills": 3, "outcome": "x"}), encoding="utf-8")
        surrogate.write_text(json.dumps(make_raid("20261025-120000-woods", title="\ud800")), encoding="utf-8")
        for path in (odd, edits, surrogate):
            self.addCleanup(path.unlink)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status, _, body = self.request("GET", "/api/raids")
        self.assertEqual(status, 200)
        rows = json.loads(body.decode("utf-8"), parse_constant=lambda name: self.fail(f"browsers reject {name}"))
        self.assertEqual([r["id"] for r in rows], ["20261027-224801-bigmap", "20261026-120000-woods", "20261025-120000-woods"])
        self.assertEqual(rows[2]["title"], "?")  # the lone surrogate, as the raid itself shows it
        self.assertEqual(err.getvalue(), "")

    def test_raid_detail_of_files_of_another_shape(self):
        # GET /api/raids/<id> sent them as they were, while the list showed them: NaN failed the browser's
        # res.json(), a lone surrogate closed the connection without a reply (a traceback per request),
        # and "kills": "abc" broke app.js's .map(). Each opens now, and draws what the list counts.
        nan, inf = float("nan"), float("inf")
        track = [{"t": 1.0, "x": 0.0, "y": nan, "z": 0.0, "yaw": inf, "tod": nan}, {"x": 3.0, "z": 4.0}]
        odd_id, edited_id = "20261026-120000-woods", "20261025-120000-woods"
        files = {  # raid id: (raid file, edits file)
            odd_id: (make_raid(odd_id, duration=nan, track=track, kills="abc", logFixes={"t": 1.0}), None),
            edited_id: (make_raid(edited_id), {"notes": "a\ud800b", "kills": [{"t": 5, "name": "\udfff"}]}),
        }
        for raid_id, (raid, edits) in files.items():
            for suffix, doc in ((".json", raid), (".edits.json", edits)):
                if doc is not None:
                    path = self.store.raids_dir / f"{raid_id}{suffix}"
                    path.write_text(json.dumps(doc), encoding="utf-8")  # NaN and "\ud800" as they are
                    self.addCleanup(path.unlink)
        err = io.StringIO()
        docs = {}
        with contextlib.redirect_stderr(err):
            rows = {r["id"]: r for r in json.loads(self.request("GET", "/api/raids")[2])}
            for raid_id in files:
                status, ctype, body = self.request("GET", f"/api/raids/{raid_id}")
                self.assertEqual((status, ctype), (200, "application/json; charset=utf-8"), raid_id)
                text = body.decode("utf-8")  # UTF-8 throughout
                docs[raid_id] = doc = json.loads(text, parse_constant=lambda name: self.fail(f"browsers reject {name}"))
                self.assertEqual(len(doc["track"]), rows[raid_id]["samples"], raid_id)
        self.assertEqual(err.getvalue(), "")
        odd, edited = docs[odd_id], docs[edited_id]
        self.assertEqual((odd["duration"], odd["kills"], odd["logFixes"]), (None, [], []))
        self.assertEqual(odd["track"], [{"t": 1.0, "x": 0.0, "y": None, "z": 0.0, "yaw": None, "tod": None}])
        self.assertEqual((edited["notes"], edited["kills"][0]["name"]), ("a?b", "?"))

    def test_reply_that_cannot_be_sent_is_a_json_error(self):
        # json.dumps wrote NaN as it was (JSON.parse rejects it), and encode("utf-8") raised on a lone
        # surrogate: the connection closed without a reply, with a traceback on the console
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for doc in ({"version": float("nan")}, {"version": "\ud800"}):
                with self.subTest(doc=doc), mock.patch.object(self.ctx, "status", return_value=doc):
                    status, ctype, body = self.request("GET", "/api/status")
                    self.assertEqual((status, ctype), (500, "application/json; charset=utf-8"))
                    self.assertIn("응답을 만들지 못했습니다", json.loads(body.decode("utf-8"))["error"])
            with mock.patch.object(self.store, "merged", return_value={"track": [float("inf")]}):
                status, _, body = self.request("GET", "/api/raids/20261027-224801-bigmap")
            self.assertEqual(status, 500)
            self.assertIn("error", json.loads(body))
        self.assertNotIn("Traceback", err.getvalue())
        self.assertEqual(err.getvalue().count("JSON으로 보낼 수 없는 응답"), 3)  # one line each, naming the request
        self.assertIn("(/api/raids/20261027-224801-bigmap)", err.getvalue())
        self.assertEqual(self.request("GET", "/api/status")[0], 200)

    def test_raid_nested_too_deep_to_send(self):
        # json.load reads it, merged()'s JSON round trip can't (a file changed by hand): its GET and an edits PUT
        # closed the connection without a reply, the PUT after saving, and the console got a traceback with a
        # note for every level
        raid_id = "20261023-120000-woods"
        raid_file, edits_file = (self.store.raids_dir / f"{raid_id}{suffix}" for suffix in (".json", ".edits.json"))
        deep = '{"a": ' * SEND_DEEP + "1" + "}" * SEND_DEEP
        raid_file.write_text(json.dumps(make_raid(raid_id))[:-1] + ', "deep": ' + deep + "}", encoding="utf-8")
        self.addCleanup(self.store.delete_raid, raid_id)
        self.addCleanup(self.store.delete_edits, raid_id)
        self.assertIsNotNone(self.store.load_raid(raid_id))  # not too deep for json.load
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status, ctype, body = self.request("GET", f"/api/raids/{raid_id}")
            self.assertEqual((status, ctype), (500, "application/json; charset=utf-8"))
            self.assertIn("응답을 만들지 못했습니다", json.loads(body.decode("utf-8"))["error"])
            # saved: the reply is what was saved, never an error (app.js says "저장하지 못했습니다" on any)
            status, ctype, body = self.request("PUT", f"/api/raids/{raid_id}/edits", b'{"notes": "x"}',
                                               {"Content-Type": "application/json"})
            self.assertEqual((status, ctype, json.loads(body)), (200, "application/json; charset=utf-8", {"notes": "x"}))
            self.assertEqual(self.store.load_edits(raid_id), {"notes": "x"})
            self.assertEqual(self.request("GET", f"/api/raids/{raid_id}")[0], 500)  # with its edits, too
            status, _, body = self.request("GET", "/api/raids")
        self.assertEqual((status, [r["id"] for r in json.loads(body)]), (200, ["20261027-224801-bigmap"]))
        # one line each, no traceback. A GET names the raid file (an edits file cannot fail merged()'s round
        # trip); the list can't tell, so it names both once there is an edits file
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 3, lines)
        for line in lines[:2]:
            self.assertTrue(line.startswith(f"JSON으로 보낼 수 없는 응답이라 오류로 답했습니다 (/api/raids/{raid_id}, {raid_file}): "), line)
        self.assertEqual(lines[2], f"레이드 목록에서 뺐습니다 (읽을 수 없는 모양): {raid_file} 또는 {edits_file}")

    def test_put_edits_with_a_lone_surrogate(self):
        # "\ud800" in the body (JSON.stringify writes one it holds that way) failed the write: 400, nothing saved
        raid_id = "20261024-120000-woods"
        self.store.save_raid(make_raid(raid_id, map="woods"))
        self.addCleanup(self.store.delete_raid, raid_id)
        self.addCleanup(self.store.delete_edits, raid_id)
        payload = b'{"notes": "a\\ud800b", "kills": [{"t": 30, "name": "\\udc00Reshala"}]}'
        status, _, body = self.request("PUT", f"/api/raids/{raid_id}/edits", payload, {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        doc = json.loads(body.decode("utf-8"))
        self.assertEqual((doc["notes"], doc["kills"][0]["name"]), ("a?b", "?Reshala"))
        self.assertEqual(self.store.load_edits(raid_id)["notes"], "a?b")

    def test_port_in_use_is_not_shared(self):
        # Windows SO_REUSEADDR let a second copy bind a listening port without an error
        with self.assertRaises(OSError):
            make_server(self.ctx, port=self.port).server_close()
        self.assertEqual(self.request("GET", "/api/status")[0], 200)

    def test_put_edits(self):
        payload = json.dumps({"kills": [{"t": 30, "name": "Reshala"}], "junk": 1}).encode()
        status, _, body = self.request("PUT", EDITS_URL, payload, {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        doc = json.loads(body)
        self.assertEqual(doc["kills"][0]["name"], "Reshala")
        self.assertNotIn("junk", self.store.load_edits("20261027-224801-bigmap"))

    def test_put_rejections(self):
        url = EDITS_URL
        self.assertEqual(self.request("PUT", url, b"{}", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("PUT", url, b"{bad", {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("PUT", url, b"[1]", {"Content-Type": "application/json"})[0], 400)
        big = b'{"notes":"' + b"a" * (300 * 1024) + b'"}'
        self.assertEqual(self.request("PUT", url, big, {"Content-Type": "application/json"})[0], 413)
        self.assertEqual(self.request("PUT", "/api/raids/20991231-000000-woods/edits", b"{}", {"Content-Type": "application/json"})[0], 404)

    def test_early_rejections_keep_the_reply(self):
        # These are answered before the body is read. Closing with the body unread
        # made Windows reset the connection, and the client lost the reply (WinError 10053).
        mid = b'{"notes":"' + b"a" * (100 * 1024) + b'"}'
        big = b'{"notes":"' + b"a" * (300 * 1024) + b'"}'
        for _ in range(10):
            self.assertEqual(self.request("PUT", EDITS_URL, mid, {"Content-Type": "text/plain"})[0], 415)
            self.assertEqual(self.request("PUT", EDITS_URL, big, {"Content-Type": "application/json"})[0], 413)
            self.assertEqual(self.request("PUT", EDITS_URL, mid, {"Host": "evil.com", "Content-Type": "application/json"})[0], 403)
            self.assertEqual(self.request("PUT", "/api/raids/x/nope", mid, {"Content-Type": "application/json"})[0], 404)
        # more than is drained, or a length that can't be trusted: reply, then a lingering close
        huge = b"a" * (DRAIN_LIMIT + 1024)
        self.assertEqual(self.request("PUT", EDITS_URL, huge, {"Content-Type": "application/json"})[0], 413)
        headers = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json", "Content-Length": "lots"}
        self.assertEqual(self.raw_request("PUT", EDITS_URL, headers, mid)[0], 413)

    def test_client_leaving_mid_body_is_quiet(self):
        # The reply to a rejected PUT went to a client that had already left, and the
        # traceback (WinError 10053/10054) landed in the user's console window.
        cut = b"a" * 10240
        cases = [
            (self.put_head(100 * 1024, "text/plain") + cut, False),  # 415
            (self.put_head(100 * 1024, host="evil.com") + cut, False),  # 403
            (self.put_head(100 * 1024, path="/api/raids/x/nope") + cut, False),  # 404
            (self.put_head(300 * 1024) + cut, False),  # 413
            (self.put_head(100 * 1024, "text/plain") + cut, True),  # reset instead of a FIN
            (self.put_head(1000) + b'{"notes": ', False),  # accepted, body cut short
        ]
        err = io.StringIO()
        with self.handlers_tracked() as finished, contextlib.redirect_stderr(err):
            for data, reset in cases:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
                client = s.getsockname()[1]
                s.sendall(data)
                time.sleep(0.05)
                if reset:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, RESET_ON_CLOSE)
                s.close()
                self.assertTrue(finished(client), data[:60])
            # a client that only stopped sending (half-close) still reads the reply
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
                s.sendall(self.put_head(100 * 1024, "text/plain") + cut)
                s.shutdown(socket.SHUT_WR)
                self.assertTrue(s.recv(4096).startswith(b"HTTP/1.0 415"))
        self.assertNotIn("Traceback", err.getvalue())

    def test_handle_error_skips_client_hangups(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for exc in (ConnectionAbortedError(10053, "aborted"), ConnectionResetError(10054, "reset"), BrokenPipeError(32, "broken pipe")):
                try:
                    raise exc
                except OSError:
                    self.server.handle_error(None, ("127.0.0.1", 1))
            self.assertEqual(err.getvalue(), "")
            try:
                raise ValueError("a real bug")
            except ValueError:
                self.server.handle_error(None, ("127.0.0.1", 1))
        self.assertIn("ValueError: a real bug", err.getvalue())  # everything else is still printed

    def test_reject_when_the_reply_cannot_be_sent(self):
        # the client is gone: close, and don't linger on a dead socket
        fake = SimpleNamespace(close_connection=False, _content_length=lambda: 100 * 1024, _discard=lambda length: False,
                               _error=mock.Mock(side_effect=ConnectionAbortedError(10053, "aborted")), _linger=mock.Mock())
        Handler._reject(fake, 415, "expected application/json")
        self.assertTrue(fake.close_connection)
        fake._linger.assert_not_called()

    def test_trickling_rejected_body_gets_its_reply(self):
        # The drain had no overall deadline: a body arriving a byte at a time held back the 415
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(s.close)
        stop = threading.Event()

        def drip():
            while not stop.wait(0.2):  # each byte comes well within the per-read timeout
                try:
                    s.sendall(b"a")
                except OSError:
                    return

        with mock.patch("eftjourney.server.DRAIN_TIMEOUT", 0.5):
            s.sendall(self.put_head(1000, "text/plain") + b"a")
            threading.Thread(target=drip, daemon=True).start()
            start = time.monotonic()
            try:
                reply = s.recv(4096)
            finally:
                stop.set()
        self.assertTrue(reply.startswith(b"HTTP/1.0 415"), reply)
        self.assertLess(time.monotonic() - start, 3)

    def test_stalled_clients_are_dropped(self):
        # Without a timeout, a client that stopped mid-request kept its handler thread forever
        self.assertIsNotNone(Handler.timeout)
        self.assertLessEqual(Handler.timeout, 60)

        def dropped(data):
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
                s.sendall(data)
                try:
                    return s.recv(4096) == b""
                except ConnectionError:
                    return True

        def slow(result):
            def call(*args, **kwargs):
                time.sleep(0.6)
                return result
            return call

        svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        with mock.patch.object(self.server.RequestHandlerClass, "timeout", 0.2):
            self.assertTrue(dropped(f"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n".encode()))  # headers never end
            self.assertTrue(dropped(self.put_head(1000) + b'{"notes": '))  # body never ends
            # slow work on our side is no stall: a map download or a thumbnail still gets through
            with mock.patch.object(self.ctx, "map_entry", return_value={"svgUrl": "https://assets.tarkov.dev/maps/x.svg"}), \
                    mock.patch("urllib.request.urlopen", slow(io.BytesIO(svg))):
                self.assertEqual(self.request("GET", "/mapsvg/slowmap.svg")[::2], (200, svg))
            with mock.patch.object(self.ctx, "thumbnail", slow((self.shots / SHOT_NAME, "image/png"))):
                self.assertEqual(self.request("GET", "/thumbs/x.png")[::2], (200, b"\x89PNG fake"))
        self.assertEqual(self.request("GET", "/api/status")[0], 200)

    def test_put_bad_nesting_and_failed_save(self):
        # nested past what json.loads follows, closed or not, arrays or objects: a 400 the viewer can show
        before = self.store.load_edits("20261027-224801-bigmap")
        for payload in (b"[" * 50000, b'{"notes": ' + b"[" * TOO_DEEP + b"]" * TOO_DEEP + b"}",
                        b'{"notes": ' + b'{"a":' * 40000 + b"1" + b"}" * 40000 + b"}"):  # within MAX_BODY
            with self.subTest(size=len(payload)):
                status, ctype, body = self.request("PUT", EDITS_URL, payload, {"Content-Type": "application/json"})
                self.assertEqual((status, ctype), (400, "application/json; charset=utf-8"))
                self.assertIn("invalid edits", json.loads(body)["error"])
        self.assertEqual(self.store.load_edits("20261027-224801-bigmap"), before)  # nothing saved
        # Windows: a reader kept the file open past the store's retries
        with mock.patch.object(self.store, "save_edits", side_effect=PermissionError(13, "in use")):
            status, ctype, body = self.request("PUT", EDITS_URL, b'{"notes": "x"}', {"Content-Type": "application/json"})
        self.assertEqual(status, 503)
        self.assertIn("application/json", ctype)
        self.assertIn("error", json.loads(body))

    def test_foreign_host_rejected(self):
        # DNS rebinding: a page on evil.com resolving to 127.0.0.1 must not read or write data
        self.assertEqual(self.request("GET", "/api/raids", headers={"Host": "evil.com"})[0], 403)
        status = self.request("PUT", EDITS_URL, b"{}", {"Host": "evil.com:80", "Content-Type": "application/json"})[0]
        self.assertEqual(status, 403)
        # a Host without a port only means port 80
        self.assertEqual(self.raw_request("GET", "/api/maps", {"Host": "127.0.0.1"})[0], 403)

    def test_host_without_port_on_port_80(self):
        # with --port 80 browsers send "Host: 127.0.0.1", no port
        def host_ok(host, port):
            fake = SimpleNamespace(headers={"Host": host}, server=SimpleNamespace(server_address=("127.0.0.1", port)))
            return Handler._host_ok(fake)

        for name in ("127.0.0.1", "localhost", "[::1]", "LocalHost"):
            self.assertTrue(host_ok(name, 80), name)
            self.assertTrue(host_ok(f"{name}:80", 80), name)
            self.assertFalse(host_ok(name, 8765), name)
        for bad in ("evil.com", "evil.com:80", "127.0.0.1:8765", ""):
            self.assertFalse(host_ok(bad, 80), bad)

    def test_screenshot_serving_is_confined(self):
        name = SHOT_NAME
        from urllib.parse import quote

        status, ctype, body = self.request("GET", "/shots/" + quote(name))
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "image/png")
        for bad in ("/shots/..%2Fsecret.txt", "/shots/%2E%2E%5Csecret.txt", "/shots/secret.txt", "/thumbs/..%5C..%5Csecret.txt", "/viewer/../store.py", "/viewer/..%2F..%2Fpyproject.toml"):
            self.assertIn(self.request("GET", bad)[0], (404,), bad)
        # These pass the route patterns and the suffix whitelist and name real
        # files, so only the containment checks keep them out.
        maps_json = VIEWER_DIR.parent / "data" / "maps.json"
        self.assertTrue(maps_json.is_file())
        probes = ["/viewer/..%2Fdata%2Fmaps.json", "/viewer/" + quote(str(maps_json)), "/shots/.hidden.png", "/thumbs/.hidden.png"]
        if sys.platform == "win32":
            # "Z:x.png" joined to the folder is a path on another drive, not inside it.
            # Nothing exists there, so also check the name rule with every file "present".
            other = next(d for d in "ZYXWVUTSRQPONMLKJIHGFED" if d != self.shots.drive[:1].upper())
            probes.append(f"/shots/{other}:x.png")
            with mock.patch.object(Path, "is_file", return_value=True):
                self.assertIsNone(self.ctx.screenshot(f"{other}:x.png"))
                self.assertIsNotNone(self.ctx.screenshot("x.png"))
        for bad in probes:
            self.assertEqual(self.request("GET", bad)[0], 404, bad)

    def test_svg_host_whitelist(self):
        # maps.json pointing at a foreign host must never be fetched
        self.assertEqual(self.request("GET", "/mapsvg/customs.svg")[0], 404)
        self.assertEqual(self.request("GET", "/mapsvg/unknown.svg")[0], 404)

    def test_damaged_map_cache_is_downloaded_again(self):
        # A power cut can leave the cached SVG zero-filled; only is_file() was checked, so it was served forever
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1"/></svg>'
        cached = self.ctx.cache_dir / "maps" / "zeroed.svg"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b"\0" * 8192)
        entry = {"svgUrl": "https://assets.tarkov.dev/maps/zeroed.svg"}
        with mock.patch.object(self.ctx, "map_entry", return_value=entry), \
                mock.patch("urllib.request.urlopen", return_value=io.BytesIO(svg)) as download:
            self.assertEqual(self.request("GET", "/mapsvg/zeroed.svg")[::2], (200, svg))
            self.assertEqual(self.request("GET", "/mapsvg/zeroed.svg")[::2], (200, svg))  # now from disk
        self.assertEqual(download.call_count, 1)
        self.assertEqual(cached.read_bytes(), svg)

    def test_map_cache_is_on_disk_before_the_rename(self):
        # without an fsync the rename can reach the disk before the data, and a power cut leaves zeros
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        cached = self.ctx.cache_dir / "maps" / "synced.svg"
        real_fsync, synced = os.fsync, []

        def fsync(fd):
            synced.append((os.fstat(fd).st_size, cached.exists()))
            real_fsync(fd)

        entry = {"svgUrl": "https://assets.tarkov.dev/maps/synced.svg"}
        with mock.patch.object(self.ctx, "map_entry", return_value=entry), \
                mock.patch("urllib.request.urlopen", return_value=io.BytesIO(svg)), mock.patch("os.fsync", fsync):
            self.assertEqual(self.request("GET", "/mapsvg/synced.svg")[::2], (200, svg))
        self.assertEqual(synced, [(len(svg), False)])  # all of the new file, before it took the name


class ThumbnailCacheTest(unittest.TestCase):
    """Pillow is optional; a stand-in for it writes the thumbnails here."""

    JPEG = b"\xff\xd8\xff thumbnail \xff\xd9"

    def test_damaged_thumbnail_is_made_again(self):
        # Pillow saved straight onto the cached file: a power cut could leave it zero-filled,
        # and it was then served until the screenshot itself changed
        made = []

        class Image:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def convert(self, mode):
                return self

            def thumbnail(self, size):
                pass

            def save(self, fh, fmt, quality):
                made.append(fmt)
                fh.write(ThumbnailCacheTest.JPEG)

        pil = SimpleNamespace(Image=SimpleNamespace(open=lambda path: Image()))
        with tempfile.TemporaryDirectory() as tmp:
            shots = Path(tmp) / "shots"
            shots.mkdir()
            (shots / SHOT_NAME).write_bytes(b"\x89PNG fake")
            ctx = AppContext(RaidStore(Path(tmp) / "data"), MAPS, shots, Path(tmp) / "cache")
            with mock.patch.dict(sys.modules, {"PIL": pil}):
                target, ctype = ctx.thumbnail(SHOT_NAME)
                self.assertEqual((target.read_bytes(), ctype), (self.JPEG, "image/jpeg"))
                later = time.time() + 60  # newer than the screenshot, as a cached thumbnail is
                for damaged in (b"\0" * 64, self.JPEG[:-4], b""):  # zero-filled, cut short, empty
                    target.write_bytes(damaged)
                    os.utime(target, (later, later))
                    self.assertEqual(ctx.thumbnail(SHOT_NAME)[0].read_bytes(), self.JPEG)
                self.assertEqual(len(made), 4)
                ctx.thumbnail(SHOT_NAME)  # whole and newer: from the cache
                self.assertEqual(len(made), 4)
            self.assertEqual(list(target.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
