"""A fake website for World Login tests, on two local origins: no real login is ever used.

    with FakeSite("ana", "pa55") as site:
        site.a, site.b  # http://127.0.0.1:<port>, each its own origin

Origin A signs in with a POST to `/login` (username and password, as a form), answering with a
session cookie; `/account` answers only with that cookie, and echoes the password and the cookie
in its text, so a test can prove they are scrubbed. Origin B serves the same form: the phishing
case, which a stage with origin A's login must never reach. `expire()` forgets every session.
"""

import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self
from urllib.parse import parse_qs


class FakeSite:
    def __init__(self, username: str, password: str) -> None:
        self.username, self.password = username, password
        self.sessions: set[str] = set()
        self.hits: list[tuple[str, str]] = []
        """(origin, path) of every request, in order: what reached which origin."""
        site = self

        def handler(origin: str) -> type[BaseHTTPRequestHandler]:
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: Any) -> None:
                    pass

                def do_GET(self) -> None:
                    site.hits.append((origin, self.path))
                    cookie = (self.headers.get("Cookie") or "").removeprefix("sid=")
                    if self.path == "/account" and origin == "a" and cookie in site.sessions:
                        said = f"Signed in as {site.username}. pw={site.password} sid={cookie}"
                        return self._send(200, said)
                    if self.path == "/account":
                        return self._send(401, "Sign in first.")
                    self._send(200, "<form method=post action=/login>user, password</form>")

                def do_POST(self) -> None:
                    site.hits.append((origin, self.path))
                    length = int(self.headers.get("Content-Length", 0))
                    form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                    good = (form.get("username"), form.get("password")) == (
                        site.username, site.password
                    )  # fmt: skip
                    if self.path != "/login" or origin != "a" or not good:
                        return self._send(403, "No.")
                    sid = secrets.token_hex(12)
                    site.sessions.add(sid)
                    self._send(200, "Welcome.", {"Set-Cookie": f"sid={sid}; Path=/"})

                def _send(self, status: int, text: str, headers: dict[str, str] = {}) -> None:  # noqa: B006
                    body = text.encode()
                    self.send_response(status)
                    for k, v in {"Content-Type": "text/html", **headers}.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            return Handler

        self._servers = [ThreadingHTTPServer(("127.0.0.1", 0), handler(o)) for o in "ab"]
        a, b = (f"127.0.0.1:{s.server_address[1]}" for s in self._servers)
        self.host_a, self.host_b = a, b
        """Each origin as a login's domain: `127.0.0.1:<port>`."""
        self.a, self.b = f"http://{a}", f"http://{b}"

    def expire(self) -> None:
        self.sessions.clear()

    def __enter__(self) -> Self:
        for server in self._servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_: object) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()
