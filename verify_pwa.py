#!/usr/bin/env python3
"""Serves dist/ in real Chromium and checks the two claims that only break in
production: it works with no signal once installed, and a redeploy reaches a
user who already installed it.

    pip install playwright && playwright install chromium
    python3 verify_pwa.py

**Offline means the server is dead, not emulated.** The first version of this
harness used the browser's offline emulation, and it silently failed to apply:
every check passed against a live server, including one for an asset that was
never cached. So the origin is shut down instead, and the first assertion is a
control — a fetch that must be unreachable. If that control ever passes, the
run is meaningless and everything below it is noise.

This is the check that caught icon-180.png. The page requests it on every load
as its apple-touch-icon, it was the only asset the worker did not precache, and
`precached >= 6` was true throughout.
"""

import functools
import hashlib
import http.server
import re
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
PORT = 8143
ORIGIN = f"http://127.0.0.1:{PORT}"

# The answer to the seeded query. If the app runs at all, it produces this.
EXPECTED = "saturation point"


class Handler(http.server.SimpleHTTPRequestHandler):
    # Render serves these; python's guesses are close but not identical, and a
    # manifest with the wrong type is not read as a manifest.
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".webmanifest": "application/manifest+json",
                      ".js": "text/javascript"}

    def log_message(self, *args):
        pass


class Origin:
    """The host. Killable, because that is the only honest way to be offline."""

    def __init__(self, root: Path):
        self.root = root
        self.server = None

    def up(self):
        socketserver.TCPServer.allow_reuse_address = True
        self.server = socketserver.TCPServer(
            ("127.0.0.1", PORT), functools.partial(Handler, directory=str(self.root)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def down(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            time.sleep(0.3)


def redeploy(root: Path) -> str:
    """Exactly what build_dist.py does: change the page, restamp the cache."""
    page = root / "index.html"
    page.write_text(page.read_text().replace(
        '<div class="mark">anagrind</div>', '<div class="mark">anagrind v2</div>'))
    build = hashlib.sha256(page.read_bytes()).hexdigest()[:12]
    sw = root / "sw.js"
    sw.write_text(re.sub(r"anagrind-[a-f0-9]+", "anagrind-" + build, sw.read_text()))
    return build


def main() -> int:
    src = (HERE / (sys.argv[1] if len(sys.argv) > 1 else "dist")).resolve()
    if not (src / "index.html").exists():
        raise SystemExit(f"no built page at {src} — run python3 build_dist.py")

    stage = Path(tempfile.mkdtemp(prefix="anagrind-deploy-"))
    shutil.copytree(src, stage, dirs_exist_ok=True)
    origin = Origin(stage)
    origin.up()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        requested = []
        page.on("request", lambda r: requested.append(r.url))

        # --- install ---
        page.goto(ORIGIN + "/", wait_until="load")
        page.wait_for_selector(".answer .txt", timeout=30000)
        page.evaluate("navigator.serviceWorker.ready")
        first = page.text_content(".mark")
        cache_before = page.evaluate("async () => (await caches.keys())[0]")
        precached = page.evaluate(
            "async () => (await (await caches.open((await caches.keys())[0])).keys())"
            ".map(r => new URL(r.url).pathname).sort()")
        manifest = page.evaluate("fetch('manifest.webmanifest').then(r => r.json())")
        touch_icon = page.evaluate("fetch('icon-180.png').then(r => r.status)")
        external = [u for u in requested if not u.startswith(ORIGIN)]

        # --- the train test: no server, and no HTTP cache to hide behind ---
        context.new_cdp_session(page).send("Network.clearBrowserCache")
        origin.down()

        control = page.evaluate("fetch('/never-cached').then(r => 'HTTP ' + r.status)"
                                ".catch(() => 'unreachable')")
        page.reload(wait_until="load")
        try:
            page.wait_for_selector(".answer .txt", timeout=25000)
            offline = page.text_content(".answer .txt") == EXPECTED
        except Exception as err:
            offline = False
            print(f"  offline reload: {str(err).splitlines()[0]}")

        reachable = page.evaluate("""async () => {
            const out = {};
            for (const a of ['./', 'index.html', 'manifest.webmanifest', 'icon-180.png',
                             'icon-192.png', 'icon-512.png', 'icon-maskable.png'])
                out[a] = await fetch(a).then(r => r.status).catch(() => 'FAIL');
            return out;
        }""")

        # A URL nobody cached. Every URL here is the same single page, so this
        # is the app, not the browser's offline error.
        probe = context.new_page()
        try:
            probe.goto(ORIGIN + "/deep/link", wait_until="load", timeout=20000)
            probe.wait_for_selector(".answer .txt", timeout=25000)
            fallback = probe.text_content(".answer .txt") == EXPECTED
        except Exception as err:
            fallback = False
            print(f"  navigation fallback: {str(err).splitlines()[0]}")
        probe.close()

        # --- back online, with a new build waiting ---
        build = redeploy(stage)
        origin.up()
        page.reload(wait_until="load")
        for _ in range(30):
            try:
                if page.evaluate(
                        """async b => (await caches.keys()).includes('anagrind-' + b)
                           && document.querySelector('.mark').textContent === 'anagrind v2'""",
                        build):
                    break
            except Exception:
                pass       # the worker reloads the page under us; that is the point
            time.sleep(0.4)
        page.wait_for_selector(".answer .txt", timeout=30000)
        updated = page.text_content(".mark")
        cache_after = page.evaluate("caches.keys()")
        browser.close()

    origin.down()
    shutil.rmtree(stage, ignore_errors=True)

    icon_sizes = {i["sizes"] for i in manifest.get("icons", [])}
    checks = [
        ("offline really means offline (control)", control == "unreachable"),
        ("manifest is installable",
            manifest.get("name") == "anagrind"
            and manifest.get("display") == "standalone"
            and {"192x192", "512x512"} <= icon_sizes),
        ("apple-touch-icon served", touch_icon == 200),
        (f"every asset precached ({len(precached)}) {precached}", len(precached) >= 7),
        ("no external requests", not external),
        ("first load serves the current build", first == "anagrind"),
        ("WORKS OFFLINE once installed", offline),
        (f"every asset answerable offline {reachable}",
            all(status == 200 for status in reachable.values())),
        ("an unknown URL offline still opens the app", fallback),
        ("a redeploy reaches an installed user", updated == "anagrind v2"),
        ("the superseded cache is deleted",
            len(cache_after) == 1 and cache_after[0] != cache_before),
    ]

    failed = sum(not ok for _, ok in checks)
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print("\n  FAILED" if failed else "\n  dist/ is deployable")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
