# How Viddl is put together

For whoever changes the code next. The README says what Viddl does; this says
where each part lives and which rule decides what, so a change lands in one
place instead of three.

## The shape

One terminal program with one runtime dependency (`textual`), and three ways in:

| Front end | File | What it is |
| --- | --- | --- |
| The full-screen app | `viddl/tui/` | Screens, widgets and one stylesheet |
| `--plain` / `--json` | `viddl/plain.py` | A line at a time, for scripts and old consoles |
| `--web` | `viddl/web_ui/` + `viddl/web.py` | One page served on loopback |

None of them owns the work. They all reach the same core: `download.py` (the
job, progress parsing, probing a link, listing a playlist), `formats.py` (the
quality rows and their size estimates), `engine.py` (finding, verifying and
installing yt-dlp and ffmpeg), `queue.py` (playlists taken one video at a time),
`errors.py` with `i18n/` (engine output turned into a sentence, in English or
Spanish), and `config.py` / `appdata.py` (preferences and paths). A front end is
a view and a set of decisions; if a rule about what a download *is* starts
growing inside one of them, it belongs in the core, where the other two will
need it too.

`cli.py` is the entry point: it parses the arguments, applies the precedence
rules below, and decides which front end runs. Nothing else parses `sys.argv`.

## The `Services` seam

`viddl/tui/app.py` defines one dataclass:

```python
@dataclass
class Services:
    engine: Engine
    probe: Callable[..., MediaInfo] = probe_video
    make_job: Callable[..., DownloadJob] = DownloadJob
    downloads_dir: Callable[[], Path] = appdata.downloads_dir
    browsers: Callable[[], List[str]] = cookies_mod.installed_keys
    fetch_playlist: Callable[..., Playlist] = probe_playlist
    update_engine: Callable[..., Tuple[Optional[Tool], Tool]] = _update_engine
    recent: Callable[[], List[history_mod.Entry]] = history_mod.entries
    clipboard: Callable[[], str] = clipboard_mod.read_text
```

Every field is a call the screens make into the outside world: the engine, the
network (through `probe` and `make_job`), the user's folders, their browsers,
their clipboard, their history. Production passes
`Services(engine=Engine())` and gets the real functions; a test passes its own
and gets a world it wrote.

The reason is not tidiness. A screen is a sequence of states — waiting, stale
engine, sign-in needed, retrying, failed — and the only way to see a state is to
drive the screen there. `tests/test_tui.py`, `tests/test_playlist.py`,
`tests/test_update.py` and `tests/test_theme.py` run the real app through
Textual's pilot against a `FakeEngine`, a probe that answers with a fixed
`MediaInfo`, and a job that blocks until the test releases it. No test opens a
socket, reads a real clipboard or depends on which browsers the machine has —
which is also the house rule in `CONTRIBUTING.md`.

So there are two rules here:

- **A screen does not call the world directly.** It calls `self.services.<field>`.
  If a screen needs something new from outside, add a field with a production
  default; the test that needs a different answer overrides that one field.
- **The default is the real thing.** A field without a default would make every
  test name every dependency, and a test that has to say `probe=None` learns
  nothing from it. `_update_engine` is a one-line wrapper around
  `Engine.update_ytdlp` for the same reason: it gives the field a default that
  can be swapped without the screens knowing.

`State` is the other half: one dataclass holding what the person has chosen —
the link, the quality, the extras, the queue, the network options. It lives on
the app rather than on a screen so that going back, opening the playlist or
resuming a stopped download keeps the same answers.

## Why it is local-only

Viddl has no server, no account and no telemetry, and that is a design decision
rather than an unfinished feature.

The work cannot be moved. yt-dlp and ffmpeg have to run somewhere with the
network, the disk and — when a site wants a sign-in — the cookies. A hosted
Viddl would have to hold your credentials and then hand you the file, which is a
different product with a different threat model.

So the two things that look like networking are not:

- **`--web`** serves one page from `127.0.0.1`, never `0.0.0.0`, so nothing on
  your network can reach it. Every request needs the random token printed when
  the page started; a wrong token and a missing one get the same 404. It sends
  no CORS header, refuses a foreign `Origin`, requires `application/json` for a
  POST, caps bodies at 64 KB, and loads nothing from anywhere else.
- **`viddl://`** is the browser extension handing over a link. Any page may open
  it, and the worst it achieves is a window with a link in it: a hand-off only
  ever fills the link box. Nothing is looked up, and nothing is downloaded, until
  a person presses Enter and then chooses a quality. A hand-off arriving right
  after another is dropped, so a page cannot open fifty windows.

`SECURITY.md` states that promise as a table of attacks and answers. The
practical consequence for a change: anything that would send something off the
machine — a crash report, an update check, a "share" button — is a product
decision, not an implementation detail, and needs to be argued in the pull
request rather than slipped in.

## What wins when two things disagree

There are two precedence ladders, and they are deliberately different.

### Settings

A preference can be set in four places. In order, highest first:

1. **The command line** — `--quality 720`, `--download-dir`, `--theme`,
   `--notify` / `--no-notify`.
2. **The environment** — `VIDDL_DOWNLOAD_DIR`, `VIDDL_QUALITY`,
   `VIDDL_THEME`, `VIDDL_ARCHIVE`, `VIDDL_NOTIFY`.
3. **The settings file** — `$XDG_CONFIG_HOME/viddl/config.toml`, or
   `~/.config/viddl/config.toml`; `%APPDATA%\viddl\config.toml` on Windows.
   Written only by `viddl --set`, removed by `--unset`.
4. **The built-in default** — chosen so that a machine with no file at all
   works: best quality, the usual Downloads folder, no notifications.

Two details matter when changing this code:

- **An empty variable is an answer, not silence.** `VIDDL_QUALITY=""` means
  "the default", and `config.quality()` returns `""` rather than falling through
  to the file. A variable that says one thing and then defers to a file is a
  second answer to a question somebody already answered.
- **`--doctor` is the report.** `config.report()` prints the file, what it holds,
  and which values were ignored and why; `appdata.environment_report()` prints
  the variables that are set. A setting that silently does not apply is the bug
  this order exists to prevent, so the way to see the order is a command rather
  than the documentation.

### The engine

Which yt-dlp runs is not a preference, it is a choice between installations:

1. **`VIDDL_YTDLP` wins outright.** Pointing at a copy is an instruction, not a
   default; it is probed once and used even when it is older.
2. **Otherwise the newest copy that runs wins.** Every candidate is asked
   `--version`: Viddl's own download, a `yt_dlp` installed as a Python package,
   and whatever `yt-dlp` is on `PATH`. The highest version key is used, and a
   tie goes to the copy Viddl manages, because that is the one it can update.

The order used to be fixed with Viddl's own copy first, and that broke the
README's promise — a copy fetched once kept shadowing a newer one the user
installed later. Hence the version race.

ffmpeg does **not** work that way. `find_ffmpeg()` takes the first candidate
that runs: `VIDDL_FFMPEG` if set, then Viddl's own copy, then whatever is on
`PATH`. The difference is intentional. yt-dlp's version decides whether a link
works at all, so newest is worth probing for; an ffmpeg that can encode a file is
an ffmpeg, and preferring the copy Viddl can replace keeps what
`--update-ffmpeg` changes predictable. Both choices are visible in
`viddl --doctor` and `viddl --version --verbose`, which name the path and where
it came from (`found on your computer`, `downloaded by Viddl`,
`installed as a Python package`, `the copy you pointed Viddl at`).

## Adding a flag, end to end

The path a change takes, using a remembered setting as the example:

1. `viddl/cli.py` — `build_parser()` gets the argument; `main()` reads it.
2. `viddl/config.py` — if it can be remembered, add it to `SETTINGS` and read it
   with an environment variable ahead of the file.
3. `viddl/plain.py` and `viddl/tui/` — the two front ends use it. In the app the
   value belongs in `State`, and any new outside call in `Services`.
4. `tests/` — one test that fails without the change. The screens are tested
   through `Services`, so a new dependency is a new field rather than a mock.
5. The documents that are checked: the README paragraph a person would look in,
   a `CHANGELOG.md` line under *Unreleased*, and `man/viddl.1` — which
   `tests/test_manpage.py` compares with `build_parser()` in both directions, so
   a flag added here and forgotten there fails the suite rather than the user.

## What is deliberately not here

- **No plugin system.** The extension point is `Services`, and it exists for
  tests.
- **No second runtime dependency.** If the standard library can do it, it does.
- **No settings screen.** `--set` and a file that is only written when asked.
- **No server, queue daemon or background process.** Closing the terminal stops
  what is downloading; that is a feature.
- **No telemetry, no update check, no crash upload.** See `SECURITY.md`.
