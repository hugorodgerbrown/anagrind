"""Compress the vocabulary into the payload the browser build embeds.

Layout (\x1e between sections, \x1d between groups):

  1  words     groups of one length:  "LEN\\nword\\nword..."
  2  freqs     one char per word, parallel to group 1
  3  phrases   groups of one total letter count: "TOTAL\\nphrase\\nphrase..."
  4  synonyms  "word syn syn ..."

The grouping is what makes the browser build usable on a phone. Sorting 238k
words into anagram keys costs 714 ms of a 2.6 s startup, and a query touches
one word length and one phrase total — so grouped, that work happens on demand
in ~65 ms instead of up front on the main thread.
"""
import base64, gzip
from collections import defaultdict

import vocab
from solver import normalise

idx = vocab.load()
SWAP_MIN_ZIPF = 3.0
ALPHA = "0123456789abcdefghijklmnopqrstuvwxyz"

words = sorted({w for d in idx.words_by_key.values() for b in d.values() for w in b})
phrases = sorted({p[0][0] + "".join(s + w for s, w in zip(p[1], p[0][1:]))
                  for v in idx.phrases_by_key.values() for p in v})

by_length: dict[int, list[str]] = defaultdict(list)
for word in words:
    by_length[len(word)].append(word)

by_total: dict[int, list[str]] = defaultdict(list)
for phrase in phrases:
    by_total[len(normalise(phrase))].append(phrase)

word_groups, freq_groups = [], []
for length in sorted(by_length):
    group = by_length[length]
    word_groups.append(f"{length}\n" + "\n".join(group))
    # Zipf 0..7.5 -> one base36 char at 0.25 resolution. Lossy, imperceptible.
    freq_groups.append("".join(ALPHA[min(30, round(idx.zipf(w) * 4))] for w in group))

phrase_groups = [f"{total}\n" + "\n".join(by_total[total]) for total in sorted(by_total)]

# Only same-length synonyms common enough to be swap candidates are usable by
# the diagnostics: 0.05 MB gzipped instead of 0.33 MB for all of them.
syn_lines = []
for word in words:
    if idx.zipf(word) < SWAP_MIN_ZIPF:
        continue
    related = sorted(s for s in idx.synonyms(word)
                     if len(s) == len(word) and idx.zipf(s) >= SWAP_MIN_ZIPF)
    if related:
        syn_lines.append(word + " " + " ".join(related))

blob = "\x1e".join(["\x1d".join(word_groups), "\x1d".join(freq_groups),
                    "\x1d".join(phrase_groups), "\n".join(syn_lines)])
raw = gzip.compress(blob.encode(), 9)
b64 = base64.b64encode(raw).decode()
open("payload.b64", "w").write(b64)

print(f"{len(words):,} words in {len(word_groups)} length groups")
print(f"{len(phrases):,} phrases in {len(phrase_groups)} total groups")
print(f"{len(syn_lines):,} synonym sets")
print(f"gzip {len(raw)/1e6:.2f} MB -> base64 {len(b64)/1e6:.2f} MB")
