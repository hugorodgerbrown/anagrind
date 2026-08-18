# anagrind

Crossword anagram solver. Give it the fodder and the enumeration, get real answers.

```
$ ./solve.py "on a train, up to its" "10,5"

  RANKED
    ● saturation point   11.2

  1 shown in 0 ms
```

## Putting it on your home screen

`dist/` is an installable web app: manifest, icons, and a service worker that
precaches everything. It is 2.2 MB of static files with no backend.

**Deploying to Render.** `dist/` is committed and `render.yaml` publishes it with
an empty build command, so the deploy has nothing to break: no Python on the
build image, no pip install, no 30-second vocabulary build. New → Static Site →
point at the repo. If you skip the blueprint, set publish directory to `dist`
and leave the build command blank.

To update: `python3 build_dist.py` locally, commit `dist/`, push.

Then, on the phone:

- **iOS** — open the URL in Safari, Share → Add to Home Screen.
- **Android** — open in Chrome, menu → Install app.

It opens without browser chrome, follows the system light/dark setting, and
**works with no signal at all** once installed. That last part is not incidental:
a crossword solver is used on trains. `sw.js` precaches the six assets on first
visit, and the offline reload is asserted in the browser tests.

The worker is cache-first, which means a stale cache would serve the old app
forever. Its cache name carries a hash of the built page, so a deploy that
changes anything invalidates it automatically — and `render.yaml` sends
`Cache-Control: no-cache` for `sw.js` itself, since a cached worker could never
learn about its own replacement.

iOS ignores manifest icons for Add to Home Screen and needs a real
`apple-touch-icon` file, which is why this ships as a folder and not as the
single HTML.

## Two ways to run it

**Standalone** — `anagrind.html`, one file, 2.2 MB. Open it on a phone or a
laptop; the dictionary is gzipped and embedded, so it needs no install and no
server. The combinatorial tier is capped at 400 results here.

| | before | now |
|---|---|---|
| startup, main thread | 3,035 ms | **380 ms** |
| heap at ready | 90 MB | **20 MB** |
| unsupported browser | hung on "Loading dictionary…" forever | named error |

Startup used to anagram-key all 238k words up front — 714 ms of it in one
synchronous loop, long enough for iOS to kill the tab. The payload is now
grouped by word length and by phrase total, so a query keys only the length it
touches (6 ms) and indexes only the phrases of its own total (7,156 of 112,672).
Frequencies stay eager because scoring any answer needs them, but they cost
104 ms without the keying.

**Test it in a browser, not in Node.** A `fetch("data:...")` loader once shipped
here and broke the page while every Node check stayed green — Node's `fetch`
loads data: URLs, and so does Chromium over `file://`. Only a page with a
Content-Security-Policy refuses them, which is exactly what a sandboxed viewer
applies. `verify_browser.js` now loads the built file in headless Chromium twice,
bare and under CSP, and the CSP pass fails on that loader and passes on the
current one. The payload is decoded in-page with `atob` and a manual byte loop
(224 ms); nothing in the load path touches the network.

The page makes **no external requests at all** — system fonts, no CDN, nothing
in the load path that touches the network. It follows the OS light/dark setting.
`verify_browser.js` asserts the request count is zero.

One caveat: `DecompressionStream` needs Safari 16.4+, Chrome 80+ or Firefox
113+. Older browsers get a named explanation instead of a spinner.

**Django service** — `python3 web.py` → http://127.0.0.1:8000. Same UI
template, served with no payload embedded, so the page calls `/api/solve` and
answers come from `solver.py` itself.

```
GET /api/solve?fodder=on+a+train,+up+to+its&enum=10,5&all=

{"answers":[{"text":"saturation point","parts":["saturation","point"],
             "band":0,"band_label":"ranked","tier":"phrase","score":11.195}]}
```

There is one fork between the two builds — `getAnswers()` in the template.
Everything else, including the banding and the tile animation, is shared.
`verify_ui.js` runs the browser solver in Node against the real payload and
checks all 15 Python expectations still hold, so the two cannot drift silently.

## Setup

```bash
pip install wordfreq nltk numpy
python3 -c "import nltk; nltk.download('wordnet')"
python3 vocab.py          # builds .vocab-cache.pkl, ~32s, 9.0 MB
python3 build_dist.py     # assembles dist/ for hosting
python3 -m pytest         # 32 passing
node verify_ui.js         # 15 browser/Python parity checks
node verify_load.js       # the real loadDictionary(), end to end
node verify_browser.js    # headless Chromium, bare and under CSP
node verify_deploy.js     # serves dist/: offline install, and redeploy reaching a user

python3 build_payload.py  # regenerate payload.b64 after changing vocab.py
python3 -c "open('anagrind.html','w').write(open('ui.template.html').read().replace('__PAYLOAD__', open('payload.b64').read().strip()))"
```

## Design

Three files, one responsibility each.

| File | Responsibility |
|---|---|
| `solver.py` | Search, banding, scoring. Pure, no I/O, no framework. |
| `vocab.py` | Where words and phrases come from. The only file you change to improve answer quality. |
| `solve.py` | CLI. |
| `web.py` | Django service: `/`, `/api/solve`, `/api/diagnose`. |
| `ui.template.html` | The interface, shared by both builds. |

### The search is not combinatorial

For a multi-word answer we don't generate word splits and filter them — we
index 112k attested phrases by `(anagram_key, length_pattern)` and look the
answer up in O(1). "10,5" over 15 letters resolves in 0.3 ms and returns
exactly one thing. Combinatorial search exists as an opt-in fallback (`--all`)
and is labelled unattested in the output.

Hyphens are carried through: `4-3` renders `take-out`, while a loose `4,3`
still finds it, because solvers type the comma out of habit.

### Three bands, because our sources disagree about what they know

| Band | Meaning | Marker |
|---|---|---|
| `RANKED` | Attested, and we have frequency evidence for every word | ● |
| `ATTESTED, UNRANKED` | In the dictionary, but at least one word has no frequency signal | ◐ |
| `UNATTESTED SPLIT` | A legal split of the letters, nothing more (opt-in) | ○ |

UKACD attests 250k crossword-legal entries but ships no frequency data —
50% of its single words have no Zipf signal at all. wordfreq ranks, but
attests nothing crossword-specific. Collapsing both into one number
fabricates confidence we don't have:

```
'a rope ends it' (11)          before             after
   desperation                  30.65             ● 10.7   RANKED
   esperantido                  20.00             ◐   —    ATTESTED, UNRANKED
```

That earlier 20.00 was pure membership bonus, not evidence. Scoring is now
`2·min(zipf) + mean(zipf)` — rarest word dominates, since a phrase is only as
plausible as its least plausible component — and **bands are never traded off
against score**. Obscurity is what advanced cryptics trade in, so a frequency
floor would delete exactly the entries UKACD exists to supply; the fix is
presentation, not exclusion.

## When there is no answer

An empty result looks the same whether the dictionary lacks the answer or the
solver was handed the wrong letters. In practice the second is far more common,
so `diagnose()` says what would have worked, most likely cause first.

```
$ ./solve.py "want top line" "11"
No attested answer fits those letters.

  DID YOU MEAN
    → want → need   needlepoint

  OTHER WORDS THAT WOULD ALSO FIT
      want → rays   personality
      ...
```

| Diagnostic | Catches | Cost |
|---|---|---|
| `word_swaps` | a misread clue word | 78 ms |
| `alternative_shapes` | a wrong enumeration | 2 ms |
| `letter_near_misses` | a typo | 420 ms first call, then ~20 ms |

**Why word swaps need a synonym signal.** `want` → `need` is three letter
substitutions — a letter distance of 6 out of 11, far past any threshold that
wouldn't return noise. So letter distance cannot find a misread word, and
`test_letter_distance_cannot_find_a_misread_word` pins that down.

Swapping same-length words does find it, but 25 different swaps produce a valid
11-letter answer and `needlepoint` ranks 10th by score. The one thing that
separates it: a solver who writes `want` for `need` has substituted a
**synonym**, and exactly 1 of those 25 is a WordNet synonym of the word typed.
So synonym swaps are marked confident and sorted first — the same rule as the
answer bands, grouping by kind of evidence instead of blending into one number.

Synonyms are precomputed into `.vocab-cache.pkl`. Querying WordNet live cost
8.6 s on first call, which was the entire runtime of the diagnostics; building
the map at cache time also keeps nltk off the runtime path.

## Sources

| Source | Contributes | Cannot tell us |
|---|---|---|
| [UKACD](data/LICENSE-UKACD.txt) 250k | 53k crossword-legal phrases, hyphenation, proper nouns | frequency |
| WordNet 64k lemmas | 64k phrases — overlaps UKACD by only 13k | crossword conventions |
| wordfreq | Zipf frequencies | attestation |

Union: **112,672 phrases, 237,658 words** (123k rankable).

UKACD is redistributed here under BSD-3-Clause, Copyright (c) 2009
J Ross Beresford. The notice is reproduced verbatim in
`data/LICENSE-UKACD.txt` and at the head of `data/UKACD.txt`, as its terms
require.

## Known limits

1. **Recall is still the open number.** `they see` (4,3) is a legitimate
   published answer and no gazetteer attests it. Measure recall against a
   corpus of real published answers before building UI.
2. **Capitalisation is lost.** UKACD marks proper nouns with a capital; we
   normalise it away, so `celtic cross` renders lowercase.
3. **Combo tier emits slot permutations** — `point on of return` and
   `point of on return` are separate rows. Noisy, opt-in only.
4. **4-word `--all` takes ~450 ms.** Queue it rather than serving inline.
5. **`web.py` is a dev server.** `DEBUG=True`, no CORS, no rate limiting, and
   `runserver` is single-process. Fine for testing, not for anything else.

## Django integration

`solver.py` has no framework dependencies and `Index` is immutable, so load it
once per process and share it:

```python
# apps/solver/apps.py
class SolverConfig(AppConfig):
    def ready(self):
        from . import vocab
        self.index = vocab.load()   # ~9.0 MB resident, thread-safe to read
```

Then a thin DRF view over `solve()`. At sub-millisecond per query for the
attested bands you can serve this synchronously; only `include_unattested`
needs a queue.
