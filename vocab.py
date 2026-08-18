"""Vocabulary sources for anagrind.

Two jobs, deliberately separated:

  words   — what counts as a legal word (single-word answers, combo fallback)
  phrases — what counts as a real multi-word answer

Sources, and what each one can and cannot tell us:

  UKACD     250k entries built for UK advanced cryptics, 53k of them
            multi-word. Attests, but carries no frequency data.
  WordNet   64k multi-word lemmas. Overlaps UKACD by only 13k, so the two are
            complementary rather than redundant; the union is ~105k.
  wordfreq  Zipf frequencies. Ranks, but attests nothing crossword-specific.

This file is the quality ceiling of the whole product. It is the only place
that changes when you want better answers.
"""

from __future__ import annotations

import pickle
from pathlib import Path

from solver import Index, normalise, split_entry

HERE = Path(__file__).parent
UKACD = HERE / "data" / "UKACD.txt"
CACHE = HERE / ".vocab-cache.pkl"
CACHE_VERSION = 2

# Below this Zipf a word is attested but unrankable -> BAND_UNRANKED.
RANKABLE_ZIPF = 1.0
# The combinatorial fallback only ever uses words we have solid evidence for;
# splitting letters across obscure words produces noise, not answers.
COMBO_MIN_ZIPF = 2.3
TOP_N = 200_000


def _sources() -> tuple[set[str], set[str], dict[str, float]]:
    from nltk.corpus import wordnet as wn
    from wordfreq import top_n_list, zipf_frequency

    words: set[str] = set()
    phrases: set[str] = set()
    freq: dict[str, float] = {}

    def keep(word: str) -> None:
        if word:
            words.add(word)
            if word not in freq:
                freq[word] = zipf_frequency(word, "en")

    # wordfreq — frequency evidence, plus common words UKACD may fold away.
    for raw in top_n_list("en", TOP_N):
        word = normalise(raw)
        if word and word == raw.lower() and zipf_frequency(word, "en") >= 2.3:
            keep(word)

    # WordNet — multi-word lemmas.
    for lemma in wn.all_lemma_names():
        parts, _ = split_entry(lemma)
        if not parts:
            continue
        for part in parts:
            keep(part)
        if len(parts) > 1:
            phrases.add(" ".join(parts))

    # UKACD — the crossword-specific spine. Hyphens preserved as separators.
    if UKACD.exists():
        for line in UKACD.open(encoding="latin-1"):
            entry = line.strip()
            if not entry or entry.startswith(("Copyright", "-")):
                continue
            parts, separators = split_entry(entry)
            if not parts:
                continue
            for part in parts:
                keep(part)
            if len(parts) > 1:
                phrases.add(
                    parts[0]
                    + "".join(s + p for s, p in zip(separators, parts[1:]))
                )

    return words, phrases, freq


# Only words this common can be swap candidates, so only these need synonyms.
SYNONYM_MIN_ZIPF = 3.0


def _synonym_map(freq: dict[str, float]) -> dict[str, frozenset[str]]:
    """word -> single-word lemmas sharing a synset with it.

    Precomputed into the cache rather than queried live: WordNet's corpus takes
    8.6s to load lazily, which is the entire cost of the diagnostics. Built
    here, it also keeps nltk off the runtime path altogether.
    """
    from nltk.corpus import wordnet as wn

    out: dict[str, frozenset[str]] = {}
    for word, zipf in freq.items():
        if zipf < SYNONYM_MIN_ZIPF:
            continue
        related = {
            lemma.name().lower()
            for synset in wn.synsets(word)
            for lemma in synset.lemmas()
            if "_" not in lemma.name()
        }
        related.discard(word)
        if related:
            out[word] = frozenset(related)
    return out


def load(rebuild: bool = False) -> Index:
    cached = None
    if CACHE.exists() and not rebuild:
        with CACHE.open("rb") as fh:
            version, tables, synonyms = pickle.load(fh)
        if version == CACHE_VERSION:
            cached = (tables, synonyms)

    if cached is None:
        words, phrases, freq = _sources()
        synonyms = _synonym_map(freq)
        tables = Index(words, phrases, freq).tables()
        with CACHE.open("wb") as fh:
            pickle.dump((CACHE_VERSION, tables, synonyms), fh, protocol=5)
    else:
        tables, synonyms = cached

    return Index.from_tables(
        tables,
        rankable_zipf=RANKABLE_ZIPF,
        combo_min_zipf=COMBO_MIN_ZIPF,
        synonyms=lambda word: synonyms.get(word, frozenset()),
    )


if __name__ == "__main__":
    import time

    started = time.perf_counter()
    idx = load(rebuild=True)
    n_words = sum(len(v) for d in idx.words_by_key.values() for v in d.values())
    n_phrases = sum(len(v) for v in idx.phrases_by_key.values())
    rankable = sum(1 for z in idx.freq.values() if z >= RANKABLE_ZIPF)
    print(f"{n_words:,} words ({rankable:,} rankable)  |  {n_phrases:,} phrases")
    print(f"{sum(1 for w in idx.freq if idx.synonyms(w)):,} words with synonyms")
    print(f"built in {time.perf_counter() - started:.1f}s")
