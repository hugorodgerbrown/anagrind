#!/usr/bin/env python3
"""anagrind CLI.

    ./solve.py "on a train, up to its" "10,5"
    ./solve.py "the eyes" "4,3" --all
"""

from __future__ import annotations

import argparse
import sys
import time

import vocab
from solver import (BAND_LABEL, BAND_RANKED, BAND_UNATTESTED, BAND_UNRANKED,
                    diagnose, solve)

MARK = {BAND_RANKED: "\u25cf", BAND_UNRANKED: "\u25d0", BAND_UNATTESTED: "\u25cb"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Solve a crossword anagram.")
    parser.add_argument("fodder", help="the letters; punctuation and spaces ignored")
    parser.add_argument("enumeration", help="answer shape, e.g. '10,5' or '4-3,2'")
    parser.add_argument("-a", "--all", action="store_true",
                        help="also show unattested word splits")
    parser.add_argument("-n", "--limit", type=int, default=25)
    args = parser.parse_args(argv)

    index = vocab.load()
    started = time.perf_counter()
    try:
        answers = solve(args.fodder, args.enumeration, index,
                        limit=args.limit, include_unattested=args.all)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    elapsed = (time.perf_counter() - started) * 1000

    if not answers:
        print("No attested answer fits those letters.")
        suggestions = diagnose(args.fodder, args.enumeration, index)
        if suggestions:
            width = max(len(s.detail) for s in suggestions)
            heading = None
            for s in suggestions:
                want = ("Did you mean" if s.confident else
                        {"word": "Other words that would also fit",
                         "shape": "These letters do fit another shape",
                         "letters": "Within a letter or two"}[s.kind])
                if want != heading:
                    heading = want
                    print(f"\n  {want.upper()}")
                mark = "\u2192" if s.confident else " "
                print(f"    {mark} {s.detail:<{width}}  {s.answers[0].text}")
        if not args.all:
            print("\n  Add --all to see unattested word splits.")
        return 1

    width = max(len(a.text) for a in answers)
    band = None
    for answer in answers:
        if answer.band != band:
            band = answer.band
            print(f"\n  {BAND_LABEL[band].upper()}")
        score = f"{answer.score:6.1f}" if answer.band == BAND_RANKED else "     \u2014"
        print(f"    {MARK[answer.band]} {answer.text:<{width}} {score}")

    print(f"\n  {len(answers)} shown in {elapsed:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
