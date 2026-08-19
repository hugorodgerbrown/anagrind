#!/usr/bin/env python3
"""Serve dist/ locally the way Render serves it.

    python3 devserver.py            http://localhost:8137
    python3 devserver.py 9000       another port

`python3 -m http.server` is one flag shorter and quietly wrong for this app.
It sends no Cache-Control, so Chromium heuristically caches index.html — and
the service worker's addAll() then fills a *freshly named* cache with the
*previous* build. The page looks stale, the cache name says it is current, and
nothing in the browser admits what happened. render.yaml sends no-cache for
exactly this reason; so does this.
"""

import functools
import http.server
import socketserver
import sys
from pathlib import Path

DIST = Path(__file__).parent / "dist"
NEVER_STALE = ("/", "/index.html", "/sw.js", "/manifest.webmanifest")


class Handler(http.server.SimpleHTTPRequestHandler):
    # Render serves these; python's guesses are close but not identical, and a
    # manifest with the wrong type is not read as a manifest.
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".webmanifest": "application/manifest+json",
                      ".js": "text/javascript"}

    def end_headers(self):
        path = self.path.split("?")[0]
        if path in NEVER_STALE:
            self.send_header("Cache-Control", "no-cache")
        elif path.endswith(".png"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


def main() -> None:
    if not (DIST / "index.html").exists():
        raise SystemExit("no dist/index.html — run python3 build_dist.py")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8137
    socketserver.TCPServer.allow_reuse_address = True
    handler = functools.partial(Handler, directory=str(DIST))
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"dist/ on http://localhost:{port}  (ctrl-c to stop)")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
