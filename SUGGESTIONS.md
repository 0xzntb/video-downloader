# Viddl — suggested improvements

A prioritised list of ways to make Viddl better, grouped by the axis it improves.
Every claim below was checked against the code in this repository, not recalled —
the searchable evidence is in [Appendix A](#appendix-a--evidence).

**Baseline at the time of writing:** commit `30941ff`, 222 tests collected, 93%
statement coverage (`viddl/`), `pyflakes` clean, no CI, no lockfile, single
runtime dependency (`textual>=0.80`).

Work is marked in place as it lands: ✅ **Done**, with the evidence that proved it.
Progress so far: **9 of 60** — S1–S6 in section 1, R1 in section 2, D2–D3 in
section 6.
That is the security pass on section 1: nothing the app downloads is run
unchecked, nothing it writes down is readable by anyone else, nothing it writes
down keeps a secret, and a web page can no longer start it *doing* anything —
only showing something. The threat model those four rest on is now written out
in `SECURITY.md`, which is what **D3** asked for.

**Legend**

| Mark | Meaning |
| --- | --- |
| **P0** | Security or correctness. Fix before promoting the project. |
| **P1** | High value — real user impact or it blocks growth. |
| **P2** | Worth doing; improves quality of life. |
| **P3** | Nice to have. |
| **S / M / L** | Rough effort: hours / a day or two / a week-ish. |
| ✅ **Done** | Implemented and verified; the evidence sits with the item. |

---

## Top 10, if you only do ten things

| # | Item | Why it matters |
| --- | --- | --- |
| 1 | ✅ **S1** — stop option-shaped "links" reaching yt-dlp | Proven argument injection; a web page could trigger it — **done** |
| 2 | **E1** — add CI | Nothing is checked on push today; the tests only run if someone runs them |
| 3 | **E2** — add a `LICENSE` file | README and `pyproject.toml` both claim MIT; no licence text ships |
| 4 | ✅ **R1** — resume interrupted downloads | A cancelled or dropped download started from zero — **done** |
| 5 | **R4** — `viddl --update` | Stale yt-dlp is *the* top cause of "this link stopped working" |
| 6 | **F1** — cookies from the browser | Unlocks Instagram, Threads, TikTok, sometimes Vimeo (README says unsupported) |
| 7 | ✅ **S2** + **S3** — verify and lock down downloaded helpers | Executables were fetched and `chmod +x`'d with no hash check, into a folder anyone could read — **both done** |
| 8 | **D1** — make the log tell the truth | README promises last-run details; the log is almost always empty |
| 9 | **E7** — one real end-to-end test | Unit tests stub the engine; nothing proves the pipeline works together |
| 10 | **U1** — theme and `NO_COLOR` support | Hardcoded colours; no light-terminal or high-contrast story |

---

## 1. Safety

### S1 — Option-shaped "links" reach yt-dlp's command line · **P0** · S · ✅ **Done**

> **Fixed.** The link is now separated from the options with `--` in both
> `DownloadJob.build_args()` and `probe_video()` — which had the same hole and was
> missed in the first pass — and one shared `util.safe_video_link()` refuses
> anything that is not a plain `http(s)` link at every entry point: the CLI, the
> `viddl://` parser (whose query branch previously validated nothing at all), the
> app's link box, and the link the app is started with. Both yt-dlp invocations
> also pass `--ignore-config` now. 48 tests added (222 → 270).
>
> Verified against real yt-dlp 2026.08.19 rather than assumed: without the
> separator the config named by the "link" was loaded and its
> `--exec echo RCE-MARKER-98765` **executed**; with it, the same string is
> rejected as `not a valid URL`; `--ignore-config` alone did **not** prevent it,
> which is why the separator is the fix and the flag is only a belt; and a real
> download through `DownloadJob` still completed end to end.

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

### S2 — Downloaded helpers are never verified · **P1** · M · ✅ **Done**

> **Fixed.** Nothing Viddl downloads is executed without first checking it
> against a published hash. `download_to()` now takes `expected_sha256` and
> hashes the bytes *before* the atomic rename, so a file that fails its check is
> deleted where it stands and never exists at the destination even briefly;
> `_verified_download()` refuses anything that is not HTTPS, refuses a source
> that publishes no checksum at all, and retries once on a mismatch (the one
> innocent cause: a release rebuilt mid-download). Each mirror states how it can
> be checked — yt-dlp through the `SHA2-256SUMS` beside every release (looked up,
> not pinned, so a newer engine keeps working), the BtbN bundles through
> `checksums.sha256` (the archive is checked before it is unpacked), and
> eugeneware's macOS/Linux fallback binaries through hashes pinned in the source.
> `--doctor` shows the hash recorded at install time and whether the file still
> matches it. 23 tests added (270 → 293).
>
> One deliberate behaviour change, because the alternative was worse:
> **johnvansickle.com is no longer an ffmpeg mirror** — it publishes only an MD5,
> which is not verification. macOS ffmpeg installs now depend on pinned hashes for
> eugeneware's 6.1.1 release and will refuse, with an explanation, if that
> upstream `latest` moves — a real trade-off, since meanwhile the user installs
> ffmpeg via Homebrew instead. Copies installed before this change carry no
> recorded hash, so `--doctor` stays silent about them rather than guessing, and
> system-installed tools are trusted as they are, since Viddl does not own them.
>
> Verified against the real mirrors, not simulated: a real yt-dlp install checked
> against its published sums (2026.08.19, `1fa6733c…`); a real download with a
> wrong hash refused with no file and no `.part` left behind; all three pinned
> hashes matching the downloaded files; and the full ffmpeg route — a 156 MB
> BtbN archive verified against BtbN's own checksums, extracted and running
> (`N-127564-g3c92f003c7-20261015`). The Windows zip path and an actual macOS
> install were hashed but never executed here.

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

### S3 — The app's own directory is world-readable and holds executables · **P1** · S · ✅ **Done**

> **Fixed.** `appdata._secure_dir()` now creates Viddl's folders *and* applies
> `0700` to them, so a folder left `0755` by an earlier version is tightened the
> next time the app starts rather than only for new installs. The mode is set
> after `mkdir`, because the mode passed to `mkdir` is the umask's to trim, and
> the last-resort `Downloads` folder inside the app folder gets the same
> treatment. Two places are deliberately left alone: the root directory and the
> user's own home directory, which `VIDDL_HOME` can point at and which are not
> Viddl's to lock down. On Windows the mode change is skipped rather than
> pretended about, since access there is governed by ACLs.
>
> The executables were tightened too, which the item did not ask for but the
> reasoning demands: `engine._make_runnable()` now sets `0700` on a downloaded
> helper instead of OR-ing the three execute bits onto whatever the umask gave,
> so a folder whose permissions are later reset (a restore, a copied home
> directory) does not turn two programs world-executable again. 12 tests added
> (293 → 305).
>
> Verified with the real CLI and a real install, not just in tests: starting
> `viddl --doctor` against an app folder deliberately left at `0755` returned it
> to `0700` (folder and `bin/`), and a real yt-dlp 2026.08.19 installed into it
> landed as a `0700` file that still runs. A second account reading it could not
> be tried here — this sandbox refuses to drop privileges — so cross-user access
> rests on the mode bits rather than on an attempt.

`ensure_dirs()` created `~/.local/share/viddl` and `bin/` with whatever the umask
gave (commonly `0755`). That folder holds two executables (`yt-dlp`, `ffmpeg`)
plus a log.

**Do:** create the data dir `0o700` (and `bin/` too), and chmod existing dirs to
match on startup. Cheap, and it removes a local privilege-escalation surface on
shared machines.

### S4 — Log content and permissions · **P1** · S · ✅ **Done**

> **Fixed.** The log is created `0600`, an older loose one is tightened, and the
> file stays that way through rotation — which needed a handler of its own,
> because the standard rotating handler opens each new file the way ``open``
> does and the umask would have quietly undone the mode at the first rollover.
> ``logfile.py`` now owns the whole contract: a file capped at 256 KB with one
> roll kept, and every line passed through ``util.redact()`` as it is written.
> 16 tests added (305 → 321).
>
> Redaction runs on the **formatted** line rather than on each record's message,
> which is what covers the case that matters most: an exception raised while
> fetching a link carries that link inside its traceback, and the traceback is
> the line a log is usually opened for. Only the finished text is rewritten, so
> the rest of the line still says what happened.
>
> Verified through the real CLI and the real logger, not only in tests: a fresh
> run leaves a `0600` log; a 360 KB `0644` log left by an earlier version is
> tightened to `0600` and rolled on the first write (`viddl.log.1` at `0600`,
> live file 68 bytes); and a logged line plus traceback containing
> `?token=SECRET123` and `?sig=SIGNED456` came out as `<redacted>` with
> `v=…` and the host untouched, `grep` finding neither secret in the file.
>
> One limitation, deliberately recorded rather than papered over: a credential
> sent in a shape the parameter list does not recognise — a whole cookie header,
> say — is not redacted. The list is fixed and documented (`util.SECRET_PARAMS`)
> so a miss is visible in the code that reads it, and so the filter cannot
> rewrite a link into something that merely looks right.

Video URLs can carry tokens, signed query strings or account identifiers, and
before this landed a future "log the command line" change (see **D1**) would have
written those to disk forever, as `viddl.log` was never rotated or truncated.

### S5 — Any web page can cold-open the app · **P2** · M · ✅ **Done**

> **Addressed.** The invariant is now written down where the links arrive —
> `ViddlApp`'s docstring states it, and two tests hold it
> (`test_a_hand_off_never_starts_a_download`, and the audio variant): a hand-off
> pre-fills a link and probes it, and nothing is downloaded until a person picks
> a quality. A hand-off arriving within three seconds of the last is taken to be
> the same ask, so the app opens with an empty box rather than acting on a second
> link; a link typed on the command line is never limited that way, since that
> intent can only come from the person typing. The threat model is written out in
> `SECURITY.md`, linked from the README. 11 tests added (321 → 332).
>
> The rate limit is deliberately narrow, and the plan it came from implied more
> than it delivers: it limits how many links a page can have *acted on*, not how
> many windows appear. Closing the window is the desktop environment's decision,
> and **single-instance behaviour was not implemented** — see below.
>
> Verified through the real CLI, not only in tests: three hand-offs about a second
> apart leave the recorded stamp at the first one's value (they are collapsed),
> while one after the cooldown updates it; and with only the terminal UI stubbed
> (it cannot run in this sandbox), the real `cli.main` hands the app the link on
> the first hand-off, an empty link on the second, and the link again for both
> typed forms.

**Deliberately not done: single-instance behaviour.** The cheap version is a lock
file holding a process id, and after a crash or a reused pid it can silently
swallow a click — a worse failure than an extra window. The reliable version
needs platform window APIs or a local listening socket, and a socket trades a
nuisance for real attack surface. `SECURITY.md` records the decision and the
reasoning rather than leaving it as an omission. It also collides with **R6**,
which is the same problem from the other side (two instances, one output file).

The whole design is that a page hands over a `viddl://` link, and the app opens
with that link pre-filled. That is a good default — nothing downloads without a
deliberate click on a quality. The risk is nuisance/abuse rather than data loss:
a hostile page can spawn terminal windows. Before **S1** it could do worse.

### S6 — Environment overrides are undocumented and unvalidated · **P2** · S · ✅ **Done**

> **Fixed**, and the measurements turned up a worse case than the item assumed.
> All four variables now go through one gate (`appdata.folder_override` /
> `program_override`): a value that looks like an option is refused rather than
> handed on to a tool that might read it as a flag; a folder that is really a
> file is refused instead of failing later; and a **bare program name means the
> `PATH`, never the working directory**. That last rule is the real find: with
> the old code, `VIDDL_YTDLP=yt-dlp` from inside a directory containing a
> `yt-dlp` **ran that file** — and `--doctor` reported it as *"downloaded by
> Viddl"* — while the same setting next to a file that was *not* executable found
> nothing at all, because the existence check looked in the working directory and
> the execution looked on the `PATH`. Tools found this way now carry their own
> `environment` origin, so the doctor says *"the copy you pointed Viddl at"*.
> The four are documented in the README (**D2**), and 19 tests were added
> (332 → 351).
>
> Verified with the real CLI rather than by reading the code, before and after:
> with `VIDDL_YTDLP=yt-dlp` in a directory holding a planted `./yt-dlp`, the old
> code ran the planted file and called it Viddl's own; the new code finds nothing
> and says *"ignored — not a program Viddl can run"*. An explicit path
> (`VIDDL_YTDLP=./yt-dlp`) is still honoured, as it should be — a value with a
> directory in it is a path by definition — but it is now resolved to an absolute
> path and labelled honestly, so the doctor shows which file it is. An
> option-shaped `VIDDL_HOME` is refused and the platform default is used.
>
> Not a code-execution finding: `--exec=…` reaching yt-dlp as the *value* of
> `-P` does not run it. Real yt-dlp 2026.08.19 was checked, as with **S1**: an
> unknown `--opt=value` token is taken as a path, and a *known* flag is rejected
> as a missing argument. The problem was the confusing failure and the silent
> mislabelling, not a payload.

`VIDDL_HOME`, `VIDDL_DOWNLOAD_DIR`, `VIDDL_YTDLP` and `VIDDL_FFMPEG` were all
read from the environment with no checking at all. `VIDDL_YTDLP` in particular
pointed the app at an arbitrary executable: useful and intended, but a
config-injection vector in shared/CI environments, and invisible to users.

### S7 — Extension hygiene (already good, keep it that way) · **P2** · S

The manifest is genuinely minimal: `contextMenus`, `activeTab`, `scripting`, one
content script scoped to `*://*.youtube.com/*`, MV3 service worker, no host
permissions, no remote code. This is a real strength — worth stating in the
README as a selling point.

**Do:** add `minimum_chrome_version`, keep the "no background page reads" promise
in a comment, and add a Firefox build (see **F15**). (`SECURITY.md` now exists,
with a place to report — the remaining half of this item is the manifest.)

---

## 2. Stability and correctness

### R1 — Interrupted downloads start over · **P1** · M · ✅ **Done**

> **Fixed.** `build_args()` now asks for `--continue` explicitly (yt-dlp's own
> default, but the run must not depend on a default staying put), and cancelling
> **keeps** the `.part` file instead of deleting it — the deletion was the bug,
> since it threw away work the user had waited for. `_cleanup_partial()` still
> exists but is opt-in (`DownloadJob(..., keep_partial=False)`), so nothing
> deletes a half-download unless it is asked to. The truck-and-trailer of a
> change is the screen: stopping now lands on a **Stopped** screen offering
> **Resume** (runs the same download again, and yt-dlp continues from the
> `.part`) and **Discard** (deletes it), with escape meaning "later, keep it".
> Discard only ever deletes files *this job announced* — matching the folder by
> pattern could take someone else's half-download with it. 12 tests added
> (351 → 363).
>
> Verified end to end against the real tool, not simulated: a 10 MB payload
> served slowly over local HTTP, downloaded by Viddl's own `DownloadJob` with
> yt-dlp 2026.08.19, stopped at 40% — the part (4,193,280 bytes) stayed on disk;
> a second job on the same link had yt-dlp print
> *"Resuming download at byte 4193280"*, showed the user *"Resuming the
> interrupted download…"*, finished at 10,240,000 bytes with a SHA-256 matching
> the original, and left no partial behind.
>
> What this cannot fix, and does not claim to: resuming needs the site to honour
> a ranged request. Where it does not, yt-dlp starts the file again — better
> than losing it, but not a resume.

`build_args()` set `--retries 5 --fragment-retries 5` for network hiccups, but
there was no `-c/--continue`. Worse, cancelling deleted the work:
`run()` → `_cleanup_partial()` removed partial files. A 4 GB download that the
user paused at 90% was lost, and a dropped connection mid-download was re-fetched
from byte zero.

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

Worth distinguishing from the silent failure above: `_secure_dir()` also
swallows a *`chmod`* that fails (**S3**), and that one is intentional — a
filesystem without permissions (a FAT stick, an exotic mount) is not a reason to
refuse to run. Only being unable to create the folder is worth reporting.

### R8 — The ffmpeg download is large and unresumable · **P2** · M

~100–156 MB from a mirror list pinned in the code, with `_TIMEOUT = 30` and no
resume, and on macOS arm64 the first mirror is native while the fallback is
`x64` — which only works through Rosetta, so a user without Rosetta gets a
confusing failure rather than a clear message. (The missing hash check this item
also described has since landed — see **S2** — but a dropped 156 MB transfer still
starts from zero.)

**Do:** show the download size before asking (the app already asks before
downloading large files — good), resume interrupted transfers with a Range
request, and detect Rosetta availability on macOS or say plainly that the
fallback needs it.

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
(`r` for retry is now half-taken: `r` resumes from the **Stopped** screen.) The
older note here — that `Esc` cancels instantly and **R1** made that cost real
progress — no longer applies: **R1** is done, so `Esc` keeps the part and the
screen asks whether to resume it or throw it away.

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

No `.github/` at all. The 222 tests, the 93% coverage and the `pyflakes` pass
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
text ships), `CHANGELOG.md`, `CONTRIBUTING.md`, issue/PR templates,
`CODE_OF_CONDUCT.md`. `.gitignore` also lacks the `.coverage` artifact and
`coverage.xml`, so a coverage run dirties the tree.

`SECURITY.md` is no longer on this list — **S5** wrote it.

**Do:** add the licence first — it is a one-minute fix for an actual legal gap.

### E3 — Make the log tell the truth (see D1) · **P1** · S

The README promises: "The log holds the last run's details if something failed in
a way the screen could not explain." In practice the file is created and left at
**0 bytes**: `LOGGER` appears twice in the whole codebase — its definition and a
single `LOGGER.exception("Viddl: could not update the screen")`. Nothing logs a
failed download, the yt-dlp command line, the exit code or the raised error.

**Do:** log the command line, exit code and error details on failure. The
redaction **S4** asked for is now in place, so this can simply log and get the
filtering for free. Then the README becomes true, and `--doctor` becomes
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
**E12**, the platform, which tool origin was used, the last error (redacted
automatically by the log's own filter, see **S4**) and the log path. No telemetry, no network — consistent with the
project's "nothing leaves your machine" promise.

---

## 6. Documentation

| # | Item | Detail |
| --- | --- | --- |
| **D1** | **The log promise is not met** | README: "The log holds the last run's details if something failed." Reality: the file is created empty, and one `LOGGER.exception` call exists in total. Either log failures (**E3**) or soften the claim. |
| **D2** | ✅ **Undocumented environment variables** | Done with **S6**: the README has a table of all four — what each overrides, why you would use it, and how a value is checked — and `--doctor` prints which are in effect. |
| **D3** | ✅ **Document the `viddl://` security model** | Done with **S5**: `SECURITY.md` states the guarantee (any page may open the app; nothing downloads without a click) and what it does *not* cover, and the README links to it. |
| **D4** | **Troubleshooting / FAQ** | Stale extractor ("run `viddl --update`"), login-required sites (**F1**), corporate proxies, Windows console rendering (**R10**), where the log lives. Each is a support question waiting to happen. |
| **D5** | **`--version --verbose` and a man page** | `viddl.1` for Unix packaging; the verbose version block from **E12** embedded as a code block in the README. |
| **D6** | **Architecture notes** | A short `docs/` page on the `Services` seam, why the app is local-only, and the precedence rules for engine and config. It is the thing a new contributor needs most. |

---

## 7. Suggested order

1. ✅ **S1** (argument injection) + regression tests — **done**.
2. **E2** (LICENSE) and **E1** (CI) — cheap, and they protect everything after.
3. ✅ **S3**, **S4** and **S5** (directory, log, and what a page may start) —
   **done**; **E3/D1** (make the log real) remain, and are now cheaper because
   the log is a safe place to put things.
4. ✅ **R1** (resume) — **done**; **R2** (actually stop the children) is the other
   half of the same pain and remains, which matters more now: a partial file is
   only worth keeping if the process writing it has really stopped.
5. **R4**/**F3** (`viddl --update` + staleness hint) — the fix for the most common complaint.
6. ✅ **S2** (verify downloads) — **done**; **R3** (expose the config escape hatch) still open. `--ignore-config` itself landed with **S1**.
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
`viddl.log`. (Both still hold; only their modes have moved on — `0700` and
`0600` as of **S3** and **S4**.)

**The test suite is hermetic** — `tests/conftest.py` has an autouse fixture that
redirects `VIDDL_HOME`, `VIDDL_DOWNLOAD_DIR`, `HOME`, `XDG_DATA_HOME`,
`XDG_CONFIG_HOME` and `LOCALAPPDATA` into a temp dir, so nothing touches the real
user's folders. Worth keeping, and worth stating in **E10** as an invariant that
new tests must not break.

**Missing project files** — `ls LICENSE* COPYING*` → nothing; `ls .github/workflows`
→ nothing; `ls requirements*.txt uv.lock poetry.lock` → nothing; `.gitignore` has
no `.coverage` entry.

**Suite at the baseline** — `pytest --collect-only` → 222 tests collected, all
passing; 93% of statements;
`python -m pyflakes viddl tests` → clean. Per-file: `cli.py` 100%, `util.py` 97%,
`appdata.py` 95%, `tui/app.py` 95%, `engine.py` 94%, `download.py` 93%,
`formats.py` 91%, `tui/widgets.py` 89%, `protocol.py` 87%, `errors.py` 86%,
`__main__.py` 0%.

**Extension** — `manifest.json` is MV3 with `contextMenus`, `activeTab`,
`scripting` and a single `*://*.youtube.com/*` content script; no host
permissions, no remote code, no `browser_specific_settings` (so Chromium-only).

**S1 re-checked against a real tool** — yt-dlp 2026.08.19 in a throwaway
environment, with a local HTTP server standing in for a site, 8/8 checks:

| Command line | Result |
| --- | --- |
| `yt-dlp --config-locations=./evil.conf <url>` | the file was loaded and its `--exec echo RCE-MARKER-98765` ran |
| `yt-dlp -- --config-locations=./evil.conf` | treated as a link: *is not a valid URL* |
| `yt-dlp --ignore-config --config-locations=./evil.conf <url>` | still loaded — the flag alone never fixes this |
| the app's own `DownloadJob` with the hostile link | refused, nothing executed |
| the app's own `DownloadJob` with a real link | downloaded `clip [clip].mp4`, 4128 bytes, phase `done` |

**S2 checked against the real mirrors** — no simulation; every URL below was
fetched for real and hashed:

| Check | Result |
| --- | --- |
| Publish a checksums file at all? | yt-dlp yes (`SHA2-256SUMS`); BtbN yes (`checksums.sha256`); eugeneware no (stable per-asset hashes, no list); **johnvansickle MD5 only** |
| Real yt-dlp install through `_verified_download()` | version 2026.08.19, `1fa6733c…`, matching the published sums |
| A real download whose expected hash is wrong | refused, `ChecksumMismatch`, no file and no `.part` left |
| The three pinned eugeneware hashes vs. the real files | all matched (`a90e3db6…`, `ebdddc93…`, `e7e7fb30…`) |
| The BtbN list against the build Viddl asks for | `be655b0766…` present and matching |
| Full ffmpeg route: 156 MB archive → verify → extract → run | `N-127564-g3c92f003c7-20261015`, archive hash checked, recorded hash matches what `--doctor` prints |

**S3 checked by running the app** — real CLI, real install, `stat` on the result:

| Step | Result |
| --- | --- |
| `viddl --doctor` with the app folder and `bin/` pre-created `0755` | both returned `0700`, exit 0 |
| A real yt-dlp 2026.08.19 installed through the engine | file `0700`, `bin/` `0700`, still prints its version when run |
| Downloads folder when `VIDDL_DOWNLOAD_DIR` points elsewhere | left as the user's own folder (`0755`) — not ours to lock |

**S4 checked by running the app and the real logger** — every line below is a
`stat`, `cat` or `grep` on a real file, not a simulation:

| Step | Result |
| --- | --- |
| `viddl --doctor` with a fresh folder | `viddl.log` created `0600`, 0 bytes |
| An existing `0644` log, 360,000 bytes | tightened to `0600` by the next run |
| The first write after that | rolled over: `viddl.log` 68 bytes, `viddl.log.1` 360,000 bytes, **both `0600`** |
| `LOGGER.error` with `?token=SECRET123&v=dQw4w9WgXcQ` | written as `?token=<redacted>&v=dQw4w9WgXcQ` |
| `LOGGER.exception` whose traceback holds `?sig=SIGNED456` | traceback written, `sig=<redacted>` |
| Grepping the log for either secret | no match, exit 1 — neither value reached the file |

**S5 checked through the real CLI** — the actual program, with only the terminal
UI stubbed out (it needs a real terminal, which this environment does not have):

| Step | Result |
| --- | --- |
| Three `viddl --uri …` runs about a second apart | the recorded stamp keeps the first run's value — the later two are collapsed |
| A fourth run after a 3-second pause | stamp updated: the hand-off is acted on again |
| Where the *app* was sent: first hand-off | `url=https://www.youtube.com/watch?v=abc`, `audio=True` |
| Second hand-off, immediately after | `url=""`, `audio=False`, exit 0 — no link, and the app still opens |
| A typed link, then `--audio <link>`, right after both | both acted on — the cooldown never applies to these |

**S6 measured before and after**, with the real CLI and real files — the rows
marked *before* are the old code, run again from this repository:

| Setting | Before | After |
| --- | --- | --- |
| `VIDDL_YTDLP=yt-dlp`, with `./yt-dlp` planted in the working directory | **ran the planted file** (`PLANTED-9.9.9`) and reported it as "downloaded by Viddl" | finds nothing; doctor says `ignored — not a program Viddl can run` |
| `VIDDL_YTDLP=yt-dlp`, same directory, but the file is not executable | silently "not installed", though the file it checked exists | same answer, now with a reason |
| `VIDDL_YTDLP=./yt-dlp` (an explicit path) | ran it, labelled "downloaded by Viddl" | still runs it — a path is a path — but as `/tmp/plant/yt-dlp`, "the copy you pointed Viddl at" |
| `VIDDL_HOME=--exec=echo RCE-MARKER-13579` | accepted verbatim as the app folder | refused; the platform default is used and the refusal printed |
| `viddl --doctor` with all four set | printed none of them | prints each, with `in use` or the reason it was ignored |

**R1 checked end to end with the real tool** — real yt-dlp 2026.08.19, a 10 MB
payload served slowly over local HTTP, then stopped and resumed through Viddl's
own `DownloadJob`:

| Step | Result |
| --- | --- |
| Stopped at 40% | `.part` kept: `video [video].mp4.part`, **4,193,280 bytes** |
| Second run, same link | yt-dlp printed `[download] Resuming download at byte 4193280` — the exact byte left behind |
| What the user saw | `Resuming the interrupted download…` |
| Final file | 10,240,000 bytes, **SHA-256 identical to the original**, no `.part` left |
| `build_args()` | contains `--continue`, before the `--` separator |
| A stray `other.mp4.part` in the folder | left alone: Discard deletes only what the job announced |
| `yt-dlp --ignore-config -P --exec=echo MARKER <url>` (real yt-dlp 2026.08.19) | — | the token is taken as a *value*, not a flag: no execution |

## Appendix B — Verified vs. assumed

**Verified by running code or reading it here:** every code reference and every
claim in Appendix A; the precedence orders; the flag lists; the coverage numbers;
the absence of CI, lockfile and licence.

**Not verified, and marked as such in the text:** Windows and macOS *runtime*
behaviour (the registration paths are simulated in tests); real-site extractor
behaviour (no network access to third-party sites during this survey); and
whether Chrome demands a user gesture before launching a `viddl://` handler — a
browser question this environment cannot answer. The *severity* of **S1** is no
longer in doubt: it was reproduced against a real yt-dlp, which executed the
payload in the injected config file. For **S2** the same line applies to the
Windows ffmpeg zip and to any actual macOS install: their hashes were confirmed
by downloading and hashing the real files, not by installing them on those
systems. For **S3**, what a *second account* can reach was not attempted — this
sandbox refuses to drop privileges — so the claim rests on the mode bits being
`0700` on disk and on the kernel enforcing them, not on a rival user trying and
failing. Locking a folder down also cannot be verified on Windows at all here.
For **S4** the same caveat applies to the log's mode on Windows, where access is
governed by ACLs, and redaction was checked against the shapes listed in
Appendix A — a credential sent in some other shape is not covered by it.

**R1 is verified for a single-file download and not beyond that**: a *merged*
download (two streams, two `.part` files) was not exercised end to end, nor was a
resume after the app itself restarted, nor any of it on Windows. Resuming also
depends on the site accepting a ranged request — where it does not, yt-dlp
starts again, which the item records rather than hides.

**S6's fix is visibility plus a boundary that cannot be crossed by accident**, not
sandboxing: the check makes an override visible and stops a *name* from silently
meaning a file in the working directory, but anyone who can set your environment
can still point Viddl at a program of their choosing. `SECURITY.md` says so under
"not defended against". Windows' `PATH`/`PATHEXT` lookup for a bare name was not
exercised here, only its POSIX behaviour.

**S5 is honest about what it does not do**, which is why it is marked done
anyway: the cooldown limits the links a page can have acted on, not the windows
it can cause, and single-instance behaviour was decided against rather than
overlooked (the reasoning is in `SECURITY.md` and with the item). The claim that
a link is never acted on without a chosen quality was checked by driving the real
app through Textual's pilot; the app itself has still never been run in a real
terminal here, and whether Chrome demands a user gesture before opening a
`viddl://` handler remains a browser question this environment cannot answer.

**Checked rather than assumed now:** that `--ignore-config` leaves an explicit
`--config-locations` alone, and that `--` really does end the options for yt-dlp
— both confirmed by running yt-dlp 2026.08.19. Likewise for **S2**: which mirrors
actually publish a usable checksum (`SHA2-256SUMS`, `checksums.sha256`, the
pinned eugeneware digests) and which publishes only an MD5 (johnvansickle) were
established by fetching the files, not by reading documentation. The
[yt-dlp README](https://github.com/yt-dlp/yt-dlp/tree/master#configuration) and
the [Arch man page](https://man.archlinux.org/man/extra/yt-dlp/yt-dlp.1.en) match
what the tool actually does.
