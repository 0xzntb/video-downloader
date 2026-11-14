# Code Review — Viddl

### Summary

Viddl is a terminal application (plus `--plain`, `--web` and a `viddl://` handler) that turns a pasted video link or search phrase into a file by driving the `yt-dlp` and `ffmpeg` binaries. Roughly 16.7k lines of Python across 30 package modules, one runtime dependency (`textual>=3.0,<9`), `requires-python = ">=3.9"`, no framework beyond the standard library on the server side (`http.server`, `urllib`, `subprocess`). Architecture is a clean layered split: `appdata`/`config` (paths and preferences), `engine` (tool discovery, verified downloads), `formats`/`errors`/`download`/`queue` (parsing and running work), `protocol`/`shortcut`/`web`/`cli`/`plain`, and `tui` (Textual screens).

First impression: this is unusually disciplined code. Boundaries that usually leak (untrusted metadata, argv construction, file permissions, log redaction) are each handled by a named module with a docstring that explains the threat, and the test suite has a coverage floor, property-based parser tests and a weekly mutation run. The weaknesses that remain are concentrated in the newest OS-integration surface — the `viddl://` handlers and the Windows shortcut — where OS-supplied strings are still turned into script or batch syntax, plus a handful of robustness gaps that the rest of the codebase already knows how to avoid. No Critical or High findings remain (kept below, marked fixed); what is left is the Medium lifecycle and robustness gaps the rest of the codebase already knows how to avoid.

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

**🟡 Medium**

#### 🟡 Bug — `_to_int` raises `OverflowError` on a non-finite progress field
- **Location**: `viddl/download.py:199-205`, called at `viddl/download.py:1632` and `1636-1637`
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

#### 🟡 Bug — A stale claim file permanently blocks resuming that download
- **Location**: `viddl/download.py:747-781` (`_enter_work_dir`)
- **Problem**: The claim is an empty `.viddl-busy` file created with `O_EXCL`, and nothing ever expires it. After a crash, a `SIGKILL`, or a power loss, the folder stays claimed forever: the `.part` inside can never be resumed, and every later attempt for the same link/quality takes the next name along (`-2`, `-3`, …), leaving hidden folders in the user's Downloads until the loop's 99 (or the 20-copies message) limit turns this into a permanent failure.
- **Fix**: Write the owner and birth time into the claim, and treat a claim whose process is gone (or that is older than a generous TTL) as free.
  ```python
  os.write(handle, f"{os.getpid()} {time.time():.0f}\n".encode("ascii"))

  def _claim_is_stale(claim: Path, ttl: float = 12 * 3600) -> bool:
      try:
          pid_text, born_text = claim.read_text(encoding="ascii").split()
          pid, born = int(pid_text), float(born_text)
          return time.time() - born > ttl or not _process_alive(pid)
      except (OSError, ValueError):
          return False
  # in the collision branch: if claim.exists() and _claim_is_stale(claim):
      #     contextlib.suppress(OSError, claim.unlink)
      #     continue with this name
  ```

#### 🟡 Bug — The web page's job table only ever grows
- **Location**: `viddl/web.py:144`, insertion at `viddl/web.py:236-241`
- **Problem**: `self._jobs` keeps every `_Job` — with its `Progress`, paths and error object — for the lifetime of the server, and each `start` also spawns an unthrottled thread. A `--web` session left open (the intended usage) grows without bound while the page polls `/api/jobs/<id>`.
- **Fix**: Keep the recent window and refuse work beyond a small cap.
  ```python
  MAX_JOBS = 50
  ...
  with self._lock:
      self._jobs[record.id] = record
      while len(self._jobs) > MAX_JOBS:
          stale = next((key for key, item in self._jobs.items()
                        if item.state != "running"), None)
          if stale is None:
              break
          del self._jobs[stale]
  ```

#### 🟡 Bug — The Downloads fallback can itself raise
- **Location**: `viddl/appdata.py:645-657`
- **Problem**: When the Downloads folder is unusable the code falls back to `data_dir()/Downloads` and calls `_secure_dir(fallback)` inside the `except OSError` block. `_secure_dir` re-raises if `mkdir` fails, so on a read-only or full home directory `downloads_dir()` raises instead of returning the fallback it promises. Callers such as `viddl/web.py:220` and the TUI do not guard it, so the whole screen fails with a traceback rather than a sentence.
- **Fix**:
  ```python
  except OSError:
      fallback = data_dir() / "Downloads"
      # The fallback is a best effort: if it cannot be created either, the
      # download job will say so with its own message rather than a traceback
      # escaping a function documented as returning a path.
      with contextlib.suppress(OSError):
          _secure_dir(fallback)
      return fallback
  ```

#### 🟡 Bug — Two Viddl windows can clobber the recent list and the settings file
- **Location**: `viddl/history.py:58` and `viddl/history.py:112-145`; the same pattern at `viddl/config.py:394-420`
- **Problem**: Writes go through a fixed neighbour name (`recent.json.tmp`, `config.toml.tmp`) and are serialised only by a module-level `threading.Lock`. Two processes — a scenario the project explicitly supports and designs for (`R6`, concurrent downloads) — each open the same temporary file, truncate it, write, and `os.replace` it. The result is either lost rows or a mixed file; the lock does nothing across processes.
- **Fix**: Give each writer its own temporary file, and serialise cross-process when the data matters.
  ```python
  temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
  ...
  os.replace(temp, path)
  # For true serialisation across windows, wrap the read-modify-write in an
  # OS file lock (fcntl.flock on POSIX, msvcrt.locking on Windows).
  ```

#### 🟡 Bug — A crash between claiming and moving leaves an empty "finished" file
- **Location**: `viddl/download.py:1276-1302` (`_deliver`)
- **Problem**: The destination name is claimed by creating a zero-byte file, then `os.replace` fills it. The `except OSError` path cleans up, but a process kill in between does not: the user is left with `Title.mp4` at 0 bytes in Downloads, which looks like a successful download of a broken video.
- **Fix**: Record claimed names in the job's working folder before the move, and reap zero-byte orphans for that job on the next run — or claim under a dot-prefixed name and rename onto the final name only after the move succeeds.

**🟢 Low**

#### 🟢 Bug — `_as_float` lets `inf` into the recent list
- **Location**: `viddl/history.py:263-270`
- **Problem**: `float("inf") > 0` is true, so a hand-edited `"when": 1e400` survives parsing. `datetime.fromtimestamp(inf)` raises `OverflowError`/`OSError` wherever the row is rendered — the module's own docstring says hand editing is expected.
- **Fix**: `return number if math.isfinite(number) and number > 0 else 0.0`

#### 🟢 Bug — `assert` used on production paths
- **Location**: `viddl/download.py:1164`, `viddl/tui/app.py:3355`
- **Problem**: `python -O` / `PYTHONOPTIMIZE=1` removes both checks; `self._process.stdout` and `path` then flow on as `None` and fail further away with a less useful error.
- **Fix**: Replace with an explicit `if … is None: raise ToolError(...)` (and the same for the screen's copy) or a `cast` plus a test-visible invariant.

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

This is comfortably production-grade for a desktop tool: the failure modes are explained to the user, the dangerous boundaries are documented and enforced, and the test discipline is above what most applications of this size carry. With every Critical and High closed, the remaining drag is the Medium lifecycle gaps — stale claims, the unbounded web job table, cross-process writes — and a few of the codebase's own lessons (finite-number guards, delivery windows, option-shaped values) not being applied consistently across modules. None of the issues require redesign; the fixes so far were local, and the rest are the same kind of hardening.

### Top 3 Priority Fixes

1. **Fix the lifecycle gaps that turn a crash into permanent breakage.** Reap stale `.viddl-busy` claims with a PID/TTL check (`download.py:747-781`), bound the web job table, and give `history`/`config` per-process temporary names so two windows cannot erase each other's rows.

2. **Guard the numbers that come from elsewhere.** `_to_int`/`_to_float` lack the `isfinite` check their sibling in `formats._num` has (`download.py:191-205`), and `history._as_float` lets `inf` into the recent list — a malformed progress line or a hand-edited file should not end a download or a screen.

3. **Make the Downloads fallback keep its promise.** `downloads_dir()` calls `_secure_dir` inside its own `except OSError`, so a read-only home directory raises instead of returning the path it documents (`appdata.py:645-657`) — wrap the fallback creation and let the job report the problem in its own sentence.
