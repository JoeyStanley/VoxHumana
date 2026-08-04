"""Local reverse proxy for testing VoxHumana under a sub-path deployment.

Production sits behind a reverse proxy that strips a path prefix (e.g.
/VoxHumana) before forwarding to uvicorn -- uvicorn's own routes are always
unprefixed (see web/app.py's VXH_ROOT_PATH handling). Running uvicorn alone
on your machine can't reproduce a URL like http://localhost:8080/VoxHumana/,
since there's nothing stripping that prefix. This script is that missing
piece for local testing: it does nothing but add/strip the prefix, exactly
like the nginx example in TODO_for_server.md §8a.

Usage:
    # Terminal 1 -- the app itself, told it's deployed under /VoxHumana
    VXH_ROOT_PATH=/VoxHumana uv run uvicorn web.app:app --host 127.0.0.1 --port 8001

    # Terminal 2 -- this proxy, sitting in front
    uv run python scripts/local_proxy.py

Then browse to http://localhost:8080/VoxHumana/ -- that's the real,
fully-wired sub-path URL, not just uvicorn's bare root.

Override the prefix/ports with env vars if needed:
    VXH_ROOT_PATH=/VoxHumana PROXY_PORT=8080 BACKEND_PORT=8001 uv run python scripts/local_proxy.py
"""
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREFIX = os.environ.get("VXH_ROOT_PATH", "/VoxHumana").rstrip("/")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8080"))
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8001"))
BACKEND = f"http://127.0.0.1:{BACKEND_PORT}"


class ProxyHandler(BaseHTTPRequestHandler):
    def _proxy(self):
        path = self.path
        if path == PREFIX:
            # Mirror production: the prefix with no trailing slash doesn't
            # match a real proxy's location block, so it 404s too -- this
            # is deliberate, not a bug in this script.
            self.send_response(404)
            self.end_headers()
            return
        if not path.startswith(PREFIX + "/"):
            self.send_response(404)
            self.end_headers()
            return
        backend_path = path[len(PREFIX):]

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None

        req = urllib.request.Request(BACKEND + backend_path, data=body, method=self.command)
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length"):
                req.add_header(k, v)

        try:
            resp = urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            resp = e

        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "connection"):
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp.read())

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def log_message(self, fmt, *args):
        print(f"[local_proxy] {self.command} {self.path} -> {fmt % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PROXY_PORT), ProxyHandler)
    print(f"Proxying http://localhost:{PROXY_PORT}{PREFIX}/  ->  {BACKEND}  (strips {PREFIX})")
    server.serve_forever()
