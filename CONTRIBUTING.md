# Contributing to Viddl

Thanks for looking. Viddl is deliberately small — one terminal program, one
runtime dependency, no server — and that shape is the thing worth keeping. Most
of what follows is about not growing it by accident.

Bug reports are as welcome as code. So are translations: the explanations are
English and Spanish, and a third language is a copied module and a translated
dictionary rather than a change to any logic (see *Adding a language* in the
README).

## Setting it up

You need Python 3.9 or newer. Nothing else: no Node, no ffmpeg, no yt-dlp.

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

On Windows the same commands live in `.venv\Scripts\`, and `.venv\Scripts\python`
is the one to run.

## Before you commit

Those checks can run themselves, on every commit, in about two seconds. Opt in
once per clone — nothing to install, since the hook calls the tools the `dev`
extra already gave you:

```sh
git config core.hooksPath .githooks
```

`.githooks/pre-commit` looks at the files you staged: trailing whitespace in any
of them, `ruff` on the Python, `mypy` if anything under `viddl/` moved, and the
six pure test modules (225 tests, under two seconds). The rest of the suite takes
minutes and is the runner's job.

`git commit --no-verify` skips it. That is fine — the hook is a convenience, not
a gate, and CI is still the thing that decides. It also reads the files as they
are on disk rather than the staged copy, so a partial stage (`git add -p`) can
pass here and fail there.

## The commands CI runs

These five, in this order. A pull request that passes them locally will pass on
the runners; if it does not, that is worth an issue, not a retry.

```sh
.venv/bin/python -m pytest                                     # the whole suite
.venv/bin/python -m ruff check viddl tests                     # lint
.venv/bin/python -m mypy                                       # types (viddl/)
.venv/bin/python -m pytest --cov=viddl --cov-fail-under=93     # the coverage floor
.venv/bin/python -m pytest -m integration -rs                  # real yt-dlp; the merge needs ffmpeg
```

Coverage is 94.0% when the floor was set and the floor is 93, so new code can
land without the build failing for being new — but it cannot leave a hole.
`pytest-cov` arrives with the `dev` extra, which is why the command above is the
same one CI runs.

The last line needs a real `yt-dlp`, and is allowed to **skip** without one:
those tests are in `tests/test_integration.py`, marked `integration`, and they
serve a file from `127.0.0.1` and download it through the app's own
`DownloadJob`. The merge test in the same file also needs a real `ffmpeg`, since
it is the one that actually runs one. A skip is honest there; a pass is not, so
never make one mark a test that did not run as passing.

`ruff` and `mypy` are configured in `pyproject.toml`, and both are quiet
before a change rather than after one: a rule is switched on because it caught
something or because the codebase already obeys it, not because ruff ships it.
Adding a rule means fixing what it finds in the same commit.

Those five run against whatever the ranges in `pyproject.toml` allow today. To
run them against the versions they are known to pass with, use the lock:

```sh
uv sync --locked --extra dev
uv run python -m pytest
```

If you change a dependency — add one, move a bound — run `uv lock` and commit
`uv.lock` with the change. CI's `locked` job refuses to re-resolve, so the two
falling out of step fails the pull request instead of somebody's install.

## Where things are

| File | What lives there |
| --- | --- |
| `viddl/cli.py` | The arguments, `--doctor`, `--update`, and which front end runs |
| `viddl/download.py` | The download job, progress parsing, probing a link, playlist listing |
| `viddl/formats.py` | The quality menu: grouping rows, estimating sizes |
| `viddl/engine.py` | Finding, installing, verifying and updating yt-dlp and ffmpeg |
| `viddl/errors.py`, `viddl/i18n/` | The rules that turn engine output into a sentence, and their words |
| `viddl/tui/` | The full-screen app: screens, widgets, and the one stylesheet |
| `viddl/plain.py` | `--plain` and `--json` |
| `viddl/queue.py` | The playlist queue that takes one video at a time |
| `viddl/protocol.py` | `viddl://` parsing and registering it with the OS |
| `viddl/appdata.py` | Where files live, and the permissions they are given |
| `viddl/config.py` | The optional settings file |
| `viddl/history.py` | The last fifty completed downloads |
| `viddl/theme.py` | Colour schemes, contrast, `NO_COLOR` |
| `viddl/browser_extension/` | The Chrome and Firefox extension |
| `viddl/web_ui/` | The page `--web` serves |
| `viddl/util.py` | Link checking, redaction, and the small shared pieces |
| `docs/demo/` | The recorder and renderer behind the README's GIF and banner |

## House rules

- **One runtime dependency.** It is `textual`, and it stays one: if something can
  be done with the standard library, it is done there. Test tools belong in the
  `dev` extra. A pull request that adds a second dependency needs to say why in
  its first paragraph — and every dependency here carries an upper bound, because
  a new major is a version to try, not a version to discover on install.
- **The README is the manual, and it must be true.** A user-visible change gets
  its paragraph, in the section a person would look in. A number in the README is
  measured, not estimated — and when a measurement changes, the README changes
  with it.
- **Every fix gets a test that fails without it.** Not a test that the function
  was called: a test that would have caught the bug. The dialog for the quality
  menu, the progress template and the `viddl://` parser are where the last real
  bugs lived.
- **The suite may not depend on what the machine has installed.** A `yt-dlp`
  installed as a Python package is hidden from every test but the `integration`
  ones (see `tests/conftest.py`); Node being present adds the extension tests and
  its absence skips them. A test that only passes on your laptop is a test that
  fails on someone else's.
- **Never make a check quiet to get green.** Not the coverage floor, not a
  strictness flag, not an assertion. If a check is wrong, the fix is an argument
  in the pull request.
- **Add a `CHANGELOG.md` line** under *Unreleased* for anything a user would
  notice: a new flag, a changed default, a fixed annoyance. No version bump — the
  maintainer does that at a release, and the entry moves with the number.
- **Prose is British English** (licence, colour, behaviour); the code follows the
  libraries it talks to, including their American spellings in their own names.
- **Comments explain why.** The `#` lines in this codebase say what was tried, what
  broke, and which alternative was rejected — that is the part a reader cannot
  recover from the code.

## The screens change, the pictures too

The GIF and the banner at the top of the README are generated from the real app:

```sh
pip install pillow pyte matplotlib
.venv/bin/python docs/demo/record.py
.venv/bin/python docs/demo/render.py gif
.venv/bin/python docs/demo/render.py banner
```

If your change moves a screen, a colour or the progress bar, regenerate them and
look at the frames — the last two real bugs (a progress bar with a height of zero,
and a pasted link stamped across a border) were found by looking, not by a test.
Say in the pull request that the images are regenerated, so a reviewer knows the
diff is meant.

## Sending a pull request

Small and about one thing, please: a pull request that fixes a bug and also
renames a module cannot be reviewed or reverted in parts. The template asks for
what a reviewer cannot see from the diff — why this change, and what you tried
that did not work.

Say so in the description if a test is a skip on your machine, if something is
only checked by reading, or if you could not verify a platform. **A limitation
named is worth more than a claim that cannot be checked** — that is the rule the
README and `SUGGESTIONS.md` are written by, and it applies to pull requests too.

## Reporting something

- **A bug**: open an issue; the form asks for `viddl --version`, the platform,
  `viddl --doctor` and the lines around the failure in the log (they are redacted
  already, but worth reading before posting). A link that fails is useful; a link
  with a token in it is not — cut anything after a `?`.
- **A security problem**: not an issue. `SECURITY.md` explains the private
  advisory route and what the threat model already covers.
- **A site that stopped working**: try `viddl --update` first — a stale engine is
  the usual answer — and mention the engine's version if it is not.

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).
