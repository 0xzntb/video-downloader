# Viddl — suggested improvements

A prioritised list of ways to make Viddl better, grouped by the axis it improves.
Every claim below was checked against the code in this repository, not recalled —
the searchable evidence is in [Appendix A](#appendix-a--evidence).

**Baseline at the time of writing:** commit `30941ff`, 222 tests collected, 93%
statement coverage (`viddl/`), `pyflakes` clean, no CI, no lockfile, single
runtime dependency (`textual>=0.80`).

Work is marked in place as it lands: ✅ **Done**, with the evidence that proved
it (◐ **partly done** says what landed and what did not).
Progress so far: **31 of 60** — S1–S6 in section 1, R1–R11 in section 2,
F1–F8, F10–F12 in section 3, U4 in section 4, D2–D3 in section 6.
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
| ◐ **partly done** | Some of it landed; the item says what is still missing. |

---

## Top 10, if you only do ten things

| # | Item | Why it matters |
| --- | --- | --- |
| 1 | ✅ **S1** — stop option-shaped "links" reaching yt-dlp | Proven argument injection; a web page could trigger it — **done** |
| 2 | ◐ **E1** — add CI | The matrix (Linux/macOS/**Windows**) and `pyflakes` landed with **R10**; the coverage floor and the weekly extractor job are still missing |
| 3 | **E2** — add a `LICENSE` file | README and `pyproject.toml` both claim MIT; no licence text ships |
| 4 | ✅ **R1** + **R2** — resume interrupted downloads, and really stop them | A cancelled or dropped download started from zero, and a stop left ffmpeg writing — **both done** |
| 5 | ✅ **R4** — `viddl --update` | Stale yt-dlp is *the* top cause of "this link stopped working" — **done** |
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

### R2 — Cancelling does not stop the children · **P1** · S · ✅ **Done**

> **Fixed.** The download now starts in a session of its own
> (`start_new_session=True`; `CREATE_NEW_PROCESS_GROUP` on Windows), so stopping
> signals the **group** — yt-dlp *and* the ffmpeg it started — rather than one
> process. The stop stays polite (`SIGTERM`; `taskkill /T` without `/F`), and a
> watchdog escalates to `SIGKILL` (and `/F`) five seconds later if the tree is
> still there; the `wait()` that follows has a bounded timeout too. **Quitting**
> the app skips the politeness (`cancel(force=True)`): a grace period needs a
> watchdog, and that watchdog would die with the app, so nothing may outlive a
> quit. A stop that lands *before* the process starts is now honoured too — it
> used to be recorded and forgotten, and the download ran anyway.
>
> The stray process was only half the damage: ffmpeg inherits yt-dlp's stdout, so
> the pipe Viddl reads stays open after yt-dlp itself is gone and the screen sits
> on *"Stopping…"* for ever. Killing the group closes the pipe, which is why the
> stop now ends the screen even when the engine never answers.
>
> Measured with real yt-dlp 2026.08.19 and a real child process, old behaviour
> against new on the same scripted run: **old** — child still running after the
> cancel, reading thread still blocked after 25 s, yt-dlp printing `Killed`;
> **new** — stopped in 0.01 s, child gone. A cancel *mid-download* with the real
> tool (27%, 2,267,431 bytes) stopped in 0.01 s and kept the `.part` at exactly
> the bytes it had, so R1 and R2 hold together. The three process-group tests were
> also run against a mutated build that signals only the parent — all three fail,
> so they test the fix rather than the weather. 9 tests added (363 → 372).
>
> Not exercised, and not claimed: a real **ffmpeg merge** caught in flight (there
> is no ffmpeg in this environment; the child standing in for it is a real process
> started by real yt-dlp, not a simulation), and the Windows `taskkill` path,
> whose command line is asserted but never run on Windows.

`cancel()` called `process.terminate()` on the yt-dlp process only. ffmpeg is a
child of yt-dlp, so a merge in flight could outlive the UI's cancel and keep
writing to the very `.part` file **R1** now promises to keep. `terminate()` is
also `SIGTERM`: yt-dlp handles it, but a wedged process was never escalated.

**Do:** start the process with `start_new_session=True` (POSIX) and kill the
process group; escalate `terminate()` → `kill()` after a few seconds; `wait()`
with a timeout. On Windows use `CREATE_NEW_PROCESS_GROUP` / `taskkill /T`. Add a
test that a fake long-running child is actually gone after cancel.

### R3 — yt-dlp's user config leaks into every download · **P1** · S · ✅ **Done**

> **Fixed.** Both invocations already passed `--ignore-config` (that came with
> S1); what was missing was the deliberate counterpart, and it is now
> `VIDDL_YTDLP_ARGS`. The value is split the way a shell splits it — quotes and
> all, never through a shell — checked token by token, and added to **both** the
> download and the look-up that builds the quality menu, since a link that needs
> cookies fails at the probe long before a download starts. It goes last, so a
> deliberate choice wins over Viddl's defaults, and the few options that would
> take over a decision Viddl has already made are refused whole: `--paths`,
> `--output`, `--format`, `--batch-file`, `--simulate`, `--skip-download`,
> `--progress-template` and `--config-locations`. Half a command line is a
> command line nobody wrote. A value containing a link is refused too — it would
> be a second thing to download while Viddl reports one. Refusals are named in
> `viddl --doctor`, like every other override, and `ENVIRONMENT_VARIABLES` now
> lists five.
>
> **What the first version of the guard missed, measured rather than assumed:**
> yt-dlp's parser accepts abbreviations of long options — `--form best`,
> `--path /tmp`, `--sim` and `--ski` all parse — so a check on the full spellings
> alone could be walked around by typing less. The matching now covers
> abbreviations, and `--format-sort` (a different, real option) still passes.
> The same review found a Windows one: POSIX quoting everywhere would eat the
> backslashes of `--cookies C:\Users\me\cookies.txt`, so Windows keeps the
> quotes and strips them instead. That branch is exercised by a test with the
> platform faked, not on a real Windows machine.
>
> Measured with real yt-dlp 2026.08.19 and the same `~/.config/yt-dlp/config`
> throughout: **plain yt-dlp** with that file ran its `--exec` line and wrote the
> marker; **Viddl's own command** with the same file downloaded the video and did
> not (`--ignore-config`), so the leak the item describes is real and closed.
> `VIDDL_YTDLP_ARGS=--user-agent Viddl-R3-UA` was seen by a local HTTP server on
> the download **and** on the probe; a refused `-o` left the file in Viddl's own
> folder and never created the directory it asked for; `--doctor` prints
> `(in use)` or `(ignored — sets --format, which Viddl decides)`. 32 tests added
> (372 → 404).
>
> Not defended against, and now stated in `SECURITY.md`: this variable is
> *options*, so anyone who can set your environment can ask yt-dlp to do almost
> anything, `--exec` included. The checks are a boundary against forgetting and
> mistyping, not a sandbox.

No `--ignore-config`, so a user's `~/.config/yt-dlp/config` (or a portable
`yt-dlp.conf` next to the binary) silently changes Viddl's behaviour — output
paths, post-processors, `--exec`. Some of that is desirable; the surprise factor
is not, and combined with **S1** it widens the injection surface.

**Do:** pass `--ignore-config` and expose the escape hatch deliberately
(`VIDDL_YTDLP_ARGS` or a documented `--ytdlp-arg` passthrough), so behaviour is
reproducible and anything unusual is explicit. See
[yt-dlp README](https://github.com/yt-dlp/yt-dlp/tree/master#configuration) and
the [Arch man page](https://man.archlinux.org/man/extra/yt-dlp/yt-dlp.1.en).

### R4 — Nothing updates the engine · **P1** · M · ✅ **Done**

> **Fixed.** `viddl --update` fetches this release of yt-dlp again, checks it
> against the published SHA-256 exactly as the first install does (an update is
> an install from a known state, not a second code path that could skip the
> checks), and prints **what it moved from and to** — "yt-dlp 2023.01.06 →
> 2026.08.19 (downloaded by Viddl)", or "already the newest release".
> `viddl --update-ffmpeg` does the same for ffmpeg, and only that: one flag, one
> thing fetched.
>
> The precedence the item flags is gone. Every copy of yt-dlp that runs is now
> asked its version and the **newest** is used, whatever folder it is in — so the
> copy Viddl fetched once can no longer shadow a newer one installed since,
> which is what made the README's "if you already have it installed, Viddl uses
> yours" quietly untrue after the first run. `VIDDL_YTDLP` still wins outright,
> older or not: pointing at a copy is an instruction, not a default. A copy
> whose version names no date (a nightly, a git checkout) never displaces a
> dated release, because nothing here can tell which of them is newer; the
> README says so and `VIDDL_YTDLP` is the answer.
>
> Staleness is said out loud: over `STALE_DAYS` (30) the home screen shows
> *"Engine 41 days old — run `viddl --update`"* in place of its ready line, and
> `--doctor` adds the same note under the yt-dlp line. The screen matters more
> than the doctor here: the person whose link just broke is not running
> `--doctor`.
>
> Measured with the real thing, not simulated. A real yt-dlp 2023.01.06 in
> Viddl's folder next to a real 2026.08.19 on `PATH`: `--doctor` chose the newer
> one (it used to choose the old one), and `--update` then downloaded the genuine
> GitHub release — 3,072,469 bytes, checksum verified — and reported
> "2023.01.06 → 2026.08.19", at which point the doctor showed
> `sha256 1fa6733c37ea… (matches what was installed)`. A second `--update` said
> "already the newest release". `--update-ffmpeg` fetched and verified a real
> 176 MB ffmpeg build in 18.5 s. 27 tests added (404 → 431).
>
> What the first working version got wrong, caught by that real run: it reported
> the version of the copy **in use** rather than the one it replaces, so an old
> bundled copy replaced by a three-years-newer release printed "already the
> newest release". ``installed_ytdlp()``/``installed_ffmpeg()`` now answer "what
> is in Viddl's folder", which is what an update changes.
>
> Not verified here: an update on Windows (the platform branches are exercised by
> tests with the platform faked), and the nightly/pre-release path — this fetches
> the latest **stable** release, which is the one that fixes a link that stopped
> working.

The README is candid: "a link that works one week can stop working the next
until yt-dlp updates." Yet there is no way to update it. Whichever copy
`find_ytdlp()` picks — order is `VIDDL_YTDLP` → **Viddl's own copy** →
`python -m yt_dlp` → system `PATH` — is whatever was installed first and stays
there. (Note this contradicts the README's "If you already have it installed,
Viddl uses yours instead": that is true until Viddl fetches its own copy, after
which its own copy wins and can shadow a newer system one.)

**Do:**

- `viddl --update` to refresh the bundled yt-dlp (and `--update-engine` for
  ffmpeg), with the new version printed. *(Landed as `--update` and
  `--update-ffmpeg`.)*
- A staleness note in the UI/`--doctor` when the engine is older than N days
  ("This engine is 41 days old — run `viddl --update` if a site stopped working").
- Reconsider precedence: prefer a newer of the two rather than always preferring
  the bundled copy. `Tool.version` already exists, so this is a small comparison.
- Mention `VIDDL_YTDLP` as the escape hatch for users who keep their own.

### R5 — No job-level retry, and no distinction between temporary and permanent · **P1** · M · ✅ **Done**

> **Fixed.** Every explanation now carries a verdict — `FriendlyError.temporary`
> — and it is written in the **same table** as the message (`viddl/errors.py`),
> so an explanation that says "try again" and a retry policy that does not can no
> longer drift apart. The verdict is carried on `ToolError` too, so the caller can
> tell "the site was busy" from "that video is private" without reading raw
> output again.
>
> `DownloadJob.run()` retries only the temporary kind: three attempts in all,
> waiting **4 s then 8 s**, and each attempt continues from the `.part` file the
> previous one left rather than starting over. The wait is visible —
> *"Retrying in 4s (attempt 2 of 3)…"* — keeps the progress bar where the failed
> attempt reached (a wait is not progress, and watching the bar reset feels like
> losing the work), clears the now-meaningless speed and time-left figures, and is
> written to the log with its reason. The wait is slept in 100 ms slices, so the
> stop button is answered at once: cancelling during a wait starts no further
> attempt. A permanent failure is reported **after one attempt**, and if all
> three temporary attempts fail the message says how many times Viddl tried.
>
> Which is which, and why: 429, 5xx, `<urlopen error>`, DNS, refused/reset/timed
> out/SSL, and 403 are temporary — a 403 is usually a per-request refusal (an
> expired URL, a throttled client) that a fresh extraction fixes, while a real
> region block has its own words and is caught as permanent. Private, removed,
> region-blocked, members-only, ffmpeg missing, format gone, no space, permission
> denied, disk I/O and invalid URLs are permanent. Unrecognised errors are
> temporary, because their explanation already tells the user to try again —
> retrying on their behalf is the same advice, sooner. A failure with no output at
> all is temporary only when the process reported one.
>
> Measured with real yt-dlp 2026.08.19 against a local server that answers 429
> twice and then serves the file: the shipped defaults waited 4 s and 8 s, the
> server saw exactly three attempts, and the 4,096-byte file was saved in 16.8 s
> with `phase=retry` events giving way to `download` as each attempt began. The
> same server answering 404 produced **one** attempt in 1.6 s, `temporary=False`,
> and no retry event. 33 tests added (431 → 464).
>
> What the first working version got wrong, caught by that real run: after a wait
> the phase stayed `retry`, so the screen kept claiming to be waiting while the
> second attempt was downloading (the bar only moves during `download`). Fixed at
> the start of every attempt; the test now asserts the first event after a retry
> is a download one.
>
> Not done here: the **probe** (`viddl --list-qualities`, and the quality menu) is
> not retried — R9 is about that call taking 150 s, and retrying it three times
> would treble it. Probe failures still say whether they were temporary.

`errors.explain()` maps raw output to friendly text and `is_fatal_ytdlp_line()`
exists, but a run either succeeds or raises. A 429, a flaky CDN or a momentary
DNS failure ends the download with a message about the network.

**Do:** classify failures (permanent: private/removed/geo/404; temporary: 429,
5xx, timeouts, `<urlopen error>`), and retry temporary ones with exponential
backoff and a visible "retrying in 4s (2/3)" state. Nothing erodes trust like a
failure that a second attempt would have fixed.

### R6 — Two instances can collide on the same output · **P2** · S · ✅ **Done**

> **Fixed.** Each download now writes in a **working folder of its own** inside
> the downloads folder, and only the finished file is moved out of it. Two
> windows never touch the same bytes, so neither can overwrite the other, and
> the "newest file in the folder" fallback that used to pick up somebody else's
> download now only ever looks inside the job's own folder.
>
> The move itself is the other half. The final name is **reserved with an
> exclusive create before the move** and then filled with `os.replace`, so the
> check and the claim are one step and a finish is atomic: two instances
> finishing at the same instant take `Title.mp4` and `Title (2).mp4`, never
> `Title.mp4` twice. A file the user already has is never replaced.
>
> The folder is named after **what is being downloaded** (link + format), not
> after the job object, and a claim file inside it marks it as in use. That is
> what keeps the two properties that matter apart: a stopped download's `.part`
> is found again by a *later* run and resumed — even in a new session — while a
> folder another window is writing in is left alone and the next name along is
> claimed. A stopped or failed download releases its claim; a folder with
> nothing in it but the claim is removed rather than left as litter, and a
> delivered file takes the whole folder with it.
>
> Measured with real yt-dlp 2026.08.19 and a slow local server, with **two real
> Viddl processes** downloading a 1.2 MB file at the same time: they produced
> `big [big].mp3` and `big [big] (2).mp3`, both 1,200,000 bytes, and nothing
> half-finished or hidden was left in the folder. Downloading it again gave
> `(3)` with the earlier files untouched. Stopping one at 2 s left
> `big [big].mp3.part` inside `.viddl-2a8224b7` — the visible folder held only
> that hidden one — and a **new job** for the same link and quality claimed the
> same folder, yt-dlp said "Resuming download at byte …", and the finished
> 1,200,000-byte file was delivered with the folder removed. 13 tests added
> (464 → 477).
>
> What changes for the user, said plainly: downloading a video that is already
> in the folder now produces a second copy instead of reusing the first, because
> "is the destination already there?" was the same shared-name check that let
> two windows collide. A stopped download keeps its half-file in a hidden folder
> rather than beside the finished one — **Discard** removes it. And a crash
> leaves that folder claimed, so the next run of the same download takes the
> next name and starts fresh; the stale folder stays until it is deleted. None
> of those can corrupt a file, which is the point.

`_find_output()` prefers a recorded path, then the newest non-intermediate file
in the output directory. Run two downloads of the same video at once (two
terminal windows) and, with `--no-overwrites` unset, the second can clobber the
first while the first instance reports a file whose bytes changed underneath it.

**Do:** write to a unique temporary name and rename into place (as
`engine.download_to` already does for its own downloads), and/or take a lock file
per output directory.

### R7 — `ensure_dirs()` hides a fatal problem · **P2** · S · ✅ **Done**

> **Fixed.** `ensure_dirs()` still never raises — a read-only home must not crash
> every code path that only wants a path — but it now **returns** what it could
> not create: `FolderProblem(path, error)`, with a one-line version for a screen
> and two lines for the doctor. It is reported in both places the item asked
> for: `viddl --doctor` puts it directly under the app folder
>
> ```
> Problem:      cannot create /home/me/.local/share/viddl (Permission denied)
>               Check that you can write there, or set VIDDL_HOME to a folder you own.
> ```
>
> and the home screen keeps *"Cannot save Viddl's files (Permission denied) —
> run `viddl --doctor`"* on screen, above the ready line when the engine is fine
> and **kept** when the engine setup also failed — that failure is usually the
> same problem wearing a different hat, so the cause stays visible under the
> notification.
>
> The one-line form is 66–72 characters depending on the reason (68 with
> "Permission denied") and deliberately names the doctor rather than attempting
> the fix: the path and the way out do not fit on one line of an 80-column
> terminal, and the doctor already has both.
>
> Measured with the real CLI and the real app. `--doctor` against a folder with a
> file in the way printed `Not a directory`; against a real `chmod 500` folder
> printed `Permission denied`; against a usable folder printed no `Problem:` line
> at all, so the message is not noise on a healthy install. The real app with a
> real engine (yt-dlp 2026.08.19) started normally and showed the line above
> "Engine 60 days old"; with **no engine anywhere and a read-only home**, the
> engine failure toast was still the bare
> `[Errno 13] Permission denied: '/…'`, and the screen above it now read
> *"Cannot save Viddl's files (Permission denied)"* rather than leaving the errno
> to explain itself. 7 tests added (477 → 484).
>
> What was deliberately left alone: the `chmod` swallow in `_secure_dir()` stays
> — a filesystem without permission bits (a FAT stick, an exotic mount) is not a
> reason to refuse to run, which is **S3**'s point — and the engine's own error
> is still the raw `OSError`; the screen above it is what names the cause now.
> Both folders are checked, so a failure in `bin/` is reported as `bin/`, not as
> the folder before it.

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

### R8 — The ffmpeg download is large and unresumable · **P2** · M · ✅ **Done**

> **Fixed.** A dropped transfer is now a pause. Each attempt sends
> `Range: bytes=<have>-` for whatever is already in the part-file, and three
> attempts are made with a growing wait between them, so a connection that
> drops twice still finishes. A server that will not continue — it ignores the
> range, or the file has changed under it (a `416`) — cannot be glued onto the
> half already held, so the part is thrown away and that one download starts
> again; the checksum is what decides the result either way.
>
> **The part-file is no longer allowed to be a temporary one.** It used to live
> in a `TemporaryDirectory`, which is gone by the next run, so the resume could
> only ever work inside a single attempt. It now sits in Viddl's own `bin/`
> folder, named after the URL it belongs to, so the next run finds it — and two
> mirrors of the same program can never be spliced into one file, because each
> has its own. A run killed **after** the download but before unpacking leaves a
> complete archive behind, and that is reused rather than fetched again (it is
> hashed against the published checksum first, so only the right bytes count).
> Once ffmpeg is in place, archives and parts left by failed attempts are
> removed: a 150 MB half-file is not something to leave lying around.
>
> Two more things the item asked for: the size is now **shown before the
> click** — the qualities screen asks the mirror with a `HEAD` request and says
> *"The download is about 149.2 MB"* beside the button, and the install line
> repeats it; and on Apple silicon the Intel fallback is only tried when
> Rosetta 2 can actually run it, with the way to get it (`softwareupdate
> --install-rosetta`) in the error when it cannot.
>
> **Measured against the real 156,424,004-byte BtbN bundle, with real
> processes.** One run was killed 1 s in, leaving 42,729,472 bytes in
> `ffmpeg-download-f9d04321.tar-xz.part`; the **next process** reported its first
> progress callback at 42,729,472 of 156,424,004 — a server that ignored the
> Range would have started it at 0 — fetched the remaining 113.7 MB, unpacked
> it, verified it and installed `ffmpeg N-127617` that runs (`-version` exit 0),
> leaving only `ffmpeg` and `ffmpeg.sha256` in `bin/`. A second measurement
> killed the run **during unpacking**, after the 149 MB had landed: the next run
> showed *no progress callback at all* — zero bytes downloaded — and installed
> from the archive already on disk. Against a localhost server the exact
> sequence was checked request by request: `GET bytes=4000-`, a mirror that
> ignores the range and gets a clean restart, a `416` that throws the stale part
> away, and a connection dropped mid-body retried as `GET bytes=2000-`. 23 tests
> added (484 → 507).
>
> What changes for the user, said plainly: up to a mirror's worth of bytes can
> sit in `bin/` after a failed attempt until a later install succeeds or the
> folder is cleaned — that is the price of being able to continue, and it is
> bounded by one file per mirror. A rewrite of the release mid-download still
> means starting over, because two different builds cannot be joined; the
> checksum is what makes that safe rather than silent. And two Viddl windows
> setting ffmpeg up at the same instant now write into the same part-file
> instead of two temporary folders of their own; the hash refuses whatever
> comes out of that, so the failure is "install it again", not a bad binary —
> the claim-file trick from **R6** is what this would need to be tidy, and
> installing ffmpeg is a one-time act by one person.

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

### R9 — Probing is a single 150-second blocking call · **P2** · M · ✅ **Done**

> **Fixed, all three ways the item asked for.** The look-up runs in a process of
> its own, so it can be stopped: the qualities screen counts the seconds
> (*"Looking up the link… 4s"*), and at five of them it says *"Still looking —
> some sites are slow to answer. Press “Stop looking” to give up."* with a
> button that becomes visible at the same moment. Stopping kills the whole
> process tree — the same helper a cancelled download uses — and raises
> `ProbeCancelled`, so the user sees the link box again rather than a failure
> they did not have; **Back** and `Esc` stop it too, and so does leaving the
> screen, because a 150-second process nobody is watching is exactly what the
> item was about.
>
> **Answers are remembered for five minutes**, keyed by the link *and* the
> options that change what a site hands back (cookies and the like), capped at
> twenty links so a long session cannot grow. Only a look-up that worked is
> kept: a failure is the thing most likely to be fixed by trying again, and
> caching it would take that away.
>
> **Playlists no longer cost more than the app can use.** The probe now passes
> `--playlist-end 1` alongside `--no-playlist`, and `parse_info` takes the total
> from the playlist's own `playlist_count` rather than from the entries it was
> handed, so the note still names the real number. `--flat-playlist` was
> considered and **not** used, and the reason is worth keeping: a flat entry has
> no formats, which is the one thing the quality menu is made of — the menu
> would come back as a single row of "mp4, size unknown".
>
> Following that through found a second, worse one on the **download** side: the
> screen said *"Only the first of 13 videos in this playlist"* and the command
> Viddl built said nothing of the kind. Built the way it was, a playlist link
> asked yt-dlp for **six items** (measured with the real engine:
> `--no-playlist --simulate --print '%(id)s'` prints 6; with `--playlist-end 1`
> it prints 1) while the README promised a playlist link "downloads its first
> video". The download command carries the same flag now, so the app's own words
> are true and the two halves agree.
>
> **Measured, with the real yt-dlp 2026.08.19 and real processes.** A real
> 13-video playlist link: **12.9 s and then `rc=1`** before — the old code read
> every entry, 2.85 MB of JSON, and the *whole look-up failed* because one video
> in the list is unavailable, so the user got nothing at all — against **3.6 s,
> 638 KB, eleven quality rows and `note='Only the first of 13 videos in this
> playlist.'`** after. The same playlist asked for again: **0.0 s**; a single
> video: 2.5 s, then 0.0 s. Against a server that accepts the connection and
> says nothing, a real `Engine` look-up stopped on request after **1.6 s**
> (cancel asked for at 1.5 s, the poll slice is 0.1 s) with **no yt-dlp process
> left behind**. And the real app, driven with the real engine against that same
> silent server, printed `status='Looking up the link… 2s'` and `'…4s'` with the
> button hidden, then at five seconds the hint with `stop-visible=True`, and
> clicking it landed back on the home screen. 14 tests added (507 → 521), and the
> ones that matter start real processes: the cancel test stops a real script and
> then checks that the file it would have written is not there.
>
> Two things this turned up that were not the item's point but were wrong:
> pressing **Back** while a look-up was running popped the qualities screen *and
> then* the screen behind it when the stopped worker reported back, leaving the
> app on an empty screen — the stopped worker now only pops if its own screen is
> still the top one — and a cached answer would otherwise have leaked between
> tests, so the suite clears the cache around every test.

`probe_video()` runs `--dump-single-json` with a 150 s timeout and no cache. On a
slow site the qualities screen simply sits there; clicking a second link
re-probes from scratch; and for a large playlist link yt-dlp may fetch far more
metadata than the app needs.

**Do:** show elapsed time and a "still working" hint after ~5 s with a cancel
option; cache probe results in memory for a few minutes; the app already passes
`--no-playlist`, so also consider `--flat-playlist` tolerance where relevant.

### R10 — Windows is ported but not exercised · **P2** · M · ✅ **Done**

> **Fixed, all three ways the item asked for.** The installer, the Windows CI
> job and the ASCII path are all in place; the evidence follows.
>
> **An installer exists, and it is the same shape as the shell one.**
> `install.ps1` finds `py -3` (then `python`), checks the version, builds a
> private environment in `%LOCALAPPDATA%\viddl\venv`, installs the project,
> copies the `viddl.cmd` shim into `%LOCALAPPDATA%\viddl\bin`, adds that folder
> to the **user** `PATH`, and registers `viddl://` through the shim (so the
> registry entry names the `.exe` inside the environment, not a bare `python`).
> `-NoPath` and `-NoProtocol` skip the two machine-facing steps. `uninstall.ps1`
> mirrors it — unregister, remove the launcher and the environment, take the
> folder off `PATH` — and keeps the helpers unless `-Purge`, exactly as
> `uninstall.sh` does.
>
> All four files are pure ASCII, because Windows PowerShell 5.1 reads a
> BOM-less script as ANSI and a non-ASCII character in one would be mangled
> before it ever ran.
>
> **CI runs the suite on Windows.** `.github/workflows/tests.yml` is a matrix of
> Linux, macOS and Windows × Python 3.9, 3.12, 3.13 running `pytest -q` and
> `pyflakes`, and a separate Windows job that runs **`install.ps1` for real**
> (`-NoPath -NoProtocol`, a private `VIDDL_HOME`) and then runs the launcher it
> wrote: `viddl --version`, `viddl --doctor`, `viddl --help`. The workflow YAML
> was parsed, not eyeballed, and both PowerShell scripts were checked with
> PowerShell 7.4.6's own parser.
>
> Getting the suite *able* to run there was the larger half. Every fake
> yt-dlp/ffmpeg in it is a two-line shebang script, which Windows cannot
> execute: `yt-dlp.exe` holding Python source is text to `CreateProcess`. A
> test-only fixture now translates those invocations — Python scripts through
> the interpreter running the tests, shell scripts through the bash that ships
> with Git for Windows (present on the runners).
>
> The shims are named the way `Engine` looks for them (`yt-dlp.exe`,
> `ffmpeg.exe`), which a handful of tests used not to be. The XDG/desktop and
> POSIX-permission tests are skipped on Windows with the reason instead of
> failing there, and the suite sets `USERPROFILE`/`APPDATA` so no test can touch
> the real profile.
>
> **`--plain` is the ASCII path.** `viddl --plain` prints one line at a time:
> the link's title, uploader and menu, then progress on a single overwritten
> line, then **the saved path alone on stdout** — the app's own text passes
> through a translation table (`·`->`-`, `…`->`...`, `—`->`-`, curly quotes to
> straight, `→` to `->`) and then an NFKD fold, so `café` stays readable and a
> title full of emoji becomes `?` instead of a `UnicodeEncodeError`. `--quality`
> takes a height, `best` or `audio`; `--yes` answers both questions in advance
> (including ffmpeg, below); without it a terminal is asked, and a script with
> no terminal is refused rather than downloading something nobody agreed to.
> `Ctrl+C` stops the download and keeps what was fetched, as in the app.
>
> **The console is no longer a way for output to crash.** `cli.main` sets
> `errors=backslashreplace` on stdout/stderr, so a redirected Windows console on
> a legacy code page cannot raise from the middle of `--doctor`: measured with
> `PYTHONIOENCODING=cp437`, the em dash in an ignored-override line comes out as
> `\u2014` and the command exits 0. A test scans **every string literal in
> `viddl/`** for cp1252 encodability (the translation table itself is the one
> exemption, since those characters exist there in order to be replaced), and
> another drives the real Textual screens and encodes every `Static`, `Button`
> and row's text to cp1252. The one literal that failed that scan was a real
> bug: `yt-dlp 2099.05.05 → …` in `--update`'s output, now `->`.
>
> **Measured against real yt-dlp 2026.08.19 and a real download.** With the
> plain path against *Me at the zoo*: the menu printed, `--quality 360` picked
> the 240p row, and because ffmpeg was missing the run said so and fetched it
> (149.2 MB) before continuing; the merge produced a 744,413-byte `mp4` that
> **decodes** (`ffmpeg -i` plus a decode to null: 284 frames, 19.06 s, video and
> audio), and **neither stdout nor stderr held a single non-ASCII byte**
> (checked with `grep -P '[^\x00-\x7F]'`). Piped with no terminal and no
> `--yes`, the same run exits **1** with *"ffmpeg is not set up and there is no
> terminal to ask on"* and downloads nothing.
>
> Following that through found a bug that had nothing to do with Windows:
> **real yt-dlp exits 0 when ffmpeg is missing**, having downloaded both streams
> and skipped the merge, and `_find_output`'s last-resort branch then reported
> the leftover **`.f140.m4a` audio fragment as the finished download**. Measured
> with real yt-dlp and no ffmpeg: the job now raises *"The video and audio could
> not be joined into one file"*, names ffmpeg in the hint, delivers nothing to
> Downloads, and leaves the two streams in the work folder so a later run
> carries on. That would have hit the full-screen app too, on any machine where
> ffmpeg was missing or had been removed since the check.
>
> 55 tests added (521 → 576), `pyflakes` clean, coverage 94% overall
> (`plain.py` 94%); the installer artifacts and the Windows translation are
> covered from Linux, and the Windows job is where they are actually run.
>
> **Not verified here, and it is the honest caveat:** there is no Windows
> machine in this environment. The workflow and the installer have been
> validated as far as they can be here — YAML parsed, both `.ps1` files parsed
> by PowerShell 7.4.6, the launcher's argument passing and exit code inspected,
> the platform-faked tests run — but **the first Windows CI run has not happened
> yet**, so Windows behaviour is asserted by construction, not observed.

The code handles the registry, `ffmpeg.exe`, `%LOCALAPPDATA%` and `USERPROFILE`,
and the README admits Windows is tested less. There is no `install.ps1`. Terminals
on Windows also default to legacy encodings, and the TUI uses box-drawing
characters and `·`/`…` throughout — a real risk of mojibake that no test covers.

**Do:** add `install.ps1` (and a `.cmd` shim), run the suite on Windows in CI
(**E1**), and add an ASCII/`--plain` rendering path (**U4**) with a test that the
UI text survives `cp1252` encoding.

### R11 — Timeouts and limits are constants · **P3** · S · ✅ **Done**

`_TIMEOUT = 30`, `_PROBE_TIMEOUT = 25`, `probe_video(timeout=150)`,
`--socket-timeout 20`, `--trim-filenames 150`, `_MAX_ROWS = 9`, `_MIN_HEIGHT = 144`,
`MP3_BITRATES`. All sensible defaults; none adjustable. A user on a slow link or
a fast one has no lever.

**Do:** expose the network ones behind `VIDDL_TIMEOUT` / a config file (**F13**)
and leave the presentation ones alone unless someone asks.

> **Fixed, for the network waits, which is the half the item asked for.** The
> presentation constants are untouched on purpose.
>
> **One variable, checked like the other five.** `VIDDL_TIMEOUT` takes a number
> of seconds — `45`, `45s`, `1.5` — and is refused, with a reason, when it is not
> one: empty, not a number, option-shaped (`--limit-rate`), carrying a control
> character, `nan`/`inf`, under a second, or over an hour. A bad value is
> ignored rather than half-applied, and `viddl --doctor` says so —
> `VIDDL_TIMEOUT=0 (ignored — shorter than 1 second)`. A good one is quoted back
> on a line the app did not have before: `Network:      waiting 45s per network
> request (VIDDL_TIMEOUT)`, or `Network:      built-in waits (set VIDDL_TIMEOUT
> to change them)`.
>
> **Unset keeps each wait where it was designed; set moves them together.**
> Leaving the variable alone is not a fallback to one number: the defaults differ
> on purpose, and the override replaces all of them, because they only make sense
> as a set — 20 s on a yt-dlp socket (the look-up and the download alike), a whole
> look-up bounded at seven and a half of those socket waits (150 s by default, so
> 75 s when the socket wait is 10 s), 30 s for a checksum list, 10 s for a
> mirror's size, 25 s for the check that a newly installed tool runs. Waiting
> 300 s on a socket while giving up on the look-up at 150 s would be two answers
> to one question.
>
> **26 new tests** (576 → 602), `pyflakes` clean, coverage 94% overall and 95% on
> `appdata.py`. **Measured, not asserted:** a local server that never answers gave
> up after **2.02 s / 4.00 s** with `VIDDL_TIMEOUT=2`/`4`, and after **10.01 s**
> with it unset — the variable really is the socket wait. Real yt-dlp 2026.08.19,
> through a wrapper that logs its command line, was given `--socket-timeout 7`
> for a `VIDDL_TIMEOUT=7` look-up of *Me at the zoo*, which succeeded in 1.8 s.
>
> **Not done:** `--trim-filenames`, `_MAX_ROWS`, `_MIN_HEIGHT` and `MP3_BITRATES`
> stay as they are, as the item says they should; **F13** (a config file) is still
> open, so these settings are environment-only for now.

---

## 3. Functionality

### F1 — Cookies from the browser · **P1** · M · ✅ **Done**

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

> **Fixed — and the README no longer lists signed-in sites as a dead end.**
>
> **A row per browser that is installed.** `viddl/cookies.py` knows the five
> browsers yt-dlp accepts (Chrome, Firefox, Edge, Brave, Safari) and where each
> keeps its data on Windows, macOS and Linux; the qualities screen offers a
> `Sign in with Chrome` row for every one it finds there. Choosing it re-runs the
> look-up with `--cookies-from-browser <browser>`, and every download started
> afterwards carries the same argument; choosing it again turns it off. Safari
> exists only on macOS, and a folder that is not there is not offered — but the
> table keeps it, because "not installed" is worth saying rather than ignoring.
>
> **The state is visible, not implied.** The row reads `Signing in with Chrome`
> while it is on, the heading under the video title says `signed in with Chrome`,
> and a notification says which browser is being read. A download that acts as an
> account must never look like one that does not.
>
> **What is read is said in the interface, before the click** — which is what the
> item asked for, so it is the row's own note: *"Viddl will ask Chrome for the
> cookies it already has for this site, and use them for this download only. Your
> password is never read and nothing is saved."* While it is on the note becomes
> the same promise in the present tense, ending *"Choose the row again to turn it
> off."* `--plain` prints that sentence to stderr before it downloads anything, so
> a script's log records what was asked of the site. `SECURITY.md` gained the
> matching line on both sides: a web page cannot set the choice, and the reader is
> told what it costs.
>
> **Nothing is stored, and nothing else can get in.** Viddl reads no cookie
> itself, asks for no password and writes no cookie file: it passes a browser's
> *name* to yt-dlp, which reads the store in place. That name is checked against
> the closed list in `cookies.BROWSER_KEYS` at the last step before the command
> line, so `--exec=calc.exe`, `netscape`, `cookies.txt` and `chrome; rm -rf /` all
> become *no arguments at all* — held by
> `test_a_name_that_is_not_a_browser_never_reaches_a_command_line`. A `viddl://`
> hand-off cannot turn it on: the choice is a click on the qualities screen or a
> validated flag. A signed-in look-up is also a different entry in the remembered
> answers (`probe_key`), so one viewer's session cannot serve the next anonymous
> look-up.
>
> **Failures are explained rather than echoed.** yt-dlp's real wording —
> `ERROR: could not find chrome cookies database in …`, taken from yt-dlp
> 2026.08.19 — becomes *"Viddl could not read your browser's cookies."* with the
> three things worth trying: sign in there first, close the browser, or pick a
> different browser. The older "the site wants you to be signed in" rule now
> points at the new row instead of saying the feature does not exist.
>
> **CLI and doctor.** `--cookies-from-browser auto|chrome|firefox|edge|brave|safari`
> works with the app, with `--plain` and with `--list-qualities`; `auto` takes the
> first browser found and is refused, with advice, when there is none. Safari on
> Windows or Linux is refused before anything runs. `viddl --doctor` gained a
> `Cookies:` line naming what it could see, or saying none was found.
>
> **The row is where the dead end is, not only where the menu is.** A link that
> answers only to a signed-in session fails the look-up, so the sign-in rows are
> offered on the failure screen too — the advice would otherwise point at a row
> that is not on screen. On a computer with no browser at all, that advice is
> replaced with what is actually missing rather than left pointing at nothing.
>
> **56 new tests** (602 → 658; 24 of them in `tests/test_cookies.py` over browser
> detection, the closed list, the notes, the refusals and the advice), `pyflakes`
> clean, coverage 95% overall and 99% on `cookies.py`.
>
> **Measured with real yt-dlp 2026.08.19, and one thing not measured.** An
> anonymous look-up still works (1.7 s); `cookies="chrome"` reaches yt-dlp — seen
> in the recorded argv — and fails in 0.5 s with the translated sentence, before
> any extraction; the same with an empty Chrome profile, so the translation is not
> tuned to one string. The three CLI refusals were run for real. **The positive
> path was not observed:** no browser is installed in this environment and no
> signed-in account exists here, so "a real session unlocks a private video" rests
> on yt-dlp's own documented behaviour and on the argument reaching it intact —
> not on a download this repository performed.

### F2 — Playlists · **P1** · M · ✅ **Done**

Currently `--no-playlist` plus a note: "Only the first of N videos in this
playlist." That is a reasonable default and a good message, but a playlist link is
common.

**Do:** when `parse_info()` detects a playlist, offer a checklist screen (all /
some / just this one) and run a sequential queue with per-item progress and a
final summary. Reuse the batch machinery from **F7**.

> **Done, and the machinery is a module F7 can use rather than a screen it would
> have to copy.** `viddl/queue.py` holds `QueueJob`: it downloads a list one
> video at a time, reports each start and each progress line, keeps going after
> a failure, and returns a `QueueSummary` whose `headline` is true whether the
> run finished, partly failed or was stopped. F7 (paste several links) needs to
> add a way to build the list; it does not need to touch the running of it.
>
> **The first video is still the default, and the note now offers the rest.**
> `parse_info` puts a `PlaylistSummary` on `MediaInfo` beside the first entry's
> qualities, so the qualities screen can say *"This is a playlist of 182 videos:
> “Popular Music Videos”. Download takes the first one; “Choose videos…” picks
> which of them to take."* A click never starts forty downloads.
>
> **The checklist is a listing, not 182 look-ups.** `probe_playlist` runs
> yt-dlp with `--flat-playlist --dump-single-json --playlist-end 200`: titles,
> links and lengths, no video page read. **Measured with yt-dlp 2026.08.19 on a
> real 182-video playlist: 1.7 s to list all 182** (against 2.9 s for the
> ordinary look-up of the first video), and **0.00 s** when the same list is
> asked for again — remembered in memory for five minutes, keyed on the link,
> the cookie choice and Viddl's own options, exactly like a look-up. A playlist
> longer than the limit says *"Viddl lists the first 200 of 500 videos…"* rather
> than pretending to be complete.
>
> **All, some, or just this one.** The checklist opens with every video ticked;
> a click or `Space` leaves one out, `a`/`n` tick all or none, and the quality
> chosen on the screen before it is repeated on the list (*"2 of 3 selected ·
> 1080p each"*) so it is never a surprise. **Download** on the qualities screen
> is still just this one video. The queue's heading says `3 videos`, the status
> says `Video 2 of 3 · La Diabla`, one bar covers the whole list, and the end
> screen says *"Saved all 2 videos."* or names the videos that failed by title.
>
> **A bad video costs one item.** The queue catches the failure, records it, and
> starts the next video; the summary names what did not work. Stopping the queue
> stops the video it is on and starts nothing else — the items after it are
> marked skipped rather than left `waiting` — and `Download the rest` runs the
> list again, which costs a look-up per finished file rather than a download.
>
> **`--plain --playlist`** takes every video at the chosen quality, prints
> `[3/9] Title` and the same progress line for each, writes **one path per line**
> on stdout, and ends with the summary. One failure means exit code 1 even when
> nine files were saved, so a script cannot mistake a partial run for a complete
> one. On a single video the flag is harmless; on a playlist without it the
> output says *"Viddl will download the first of the 3 videos. Add --playlist to
> download all of them."* — the same advice the app gives with a button.
> `viddl --playlist` without `--plain` is refused with the right example.
>
> **A listing that gives identifiers instead of links still works.** Some
> extractors name an entry by id rather than address; for those the queue asks
> the playlist for item *N* (`--playlist-items N`) instead of refusing, while a
> download that is not part of a queue keeps `--no-playlist --playlist-end 1`
> exactly as before.
>
> **27 new tests** (658 → 685; all in `tests/test_playlist.py`), `pyflakes`
> clean, coverage 93% overall and 93% on `queue.py`.
> **Measured end to end with real yt-dlp 2026.08.19 and a real ffmpeg:** two
> videos from that playlist downloaded through `QueueJob` in **6.4 s**, written
> as `Real Boston Richey … [jWdxXdp8sZI].mp4` (6.4 MB) and `La Diabla
> [s6cdjJvGM2o].mp4` (3.1 MB), with the callbacks firing `start 1/2`, `end 1
> done`, `start 2/2`, `end 2 done` and the summary *"Saved all 2 videos."*
>
**Not done:** a queue is not paused and resumed item by item (stopping keeps
> the partial file and the finished files, and `Download the rest` re-checks
> them), and a playlist is listed to 200 videos. A `watch?v=…&list=…` link is
> still read as the one video it names: the look-up asks yt-dlp not to read a
> list, so Viddl never learns the video belongs to one — opening the playlist
> address itself is what offers the rest. **F7** (pasting several links) is
> still open, and now has the runner it was told to reuse.

### F3 — `viddl --update` · **P1** · S · ✅ **Done**

See **R4** — listed again because it is also the single most requested kind of
feature in this space: "it broke, make it work again."

> **R4 built the command; F3 closes the loop around where the advice is read.**
> The app used to tell an old engine's owner to run `viddl --update` and then
> offer nothing else — advice that means leaving the app and finding a terminal.
> Now the sentence has a button beside it, in the two places a person is when
> they need it: the home screen's staleness line and the screen that says a link
> did not work.
>
> **The button is offered only when it is true.** It appears when the copy
> actually in use is over `STALE_DAYS` (30) old, and not on a current engine,
> because updating a current engine changes nothing. On the failure screen the
> note adds *"The video engine is old, which is the usual reason a link stops
> working. Press “Update engine”, and the link is asked again straight
> afterwards."* On a current engine that sentence and the button are absent.
>
> **It runs the same install, not a second one.** `Services.update_engine`
> calls `Engine.update_ytdlp`, so the checksum check, the size cap and the
> atomic replace are the ones the flag uses. Progress is shown where the status
> line is (`Updating the video engine… 50%`), the version is reported as a move
> (*"yt-dlp 2020.01.01 -> 2026.08.19"*, "Installed yt-dlp …" for a first
> install), and a failure says why and re-enables the button without touching
> the failure that prompted it.
>
> **Then it asks the link again by itself** — and that is the feature. The
> remembered answers are cleared first (`clear_probe_cache`), because a new
> engine gives new answers and reusing the old ones would defeat the point. If
> there was nothing newer the screen says *"already the newest release"*, the
> link is **not** asked a second time, and the button stops being offered for
> the rest of the session: an update that changed nothing is an answer, not a
> prompt to press it again.
>
> **Said where a script can read it too.** `viddl --plain` appends
> `The video engine is 62 days old, which is the usual reason a link stops
> working. Run `viddl --update` to replace it.` to a failed look-up or download,
> and `--list-qualities` appends the same to its failure — but only when the
> engine really is old, and once per playlist run rather than once per failed
> video (`viddl/engine.py:staleness_advice`).
>
> **13 new tests** (685 → 698, all in `tests/test_update.py`), `pyflakes` clean,
> coverage 93% overall and 96% on `engine.py`.
> **Measured with the real release:** `update_ytdlp` on an empty folder fetched
> and checksum-verified yt-dlp **2026.08.19 in 1.8 s**; the doctor then read
> `2026.08.19 (downloaded by Viddl), sha256 1fa6733c37ea… (matches what was
> installed)`. A second update took 2.0 s, reported the same version, and
> `changed=False` — which is exactly the branch that does not re-ask the link.
>
> **What that measurement found, and what was changed because of it:** the
> newest stable release was itself **62 days old** on the day this was measured,
> so a freshly updated engine is still "stale" by the 30-day rule. Left alone,
> the app would have nagged about an engine the user had just replaced. It now
> clears the staleness once an update has been attempted and says "already the
> newest release" instead; the *next* launch may still call it old, which is
> true, and pressing the button then costs two seconds and answers honestly.
>
> **Not done:** a download that fails *after* a successful look-up shows the
> error without the update button (the engine could read the site, so it is the
> less likely cause); the update needs a working connection and fetches the
> latest **stable** release, not a nightly; and Windows is exercised by tests
> with the platform faked, not by a real in-app update.

### F4 — More than three audio options · **P2** · S · ✅ **Done**

`MP3_BITRATES = ((320, "0"), (192, "5"), (128, "7"))` and the audio path always
targets MP3. m4a/opus/flac are lossless-or-better choices people actually want,
and for many videos the source is already m4a/opus — so re-encoding to MP3
throws quality away for no reason.

**Do:** add "Audio only · original (m4a/opus, no re-encode)" and a format picker
(mp3/m4a/opus/flac/wav) with bitrate presets, reusing the existing estimate logic.

> **Done, and the first row is the one that costs nothing.** `parse_info` now
> puts **`Audio only · original M4A`** (or `Opus`, or whatever the stream holds)
> first: the site's own audio stream by its own format id, `needs_ffmpeg=False`,
> no `-x`, no encoder. `MP3_BITRATES` became a table — `AudioFormat` with
> `AudioPreset` rows — covering **MP3 (320/192/128), M4A (256/128), Opus
> (160/96), FLAC and WAV**, each labelled with its container and each carrying
> the `--extract-audio --audio-format … --audio-quality …` the old MP3 rows
> carried. Sizes reuse `estimate_size`, still capped by the source bitrate;
> FLAC and WAV are sized from the clock instead (44.1 kHz stereo for WAV, half of
> that for FLAC) because their size does not follow the source bitrate at all.
>
> **`--quality` names a format now.** `audio` (and `--audio`) means the best
> sound the link has — the untouched file — while `mp3`, `m4a`, `opus`, `flac`,
> `wav`, `original` and a preset such as `mp3-192` each pick exactly that row. A
> format name that the link already *is* resolves to the original row, which is
> the same thing without the encoder; `mp3` still means MP3, so a script that
> asked for one keeps getting one. The note under the list says which of the two
> it is looking at — *"the site's own M4A file … without re-encoding"* against
> *"converted to FLAC … converting cannot add quality the source does not
> have"* — and the engine line, `--help`, the README and the browser extension's
> "Audio only" button all stopped assuming MP3.
>
> **What the measurement changed — two real behaviours.** First, asking yt-dlp
> for the container the source already uses does not re-encode it, it **copies**
> it: `--audio-format m4a` on an m4a source wrote **the same 309,205 bytes** a
> second time. An "M4A 128 kbps" row would therefore have been the source file
> under a bitrate nobody honours, so a format whose key matches the source's own
> codec is **not offered at all** — the original row is that format. On the real
> link that left 8 audio rows, no M4A pair, and `--quality m4a` resolving to the
> original.
>
> Second, AAC is asked for a **fixed** rate and writes it: `M4A 256 kbps` from a
> 130 kbps Opus source produced **612,353 bytes at 255 kb/s in 3.3 s**. The old
> "cap the estimate by the source" rule is right for libmp3lame's VBR steps and
> for libopus (measured: MP3 320 from a 130 kbps source → 323 KB at 139 kb/s;
> Opus 160 → 241 KB), but it understated a fixed-rate row by half.
> `AudioPreset` now carries `fixed_rate`, so an M4A row shows what the file will
> weigh and its note says where the sound came from.
>
> **Measured end to end with real yt-dlp 2026.08.19, a real ffmpeg and the real
> "Me at the zoo" link** (probe 3.0 s): original **309,157 bytes** AAC 44.1 kHz
> with no ffmpeg step (2.5 s); MP3 128 **105,045 bytes**; Opus 96 **247,111
> bytes**; FLAC **1,416,212 bytes**; WAV **3,649,142 bytes** — each confirmed by
> ffmpeg as the codec its row promised, each in 1.9–2.8 s on top of the
> download.
>
> **21 new tests** (698 → 719), `pyflakes` clean, coverage 93 % overall and
> 94 % on `formats.py`, where the new half lives.
>
> **Not done:** the MP3 presets are still libmp3lame **VBR steps** labelled with
> rough nominal bitrates — the row that says 128 kbps wrote 44 kb/s on a
> 19-second speech clip, marked `~` but a 65 % miss is not "rough"; making them
> fixed-rate is a separate decision about quality per byte. M4A and Opus
> conversions were exercised against a real source through the same arguments a
> real run builds, not through a site whose own sound is opus (none was to hand).

### F5 — Subtitles, thumbnails and metadata · **P2** · M · ✅ **Done**

`--write-subs`, `--embed-subs`, `--embed-thumbnail`, `--embed-metadata` and
`--embed-chapters` are all one flag each, and the app already owns the ffmpeg
prompt that these need.

**Do:** a small "extras" toggles row on the qualities screen, off by default,
with "needs ffmpeg" surfaced the same way the quality rows already do it.

> **Done, as three switches rather than four, because yt-dlp has three
> behaviours.** A one-line `Extras` row sits under the quality list —
> `[ ] Subtitles  [ ] Details  [ ] Cover image` — and starts off. Click it,
> Tab to it and press Enter or Space, or use `s`, `m` and `t`; the tick is in
> the label, not only in the colour. `--extras subs,details,cover` names the
> same set from a script (and opens the app with those switches already on),
> `--extras all` takes the three, and a mistyped word is refused with the list
> of real ones.
>
> `--embed-metadata` **already embeds the chapters**: yt-dlp sets `addchapters`
> whenever `addmetadata` is set, so a separate chapters switch would do nothing
> while details were on. The two are one row. `--write-subs` and
> `--embed-subs` are one decision too, so they are one row.
>
> **Subtitles ask for English only.** Measured first with `--sub-langs "en.*"`:
> YouTube matched `en`, `en-en` and `en-de`, the last answered **HTTP 429**,
> and yt-dlp ended the whole run with `ERROR` before it had fetched the video
> at all. One language is one request, and it is embedded (`mov_text` in MP4).
> An audio download skips the row and says why rather than asking ffmpeg for a
> stream nothing can show.
>
> **The cover image is saved beside the file, not inside it — and that is the
> measurement.** yt-dlp's own embedding needs mutagen or AtomicParsley; the
> release Viddl installs has **neither**, and the ffmpeg it installs is the
> binary **without ffprobe**. On a real MP4, `--embed-thumbnail` therefore
> failed the *entire download* with `Unable to embed using ffprobe & ffmpeg`
> after the video had already downloaded. `--write-thumbnail
> --convert-thumbnails jpg` works with the tools that are actually there, so
> the row promises exactly that: a JPEG next to `Title [id].mp4`, named
> `Title [id].jpg`, moved out of the working folder by the job and named on the
> result screen. When the name is already taken and the video becomes
> `Title (2).mp4`, the picture becomes `Title (2).jpg` to match. A picture that
> cannot be moved is logged, never a reason to fail a saved video.
>
> **A picture is not the download.** yt-dlp announces the thumbnail and the
> caption files with the same `[download] Destination:` line as the video, and
> a thumbnail can be announced last — enough for the old "newest file in the
> folder" fallback to report a 23 KB JPEG as the finished video. Attachments
> are now filtered out of the remembered paths and of both output scans, which
> is held by tests. The download also reports `Adding the subtitles…`,
> `Adding the details…` and `Saving the cover image…` while it happens, and the
> extras are part of `needs_ffmpeg`, of the one-time ffmpeg prompt and of the
> working-folder identity, so two runs with different extras cannot share a
> folder.
>
> **Measured end to end with real yt-dlp 2026.08.19 and ffmpeg 7.0.2** (the
> same binary-only ffmpeg Viddl downloads), on “Me at the zoo”. 144p with all
> three extras took **14 s** and produced a **508,685-byte** MP4 with an `eng`
> `mov_text` subtitle stream, `title`/`artist`/`date`/`description` tags, **3
> chapters** (Intro, The cool thing, End) and a **23,688-byte 480×360** JPEG
> beside it; stdout was the MP4 path alone and stderr said
> `Also saved: … .jpg`. `--quality mp3-192 --extras all` took **7 s**, wrote a
> **121,718-byte** MP3 with the same tags and chapters in ID3, put the JPEG
> beside it, and printed `Skipped: subtitles - an audio download has nowhere
> to put them.` Both exited 0. The real app was driven headlessly too, with the
> real engine and the real download: the look-up took **3.2 s**, `s` + `t`
> put `[x] Subtitles   [x] Cover image` on the one-line row, Enter saved the
> file in a further 3.4 s, and the result screen read `240p · 728.0 KB ·
> with Me at the zoo [jNQXAC9IVRw].jpg` with an `eng` `mov_text` stream in
> the MP4 and the JPEG beside it.
>
> **44 new tests** (719 → 763), `pyflakes` clean, coverage 93 % overall and
> 100 % on `extras.py`; the screen's own tests cover clicking, `s`/`m`/`t`,
> Tab + Enter/Space, the audio-row warning, the ffmpeg prompt the extras create
> on their own, the extras reaching the job, and the result screen naming the
> picture.
>
> **Not done:** the subtitle language is English, with no picker; cover art is
> never *inside* the media — that needs mutagen shipped with yt-dlp, or ffprobe
> plus container-specific work, and neither is a toggle's worth of work; a link
> the site has no thumbnail for silently adds no picture (there is nothing to
> say without a second request); and the one-line row has been measured (27-row
> panel at 80×24, the row hit-tested and not overlapping the buttons) and
> driven by the pilot, but not looked at by a human eye.

### F6 — Download archive and a recent-downloads list · **P2** · M · ✅ **Done**

Re-downloading something already on disk is a common frustration; the app notices
only via yt-dlp's own "has already been downloaded" line (`_ALREADY_RE`), which
it handles — but nothing is remembered between runs.

**Do:** optional `--download-archive` in the app data dir (which also makes
playlist downloads incremental), plus a "Recent" screen built from a small JSON
file (path, title, size, when) with Reveal/Copy/Re-download actions. That is also
the natural home for **F9** notifications.

> **Done, and the archive turned out to be two features and a bug-fix.** A new
> `viddl/history.py` owns both memories: `recent.json` (link, path, title, size,
> when — the last fifty) and `download-archive.txt` (yt-dlp's own format, one
> `extractor id` per finished video). `--download-archive` turns the second on
> for a run, as does `VIDDL_ARCHIVE=1`; **off is the default**, because skipping
> a download you asked for is a promise to keep, not a surprise to spring. Both
> front doors read it (`DownloadJob(archive=…)`, threaded through `QueueJob`,
> `plain.run_plain` and `State`), so the app and `--plain` agree about what
> "already have this" means, and `--doctor` prints the archive's state and where
> the recent list lives.
>
> **The bug the archive exposed: a skip looked like a broken download.** yt-dlp
> exits **0** when the archive refuses a video, so `DownloadJob` saw success, no
> file in its working folder, no ffmpeg merge and raised *"the download finished
> but the file could not be found"* — for a run that did exactly what it was
> told. `_attempt` now reads the skip line the tool prints (`[download] <id>:
> <title> has already been recorded in the archive`, measured on 2026.10.21),
> joins it with the id in the file name (`Title [id].mp4`, the output template's
> own marker), and hands back the newest file in the Downloads folder whose name
> carries it — attachments and `.part` files excluded, as everywhere else. That
> path is then reported as **"Already had"** instead of "Saved": in `--plain`,
> on the queue's per-item line, and on the result screen, which also drops the
> quality label for such a file, since it was fetched at some other quality.
>
> **A memory that blocks a download is worse than no memory.** If the archive
> remembers a video but the file is gone (tidied Downloads folder), the run drops
> that line with `history.forget_video()` and asks **once** more — the archive
> catching up with the disk rather than the disk being asked to prove itself.
>
> **The Recent screen** is one key from the home screen (`r`, or the **Recent**
> button) and lists the last fifty newest-first with size and "12 min ago" /
> "3 days ago"; `Enter` or a click asks for the qualities screen with the link
> refilled (nothing downloads, same promise as a link from outside, **S5**),
> `o` opens the folder, `c` copies the path. A row whose file has since been
> deleted stays in the list marked *"file is gone"* — hiding it would be a
> second surprise on top of the missing file — and a hand-edited `recent.json`
> (every field coerced, a bad row skipped) degrades the list, never the screen.
> Both files are written through a temporary name and `os.replace` under one
> lock, so a read never sees half a list and two downloads finishing in the same
> second cannot lose a row.
>
> **Measured end to end with real yt-dlp 2026.10.21** (no ffmpeg; a
> 200,020-byte MP4 served over local HTTP), through the real CLI front door
> (`--plain --yes --download-archive`): run 1 printed **`Saved (195.3 KB)`** and
> wrote `generic clip` to the archive; run 2 printed **`Already had
> (195.3 KB)`**, exited 0, and left **one** file in the folder; `recent.json`
> held **one** row (the same path de-duplicated, not a second line). Deleting
> the file and running again dropped the archive line, re-downloaded, and
> reported **`Saved`** with the file back on disk — 200,020 bytes, same name. At
> the job level the same link twice gave `already_had=False` then `True` with
> the identical path, and the skip line was read from the tool's own output
> rather than from a stub. The files came out **0600** for `recent.json` and
> **0644** for `download-archive.txt` — yt-dlp's own mode, inside the app's
> **0700** folder — which is what the module docstring now says, instead of
> claiming both.
>
> **58 new tests** (763 → 821), `pyflakes` clean, coverage **93 %** overall
> (`history.py` **95 %**, `download.py` **95 %**, `tui/app.py` **89 %**), and the
> README has a "What is remembered between runs" section, the flag in its table,
> and `VIDDL_ARCHIVE` in the environment table.
>
> **Not done:** the archive cannot be switched on from inside the app — there is
> no settings screen yet (**F13**), so it is a flag or an environment variable;
> the recent list has no search, no "clear", and no way to remove one row from
> the app; **F9** notifications were not built on top of it; and incremental
> playlists are held by tests through `QueueJob` with a fake job, not measured on
> a real playlist link (the archive arguments are the same ones the single-video
> run used, and that part *was* measured).

### F7 — Batch queue · **P2** · L · ✅ **Done**

Paste several links (or select playlist entries) and let them run in order, with
per-item state and an overall summary.

> **Done, and the runner was the easy half.** `viddl/queue.py` (F2) already did
> the downloading — one at a time, per-item state, a failure that costs one item
> and not the list, a summary that names what went wrong. What was missing was
> somewhere to *collect* the links, and that is now **Links** (`LinksScreen`):
> paste several into the home box on separate lines or separated by spaces, and
> the home screen splits them **on whitespace only** — a comma or a dot is legal
> inside an address, so cutting there would corrupt a link — puts each through
> `safe_video_link`, the same gate a single link goes through (S1), collapses
> duplicates, and opens the list in the order they were pasted. Punctuation
> stuck to the end of a link (“…?v=abc,”) is dropped; a word caught between two
> links is left out and **said so**, because a silently dropped link is a person
> wondering later why one of three never arrived.
>
> **The interaction the item asked for:** one row per link (`1. youtube.com` and
> the path beside it, with the query redacted the way the log is, S4), `Delete`
> or **Remove** for the row under the cursor, `c` or **Clear** for the lot, and a
> box at the top where another link can be pasted and added with `Enter` — the
> box keeps the focus so a batch can be pasted in a row, and a link already in
> the list is refused instead of queued twice. `Enter`/`d`/**Download all** hands
> the whole list to the qualities screen, where **one** quality is chosen from
> the first link's menu and used for every link, in order; the note says exactly
> that, and “Choose videos…” is not offered, because replacing the batch with
> one link's playlist would throw the rest away.
>
> **The bug that wiring found:** `OptionsScreen.on_choice_list_selected` started
> a download directly instead of going through `action_download`, so pressing
> Enter on the quality list would have fetched *one* video and quietly ignored
> the other links. It now takes the same path as the button — choosing a row and
> pressing Download mean one thing.
>
> **A batch of links is not a playlist,** and the summary screen no longer says
> it is: `All links saved` / `Links finished with problems` / `Links stopped`,
> and “the links that were not started are still on the list” rather than
> “unticked”. Under it is the same summary F2 built: every file named, every
> failure named, the stop button, the resume, the one-time ffmpeg prompt, the
> extras and the archive (F5, F6).
>
> **The links arrive without titles**, and the queue now records the *entry's*
> title rather than the row's `Video 3` placeholder, so the recent list names the
> file by its own title instead of by the position it happened to hold.
>
> **Measured end to end, in the real app, with real yt-dlp 2026.10.21** and two
> MP4s served over local HTTP (no stubs for the probe, the job or the queue).
> Pasting the two links opened Links with `1. 127.0.0.1 (/first.mp4)` and
> `2. 127.0.0.1 (/second.mp4)` and the note *“2 links · they download in order”*;
> the qualities screen probed **only the first** link (the second was never
> looked up), said *“2 links · this quality is used for all of them, in the order
> they were added”*, and the run ended on **`All links saved` / `Saved all 2
> videos.`** with `first [first].mp4` and `second [second].mp4` both in the
> folder.
>
> **10 new tests** (837 → 847): seven driving the app (a paste that opens the
> list without probing anything, a word left out and counted, the same link
> twice as one video, add / refuse-duplicate / remove / clear, the empty list
> refusing to start, the full two-link run in order at one quality, and
> “Choose videos…” staying hidden), two on the splitting (whitespace only, and
> a trailing comma dropped while `…/clip.mp4` survives intact) and one on the
> queue (an unnamed entry is labelled `Video 2` but is not *named* it).
> `pyflakes` clean; coverage **93 %** overall (`tui/app.py` **90 %**,
> `queue.py` **94 %**). README has the row and a paragraph on the queue.
>
> **Not done:** rows cannot be reordered — the list is the order, and adding a
> link again is how a row moves; `--plain` still takes one link per run (a batch
> script loops, and `--playlist` covers a list from one link), so the queue is
> the app's; the quality menu comes from the first link, so a batch that mixes
> sites is offered that link's rows and the generic selectors behind them
> (`best`, `1080p`) rather than each site's own labels; and there is no
> per-item cancel — stopping still stops the queue after the item in flight,
> with the rest left on the list.

### F8 — Clipboard-aware start · **P2** · S · ✅ **Done**

The home screen says "Paste a video link", but nothing offers the link that is
already on the clipboard. Checking the clipboard on launch is what mail clients
do, and it removes the most common first friction.

**Do:** offer "The clipboard has a link — download it?" with a *Paste* key
binding (**U5**). Never auto-fill silently, never auto-start.

> **Done, and it is an offer rather than an action.** A new `viddl/clipboard.py`
> reads the clipboard **once**, when the home screen mounts, through the
> platform's own tool — `Get-Clipboard`, `pbpaste`, `wl-paste`, `xclip`,
> `xsel`, first one installed wins — with a fixed argument list, no shell, and a
> 3 s timeout. Everything it returns goes through `safe_video_link`, the same
> gate every other way in uses, so a password, a paragraph or an option-shaped
> string is dropped where it is read and never reaches a variable, let alone
> the log.
>
> **What the screen does with it:** one line under the link box — *"The
> clipboard has a link to www.youtube.com — click Paste link, or press p."* —
> and a **Paste link** button that is hidden until there is something to paste.
> The host is shown and never the whole link, because a copied address can
> carry a signed token and a screen is read over shoulders. Clicking it (or
> pressing `p`, under the rule `r` already follows: while the box has the focus,
> a letter is a letter) fills the box and **stops** — nothing is probed, no
> screen is pushed, and Enter is left to the person. Opening Viddl with a link
> in hand does not read the clipboard at all: no subprocess, and no offer on top
> of the download already being asked about. The clipboard is never polled.
>
> **Measured** with stand-in clipboard tools on `PATH` (the platform's own tool
> replaced, argv and all): a tool printing a link gave `read_text()` that link
> with the trailing newline trimmed; a tool exiting 1 gave `""`; a tool that
> slept gave `""` after **3.0 s**; and the real app, driven headlessly with the
> *default* wiring rather than a stub, showed `The clipboard has a link to
> www.youtube.com — click Paste link, or press p.` with the link box **empty**,
> then filled the box with the whole link — signed token and all — on the
> click, and stayed on the home screen.
>
> **16 new tests** (821 → 837): nine for the reader (which tool per platform, a
> failed reader falling through to the next, a hung one giving up, the timeout
> reaching every tool, NUL and newline trimming, no shell) and seven driving the
> app (an offer that does not fill the box, a button that does not submit, `p`
> as a letter in the box and as a shortcut outside it, a clipboard with no link,
> hostname-only display, no read at all when opened with a link, a read that
> raises). `pyflakes` clean; coverage **93 %** overall (`clipboard.py` **100 %**,
> `tui/app.py` **90 %**). The README has the row and the paragraph, and
> `SECURITY.md` gained a **The clipboard** section and a "cannot start something
> from your clipboard" row.
>
> **Not done:** there is no switch to turn the read off — no settings screen yet
> (**F13**) — so it is one read at launch, ignored by leaving the offer alone;
> `p` cannot work while the link box is focused (a letter there is a letter, as
> `r` already was), so from a fresh launch the button is the path; `--plain` has
> no clipboard step, since there is no interface to offer anything in; and the
> Windows and macOS commands are covered by tests with the platform simulated,
> not run on those systems.

### F9 — Tell the user when a long download finishes · **P2** · M

A 40-minute download ends in a terminal the user has long since switched away
from. There is no desktop notification and no terminal bell.

**Do:** opt-in notifications (`notify-send`, `osascript`, PowerShell toast) with a
terminal-bell fallback, plus a "when done" toggle on the progress screen.

### F10 — Machine-readable output · **P2** · M · ✅ **Done**

`--list-qualities` prints human text. `--doctor` prints human text. Neither is
parseable, which blocks scripts and the Playwright/desktop front-ends people
build on top of tools like this.

**Do:** `--json` for `--list-qualities`, `--doctor` and (optionally) download
progress as JSON lines; a `--quiet` that prints only the final path. Small, and
it multiplies the project's usefulness.

> **Done, in two pieces that answer the same question differently.** `--json`
> prints one object instead of lined-up sentences, for the two read-only
> answers a script actually asks for: `viddl --json --list-qualities <link>` and
> `viddl --doctor --json`. Both are built in a new `viddl/machine.py` from the
> same places the sentences are built from — not by parsing the text — so the
> two cannot drift apart, and the shapes are documented in the README with
> every key always present. A missing size is `null` and never `0`, a missing
> engine is `null` and never an empty string, and rows are in the order the app
> shows them, so `qualities[0]` is what pressing Enter would pick. Output is
> sorted, indented and UTF-8 (a Japanese title survives), which makes it
> diffable and testable.
>
> `--quiet` is the download half: with `--plain` it keeps the stdout contract
> and drops everything else, so **stdout is the path and stderr is empty**. The
> split is one stream (`plain._Quiet`) rather than a flag in twenty functions:
> progress, the menu and the notes go there, while failures keep the real
> stderr — a command that fails silently is a command nobody can debug. It
> works for a playlist too (one path per line, nothing else).
>
> **Measured against the real CLI and real yt-dlp 2026.10.21** on an MP4 served
> over local HTTP: `--doctor --json` produced a parseable object with **0 bytes
> on stderr**; `--json --list-qualities` gave `title: first`, rows
> `[('best','video',None), ('mp3-320','audio',None), …]` and `playlist: null`,
> parsed with `json.load` and nothing on stderr; `--plain --yes --quiet
> --quality best` exited **0** with `stdout` exactly
> `/tmp/f10e2e/out/first [first].mp4` and **stderr 0 bytes**, while the same
> run without `--quiet` wrote **16 stderr lines** (menu, `Selected:`, progress,
> `Saved`).
>
> **15 new tests** (847 → 862): seven on the objects (the list is the screen's
> order, a `null` size stays `null`, a playlist summary carries its count, the
> doctor carries the facts the sentences carry, a missing engine is `null`,
> `dumps` is sorted and ends with one newline, non-ASCII survives), five on the
> CLI (`--list-qualities --json` parses, `--doctor --json` has no human labels,
> `--json` alone is refused with the `--quiet` hint, `--quiet` alone is
> refused, `--quiet` reaches `run_plain`), two on the quiet download path (path
> only, and failures still speak) and one on a quiet playlist. `pyflakes` clean;
> coverage **93 %** overall (`machine.py` **100 %**, `cli.py` **98 %**,
> `plain.py` **88 %**).
>
> **Not done:** download progress as JSON lines — deliberately, because
> `--quiet` already gives a script the one thing it needs and a second format
> would be a second contract to keep in step; `--json` does not cover failures
> (they stay sentences on stderr with exit code 1) or `--update`; the flag has
> to come *before* `--list-qualities` (`viddl --json --list-qualities <link>`)
> because argparse reads the value that follows the option; and the doctor's
> `environment` list carries the raw values, exactly as the text form already
> prints them.

### F11 — Bandwidth and network control · **P3** · S · ✅ **Done**

`--limit-rate`, `--proxy`, `--concurrent-fragments` tuning, `--throttled-rate`
for slow hosts. Worth exposing behind an "Advanced" row once **F13** exists.

> **Done as four flags, on both front doors, with no settings screen to wait
> for.** A new `viddl/network.py` holds one frozen `NetworkOptions` — a speed
> limit, a proxy, a fragment count, a throttle — and turns it into the engine's
> own arguments in a fixed order. Every value is checked at the front door
> rather than passed on, because these strings *are* argv: `--limit-rate fast`
> is refused with the shape it wants, and a proxy must start with `http`,
> `https`, `socks4`, `socks4a`, `socks5` or `socks5h` and have a host, a port in
> range, no whitespace and no control characters — so `--proxy 127.0.0.1:8080`
> (no scheme, and shaped like an argument) and
> `http://host:8080` + a newline + `--exec=rm` are mistakes, not flags. Speeds
> are normalised (`2m` → `2M`, `2MB` → `2M`), which is also what the sentence in
> the log says.
>
> The choices are carried by `DownloadJob`, `QueueJob`, `run_plain` and the
> app's `State`, so one proxy and one ceiling cover every video of a playlist or
> a batch — a limit on the first of nine would be a limit on nothing. They are
> emitted where the fragment count already was (before the user's own yt-dlp
> options, R3, and before the `--` that ends the options), and the hard-coded
> `--concurrent-fragments 4` became `NetworkOptions.fragments`, so the flag is
> still passed exactly once. In the app the qualities screen and the playlist
> checklist repeat the terms (*Network: through 127.0.0.1:1080, no faster than
> 2M/s*), and `--plain` prints one `Network:` line before anything is fetched;
> `--quiet` drops it with the other notes.
>
> **A proxy password is a password**, so `summary()` names the host and port and
> never the credentials, while the engine still gets the whole address — the one
> place it is needed. Tested in both the plain log and the app's note.
>
> **Measured against real yt-dlp 2026.10.21** on a 4 MB MP4 served over local
> HTTP, one run each: no flags **1.75 s**, `--limit-rate 4M` **2.75 s**,
> `--limit-rate 2M` **3.83 s** — the extra seconds are the file at the rate that
> was asked for, on top of the same start-up. A run at `1M` with
> `--concurrent-fragments 2` took **5.69 s** and exited 0, and the same command
> with `--throttled-rate 100K --limit-rate 8M` printed its `Network:` line and
> exited 0. Through a local forwarding proxy (`--proxy http://127.0.0.1:8899`)
> the download exited **0**, the file was 4 MB exactly, and the proxy's own log
> held its two GETs (own look-up first, then the download); pointed at a closed
> port the same command failed with exit **1** after the usual three tries, so
> the flag is used and not ignored. `--limit-rate fast` and
> `--proxy 127.0.0.1:8080` were refused with exit **1** and a sentence, before
> anything ran.
>
> **65 new tests** (862 → 927): 52 in `tests/test_network.py` (every value read,
> every refusal, an option-shaped speed, a proxy with a password, an IPv6 proxy,
> the fixed argument order, the note in words, the flags reaching `run_plain`
> and the app, four bad values refused before anything is downloaded), two on
> the job's arguments (Viddl's own count by default; all four flags in place
> once, before `-P` and `--`), one on the queue (every item gets the same
> network choices), four on `--plain` (the note, no note without flags, no
> password in the log, nothing under `--quiet`) and three on the app (the note
> on the qualities screen, nothing without flags, and the job the download
> gets). `pyflakes` clean; coverage **93 %** overall (`network.py` **100 %**,
> `download.py` **95 %**, `queue.py` **94 %**, `cli.py` **98 %**,
> `tui/app.py` **90 %**).
>
> **Not done** (and not asked for here): there is no **Advanced** row in the
> app, so the four choices come from the command line only — that row is what
> **F13** (a config file) or a settings screen would make possible, and this
> item says to expose it only once F13 exists; there is no env-var spelling of
> these four (`VIDDL_YTDLP_ARGS` already passes any yt-dlp option, including
> these); the fragment count is clamped to 1–32, so a site that wants more is
> not served; and `--throttled-rate` only *can* help where a site offers more
> than one source, since it is the engine that decides to try another.

### F12 — Live streams · **P3** · M · ✅ **Done**

The README says live streams work but are recorded from the current point. A
banner saying so, plus `--live-from-start` as an explicit choice and a clear
"stop" that keeps what has been recorded so far, would make that honest and
useful rather than surprising.

> **Done as a banner, a button, a flag and a real file.** A live link now says
> so on the qualities screen — *Live stream. Recording starts now, not from the
> beginning. Stop when you have enough: what has been recorded is saved* — and
> the button beside it, **Record from the start** (`l`), switches to the other
> answer, with the sentence repeating which one is in force so the button never
> has a state nobody can see. `--live-from-start` is the same choice from the
> command line, in both front doors, and the flag is only ever handed to yt-dlp
> when the probe said the link is live.
>
> **Stopping is the ending, and it is now a real one.** `DownloadJob` gained
> `is_live`: when a live job is cancelled it calls a new `keep_recording()`,
> which waits for the process it was recording with to be gone, takes the
> ``.part`` yt-dlp was writing, drops the suffix and delivers it through the
> same claim-and-move path a finished download uses — so what arrived is an
> ordinary file in the Downloads folder, not a hidden half-file nobody can
> play. Nothing is invented: a run that recorded nothing delivers nothing, a
> folder holding only unmerged streams or yt-dlp's ``.ytdl`` note is left
> alone, and a file that will not lose its suffix is left where it is rather
> than handed over looking finished. The progress bar is not told it reached
> 100% (a stop is a stop), and the delivered recording still goes into the
> recent list.
>
> The app says **Stop recording** on the progress screen for a live link
> instead of **Cancel**, and the screen that follows is the ordinary result
> screen headed **Recording stopped**, with the file's path, size and *what had
> been recorded when you stopped*. If the recording could not be put into one
> file, the stopped screen says so and withholds **Resume**: a second run
> records from the live point, which is a new file, not a continuation.
> `--plain` prints one `Live:` line (what it is doing and that `Ctrl+C` keep
> what arrived), writes the delivered path on stdout when it is stopped and
> still exits **130**, so a script can tell a stop from a success without losing
> the file.
>
> **A live stream is one video, not a list.** `--live-from-start` with
> `--playlist` is refused with a sentence, because a queue has no way to ask
> each item whether it is live before it starts.
>
> **Measured against real yt-dlp 2026.10.21.** A 4 MB MP4 over local HTTP with
> `--plain --yes --live-from-start` printed *This is not a live stream, so
> --live-from-start has no effect.* and exited 0 with the 4,194,304-byte file;
> `yt-dlp --live-from-start --simulate` on the same URL exited 0, so the flag
> is a real option and not a typo. For the stop path, a server that trickles a
> response forever stood in for a stream that never ends: real yt-dlp wrote
> `live [live].mp4.part` at 64,512 bytes, the job was cancelled, and it
> delivered `live [live].mp4` at exactly 64,512 bytes with no `.viddl-*` folder
> left behind.
>
> **25 new tests** (927 → 952): 8 in `tests/test_download.py` (the flag and its
> absence, a stopped recording delivered byte for byte, a non-live stop still
> left to resume, no `done` progress for a recording, the folder fallback when
> yt-dlp never printed a name, an empty recording delivering nothing, fragments
> and cover pictures not being the recording, and the wait for the writer), 7
> in `tests/test_plain.py` (both sentences, the flag reaching the job, the
> no-effect notice, the path on stdout, the `Ctrl+C` path and `--quiet`), 7 in
> `tests/test_tui.py` (the banner and its absence, the button and `l`, the job
> getting both values, **Stop recording**, the **Recording stopped** screen,
> and the no-Resume fallback) and 3 in `tests/test_cli.py` (the flag reaching
> both front doors, and the refusal with `--playlist`). `pyflakes` clean;
> coverage **93 %** overall (`download.py` 93 %, `plain.py` 89 %,
> `tui/app.py` 90 %).
>
> **Not done** (and not asked for here): no live stream was recorded from a
> real site — this workspace has no way to reach one, so *which* sites honour
> `--live-from-start` is the engine's answer, not Viddl's; a live link inside a
> playlist or a batch is still taken from the current point, since the queue
> has no per-item live answer; there is no "keep recording" that appends to the
> same file (a second run is a new recording); and the delivered recording is
> the container yt-dlp was writing, so a player that refuses a fragmented MP4
> cannot be helped without a remux, which F12 did not ask for.

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

### U4 — A `--plain` mode · **P2** · M · ✅ **Done**

> **Landed with R10, which needed exactly this.** `viddl --plain` is the
> line-oriented mode: ASCII only, progress on one overwritten line, and the
> saved path alone on stdout for scripts. `--quality` picks a height, `best` or
> `audio`; `--yes` answers the "ask before downloading" question in advance;
> without a terminal Viddl refuses rather than acting unattended. The full
> evidence — real measurements, the cp1252 tests, and the download bug it
> turned up — is under **R10**.

The TUI needs a real terminal; `cli.main()` catches `OSError` and says so
politely. But Textual cannot render in some SSH sessions, in terminals without
the right capabilities, in CI, or with a screen reader.

**Do:** a line-oriented mode (`viddl --plain <link> --quality 720 --yes`) that
prints progress on one line and the final path on its own — usable from scripts,
`ssh`, and a11y tooling. This also removes the need for the `OSError` dead end.

### U5 — Keyboard affordances · **P2** · S · ◐ **partly done**

Bindings today: `Esc`, `Tab`, `Ctrl+Q`, `Ctrl+C`, arrows/`j`/`k`, `Enter`, `c`.
Cheap wins: `p` to paste the clipboard link, `r` to retry a failed download,
`o` to open the folder from the done screen, and `?` for a key help overlay.
(`r` for retry is now half-taken: `r` resumes from the **Stopped** screen.) The
older note here — that `Esc` cancels instantly and **R1** made that cost real
progress — no longer applies: **R1** is done, so `Esc` keeps the part and the
screen asks whether to resume it or throw it away.

> **◐ Two of the four are in.** `p` pastes the clipboard link and `o` opens the
> folder from the done screen; both arrived with the features that needed them
> (**F8**, **F6**), and `p` follows the same rule `r` does — a letter typed into
> the link box stays a letter, so the button is what a fresh launch uses.
> Missing: `?` for a help overlay, and `r` is still only "resume" on the
> **Stopped** screen, not a retry on the failure screen.

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

### E1 — There is no CI · **P1** · S · ◐ **partly done**

The matrix and the lint landed with **R10**: `.github/workflows/tests.yml` runs
Linux/macOS/Windows × Python 3.9–3.13, `pytest -q` and `pyflakes`, plus a
Windows job that installs with `install.ps1` and runs the launcher it wrote.
What is still missing is the coverage floor (`pytest --cov` with a fail-under)
and the weekly job that runs the extractor-dependent integration test (**E7**)
against the latest yt-dlp — the failure mode that actually breaks users. The
Node extension tests already run inside `pytest` when Node is present, and Node
is on the runners.

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
10. ✅ **F6** (archive + recents) — **done**; **F9** (notifications) is the other
    half of the same "make it a daily tool" idea, and now has a natural home
    to hang off.

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
