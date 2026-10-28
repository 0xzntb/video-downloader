"""Turn the mutation run's own summary into one number and an exit status (E11).

``mutmut export-cicd-stats`` writes the run's summary next to the mutated copy;
this reads it, prints the score, lists every mutant that got through, and fails
below the floor. The floor sits a little under what the run measures, so a patch
of changed code can move the number by a mutant or two without being called a
regression — the same balance the coverage floor strikes.

Three counts are in the denominator on purpose:

* ``survived`` — the code was changed and the suite still passed. The thing this
  exists to catch.
* ``no_tests`` — a mutant in a line the selected tests never execute. Counting
  it as a kill would let the mutated files rot into untested code while the
  score stayed high, which is the failure mode a mutation score is chosen to
  avoid in the first place.
* ``timeout`` and ``suspicious`` — neither a kill nor a clean escape, so neither
  counts as one.

``skipped`` is the one count left out: those are mutants mutmut itself declined
to run, not ones the suite let through.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

STATS = Path("mutants") / "mutmut-cicd-stats.json"

#: 96.6% measured on the run this landed with (256 of 265). 95 leaves room for a
#: patch of new code without leaving room for a hole.
FLOOR = 95.0

#: How a mutant reads in ``mutmut results``, for the list printed on failure.
MISSED = ("survived", "no tests", "timeout", "suspicious")


def _mutmut(*arguments: str, capture: bool = False) -> str:
    # ``capture_output`` and an explicit ``stdout`` cannot both be given, so the
    # two cases are two calls rather than one call with a conditional argument.
    if capture:
        completed = subprocess.run(
            [sys.executable, "-m", "mutmut", *arguments],
            check=True,
            text=True,
            capture_output=True,
        )
        return completed.stdout or ""
    subprocess.run(
        [sys.executable, "-m", "mutmut", *arguments],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return ""


def main() -> int:
    _mutmut("export-cicd-stats")
    stats = json.loads(STATS.read_text(encoding="utf-8"))

    killed = stats["killed"]
    missed = sum(stats[name] for name in ("survived", "no_tests", "timeout", "suspicious"))
    scored = killed + missed
    score = 100.0 * killed / scored if scored else 0.0

    print(f"mutants scored: {scored} ({killed} killed, {missed} missed)")
    print(f"mutation score: {score:.1f}% (floor {FLOOR:.0f}%)")

    if missed:
        print("missed:")
        for line in _mutmut("results", capture=True).splitlines():
            if line.strip().endswith(MISSED):
                print(f"  {line.strip()}")

    return 0 if score >= FLOOR else 1


if __name__ == "__main__":
    raise SystemExit(main())
