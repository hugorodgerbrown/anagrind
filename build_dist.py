#!/usr/bin/env python3
"""Assemble dist/ — the folder Render publishes.

    python3 build_dist.py           rebuild the page (stdlib only, works
                                    from a fresh clone)
    python3 build_dist.py --icons   also regenerate the icons (needs Pillow)


    index.html            the app, dictionary embedded
    manifest.webmanifest  name, colours, icons
    sw.js                 caches index.html so it opens with no signal
    icon-*.png            home screen icons

iOS ignores manifest icons for "Add to Home Screen" and uses apple-touch-icon,
and it will not accept a data: URI there — which is why the icons are real
files and this is a folder rather than the single HTML.
"""

import hashlib
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
DIST = HERE / "dist"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

INK = (22, 23, 26)        # matches the app's dark background
TILE = (250, 250, 248)
ACCENT = (79, 201, 160)


def icon(size: int, maskable: bool = False):
    """A letter tile: the thing the app is made of."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (size, size), INK + (255,))
    draw = ImageDraw.Draw(img)
    # Maskable icons get cropped to a circle by Android, so keep clear of edges.
    inset = size * (0.28 if maskable else 0.18)
    box = [inset, inset, size - inset, size - inset]
    radius = size * 0.08
    draw.rounded_rectangle(box, radius=radius, fill=TILE)
    draw.rounded_rectangle(box, radius=radius, outline=ACCENT, width=max(2, size // 48))

    glyph = size * (0.42 if maskable else 0.52)
    font = ImageFont.truetype(FONT, int(glyph))
    left, top, right, bottom = draw.textbbox((0, 0), "A", font=font)
    draw.text(((size - (right - left)) / 2 - left,
               (size - (bottom - top)) / 2 - top), "A", font=font, fill=INK)
    return img


def read_payload() -> str:
    """The compressed dictionary.

    Normally payload.b64, written by build_payload.py. That file is a build
    artifact and not committed, so on a fresh clone we recover it from the
    committed dist/index.html — which already contains exactly this string.
    That keeps the edit-the-UI loop to stdlib and one command, instead of a
    pip install and a 30-second vocabulary build.
    """
    cached = HERE / "payload.b64"
    if cached.exists():
        return cached.read_text().strip()

    built = DIST / "index.html"
    if built.exists():
        text = built.read_text()
        match = re.search(r'const PAYLOAD = "([^"]*)";', text)
        if match:
            print("payload.b64 missing — recovered from dist/index.html")
            return match.group(1)

    raise SystemExit(
        "No payload found. Run ./build.sh to build the dictionary from source.")


MANIFEST = """{
  "name": "anagrind",
  "short_name": "anagrind",
  "lang": "en",
  "id": "./",
  "description": "Crossword anagram solver. Fodder in, real answers out.",
  "start_url": "./",
  "scope": "./",
  "display": "standalone",
  "background_color": "#16171a",
  "theme_color": "#16171a",
  "icons": [
    {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
    {"src": "icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
  ]
}
"""

# Cache-first: the dictionary does not change between deploys, and the whole
# point is that it works on a train with no signal. ASSETS is every file in
# dist/ except sw.js itself — including icon-180.png, which the page requests
# on load as its apple-touch-icon.
#
# CACHE carries a hash of the built page. Without it a cache-first worker serves
# the old app forever, because it never asks the network what changed.
SERVICE_WORKER = """const CACHE = "anagrind-__BUILD__";
const ASSETS = ["./", "./index.html", "./manifest.webmanifest",
                "./icon-180.png", "./icon-192.png", "./icon-512.png",
                "./icon-maskable.png"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    caches.match(event.request, {ignoreSearch: true})
      .then(hit => hit || fetch(event.request).catch(err => {
        // Offline, and the URL is not one we precached. Every navigation here
        // is the same single page, so serve it rather than the browser's
        // offline error. Subresources keep failing, which is what they mean.
        if (event.request.mode === "navigate") return caches.match("./");
        throw err;
      }))
  );
});
"""


def main() -> None:
    DIST.mkdir(exist_ok=True)

    html = (HERE / "ui.template.html").read_text()
    page = html.replace("__PAYLOAD__", read_payload())
    (DIST / "index.html").write_text(page)

    build = hashlib.sha256(page.encode()).hexdigest()[:12]
    (DIST / "manifest.webmanifest").write_text(MANIFEST)
    (DIST / "sw.js").write_text(SERVICE_WORKER.replace("__BUILD__", build))

    icons = [("icon-180.png", 180, False), ("icon-192.png", 192, False),
             ("icon-512.png", 512, False), ("icon-maskable.png", 512, True)]
    if all((DIST / name).exists() for name, _, _ in icons) and "--icons" not in sys.argv:
        pass  # committed, and Pillow is not needed to rebuild the page
    else:
        for name, size, maskable in icons:
            icon(size, maskable).save(DIST / name)
    print(f"build {build}")

    total = sum(f.stat().st_size for f in DIST.iterdir()) / 1e6
    print(f"dist/ ready: {len(list(DIST.iterdir()))} files, {total:.1f} MB")
    for f in sorted(DIST.iterdir()):
        print(f"  {f.name:24} {f.stat().st_size/1024:8.0f} KB")


if __name__ == "__main__":
    main()
