"""pytest test_solver.py"""

import pytest

import vocab
from solver import (
    BAND_RANKED,
    BAND_UNATTESTED,
    BAND_UNRANKED,
    TIER_COMBO,
    TIER_PHRASE,
    anagram_key,
    normalise,
    parse_enumeration,
    solve,
    split_entry,
)


@pytest.fixture(scope="session")
def index():
    return vocab.load()


# -- normalisation ----------------------------------------------------------

def test_normalise_strips_punctuation_and_case():
    assert normalise("On a train, up to its!") == "onatrainuptoits"


def test_normalise_folds_accents():
    assert normalise("café-crème") == "cafecreme"


def test_anagram_key_matches_across_spacing():
    assert anagram_key("on a train, up to its") == anagram_key("saturation point")


def test_split_entry_records_separators():
    assert split_entry("point of no return") == (
        ("point", "of", "no", "return"), (" ", " ", " "))
    assert split_entry("take-out") == (("take", "out"), ("-",))


# -- enumeration ------------------------------------------------------------

@pytest.mark.parametrize("text", ["10,5", "10 5", "(10,5)", " 10 , 5 "])
def test_parse_equivalent_forms(text):
    assert parse_enumeration(text).lengths == (10, 5)


def test_parse_preserves_hyphen():
    enum = parse_enumeration("4-3,2")
    assert enum.lengths == (4, 3, 2)
    assert enum.render(["hard", "top", "of"]) == "hard-top of"


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_enumeration("banana")


# -- solving ----------------------------------------------------------------

@pytest.mark.parametrize("fodder,enum,expected", [
    ("on a train, up to its", "10,5", "saturation point"),
    ("no more stars", "11", "astronomers"),
    ("dirty room", "9", "dormitory"),
    ("a rope ends it", "11", "desperation"),
    ("voices rant on", "12", "conversation"),
])
def test_known_answers_rank_first(fodder, enum, expected, index):
    answers = solve(fodder, enum, index)
    assert answers, f"no answer for {fodder!r}"
    assert answers[0].text == expected
    assert answers[0].band == BAND_RANKED


def test_letter_count_mismatch_is_rejected(index):
    with pytest.raises(ValueError, match="15 letters"):
        solve("on a train, up to its", "10,4", index)


# -- bands ------------------------------------------------------------------

def test_unrankable_entry_is_banded_not_scored(index):
    """UKACD attests 'esperantido' but gives us nothing to rank it with. It must
    appear below 'desperation' on evidence, never above it on a phantom score."""
    answers = solve("a rope ends it", "11", index)
    texts = [a.text for a in answers]
    assert texts[0] == "desperation"
    assert "esperantido" in texts
    unranked = next(a for a in answers if a.text == "esperantido")
    assert unranked.band == BAND_UNRANKED
    assert unranked.score < answers[0].score


def test_bands_are_never_traded_off_against_score(index):
    answers = solve("point of no return", "5,2,2,6", index, include_unattested=True)
    bands = [a.band for a in answers]
    assert bands == sorted(bands), "a lower band outranked a higher one"


def test_attested_phrase_outranks_unattested_split(index):
    answers = solve("on a train, up to its", "10,5", index, include_unattested=True)
    assert answers[0].text == "saturation point"
    assert answers[0].band == BAND_RANKED
    assert answers[0].tier == TIER_PHRASE
    assert any(a.band == BAND_UNATTESTED for a in answers[1:])


def test_unattested_phrase_hidden_by_default(index):
    assert solve("the eyes", "4,3", index) == []
    fallback = solve("the eyes", "4,3", index, include_unattested=True)
    assert fallback[0].text == "they see"
    assert fallback[0].band == BAND_UNATTESTED


# -- separators -------------------------------------------------------------

def test_hyphen_enumeration_renders_hyphen(index):
    assert solve("out take", "4-3", index)[0].text == "take-out"


def test_loose_enumeration_still_finds_hyphenated_entry(index):
    assert solve("out take", "4,3", index)[0].words == ("take", "out")


# -- hygiene ----------------------------------------------------------------

def test_results_are_deduplicated(index):
    answers = solve("point of no return", "5,2,2,6", index, include_unattested=True)
    assert len({a.words for a in answers}) == len(answers)


def test_limit_is_respected(index):
    assert len(solve("the eyes", "4,3", index, limit=3, include_unattested=True)) == 3


# -- diagnostics ------------------------------------------------------------
from solver import alternative_shapes, diagnose, letter_near_misses, word_swaps  # noqa: E402


def test_misread_clue_word_is_found_and_marked_confident(index):
    """The real failure: 'want top line' for 'need top line'. Three letter
    substitutions, so no letter-distance check can see it."""
    suggestions = word_swaps("want top line", "11", index)
    assert suggestions, "no swap found"
    top = suggestions[0]
    assert top.answers[0].text == "needlepoint"
    assert top.detail == "want \u2192 need"
    assert top.confident, "a synonym swap must outrank coincidental fits"
    assert top.fodder is not None


def test_only_synonym_swaps_are_confident(index):
    """Precision check: of everything that merely fits, only the synonym is
    marked confident. If this starts failing the signal has gone noisy."""
    suggestions = word_swaps("want top line", "11", index, limit=25)
    confident = [s for s in suggestions if s.confident]
    assert len(confident) == 1
    assert confident[0].answers[0].text == "needlepoint"


def test_letter_distance_cannot_find_a_misread_word(index):
    """Documents the limit that makes word_swaps necessary."""
    misses = letter_near_misses("want top line", "11", index, max_distance=2)
    assert all(s.answers[0].text != "needlepoint" for s in misses)


def test_near_misses_find_a_typo(index):
    """One letter dropped from real fodder is exactly what this is for."""
    misses = letter_near_misses("no more star", "11", index, max_distance=2)
    assert any(s.answers[0].text == "astronomers" for s in misses)


def test_alternative_shapes_finds_the_right_enumeration(index):
    shapes = alternative_shapes("on a train, up to its", index)
    assert any(s.enumeration == "10,5" for s in shapes)


def test_no_diagnostics_offered_for_letters_that_go_nowhere(index):
    assert alternative_shapes("want top line", index) == []


def test_diagnose_leads_with_the_likeliest_cause(index):
    suggestions = diagnose("want top line", "11", index)
    assert suggestions[0].confident
    assert suggestions[0].answers[0].text == "needlepoint"


def test_suggested_fodder_actually_solves(index):
    """Every suggestion must be actionable — applying it has to work."""
    for s in diagnose("want top line", "11", index):
        if s.fodder:
            assert solve(s.fodder, "11", index)[0].text == s.answers[0].text
