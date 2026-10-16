# Viddl — suggested improvements

A prioritised list of ways to make Viddl better, grouped by the axis it improves.
Every claim below was checked against the code in this repository, not recalled —
the searchable evidence is in [Appendix A](#appendix-a--evidence).

**Baseline at the time of writing:** commit `30941ff`, 288 tests passing, 93%
statement coverage (`viddl/`), `pyflakes` clean, no CI, no lockfile, single
runtime dependency (`textual>=0.80`).

**Legend**

| Mark | Meaning |
| --- | --- |
| **P0** | Security or correctness. Fix before promoting the project. |
| **P1** | High value — real user impact or it blocks growth. |
| **P2** | Worth doing; improves quality of life. |
| **P3** | Nice to have. |
| **S / M / L** | Rough effort: hours / a day or two / a week-ish. |

---

## Top 10, if you only do ten things

| # | Item | Why it matters |
| --- | --- | --- |
| 1 | **S1** — stop option-shaped "links" reaching yt-dlp | Proven argument injection; a web page can trigger it |
| 2 | **E1** — add CI | Nothing is checked on push today; 288 tests only run if someone runs them |
| 3 | **E2** — add a `LICENSE` file | README and `pyproject.toml` both claim MIT; no licence text ships |
| 4 | **R1** — resume interrupted downloads | A cancelled or dropped download starts from zero |
| 5 | **R4** — `viddl --update` | Stale yt-dlp is *the* top cause of "this link stopped working" |
| 6 | **F1** — cookies from the browser | Unlocks Instagram, Threads, TikTok, sometimes Vimeo (README says unsupported) |
| 7 | **S2/S3** — verify and lock down downloaded helpers | Executables are fetched and `chmod +x`'d with no hash check |
| 8 | **D1** — make the log tell the truth | README promises last-run details; the log is almost always empty |
| 9 | **E7** — one real end-to-end test | Unit tests stub the engine; nothing proves the pipeline works together |
| 10 | **U1** — theme and `NO_COLOR` support | Hardcoded colours; no light-terminal or high-contrast story |

---

## 1. Safety

### S1 — Option-shaped "links" reach yt-dlp's command line · **P0** · S

`DownloadJob.build_args()` appends the user's URL as the last argv element with
no `--` separator:

```
… -f bv*+ba/b --config-locations=./evil.conf
```

and the guard in front of it, `util.is_probably_url()`, happily accepts strings
that start with `-`:

| input | `is_probably_url()` |
| --- | --- |
| `--exec=calc.exe` | **True** |
| `--config-locations=./x.conf` | **True** |
| `--paths=./out` | **True** |
| `--download-archive=./a.txt` | **True** |
| `-o/tmp/x.mp4` | False |
| `ytsearch1:cats` | False |

Reachable route, verified end to end: a page opens
`viddl://download?url=--config-locations%3D.%2Fx.conf`, `parse_scheme()` returns
`--config-locations=./x.conf` as the *url*, that survives the `is_probably_url`
check, and lands on the command line where yt-dlp consumes it as a **flag**.
yt-dlp's `--config-locations` loads a config file that may itself contain
`--exec`, so this is command execution delegated through a config file rather
than a cosmetic bug. The plain CLI is not affected — `argparse` rejects a
leading-dash token before it gets that far (`--uri --config-locations=…` exits 2).

Two caveats, stated honestly: Chrome requires a user gesture before launching an
external protocol, and the injected path must satisfy that `.`-in-first-segment
heuristic (so `/tmp/evil.conf` is rejected while `./evil.conf` is not). Neither
weakens the fix.

**Do:**

1. `args.append("--")` before the URL — the actual fix.
2. Validate at the boundary: in `cli.main()` and `parse_scheme()` require
   `urlparse(url).scheme in ("http", "https")` and reject anything starting with
   `-`. Keep `is_probably_url()` as the friendly pre-check, not the guard.
3. Add `--ignore-config` to `build_args()` as defence in depth. Note it does
   *not* neutralise an explicit `--config-locations` (yt-dlp documents it as
   "don't load any more configuration files **except those given to**
   `--config-locations`"), which is why (1) comes first.
4. Regression tests: `build_args()` contains `--` before the URL; every payload
   in the table above is rejected by the CLI and by `parse_scheme()`.

### S2 — Downloaded helpers are never verified · **P1** · M

`engine.download_to()` streams over HTTPS and does a size sanity check
(empty file → error), then `install_ytdlp`/`install_ffmpeg` `chmod 0o755` and
execute the result. There is no hash or signature check anywhere, so a
compromised mirror, a DNS/host hijack or a tampered release asset becomes local
code execution with the user's privileges. The mirror list is hardcoded
(`github.com/BtbN/FFmpeg-Builds/.../latest/`, `johnvansickle.com`,
`eugeneware/ffmpeg-static`, `yt-dlp` releases).

**Do:** pin expected SHA-256 digests per platform in one constant and verify
after download; fail loudly with the mirror named. Alternatively verify yt-dlp's
published `SHA2-256SUMS`. At minimum, reject non-`https://` URLs explicitly
rather than relying on the caller's list, and record the hash of what was
installed in `--doctor` output so a later replacement is visible.

### S3 — The app's own directory is world-readable and holds executables · **P1** · S

`ensure_dirs()` creates `~/.local/share/viddl` and `bin/` with whatever the umask
gives (commonly `0755`). That folder holds two executables (`yt-dlp`, `ffmpeg`)
plus a log.

**Do:** create the data dir `0o700` (and `bin/` too), and chmod existing dirs to
match on startup. Cheap, and it removes a local privilege-escalation surface on
shared machines.

### S4 — Log content and permissions · **P1** · S

The log is created with the default mode and there is no redaction. Video URLs
can carry tokens, signed query strings or account identifiers; a future
"log the command line" change (see **D1**) would write those to disk forever, as
`viddl.log` is never rotated or truncated.

**Do:** create the log `0o600`, rotate/truncate at, say, 256 KB, and redact
query strings for a documented set of parameters before writing any URL. Make
redaction a function with tests, since it is exactly the kind of code that rots.

### S5 — Any web page can cold-open the app · **P2** · M

The whole design is that a page hands over a `viddl://` link, and the app opens
with that link pre-filled. That is a good default — nothing downloads without a
deliberate click on a quality. The risk is nuisance/abuse rather than data loss:
a hostile page can spawn terminal windows, and combined with **S1** it currently
does worse.

**Do:** keep "never auto-download from a hand-off" as an invariant and write it
down as one (a comment and a test). Consider a soft rate limit (ignore further
hand-offs while one is within N seconds), single-instance behaviour (focus the
existing window instead of opening a second one), and document the threat model
in `SECURITY.md`.

### S6 — Environment overrides are undocumented and unvalidated · **P2** · S

`VIDDL_HOME`, `VIDDL_DOWNLOAD_DIR`, `VIDDL_YTDLP` and `VIDDL_FFMPEG` are all read
from the environment. `VIDDL_YTDLP` in particular points the app at an arbitrary
executable. That is useful and intended, but it is a config-injection vector in
shared/CI environments and it is invisible to users.

**Do:** document all four (see **D2**), and have `--doctor` print which override
is in effect and where the tool came from (`origin` is already tracked on `Tool`
— surface it).

### S7 — Extension hygiene (already good, keep it that way) · **P2** · S

The manifest is genuinely minimal: `contextMenus`, `activeTab`, `scripting`, one
content script scoped to `*://*.youtube.com/*`, MV3 service worker, no host
permissions, no remote code. This is a real strength — worth stating in the
README as a selling point.

**Do:** add `minimum_chrome_version`, keep the "no background page reads" promise
in a comment, and add a Firefox build (see **F15**). Also add a `SECURITY.md`
contact so a reporter has somewhere to go.

---

## 2. Stability and correctness

### R1 — Interrupted downloads start over · **P1** · M

`build_args()` sets `--retries 5 --fragment-retries 5` for network hiccups, but
there is no `-c/--continue`. Worse, cancelling deletes the work:
`run()` → `_cleanup_partial()` removes partial files. A 4 GB download that the
user pauses at 90% is lost, and a dropped connection mid-download is re-fetched
from byte zero.

**Do:** add `-c/--continue` and keep `.part` files on cancel, with the progress
screen offering **Resume** / **Discard**. Add a `--keep-partial` internal flag
and make `_cleanup_partial()` opt-in rather than automatic. yt-dlp resumes
`.part` files; the same change makes `--keep-video` leftovers reusable.

### R2 — Cancelling does not stop the children · **P1** · S

`cancel()` calls `process.terminate()` on the yt-dlp process only. ffmpeg is a
child of yt-dlp, so a merge in flight can outlive the UI's cancel and keep
writing. `terminate()` is also `SIGTERM`: yt-dlp handles it, but a wedged process
is never escalated.

**Do:** start the process with `start_new_session=True` (POSIX) and kill the
process group; escalate `terminate()` → `kill()` after a few seconds; `wait()`
with a timeout. On Windows use `CREATE_NEW_PROCESS_GROUP` / `taskkill /T`. Add a
test that a fake long-running child is actually gone after cancel.

### R3 — yt-dlp's user config leaks into every download · **P1** · S

No `--ignore-config`, so a user's `~/.config/yt-dlp/config` (or a portable
`yt-dlp.conf` next to the binary) silently changes Viddl's behaviour — output
paths, post-processors, `--exec`. Some of that is desirable; the surprise factor
is not, and combined with **S1** it widens the injection surface.

**Do:** pass `--ignore-config` and expose the escape hatch deliberately
(`VIDDL_YTDLP_ARGS` or a documented `--ytdlp-arg` passthrough), so behaviour is
reproducible and anything unusual is explicit. See
[yt-dlp README](https://github.com/yt-dlp/yt-dlp/tree/master#configuration) and
the [Arch man page](https://man.archlinux.org/man/extra/yt-dlp/yt-dlp.1.en).

### R4 — Nothing updates the engine · **P1** · M

The README is candid: "a link that works one week can stop working the next
until yt-dlp updates." Yet there is no way to update it. Whichever copy
`find_ytdlp()` picks — order is `VIDDL_YTDLP` → **Viddl's own copy** →
`python -m yt_dlp` → system `PATH` — is whatever was installed first and stays
there. (Note this contradicts the README's "If you already have it installed,
Viddl uses yours instead": that is true until Viddl fetches its own copy, after
which its own copy wins and can shadow a newer system one.)

**Do:**

- `viddl --update` to refresh the bundled yt-dlp (and `--update-engine` for
  ffmpeg), with the new version printed.
- A staleness note in the UI/`--doctor` when the engine is older than N days
  ("This engine is 41 days old — run `viddl --update` if a site stopped working").
- Reconsider precedence: prefer a newer of the two rather than always preferring
  the bundled copy. `Tool.version` already exists, so this is a small comparison.
- Mention `VIDDL_YTDLP` as the escape hatch for users who keep their own.

### R5 — No job-level retry, and no distinction between temporary and permanent · **P1** · M

`errors.explain()` maps raw output to friendly text and `is_fatal_ytdlp_line()`
exists, but a run either succeeds or raises. A 429, a flaky CDN or a momentary
DNS failure ends the download with a message about the network.

**Do:** classify failures (permanent: private/removed/geo/404; temporary: 429,
5xx, timeouts, `<urlopen error>`), and retry temporary ones with exponential
backoff and a visible "retrying in 4s (2/3)" state. Nothing erodes trust like a
failure that a second attempt would have fixed.

### R6 — Two instances can collide on the same output · **P2** · S

`_find_output()` prefers a recorded path, then the newest non-intermediate file
in the output directory. Run two downloads of the same video at once (two
terminal windows) and, with `--no-overwrites` unset, the second can clobber the
first while the first instance reports a file whose bytes changed underneath it.

**Do:** write to a unique temporary name and rename into place (as
`engine.download_to` already does for its own downloads), and/or take a lock file
per output directory.

### R7 — `ensure_dirs()` hides a fatal problem · **P2** · S

It swallows `OSError` deliberately, with a comment saying a read-only home is
"better handled later with a clear message". It mostly is —
`downloads_dir()` falls back to `data_dir()/Downloads` when the real Downloads
folder is not writable, which is thoughtful — but a read-only home then fails
much later with a message about downloads rather than about permissions.

**Do:** remember the failure (`appdata.ensure_dirs()` returning the error, or a
module-level `last_error`) and surface it in `--doctor` and on the home screen.

### R8 — The ffmpeg download is large, unverified and unresumable · **P2** · M

~100 MB from a hardcoded mirror with `_TIMEOUT = 30`, no resume, no hash check
(**S2**), and on macOS arm64 the first mirror is native while the fallback is
`x64` — which only works through Rosetta, so a user without Rosetta gets a
confusing failure rather than a clear message.

**Do:** show the download size before asking (the app already asks before
downloading large files — good), resume interrupted transfers with a Range
request, verify the hash, and detect Rosetta availability on macOS or say plainly
that the fallback needs it.

### R9 — Probing is a single 150-second blocking call · **P2** · M

`probe_video()` runs `--dump-single-json` with a 150 s timeout and no cache. On a
slow site the qualities screen simply sits there; clicking a second link
re-probes from scratch; and for a large playlist link yt-dlp may fetch far more
metadata than the app needs.

**Do:** show elapsed time and a "still working" hint after ~5 s with a cancel
option; cache probe results in memory for a few minutes; the app already passes
`--no-playlist`, so also consider `--flat-playlist` tolerance where relevant.

### R10 — Windows is ported but not exercised · **P2** · M

The code handles the registry, `ffmpeg.exe`, `%LOCALAPPDATA%` and `USERPROFILE`,
and the README admits Windows is tested less. There is no `install.ps1`. Terminals
on Windows also default to legacy encodings, and the TUI uses box-drawing
characters and `·`/`…` throughout — a real risk of mojibake that no test covers.

**Do:** add `install.ps1` (and a `.cmd` shim), run the suite on Windows in CI
(**E1**), and add an ASCII/`--plain` rendering path (**U4**) with a test that the
UI text survives `cp1252` encoding.

### R11 — Timeouts and limits are constants · **P3** · S

`_TIMEOUT = 30`, `_PROBE_TIMEOUT = 25`, `probe_video(timeout=150)`,
`--socket-timeout 20`, `--trim-filenames 150`, `_MAX_ROWS = 9`, `_MIN_HEIGHT = 144`,
`MP3_BITRATES`. All sensible defaults; none adjustable. A user on a slow link or
a fast one has no lever.

**Do:** expose the network ones behind `VIDDL_TIMEOUT` / a config file (**F13**)
and leave the presentation ones alone unless someone asks.

---

## 3. Functionality

### F1 — Cookies from the browser · **P1** · M

The README lists Instagram, Threads, and sometimes Vimeo/TikTok as needing a
signed-in session and says "signed-in downloads are not supported yet". yt-dlp
supports `--cookies-from-browser`, which would turn a documented dead end into a
button.

**Do:** a "Sign in with my browser" row on the qualities screen that passes
`--cookies-from-browser <choice>` (Chrome/Firefox/Edge/Brave/Safari, auto-detected
from what is installed). Must never store credentials: rely on yt-dlp reading the
browser's cookie store. Add a clear note that this reads local browser cookies
for that site only — it is a trust-sensitive feature, so say so in the UI, not
just the README.

### F2 — Playlists · **P1** · M

Currently `--no-playlist` plus a note: "Only the first of N videos in this
playlist." That is a reasonable default and a good message, but a playlist link is
common.

**Do:** when `parse_info()` detects a playlist, offer a checklist screen (all /
some / just this one) and run a sequential queue with per-item progress and a
final summary. Reuse the batch machinery from **F7**.

### F3 — `viddl --update` · **P1** · S

See **R4** — listed again because it is also the single most requested kind of
feature in this space: "it broke, make it work again."

### F4 — More than three audio options · **P2** · S

`MP3_BITRATES = ((320, "0"), (192, "5"), (128, "7"))` and the audio path always
targets MP3. m4a/opus/flac are lossless-or-better choices people actually want,
and for many videos the source is already m4a/opus — so re-encoding to MP3
throws quality away for no reason.

**Do:** add "Audio only · original (m4a/opus, no re-encode)" and a format picker
(mp3/m4a/opus/flac/wav) with bitrate presets, reusing the existing estimate logic.

### F5 — Subtitles, thumbnails and metadata · **P2** · M

`--write-subs`, `--embed-subs`, `--embed-thumbnail`, `--embed-metadata` and
`--embed-chapters` are all one flag each, and the app already owns the ffmpeg
prompt that these need.

**Do:** a small "extras" toggles row on the qualities screen, off by default,
with "needs ffmpeg" surfaced the same way the quality rows already do it.

### F6 — Download archive and a recent-downloads list · **P2** · M

Re-downloading something already on disk is a common frustration; the app notices
only via yt-dlp's own "has already been downloaded" line (`_ALREADY_RE`), which
it handles — but nothing is remembered between runs.

**Do:** optional `--download-archive` in the app data dir (which also makes
playlist downloads incremental), plus a "Recent" screen built from a small JSON
file (path, title, size, when) with Reveal/Copy/Re-download actions. That is also
the natural home for **F9** notifications.

### F7 — Batch queue · **P2** · L

Paste several links (or select playlist entries) and let them run in order, with
per-item state and an overall summary.

### F8 — Clipboard-aware start · **P2** · S

The home screen says "Paste a video link", but nothing offers the link that is
already on the clipboard. Checking the clipboard on launch is what mail clients
do, and it removes the most common first friction.

**Do:** offer "The clipboard has a link — download it?" with a *Paste* key
binding (**U5**). Never auto-fill silently, never auto-start.

### F9 — Tell the user when a long download finishes · **P2** · M

A 40-minute download ends in a terminal the user has long since switched away
from. There is no desktop notification and no terminal bell.

**Do:** opt-in notifications (`notify-send`, `osascript`, PowerShell toast) with a
terminal-bell fallback, plus a "when done" toggle on the progress screen.

### F10 — Machine-readable output · **P2** · M

`--list-qualities` prints human text. `--doctor` prints human text. Neither is
parseable, which blocks scripts and the Playwright/desktop front-ends people
build on top of tools like this.

**Do:** `--json` for `--list-qualities`, `--doctor` and (optionally) download
progress as JSON lines; a `--quiet` that prints only the final path. Small, and
it multiplies the project's usefulness.

### F11 — Bandwidth and network control · **P3** · S

`--limit-rate`, `--proxy`, `--concurrent-fragments` tuning, `--throttled-rate`
for slow hosts. Worth exposing behind an "Advanced" row once **F13** exists.

### F12 — Live streams · **P3** · M

The README says live streams work but are recorded from the current point. A
banner saying so, plus `--live-from-start` as an explicit choice and a clear
"stop" that keeps what has been recorded so far, would make that honest and
useful rather than surprising.

### F13 — Optional config file · **P3** · M

The README sells "nothing to configure" — a genuine strength worth keeping as the
default. But "remember my download folder / default quality / theme" needs
somewhere to live.

**Do:** zero-config by default; write `~/.config/viddl/config.toml` only when the
user changes a preference, and keep every setting overridable by a flag. Config
precedence (flags > env > file > defaults) needs tests, since it is the kind of
thing that silently misbehaves.

### F14 — Search by name · **P3** · M

`viddl "some song"` currently fails ("does not look like a video link"). yt-dlp
can search YouTube via `ytsearch1:`. Note this must land *after* **S1** — search
strings contain spaces and colons, and the fix there is what makes it safe.

### F15 — Broader browser support · **P3** · M

The extension is Chromium-only (`chrome.*` APIs, MV3 manifest, no
`browser_specific_settings`), yet Edge and Brave are mentioned in the install
text. Firefox users are left out, and there is no store packaging — "load
unpacked" is a developer workflow, not a distribution strategy.

**Do:** a Firefox build (MV3 with `browser_specific_settings`, or an MV2 fallback
while Firefox's MV3 support settles), and publish to the Chrome Web Store so
installing does not require Developer mode.

### F16 — The local web UI, done safely · **P3** · L

The natural "second front door": a `viddl --web` that serves a page on
`127.0.0.1`, reusing `probe_video` and `DownloadJob`, with real format choices
and real downloads. Two constraints worth writing down before anyone builds it:

1. **Loopback only, tiny surface.** Bind `127.0.0.1`, not `0.0.0.0`; no CORS;
   protect state-changing endpoints against cross-site requests (a page in the
   user's browser can POST to localhost); generate a one-time token in the URL.
2. **It cannot be hosted.** yt-dlp and ffmpeg need to run on the user's machine,
   so a public site can only ever be a demo unless it proxies through a paid
   third-party download API. Anyone promising a hosted version is promising
   someone else's infrastructure.

---

## 4. Aesthetics and user experience

### U1 — Theme, `NO_COLOR`, and light terminals · **P2** · M

`ViddlApp.CSS` is a single hardcoded stylesheet. There is no theme selection, no
`NO_COLOR` handling, and no high-contrast variant, so the app looks the same on a
bright office terminal as it does on a dark one.

**Do:** move colours into Textual theme variables, respect `NO_COLOR` and
`TERM=dumb`, add `--theme light|dark|high-contrast` (and a Settings row), and
check contrast ratios rather than eyeballing them.

### U2 — Put progress in the terminal title · **P2** · S

Long downloads happen in a window the user is not looking at. Setting the window
title to `Viddl — 42% — Killing Time.mp4` (or restoring it on exit) is one escape
sequence and makes the app legible from the task bar.

### U3 — A richer done screen · **P2** · S

The done screen shows the full path with Copy/Open-folder/Download-another —
already better than most tools. Missing: the file's size and duration, a Play
action, and any way to see *why* something failed in detail.

**Do:** show size/duration/format from the probe, add Reveal-in-file-manager and
Play, and give failures a "Show details" expander with the log lines and a
"Copy details" button (reusing `errors.explain`).

### U4 — A `--plain` mode · **P2** · M

The TUI needs a real terminal; `cli.main()` catches `OSError` and says so
politely. But Textual cannot render in some SSH sessions, in terminals without
the right capabilities, in CI, or with a screen reader.

**Do:** a line-oriented mode (`viddl --plain <link> --quality 720 --yes`) that
prints progress on one line and the final path on its own — usable from scripts,
`ssh`, and a11y tooling. This also removes the need for the `OSError` dead end.

### U5 — Keyboard affordances · **P2** · S

Bindings today: `Esc`, `Tab`, `Ctrl+Q`, `Ctrl+C`, arrows/`j`/`k`, `Enter`, `c`.
Cheap wins: `p` to paste the clipboard link, `r` to retry a failed download,
`o` to open the folder from the done screen, and `?` for a key help overlay.
Also: `Esc` currently cancels a running download instantly — consider confirming,
since **R1** means the lost progress is real.

### U6 — Accessibility · **P2** · M

Mostly good already: rows carry text labels alongside any colour, status is
written out rather than implied, and errors are full sentences. Gaps: colour-only
state changes in some widgets, no screen-reader announcements (`aria-live`
equivalent in Textual), no way to disable the alternate screen buffer, and no
test that the UI is usable without colour.

**Do:** audit the CSS for colour-only meaning, announce state changes
(`app.bell()`/notifications already exist in places), and add an `--accessible`
mode that implies `--plain`-style output.

### U7 — Localisation · **P3** · L

All user-facing text is inline English, including ~20 hand-written error
explanation rules in `errors.py` — the most valuable strings in the app, and the
least translatable because they are regex-triggered prose.

**Do:** extract strings to a catalogue (gettext or a simple dict per locale),
keeping the TUI screens and `errors.py` in the same system. Start with the error
messages; that is where non-English users hit walls.

### U8 — Motion, emptiness and feedback polish · **P3** · S

An animated "finding qualities" indicator instead of a static label; a progress
bar that does not jump when `total_bytes_estimate` replaces `total_bytes`; an
explicit empty state on the home screen with an example link; and no flicker on
downloads fast enough to finish before the progress screen paints.

### U9 — Sell it visually · **P3** · S

The README has two hand-drawn ASCII screens, which is charming but does not
survive a skim. A 15-second `asciinema`/GIF of "paste link → pick 720p → done
screen", plus the existing icons reused as a banner, is the single highest-value
doc change for adoption.

---

## 5. Engineering hygiene and testing

### E1 — There is no CI · **P1** · S

No `.github/` at all. The 288 tests, the 93% coverage and the `pyflakes` pass
only happen when a human runs them. For a project whose README says "a link that
works one week can stop working the next", an unexamined suite is the wrong risk.

**Do:** GitHub Actions matrix — Linux/macOS/Windows × Python 3.9–3.13 — running
`pytest --cov` with a coverage floor, `pyflakes`/`ruff`, and the Node extension
tests (`tests/extension.test.js` already exists but is not wired into anything).
Add a weekly job that runs the extractor-dependent integration test (**E7**)
against the latest yt-dlp, because that is the failure mode that actually breaks
users.

### E2 — Project hygiene files · **P1** · S

Missing: `LICENSE` (README and `pyproject.toml` both declare MIT — no licence
text ships), `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`,
issue/PR templates, `CODE_OF_CONDUCT.md`. `.gitignore` also lacks the
`.coverage` artifact and `coverage.xml`, so a coverage run dirties the tree.

**Do:** add the licence first — it is a one-minute fix for an actual legal gap.

### E3 — Make the log tell the truth (see D1) · **P1** · S

The README promises: "The log holds the last run's details if something failed in
a way the screen could not explain." In practice the file is created and left at
**0 bytes**: `LOGGER` appears twice in the whole codebase — its definition and a
single `LOGGER.exception("Viddl: could not update the screen")`. Nothing logs a
failed download, the yt-dlp command line, the exit code or the raised error.

**Do:** log the command line, exit code and error details on failure, with the
redaction from **S4**. Then the README becomes true, and `--doctor` becomes
genuinely useful for support.

### E4 — Reproducible installs · **P2** · S

No lockfile anywhere; `textual>=0.80` has no upper bound, so a future Textual
major can break the app on a fresh install with no warning, and CI cannot be
reproducible without pins. `install.sh` says "The lockfile-free equivalent"
because there is no lockfile.

**Do:** pin dev dependencies, add a lock (`uv.lock` or `pip-tools`) for CI, and
give `textual` an upper bound (`textual>=0.80,<1.0` or a tested range). Keep the
runtime dependency list at one package — that minimalism is a feature.

### E5 — Static analysis beyond pyflakes · **P2** · M

`pyflakes` catches unused imports and typos; it does not check types, and the
codebase is fully annotated (`from __future__ import annotations` everywhere,
dataclasses, `Optional`/`List` types). That annotation effort is currently
unverified.

**Do:** add `ruff` (fast, replaces pyflakes and adds style rules) and `mypy`
(strict-ish, at least on `viddl/`) to CI. The types already written will find
real bugs immediately — `_find_output`, `MediaOption` and the `Tool` candidates
list are exactly where a checker earns its keep.

### E6 — pre-commit hooks · **P2** · S

Run `ruff`, `pyflakes`, the fast test subset and a whitespace check before commit.
Low effort, keeps CI green.

### E7 — One real end-to-end test · **P2** · M

Every test stubs the engine, `probe_video` and `DownloadJob`. Nothing proves that
Viddl + real yt-dlp + real ffmpeg produce a file. That is the seam where the last
two real bugs lived (the unrecorded `[Merger]` path and the un-awaited copy
coroutine), and unit tests will keep missing it.

**Do:** serve a tiny generated `.mp4` over `http.server` on `127.0.0.1`, run a
real yt-dlp against it (yt-dlp handles plain HTTP URLs and local files), and
assert the file lands where `_find_output` says. Deterministic, offline, no
third-party site involved. Mark it `-m integration`, run it in CI only when
yt-dlp is present, and let missing yt-dlp be a skip (never a false pass).

### E8 — Browser-level extension tests · **P2** · M

`tests/extension.test.js` covers the pure JS logic via Node, and `test_extension.py`
checks the manifest and referenced files. Nothing loads the popup, clicks the
button, or verifies the `viddl://` link the page ends up with.

**Do:** Playwright with a persistent Chromium context loading the unpacked
extension: assert the popup renders, that the YouTube button appears, and that
the created link decodes to the expected `viddl://download?url=…&audio=1`. This
is also where **S1**'s fix gets a second, independent lock.

### E9 — Fuzz and property tests for the parsers · **P2** · M

`parse_scheme()` parses untrusted input from the web; `errors.explain()` matches
regexes against arbitrary tool output; `human_size`/`human_duration`/
`estimate_size`/`_find_output` all have invariants. Those are the highest-value
targets for `hypothesis` (plus a small fuzz run over `parse_scheme`).

### E10 — Coverage ratchet and the remaining gaps · **P2** · S

93% overall; the thin spots are `tui/widgets.py` (89%), `errors.py` (86%),
`protocol.py` (87%) and `__main__.py` (0%). Meanwhile the two OS-specific
registration paths are *simulated* rather than executed.

**Do:** set a CI floor (93%) so coverage cannot silently regress, cover
`__main__.py` trivially, and be explicit in the README that Windows/macOS
registration behaviour is simulated by the suite (**E1**'s matrix is the real fix).

### E11 — Mutation testing · **P3** · M

We found two genuine bugs by hand while writing tests — a `[Merger]` regex in a
dead branch and a missing `await`. Mutation testing (`mutmut`) finds that class
of weak-assertion gap automatically: it flips code and checks the suite notices.

### E12 — Distribution and packaging · **P3** · L

Right now: `git clone` + `./install.sh`. `pipx install viddl` would be one line
if the package were on PyPI; Homebrew/Scoop/winget/AUR reach people who do not
clone repos; `install.ps1` closes the Windows gap the README admits.

**Do:** publish to PyPI (the metadata is close — add `authors`, `urls` and
`classifiers`, and ship the `LICENSE` from **E2** first), document `pipx`, then
add a Homebrew tap and a Scoop manifest. Add a `--version --verbose`
that prints Viddl, Python, yt-dlp and ffmpeg versions in one block — that is what
every bug report needs.

### E13 — The user-agent points at the wrong repo · **P3** · S

`_UA = f"Viddl/{__version__} (+https://github.com/freebuff/viddl)"` — that URL is
not this repository, so any site operator who follows it lands somewhere
unrelated. Fix the URL or make it configurable, and keep the UA informative
(version only) rather than pretending to be a browser.

### E14 — Local, sanitised failure reporting · **P3** · S

The app handles errors well but gives the user nothing to paste into a bug
report. `viddl --report` could print a copy-pasteable block: versions from
**E12**, the platform, which tool origin was used, the last error (redacted by
**S4**) and the log path. No telemetry, no network — consistent with the
project's "nothing leaves your machine" promise.

---

## 6. Documentation

| # | Item | Detail |
| --- | --- | --- |
| **D1** | **The log promise is not met** | README: "The log holds the last run's details if something failed." Reality: the file is created empty, and one `LOGGER.exception` call exists in total. Either log failures (**E3**) or soften the claim. |
| **D2** | **Undocumented environment variables** | `VIDDL_HOME`, `VIDDL_DOWNLOAD_DIR`, `VIDDL_YTDLP`, `VIDDL_FFMPEG` are all live but unmentioned. Add a table with what each overrides and why you would use it. |
| **D3** | **Document the `viddl://` security model** | Any page can open the app; the app never downloads without a click. Write that down as a guarantee, alongside the fix for **S1**. |
| **D4** | **Troubleshooting / FAQ** | Stale extractor ("run `viddl --update`"), login-required sites (**F1**), corporate proxies, Windows console rendering (**R10**), where the log lives. Each is a support question waiting to happen. |
| **D5** | **`--version --verbose` and a man page** | `viddl.1` for Unix packaging; the verbose version block from **E12** embedded as a code block in the README. |
| **D6** | **Architecture notes** | A short `docs/` page on the `Services` seam, why the app is local-only, and the precedence rules for engine and config. It is the thing a new contributor needs most. |

---

## 7. Suggested order

1. **S1** (argument injection) + regression tests — do this before anything else.
2. **E2** (LICENSE) and **E1** (CI) — cheap, and they protect everything after.
3. **S3**/**S4** (dir and log permissions) and **E3/D1** (make the log real).
4. **R1**/**R2** (resume, and actually stop children) — the biggest day-to-day pain.
5. **R4**/**F3** (`viddl --update` + staleness hint) — the fix for the most common complaint.
6. **S2** + **R3** (verify downloads, `--ignore-config`) — finish the security pass.
7. **F1** (cookies) — turns a documented dead end into a feature.
8. **E7** (one real end-to-end test) + **E10** (coverage floor).
9. **U1**/**U2**/**U3** (theme, title, done screen) — visible quality.
10. **F6**/**F9** (archive + recents + notifications) — the features that make it a daily tool.

---

## Appendix A — Evidence

Everything below was run in this repository at commit `30941ff`.

**Option-shaped input passes the URL check** — `python -c "from viddl.util import
is_probably_url; ..."`:

```
True   --exec=calc.exe
True   --config-locations=./x.conf
True   --paths=./out
True   --download-archive=./a.txt
False  -o/tmp/x.mp4
False  ytsearch1:cats
True   https://example.com/v
```

**It reaches the command line** — building a `DownloadJob` with the string as the
URL (engine stubbed, nothing executed):

```
… -P /tmp -o %(title)s [%(id)s].%(ext)s -f bv*+ba/b --config-locations=./evil.conf
option-shaped argument reached the command line: True
```

Note the absence of `--` before the final token; `grep -n 'args.append(self.url)'`
shows it is appended raw, and `grep` finds no `--ignore-config`, `--no-exec`,
`--continue` or `--no-part` anywhere in `viddl/`.

**It is reachable from a web page** — `parse_scheme` on
`viddl://download?url=--config-locations%3D.%2Fx.conf` returns
`'--config-locations=./evil.conf'` as the URL. The plain CLI route is not
affected: `build_parser().parse_args(["--uri", "--config-locations=./x.conf"])`
exits 2 with `argument --uri: expected one argument`.

**Engine precedence** — `find_ytdlp()` candidate order is `VIDDL_YTDLP`, then
`self.ytdlp_path` (Viddl's own copy), then `python -m yt_dlp`, then
`appdata.which("yt-dlp")`; `find_ffmpeg()` is the same shape. Both put the
bundled copy ahead of the system one, which the README does not say.

**The log is created and stays empty** — `LOGGER` appears twice in the codebase:
the definition (`tui/app.py:40`) and one `LOGGER.exception` (`tui/app.py:279`).
Running the real CLI into a clean home (`VIDDL_HOME=/tmp/vhome viddl --doctor`,
exit 0) produces exactly two things: an empty `bin/` and a **0-byte**
`viddl.log`.

**The test suite is hermetic** — `tests/conftest.py` has an autouse fixture that
redirects `VIDDL_HOME`, `VIDDL_DOWNLOAD_DIR`, `HOME`, `XDG_DATA_HOME`,
`XDG_CONFIG_HOME` and `LOCALAPPDATA` into a temp dir, so nothing touches the real
user's folders. Worth keeping, and worth stating in **E10** as an invariant that
new tests must not break.

**Missing project files** — `ls LICENSE* COPYING*` → nothing; `ls .github/workflows`
→ nothing; `ls requirements*.txt uv.lock poetry.lock` → nothing; `.gitignore` has
no `.coverage` entry.

**Current suite** — `pytest -q --cov=viddl` → 288 passed, 93% statements;
`python -m pyflakes viddl tests` → clean. Per-file: `cli.py` 100%, `util.py` 97%,
`appdata.py` 95%, `tui/app.py` 95%, `engine.py` 94%, `download.py` 93%,
`formats.py` 91%, `tui/widgets.py` 89%, `protocol.py` 87%, `errors.py` 86%,
`__main__.py` 0%.

**Extension** — `manifest.json` is MV3 with `contextMenus`, `activeTab`,
`scripting` and a single `*://*.youtube.com/*` content script; no host
permissions, no remote code, no `browser_specific_settings` (so Chromium-only).

## Appendix B — Verified vs. assumed

**Verified by running code or reading it here:** every code reference and every
claim in Appendix A; the precedence orders; the flag lists; the coverage numbers;
the absence of CI, lockfile and licence.

**Not verified, and marked as such in the text:** Windows and macOS *runtime*
behaviour (the registration paths are simulated in tests); real-site extractor
behaviour (no network access to third-party sites during this survey); the exact
severity of **S1** in a shipping browser (the argument injection is proven
locally; whether Chrome demands a user gesture before launching the handler was
not tested in a browser).

**Assumed:** that yt-dlp's documented `--ignore-config` and `--config-locations`
semantics hold for the version Viddl installs (per the
[yt-dlp README](https://github.com/yt-dlp/yt-dlp/tree/master#configuration) and the
[Arch man page](https://man.archlinux.org/man/extra/yt-dlp/yt-dlp.1.en)); that
`--pass` style flags used above exist in current yt-dlp releases.
