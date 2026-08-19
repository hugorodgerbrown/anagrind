"""anagrind — crossword anagram solver.

Core search. Pure functions + an in-memory index. No I/O, no framework.

Two ideas carry the design:

1.  Multi-word answers are not found by combinatorial search. A gazetteer of
    attested phrases is indexed by (anagram_key, length_pattern) and looked up
    in O(1). Combinatorial search exists only as an opt-in fallback.

2.  Results are grouped into three bands before they are scored, because our
    dictionaries disagree about what they can tell us. UKACD attests words it
    cannot rank; wordfreq ranks words it does not attest. A single score would
    silently fabricate confidence we do not have, so the band is carried
    through to the UI instead.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Sequence

ALPHABET = "abcdefghijklmnopqrstuvwxyz"

# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------


def normalise(text: str) -> str:
    """Strip everything that isn't a letter. 'Mañana, up to it!' -> 'mananauptoit'."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped.lower() if c in ALPHABET)


def anagram_key(text: str) -> str:
    """Canonical form of a letter multiset. Anagrams share a key."""
    return "".join(sorted(normalise(text)))


def split_entry(entry: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """'point of no return' -> (('point','of','no','return'), (' ',' ',' '))
       'hard-top'           -> (('hard','top'), ('-',))"""
    tokens = [t for t in re.split(r"([\s_\-]+)", entry.strip()) if t]
    words: list[str] = []
    separators: list[str] = []
    for token in tokens:
        if re.fullmatch(r"[\s_\-]+", token):
            if words:
                separators.append("-" if "-" in token else " ")
        else:
            word = normalise(token)
            if not word:
                return (), ()
            words.append(word)
    return tuple(words), tuple(separators[: len(words) - 1])


# --------------------------------------------------------------------------
# Enumeration  ("10,5" / "4-3,2" / "10 5")
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Enumeration:
    """A crossword enumeration: word lengths plus the separators between them."""

    lengths: tuple[int, ...]
    separators: tuple[str, ...]  # len == len(lengths) - 1; " " or "-"

    @property
    def total(self) -> int:
        return sum(self.lengths)

    @property
    def specifies_separators(self) -> bool:
        return "-" in self.separators

    def render(self, words: Sequence[str], separators: Sequence[str] = ()) -> str:
        seps = separators or self.separators
        out = words[0]
        for sep, word in zip(seps, words[1:]):
            out += sep + word
        return out

    def __str__(self) -> str:
        out = str(self.lengths[0])
        for sep, n in zip(self.separators, self.lengths[1:]):
            out += ("-" if sep == "-" else ",") + str(n)
        return out


_ENUM_TOKEN = re.compile(r"(\d+)|([,\-\s]+)")


def parse_enumeration(text: str) -> Enumeration:
    """Accepts '10,5', '10 5', '(10,5)', '4-3,2'. Hyphen is preserved."""
    lengths: list[int] = []
    separators: list[str] = []
    pending: str | None = None

    for number, sep in _ENUM_TOKEN.findall(text.strip().strip("()")):
        if number:
            if lengths:
                separators.append(pending or " ")
            lengths.append(int(number))
            pending = None
        else:
            pending = "-" if "-" in sep else " "

    if not lengths:
        raise ValueError(f"Could not read an enumeration from {text!r}")
    if any(n <= 0 for n in lengths):
        raise ValueError("Word lengths must be positive")
    return Enumeration(tuple(lengths), tuple(separators))


# --------------------------------------------------------------------------
# Pattern  ("r_c_n_l_" / "t_k_,o_t" / "t_k_-o_t")
#
# The other way a solver arrives at a crossword: not with fodder to rearrange
# but with a half-filled grid entry and a shape. A blank is a space, an
# underscore or a question mark — whichever the solver's hands reach for.
#
# The separators carry the enumeration, so there is nothing to type twice:
# 't_k_,o_t' is (4,3) and 't_k_-o_t' is 4-3. An enumeration entered next to a
# pattern could only ever agree with it or be wrong.
# --------------------------------------------------------------------------

BLANK = "."          # one unknown letter, in the compiled form
_BLANK_CHARS = " _?"


@dataclass(frozen=True)
class Pattern:
    """Known letters and gaps, already split into words."""

    words: tuple[str, ...]           # each character is a-z or BLANK
    separators: tuple[str, ...]      # len == len(words) - 1; " " or "-"

    @property
    def total(self) -> int:
        return sum(len(w) for w in self.words)

    @property
    def enumeration(self) -> Enumeration:
        """The shape this pattern states. Derived, never entered."""
        return Enumeration(tuple(len(w) for w in self.words), self.separators)

    @property
    def is_open(self) -> bool:
        """Nothing known at all — every position blank."""
        return all(c == BLANK for w in self.words for c in w)

    def matches(self, words: Sequence[str]) -> bool:
        if len(words) != len(self.words):
            return False
        return all(
            len(word) == len(slot)
            and all(c == BLANK or c == letter for c, letter in zip(slot, word))
            for slot, word in zip(self.words, words)
        )

    def __str__(self) -> str:
        out = self.words[0] if self.words else ""
        for sep, word in zip(self.separators, self.words[1:]):
            out += sep + word
        return out


def parse_pattern(text: str) -> Pattern:
    """Accepts 'r_c_n_l_', 'r c n l ', 'r?c?n?l?', 't_k_,o_t', 't_k_-o_t'.

    Space is a blank, not a word break: a solver typing a pattern is filling
    in squares, and the break between words is what the comma is for.
    """
    words: list[str] = []
    separators: list[str] = []
    current: list[str] = []
    pending: str | None = None

    for char in normalise_keep_shape(text):
        if char in ",-":
            if current:
                if words:
                    separators.append(pending or " ")
                words.append("".join(current))
                current = []
                pending = "-" if char == "-" else " "
            continue
        current.append(BLANK if char in _BLANK_CHARS else char)

    if current:
        if words:
            separators.append(pending or " ")
        words.append("".join(current))
    return Pattern(tuple(words), tuple(separators))


def normalise_keep_shape(text: str) -> str:
    """normalise(), but blanks and separators survive — they are the pattern."""
    stripped = unicodedata.normalize("NFKD", text).lower()
    return "".join(
        c for c in stripped
        if c.isascii() and (c.isalpha() or c in _BLANK_CHARS or c in ",-")
    )


# --------------------------------------------------------------------------
# Bands and tiers
# --------------------------------------------------------------------------

TIER_PHRASE = "phrase"  # answer is an attested multi-word entry
TIER_WORD = "word"      # answer is an attested single word
TIER_COMBO = "combo"    # letters split into valid words, phrase unattested

BAND_RANKED = 0      # attested, and we have frequency evidence for every word
BAND_UNRANKED = 1    # attested, but at least one word has no frequency signal
BAND_UNATTESTED = 2  # a legal split of the letters, nothing more

BAND_LABEL = {
    BAND_RANKED: "ranked",
    BAND_UNRANKED: "attested, unranked",
    BAND_UNATTESTED: "unattested split",
}


@dataclass(frozen=True)
class Answer:
    text: str
    words: tuple[str, ...]
    tier: str
    band: int
    score: float

    @property
    def band_label(self) -> str:
        return BAND_LABEL[self.band]


# --------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------


class Index:
    """Immutable lookup structures built once from a vocabulary.

    `freq` maps word -> Zipf frequency. A word absent from `freq`, or below
    `rankable_zipf`, is attested but unrankable — see BAND_UNRANKED.
    """

    def __init__(
        self,
        words: Iterable[str],
        phrases: Iterable[str],
        freq: dict[str, float],
        rankable_zipf: float = 1.0,
        combo_min_zipf: float = 2.3,
        synonyms: "Callable[[str], set[str]] | None" = None,
    ) -> None:
        self.freq = freq
        self.rankable_zipf = rankable_zipf
        self.combo_min_zipf = combo_min_zipf
        # Injected rather than imported: solver.py stays free of corpus deps.
        self.synonyms = synonyms or (lambda word: set())

        # length -> anagram key -> words
        self.words_by_key: dict[int, dict[str, list[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for word in words:
            norm = normalise(word)
            if norm:
                bucket = self.words_by_key[len(norm)][anagram_key(norm)]
                if norm not in bucket:
                    bucket.append(norm)

        # (anagram key, length pattern) -> [(words, separators)]
        self.phrases_by_key: dict[
            tuple[str, tuple[int, ...]], list[tuple[tuple[str, ...], tuple[str, ...]]]
        ] = defaultdict(list)
        for phrase in phrases:
            parts, seps = split_entry(phrase)
            if len(parts) < 2:
                continue
            key = (anagram_key("".join(parts)), tuple(len(p) for p in parts))
            entry = (parts, seps)
            if entry not in self.phrases_by_key[key]:
                self.phrases_by_key[key].append(entry)

        self._matrices: dict[int, tuple] = {}      # lazily built, combo tier
        self._key_matrices: dict[int, tuple] = {}  # lazily built, diagnostics

    # -- serialisation ---------------------------------------------------

    def tables(self) -> tuple:
        """The built lookup structures, ready to pickle."""
        return (dict(self.words_by_key), dict(self.phrases_by_key), self.freq)

    @classmethod
    def from_tables(
        cls,
        tables: tuple,
        rankable_zipf: float = 1.0,
        combo_min_zipf: float = 2.3,
        synonyms: "Callable[[str], set[str]] | None" = None,
    ) -> "Index":
        """Rebuild from `tables()` without redoing the work.

        Constructing the index from raw wordlists costs 6.1s, almost all of it
        sorting 238k words into anagram keys. Caching the result instead takes
        cold start from 7.5s to 0.7s, which is the difference between being
        able to use scale-to-zero hosting and not."""
        self = cls.__new__(cls)
        self.words_by_key, self.phrases_by_key, self.freq = tables
        self.rankable_zipf = rankable_zipf
        self.combo_min_zipf = combo_min_zipf
        self.synonyms = synonyms or (lambda word: set())
        self._matrices = {}
        self._key_matrices = {}
        return self

    # -- scoring ---------------------------------------------------------

    def zipf(self, word: str) -> float:
        return self.freq.get(word, 0.0)

    def band(self, words: Sequence[str], tier: str) -> int:
        if tier == TIER_COMBO:
            return BAND_UNATTESTED
        if min(self.zipf(w) for w in words) < self.rankable_zipf:
            return BAND_UNRANKED
        return BAND_RANKED

    def score(self, words: Sequence[str]) -> float:
        """Higher is better. Rarest component dominates — a phrase is only as
        plausible as its least plausible word. Pure frequency evidence: no tier
        bonus, so a score is never inflated by mere membership of a wordlist."""
        zipfs = [self.zipf(w) for w in words]
        return round(min(zipfs) * 2 + sum(zipfs) / len(zipfs), 3)

    # -- combinatorial fallback ------------------------------------------

    def _matrix(self, length: int):
        import numpy as np

        if length not in self._matrices:
            words = [
                w
                for bucket in self.words_by_key.get(length, {}).values()
                for w in bucket
                if self.zipf(w) >= self.combo_min_zipf
            ]
            mat = np.zeros((len(words), 26), dtype="uint8")
            for i, word in enumerate(words):
                for letter, n in Counter(word).items():
                    mat[i, ord(letter) - 97] = n
            self._matrices[length] = (words, mat)
        return self._matrices[length]

    def _key_matrix(self, length: int):
        """Anagram keys of one length as a count matrix, for distance scans."""
        import numpy as np

        if length not in self._key_matrices:
            keys = list(self.words_by_key.get(length, {}))
            mat = np.zeros((len(keys), 26), dtype="uint8")
            for i, key in enumerate(keys):
                for letter, n in Counter(key).items():
                    mat[i, ord(letter) - 97] = n
            self._key_matrices[length] = (keys, mat)
        return self._key_matrices[length]

    def _split(self, counts, lengths: tuple[int, ...]) -> Iterator[tuple[str, ...]]:
        """Yield every way to spend `counts` on words of the given lengths."""
        import numpy as np

        if len(lengths) == 1:
            key = "".join(chr(97 + i) * int(n) for i, n in enumerate(counts) if n)
            for word in self.words_by_key.get(lengths[0], {}).get(key, ()):
                if self.zipf(word) >= self.combo_min_zipf:
                    yield (word,)
            return

        words, mat = self._matrix(lengths[0])
        if not words:
            return
        for i in np.flatnonzero((mat <= counts).all(axis=1)):
            for rest in self._split(counts - mat[i], lengths[1:]):
                yield (words[i],) + rest


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


def solve(
    fodder: str,
    enumeration: str | Enumeration,
    index: Index,
    limit: int = 25,
    include_unattested: bool = False,
) -> list[Answer]:
    """Find answers that use the fodder's letters exactly and fit the enumeration.

    Ordered by band first, then by frequency score. Band order is a statement
    about evidence, not about likelihood, and is never traded off against score.
    """
    enum = (
        enumeration
        if isinstance(enumeration, Enumeration)
        else parse_enumeration(enumeration)
    )
    letters = normalise(fodder)
    if len(letters) != enum.total:
        raise ValueError(
            f"Fodder has {len(letters)} letters but enumeration ({enum}) needs {enum.total}"
        )

    key = anagram_key(letters)
    seen: set[tuple[str, ...]] = set()
    answers: list[Answer] = []

    def add(words: tuple[str, ...], tier: str, separators: Sequence[str] = ()) -> None:
        if words in seen:
            return
        seen.add(words)
        answers.append(
            Answer(
                text=enum.render(words, separators),
                words=words,
                tier=tier,
                band=index.band(words, tier),
                score=index.score(words),
            )
        )

    if len(enum.lengths) == 1:
        for word in index.words_by_key.get(enum.lengths[0], {}).get(key, ()):
            add((word,), TIER_WORD)
    else:
        entries = index.phrases_by_key.get((key, enum.lengths), ())
        # If the setter told us where the hyphens go, honour it — but only if
        # something matches, since users often type '4,3' for a hyphenated answer.
        if enum.specifies_separators:
            exact = [e for e in entries if e[1] == enum.separators]
            entries = exact or entries
        for words, separators in entries:
            add(words, TIER_PHRASE, separators)

    if include_unattested and len(enum.lengths) > 1:
        import numpy as np

        counts = np.zeros(26, dtype="uint8")
        for letter, n in Counter(letters).items():
            counts[ord(letter) - 97] = n
        # Longest word first: the biggest constraint prunes hardest.
        order = sorted(range(len(enum.lengths)), key=lambda i: -enum.lengths[i])
        for combo in index._split(counts, tuple(enum.lengths[i] for i in order)):
            restored = [""] * len(combo)
            for slot, word in zip(order, combo):
                restored[slot] = word
            add(tuple(restored), TIER_COMBO)

    answers.sort(key=lambda a: (a.band, -a.score, a.text))
    return answers[:limit]


def find_pattern(
    pattern: str | Pattern,
    index: Index,
    limit: int = 25,
) -> list[Answer]:
    """Find attested entries whose letters fit the pattern.

    Same bands and same ordering as solve(): evidence first, and never traded
    off against score. There is no unattested tier here — a pattern with no
    match has no legal split to fall back on, only a wrong grid.
    """
    pat = pattern if isinstance(pattern, Pattern) else parse_pattern(pattern)
    if not pat.words:
        return []

    enum = pat.enumeration
    answers: list[Answer] = []
    seen: set[tuple[str, ...]] = set()

    def add(words: tuple[str, ...], tier: str, separators: Sequence[str] = ()) -> None:
        if words in seen:
            return
        seen.add(words)
        answers.append(
            Answer(
                text=enum.render(words, separators),
                words=words,
                tier=tier,
                band=index.band(words, tier),
                score=index.score(words),
            )
        )

    if len(pat.words) == 1:
        slot = pat.words[0]
        for bucket in index.words_by_key.get(len(slot), {}).values():
            for word in bucket:
                if pat.matches((word,)):
                    add((word,), TIER_WORD)
    else:
        matched: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        for (_, lengths), entries in index.phrases_by_key.items():
            if lengths != enum.lengths:
                continue
            matched += [(w, s) for w, s in entries if pat.matches(w)]

        # Same rule as solve(): if the pattern says where the hyphen goes,
        # honour it — but only if something matches, since a solver typing a
        # comma is not asserting there is no hyphen.
        if enum.specifies_separators:
            exact = [m for m in matched if m[1] == enum.separators]
            matched = exact or matched

        # 'take out' and 'take-out' are both attested and dedupe to the same
        # words, so without an order the winner is whichever the index happened
        # to yield first — and the browser picked the other one.
        matched.sort(key=lambda m: ("".join(m[1]), m[0]))
        for words, separators in matched:
            add(words, TIER_PHRASE, separators)

    answers.sort(key=lambda a: (a.band, -a.score, a.text))
    return answers[:limit]


# --------------------------------------------------------------------------
# Diagnostics
#
# A bare "no answer" is a dead end: it looks the same whether the dictionary
# lacks the answer or the solver was handed the wrong letters. In practice the
# second is far more common, so when the search comes back empty we say what
# would have worked, and why we think so.
# --------------------------------------------------------------------------

WORD_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class Suggestion:
    kind: str                  # "word" | "shape" | "letters"
    detail: str                # the change, in the user's terms
    fodder: str | None         # replacement fodder, if the change is to the fodder
    enumeration: str | None    # replacement enumeration, if the change is the shape
    confident: bool            # a reason to believe this specific change, not just that it fits
    answers: tuple[Answer, ...]


def word_swaps(
    fodder: str,
    enumeration: str | Enumeration,
    index: Index,
    min_zipf: float = 3.0,
    limit: int = 6,
) -> list[Suggestion]:
    """Replace one word of the fodder with another of the same length.

    This is the diagnostic that matches how clues actually go wrong: the solver
    misreads one word of the surface. 'want' for 'need' is three letter
    substitutions — invisible to any letter-distance check — but a single swap
    here.

    Swaps that merely fit are cheap and plentiful. A swap to a *synonym* of the
    word the user typed is evidence about the mistake itself, so those are
    marked confident and sorted first rather than blended into one score.
    """
    enum = (enumeration if isinstance(enumeration, Enumeration)
            else parse_enumeration(enumeration))
    best: dict[tuple[str, ...], Suggestion] = {}

    for token in WORD_TOKEN.finditer(fodder):
        original = normalise(token.group())
        if not original:
            continue
        related = {w.lower() for w in index.synonyms(original)}
        for bucket in index.words_by_key.get(len(original), {}).values():
            for candidate in bucket:
                if candidate == original or index.zipf(candidate) < min_zipf:
                    continue
                probe = fodder[: token.start()] + candidate + fodder[token.end():]
                try:
                    answers = solve(probe, enum, index, limit=2)
                except ValueError:
                    continue
                for answer in answers:
                    if answer.band != BAND_RANKED:
                        continue
                    # Synset membership is symmetric, so one lookup per token
                    # suffices; checking the reverse cost 9 seconds and nothing.
                    confident = candidate in related
                    prior = best.get(answer.words)
                    if prior and (prior.confident, index.zipf(
                            normalise(prior.detail.split()[-1]))) >= (
                            confident, index.zipf(candidate)):
                        continue
                    best[answer.words] = Suggestion(
                        kind="word",
                        detail=f"{original} \u2192 {candidate}",
                        fodder=probe,
                        enumeration=None,
                        confident=confident,
                        answers=(answer,),
                    )

    return sorted(best.values(),
                  key=lambda s: (not s.confident, -s.answers[0].score))[:limit]


def alternative_shapes(
    fodder: str, index: Index, max_words: int = 4, limit: int = 6
) -> list[Suggestion]:
    """Which enumerations do these letters fit? Catches a wrong answer shape."""
    total = len(normalise(fodder))
    if not total:
        return []

    def partitions(remaining: int, prefix: tuple[int, ...]):
        if not remaining:
            yield prefix
            return
        if len(prefix) >= max_words:
            return
        for n in range(1, remaining + 1):
            yield from partitions(remaining - n, prefix + (n,))

    out: list[Suggestion] = []
    for shape in partitions(total, ()):
        enum = Enumeration(shape, (" ",) * (len(shape) - 1))
        answers = solve(fodder, enum, index, limit=3)
        if answers:
            out.append(Suggestion(
                kind="shape", detail=f"({enum})", fodder=None,
                enumeration=str(enum), confident=False, answers=tuple(answers)))
        if len(out) >= limit:
            break
    return out


def letter_near_misses(
    fodder: str,
    enumeration: str | Enumeration,
    index: Index,
    max_distance: int = 2,
    limit: int = 6,
) -> list[Suggestion]:
    """Entries within a letter or two of the fodder. Catches typos only.

    Distance counts letters added plus letters dropped, so one substitution
    costs 2. Past that the noise swamps the signal, which is why this cannot
    find a misread word and word_swaps exists.
    """
    enum = (enumeration if isinstance(enumeration, Enumeration)
            else parse_enumeration(enumeration))
    have = Counter(normalise(fodder))
    out: list[Suggestion] = []

    def describe(key: str) -> tuple[int, str] | None:
        want = Counter(key)
        add, drop = want - have, have - want
        distance = sum(add.values()) + sum(drop.values())
        if not 0 < distance <= max_distance:
            return None
        bits = []
        if add:
            bits.append("add " + " ".join(sorted(add.elements())).upper())
        if drop:
            bits.append("drop " + " ".join(sorted(drop.elements())).upper())
        return distance, ", ".join(bits)

    def emit(text: str, words: tuple[str, ...], note: str) -> None:
        tier = TIER_WORD if len(words) == 1 else TIER_PHRASE
        out.append(Suggestion(
            kind="letters", detail=text, fodder=None, enumeration=None,
            confident=False,
            answers=(Answer(text, words, tier, index.band(words, tier),
                            index.score(words)),)))

    if len(enum.lengths) == 1:
        import numpy as np

        target = np.zeros(26, dtype="int16")
        for letter, n in have.items():
            target[ord(letter) - 97] = n
        lo = max(1, enum.lengths[0] - max_distance)
        for length in range(lo, enum.lengths[0] + max_distance + 1):
            keys, mat = index._key_matrix(length)
            if not keys:
                continue
            # One vectorised pass beats 250k Counter constructions by ~50x.
            close = np.flatnonzero(
                np.abs(mat.astype("int16") - target).sum(axis=1) <= max_distance)
            for i in close:
                found = describe(keys[i])
                if found:
                    for word in index.words_by_key[length][keys[i]]:
                        emit(word, (word,), found[1])
    else:
        for (key, lengths), entries in index.phrases_by_key.items():
            if abs(sum(lengths) - enum.total) > max_distance:
                continue
            found = describe(key)
            if found:
                for words, seps in entries:
                    emit(enum.render(words, seps), words, found[1])

    out.sort(key=lambda s: (s.answers[0].band, -s.answers[0].score))
    return out[:limit]


def diagnose(
    fodder: str, enumeration: str | Enumeration, index: Index, limit: int = 4
) -> list[Suggestion]:
    """Everything worth saying when the search came back empty, most likely
    cause first: a misread clue word, then a wrong shape, then a typo."""
    return (word_swaps(fodder, enumeration, index, limit=limit)
            + alternative_shapes(fodder, index, limit=limit)
            + letter_near_misses(fodder, enumeration, index, limit=limit))
