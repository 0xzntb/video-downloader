## What this changes

<!-- One paragraph: the bug, or the thing that was missing. Link the issue if there is one. -->

## Why this way

<!--
What you tried that did not work, and why this shape was chosen over the obvious
one. The code comments explain the choice; this is where a reviewer is told what
the alternatives were.
-->

## Checks

- [ ] `python -m pytest` passes
- [ ] `python -m ruff check viddl tests` is clean
- [ ] `python -m mypy` is clean
- [ ] `python -m pytest --cov=viddl --cov-fail-under=93` passes
- [ ] The new behaviour has a test that fails without the change
- [ ] `CHANGELOG.md` has a line under *Unreleased* (anything a user would notice)
- [ ] The README said what it says now — updated if this changes what a user sees
- [ ] The GIF and banner are regenerated if a screen, a colour or the progress bar moved

## Anything unverified

<!--
Platforms you could not run, checks you could only do by reading, tests that
skip on your machine, a claim that rests on documentation rather than a run.
Naming it is worth more than a green tick that cannot be checked.
-->
