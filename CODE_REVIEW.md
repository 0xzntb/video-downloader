# Code Review — Viddl

### Summary

Viddl is a terminal application (plus `--plain`, `--web` and a `viddl://` handler) that turns a pasted video link or search phrase into a file by driving the `yt-dlp` and `ffmpeg` binaries. Roughly 16.7k lines of Python across 30 package modules, one runtime dependency (`textual>=3.0,<9`), `requires-python = ">=3.9"`, no framework beyond the standard library on the server side (`http.server`, `urllib`, `subprocess`). Architecture is a clean layered split: `appdata`/`config` (paths and preferences), `engine` (tool discovery, verified downloads), `formats`/`errors`/`download`/`queue` (parsing and running work), `protocol`/`shortcut`/`web`/`cli`/`plain`, and `tui` (Textual screens).

First impression: this is unusually disciplined code. Boundaries that usually leak (untrusted metadata, argv construction, file permissions, log redaction) are each handled by a named module with a docstring that explains the threat, and the test suite has a coverage floor, property-based parser tests and a weekly mutation run. The weaknesses that remain are concentrated in the newest OS-integration surface — the `viddl://` handlers and the Windows shortcut — where OS-supplied strings are still turned into script or batch syntax, plus a handful of robustness gaps that the rest of the codebase already knows how to avoid. No Critical, High or Medium findings remain (kept below, marked fixed); what is left is the Low robustness and code-quality gaps listed at the end.

---

### Issues Found

**🔴 Critical — none open.** The one found here is fixed; it stays in place, with what changed, because the reasoning still describes the boundary.

**✅ Fixed since the review**

#### ✅ Fixed (was 🔴 Critical) — A `viddl://` URI can no longer inject shell commands on macOS
- **Location**: `viddl/protocol.py:53-63` (runner template), used at `viddl/protocol.py:365-368`
- **Status**: Fixed — `_MACOS_RUNNER` filters the URI to characters a URL may hold and writes the scratch script with `mktemp` and `chmod 700`. `tests/test_protocol.py::test_a_hostile_uri_cannot_add_commands_to_the_macos_runner` runs the real handler with a newline payload and asserts only the sanitized URL reaches the launch line (it runs wherever `sh` can execute the generated `.command`).
- **Problem**: The handler is a generated `/bin/sh` script. It takes the OS-supplied URI, removes only the single quote (`tr -d "'"`), and interpolates it into a script body that Terminal then executes:
  ```sh
  URL=$(printf '%s' "$URL" | tr -d "'")
  LINE="__LAUNCH__ --uri '$URL'"
  printf '#!/bin/sh\n%s\nrm -f "$0"\n' "$LINE" > "$RUNNER"
  ```
  The quote-wrapping makes `$(...)` and backticks harmless, but **a line break survives**: `viddl://x` followed by a newline and a command produces a second line in the generated `.command` file, which `/bin/sh` executes with the user's privileges. The same code also writes to a predictable path, `${TMPDIR:-/tmp}/viddl-run-$$.command`, with a plain `>` redirect, so a pre-created symlink at that name is followed and its target truncated. The file is created with the umask, not `0600`.
- **Fix**: Restrict the URI to characters a URL can actually contain, and create the scratch file privately and unpredictably.
  ```sh
  URL="$1"
  [ -z "$URL" ] && exit 0
  # Only characters a URL may hold. Anything else — a quote, a newline, a
  # control byte — would become shell syntax in the script written below.
  URL=$(printf '%s' "$URL" | tr -cd 'A-Za-z0-9._~:/?#@!$&()*+,;=%-')
  [ -n "$URL" ] || exit 0
  RUNNER=$(mktemp "${TMPDIR:-/tmp}/viddl-run-XXXXXX.command") || exit 0
  chmod 700 "$RUNNER"
  LINE="__LAUNCH__ --uri '$URL'"
  printf '#!/bin/sh\n%s\nrm -f "$0"\n' "$LINE" > "$RUNNER"
  exec /usr/bin/open -a Terminal "$RUNNER"
  ```

#### ✅ Fixed (was 🟠 High) — The Windows handler no longer goes through cmd.exe
- **Location**: `viddl/protocol.py:397-437`, batch body at `viddl/protocol.py:414-416`, registry value written just after
- **Status**: Fixed — `_register_windows` registers an executable and appends `--uri "%1"`; a `.cmd`/`.bat`/`.py` launcher is replaced with `sys.executable -m viddl`, and the old `viddl-open.cmd` is deleted on register and unregister. `tests/test_protocol.py` covers both launcher shapes.
- **Problem**: The registry entry points at `viddl-open.cmd`, and the batch file is `start "" <launcher> --uri %1`. `%1` is expanded by `cmd.exe`, and the URI is attacker-influenced (any page may navigate to `viddl://…`). A URI containing a double quote ends the quoted argument and lets `&`, `|` or `>` run as batch syntax — classic unquoted-`%1` batch injection. `safe_video_link` cannot help: it runs in Python, after cmd.exe has already parsed the line.
- **Fix**: Register an executable, not a batch file, so no shell sits between the OS and the program. The URI then reaches `sys.argv` as one argument and is validated by `parse_scheme`/`safe_video_link` as designed.
  ```python
  # No .cmd, no cmd.exe, no batch quoting: ShellExecute starts the program
  # directly, and Windows performs the %1 substitution before argv exists.
  command = launch_command()          # ["…\\viddl.exe"] or [python, "-m", "viddl"]
  # A .cmd launcher (the installer's) would be run by cmd.exe again.
  if Path(command[0]).suffix.lower() in (".cmd", ".bat", ".py"):
      command = [sys.executable, "-m", "viddl"]
  handler = subprocess.list2cmdline([*command, "--uri"]) + ' "%1"'
  winreg.SetValueEx(command_key, "", 0, winreg.REG_SZ, handler)
  ```

#### ✅ Fixed (was 🟠 High) — `which()` no longer searches the working directory
- **Location**: `viddl/appdata.py:205-224` (the promise), `viddl/appdata.py:660-664` (the implementation), consumed by `Engine.find_ytdlp`
- **Status**: Fixed — `which()` walks a `PATH` with empty and working-directory entries removed and completes names with `PATHEXT` on Windows itself, because `shutil.which` prepends the working directory even when given a path. `Engine.find_ytdlp` and `program_override` both route through it unchanged.
- **Problem**: `program_override` documents and partially enforces "a plain name means the `PATH` and never the working directory", but the actual lookup is `shutil.which(program)` — and on Windows `shutil.which` prepends the current directory when it is not on `PATH`. So `viddl` started in a folder containing `yt-dlp.exe` (a Downloads folder full of downloaded junk is the obvious case) runs that binary. The same helper is used for the automatic `which("yt-dlp")` fallback, so this is not limited to the environment override.
- **Fix**: Walk the `PATH` yourself, skipping empty and working-directory entries, and complete names with `PATHEXT` on Windows — `shutil.which` prepends the working directory even when handed a path.
  ```python
  def _search_dirs() -> List[Path]:
      cwd = Path.cwd().resolve()
      return [
          Path(entry)
          for entry in (os.environ.get("PATH") or os.defpath).split(os.pathsep)
          if entry.strip() and Path(entry).resolve() != cwd
      ]

  def which(program: str) -> Optional[Path]:
      for folder in [*_search_dirs(), *_EXTRA_BIN]:
          for name in _executable_names(program):
              candidate = folder / name
              if candidate.is_file() and os.access(candidate, os.X_OK):
                  return candidate
      return None
  ```

#### ✅ Fixed (was 🟠 High) — A notification no longer blocks the UI thread
- **Location**: `viddl/notify.py:185-212` (`send`), called from `viddl/tui/app.py:3086-3108` (`_tell`)
- **Status**: Fixed — `_tell` hands the notification to an app-owned thread worker with `exit_on_error=False`; `finish_ok`/`finish_error` return as soon as the result screen is set. `tests/test_tui.py` covers that the toast is still raised on the worker and that a notifier blocked inside `tell` does not delay the result screen.
- **Problem**: `send` runs `subprocess.run(argv, timeout=5)` synchronously. `_tell` is called from `finish_ok`, which is dispatched onto the Textual event loop, so a cold PowerShell toast (or an `osascript`/`notify-send` that stalls) freezes the screen — on the exact frame where the user expects "Saved" to appear. On Windows the PowerShell start-up cost alone is commonly hundreds of milliseconds.
- **Fix**: Treat the notification as the background courtesy it is, and give the worker to the app rather than the screen so a screen replaced at the end of the download does not cancel the pending toast.
  ```python
  self.app.run_worker(
      lambda: notify_mod.tell(
          saved=saved, failed=failed, stopped=stopped, count=count, elapsed=elapsed,
      ),
      name="notify",
      group="notify",
      thread=True,
      exit_on_error=False,   # a failed courtesy must not end the app
  )
  ```

**🟠 High — none open.** The one found here is fixed and kept above with what changed.

#### ✅ Fixed (was 🟡 Bug) — A non-finite progress field no longer ends a download
- **Location**: `viddl/download.py:199-205`, called at `viddl/download.py:1632` and `1636-1637`
- **Status**: Fixed — `_to_float` rejects anything `math.isfinite` refuses before the value can reach `int()`, and `_to_int` now derives from it, so one guard covers percent, speed, ETA and both byte counts. `tests/test_download.py` sends a progress line made entirely of `inf` through the real parser.
- **Problem**: `float("inf")` passes the `>= 0` check and `int(inf)` raises `OverflowError`. That exception escapes `_handle_progress_line`, aborts the read loop in `_run_once`, and ends a download that was otherwise fine. `formats._num` (`formats.py:258-271`) already documents and fixes exactly this class of bug; the progress parser never got the same treatment, and `_to_float` (line 191-196) has the same gap for `speed`/`eta`/`percent`.
- **Fix**:
  ```python
  def _to_float(value: str) -> Optional[float]:
      try:
          number = float(value)
      except (TypeError, ValueError):
          return None
      if not math.isfinite(number) or number < 0:
          return None
      return number

  def _to_int(value: str) -> Optional[int]:
      number = _to_float(value)
      return int(number) if number is not None else None
  ```

**🟡 Medium**

#### ✅ Fixed (was 🟡 Bug) — A crash no longer leaves a folder claimed forever
- **Location**: `viddl/download.py:747-781` (`_enter_work_dir`)
- **Status**: Fixed — the claim now records the process and birth time, and `_enter_work_dir` takes over a claim whose process is gone (with `_CLAIM_TTL` as the fallback when a PID may have been reused or an older version wrote an empty claim). `tests/test_download.py` covers a dead owner, a live in-process claim, a fresh legacy claim and an expired one.
- **Problem**: The claim is an empty `.viddl-busy` file created with `O_EXCL`, and nothing ever expires it. After a crash, a `SIGKILL`, or a power loss, the folder stays claimed forever: the `.part` inside can never be resumed, and every later attempt for the same link/quality takes the next name along (`-2`, `-3`, …), leaving hidden folders in the user's Downloads until the loop's 99 (or the 20-copies message) limit turns this into a permanent failure.
- **Fix**: Write the owner and birth time into the claim, and treat a claim whose process is gone (or that is older than a generous TTL) as free.
  ```python
  os.write(handle, f"{os.getpid()} {time.time():.0f}\n".encode("ascii"))

  # In the FileExistsError branch, before trying the next name:
  if _claim_is_stale(claim):
      contextlib.suppress(OSError, claim.unlink)
      handle = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
      # somebody else may have won that race: then this name is still not ours
  ```
  ``_process_alive`` uses ``os.kill(pid, 0)`` on POSIX and ``OpenProcess`` on Windows, where signal 0 is a termination request rather than a probe.

#### ✅ Fixed (was 🟡 Bug) — The web page's job table is bounded
- **Location**: `viddl/web.py:144`, insertion at `viddl/web.py:236-241`
- **Status**: Fixed — `start` evicts the oldest finished record before inserting and refuses once `MAX_JOBS` (50) downloads are running, so a page left open holds a recent window rather than its whole history. `tests/test_web.py` covers both the window and the refusal.
- **Problem**: `self._jobs` keeps every `_Job` — with its `Progress`, paths and error object — for the lifetime of the server, and each `start` also spawns an unthrottled thread. A `--web` session left open (the intended usage) grows without bound while the page polls `/api/jobs/<id>`.
- **Fix**: Keep a recent window, and refuse a start when there is nothing finished left to drop.
  ```python
  MAX_JOBS = 50

  with self._lock:
      self._forget_finished()
      if len(self._jobs) >= MAX_JOBS:
          raise Refused(
              f"Too many downloads are already open ({MAX_JOBS}).",
              "Wait for one to finish, or stop one you no longer need.",
          )
      self._jobs[record.id] = record
  ```

#### ✅ Fixed (was 🟡 Bug) — The Downloads fallback keeps its promise
- **Location**: `viddl/appdata.py:655-664`
- **Status**: Fixed — the fallback is created inside `contextlib.suppress(OSError)`, so a read-only or full home directory still gets a path back; `tests/test_appdata.py` blocks both folders and asserts the getter answers instead of raising.
- **Problem**: When the Downloads folder is unusable the code falls back to `data_dir()/Downloads` and calls `_secure_dir(fallback)` inside the `except OSError` block. `_secure_dir` re-raises if `mkdir` fails, so on a read-only or full home directory `downloads_dir()` raises instead of returning the fallback it promises. Callers such as `viddl/web.py:220` and the TUI do not guard it, so the whole screen fails with a traceback rather than a sentence.
- **Fix**:
  ```python
  except OSError:
      fallback = data_dir() / "Downloads"
      with contextlib.suppress(OSError):
          _secure_dir(fallback)
      return fallback
  ```

#### ✅ Fixed (was 🟡 Bug) — Two Viddl windows no longer clobber the recent list or the settings file
- **Location**: `viddl/history.py:58` and `viddl/history.py:112-145`; the same pattern at `viddl/config.py:394-420`
- **Status**: Fixed — temp files carry the pid (`recent.json.<pid>.tmp`, `config.toml.<pid>.tmp`) and the whole read-modify-write runs under the operating system's own lock, taken on a sibling `<file>.lock`. `tests/test_history.py` and `tests/test_config.py` spawn two windows that each write eight/ twenty times and assert no row or setting is lost.
- **Problem**: Writes go through a fixed neighbour name (`recent.json.tmp`, `config.toml.tmp`) and are serialised only by a module-level `threading.Lock`. Two processes — a scenario the project explicitly supports and designs for (`R6`, concurrent downloads) — each open the same temporary file, truncate it, write, and `os.replace` it. The result is either lost rows or a mixed file; the lock does nothing across processes.
- **Fix**: Per-process temporary names plus an OS-level lock held across the whole read-modify-write.
  ```python
  from . import filelock

  temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
  ...
  with filelock.held(path):
      kept = [old for old in _read() if old.path != entry.path]
      kept.insert(0, entry)
      if not _write_file(path, _as_json(kept)):
          return None
  ```
  `filelock.held` uses `fcntl.flock` on POSIX and `msvcrt.locking` on Windows, on a sibling `<file>.lock` so that `os.replace` cannot swap the locked inode out from under it.

**🟡 Medium — none open.** Every issue found here is fixed and kept above with what changed.

#### ✅ Fixed (was 🟡 Bug) — A crash between claiming and moving no longer leaves an empty "finished" file
- **Location**: `viddl/download.py:1408-1445` (`_deliver`), `viddl/download.py:1479-1525` (`_claim_name`), `viddl/download.py:1527-1537` (`_release_name`), helpers at `viddl/download.py:320-343`
- **Status**: Fixed — the destination name is still claimed with `O_EXCL`, but what is created is a hidden reservation *beside* the name (`.viddl-name-<hash of the name>`, mode `0600`, holding the pid and birth time) rather than an empty file under it. `os.replace` is now the only thing that ever creates the visible name, and the reservation is dropped once the move has either filled the name or failed. A reservation whose owner is gone, or that has outlived `_CLAIM_TTL`, is reaped instead of respected, so a crashed run cannot block the name for the runs after it either. `tests/test_download.py::test_a_run_killed_between_claiming_and_moving_leaves_no_empty_video` claims a name, abandons it the way a killed run would, and asserts the next run takes that same name back and delivers a whole file; `test_a_claimed_name_is_never_handed_out_twice` still proves four claims get four names, and now also proves nothing is created under any of them.
- **Problem**: The destination name was claimed by creating a zero-byte file, then `os.replace` filled it. The `except OSError` path cleaned up, but a process kill in between did not: the user was left with `Title.mp4` at 0 bytes in Downloads, which looks like a successful download of a broken video. Nothing reaped it either, because the claim was already gone and the name was taken forever.
- **Fix**: Claim the name with a hidden reservation, and let the move be the only thing that creates the file.
  ```python
  target = self.out_dir / name
  reservation = _name_claim(self.out_dir, name)   # .viddl-name-<sha1(name)[:12]>
  if target.exists():
      _reap_name_claim(reservation)               # litter left by the run that delivered
      continue
  try:
      handle = os.open(reservation, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
  except FileExistsError:
      if not _reap_name_claim(reservation):       # a live run is delivering this name
          continue
      handle = os.open(reservation, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
  ```

**🟢 Low**

#### ✅ Fixed (was 🟢 Bug) — A hand-edited `when` of `inf` no longer reaches the recent list
- **Location**: `viddl/history.py:278-289` (`_as_float`)
- **Status**: Fixed — `_as_float` now requires `math.isfinite` as well as `> 0`, so `1e400` reads as "no date" (0, which the row already renders as nothing) instead of a number that is not a time. `tests/test_history.py::test_a_hand_edited_when_that_is_enormous_is_no_date_rather_than_a_lie` writes a real `1e400` row into the list and asserts the row still renders — with no date and no invented "just now" — instead of raising.
- **Problem**: `float("inf") > 0` is true, so a hand-edited `"when": 1e400` survived parsing. The row then rendered as "just now" (`time.time() - inf` clamps to 0) or, on a platform that reached the long-age branch, made `time.localtime(inf)` raise — the module's own docstring says hand editing is expected.
- **Fix**:
  ```python
  return number if math.isfinite(number) and number > 0 else 0.0
  ```

#### ✅ Fixed (was 🟢 Bug) — No `assert` is left on a production path
- **Location**: `viddl/download.py:1296-1308` (`_run_once`), `viddl/tui/app.py:3370-3379` (`ResultScreen.on_mount`)
- **Status**: Fixed — the download path raises `ToolError("The video engine could not be read.")` after stopping the child it can no longer read, and the result screen says on screen that the download finished but no file was recorded, instead of indexing `None`. `tests/test_tui.py::test_a_result_screen_with_no_path_says_so_instead_of_crashing` puts the screen into that state on purpose and asserts the sentence appears; the download branch is `# pragma: no cover` because `stdout=PIPE` is what makes it unreachable, and the invariant is now an answer rather than a comment.
- **Problem**: `python -O` / `PYTHONOPTIMIZE=1` removed both checks; `self._process.stdout` and `path` then flowed on as `None` and failed further away with a less useful error (a `TypeError` inside the read loop, an `AttributeError` on `path.stat()`).
- **Fix**:
  ```python
  stream = self._process.stdout
  if stream is None:  # pragma: no cover - ``stdout=PIPE`` above always gives one
      self._signal_tree(self._process, force=True)
      self._stop_watchdog()
      raise ToolError("The video engine could not be read.", "Try running Viddl again.")
  ```
  and, in the screen, an early `return` after writing the sentence — the actions beside it already answer `saved_path is None` the same way (`Show in folder`, `Play` and `Copy` each return).

#### 🟢 Security — One hand-rolled command-line quoter left
- **Location**: `viddl/shortcut.py:93-94`
- **Problem**: `_command_line` builds a Windows command line by hand (`f'"{part}"' if " " in part else part`) and does not escape cmd metacharacters (`&`, `^`, `%`). Everything that needs a command line for the `viddl://` handler now delegates to `subprocess.list2cmdline`; one home for the rule keeps the next launcher from inventing a third spelling.
- **Fix**: Route `shortcut._command_line` through `subprocess.list2cmdline` as the handler now does, so the next launcher cannot invent a fourth spelling of the same rule.

#### 🟢 Code quality — `DownloadJob` owns too many responsibilities
- **Location**: `viddl/download.py:600-1708`
- **Problem**: One class builds the argv, parses progress, claims folders, retries, downloads, merges, delivers, writes history and handles live recordings — 1,100 lines and a broad state machine. It is well documented, but the retry policy, the delivery/claim protocol and the progress parser are independently testable and currently only reachable through a real subprocess.
- **Fix**: Extract `ProgressParser`, `OutputClaimer` and `RetryPolicy` as small collaborators; `DownloadJob` then composes them. This also makes the stale-claim and 0-byte-file fixes above unit-testable without a fake engine.

#### 🟢 Tests — The Windows handler has no adversarial test
- **Location**: `tests/test_protocol.py` (runner escaping), `tests/test_shortcut.py`
- **Problem**: The macOS runner now has one: `test_a_hostile_uri_cannot_add_commands_to_the_macos_runner` runs the real handler with a newline payload and asserts that only the filtered URL reaches the launch line. The batch handler is still only checked for being written — nothing feeds it a URI with a quote or `&` and asserts that cmd.exe cannot run anything else.
- **Fix**: Add a `@given(st.text())` test that builds the Windows handler string and asserts the URI survives only inside the data position (e.g. no raw newline, no unescaped quote in the emitted command), plus a test that `build_scheme_url` round-trips a hostile URL into something `parse_scheme` refuses without side effects.

---

### What's Done Well

1. **The argv boundary is real, not aspirational.** `safe_video_link` refuses option-shaped strings and non-http schemes, every yt-dlp invocation ends with `["--", url]` (`download.py:895-896`), `--ignore-config` is always passed, and `appdata._arguments` refuses `VIDDL_YTDLP_ARGS` values that would set `-P`/`-o`/`-f`. That is four independent layers where most projects have one.
2. **Secrets are handled as data, not as a formatting afterthought.** `util.redact` plus `logfile.RedactingFormatter` censor query credentials and bearer tokens at write time, `NetworkOptions.summary` discloses the proxy host but never the userinfo, and `web._Handler.log_request` rewrites the token out of the URL before logging.
3. **File permissions are enforced at creation, not hoped for.** `OwnerOnlyRotatingFileHandler._open` uses `os.open(..., 0o600)` so rotation cannot loosen the log, `_secure_dir` re-tightens folders made by older versions, and `history._write_file` sets the mode on the temporary file that becomes the real one.
4. **The failure vocabulary is data-driven and testable.** `errors.py` keeps 21 ordered rules separate from the sentences in `i18n`, with `temporary` beside each message so "try again" and the retry policy cannot drift apart; the same table serves the CLI, the app and the web page.
5. **Verification is aimed at the right things.** Helpers are checksum-verified against published sums before being made executable, the recorded hash is re-checked by `--doctor`, `_extract_member` writes to a fixed destination (no archive path traversal), and the suite has a 94% floor, hypothesis-based parser properties and a weekly mutation job.

### Overall Score: 9/10

This is comfortably production-grade for a desktop tool: the failure modes are explained to the user, the dangerous boundaries are documented and enforced, and the test discipline is above what most applications of this size carry. With every Critical, High and Medium closed, the remaining drag is a short list of Low findings — one hand-rolled command-line quoter and one class holding too much — each one the codebase's own lesson not being applied consistently across modules. None of the issues require redesign; the fixes so far were local, and the rest are the same kind of hardening.

### Top 3 Priority Fixes

1. **Finish consolidating the command-line rule.** `shortcut._command_line` (`shortcut.py:93-94`) still builds a Windows command line by hand while everything that needs one for the `viddl://` handler delegates to `subprocess.list2cmdline`; one home for the rule keeps the next launcher from inventing a third spelling.

2. **Add the Windows handler's adversarial test.** The macOS runner now has one; the batch handler is still only checked for being written, with nothing feeding it a quote or a `&` and asserting that cmd.exe cannot run anything else.

3. **Split `DownloadJob` into its collaborators.** The retry policy, the delivery/claim protocol and the progress parser are independently testable and currently only reachable through a real subprocess (`download.py:600-1720`); extracting them is what makes the stale-claim and 0-byte-file fixes unit-testable without a fake engine.
