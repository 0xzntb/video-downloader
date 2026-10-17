# Viddl

Download a video (or just the audio) by pasting a link into a terminal app that
behaves like a real little program: a clean screen, a list of qualities with
estimated sizes, a progress bar, and the full path of the file when it is done.

Works with YouTube, X, Instagram, Threads, TikTok, Vimeo and the hundreds of
other sites [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) supports. Mouse and
keyboard both work, everywhere.

There is no server, no account, nothing to configure, and no data leaves your
machine except the download itself.

```
╭────────────────────────────────────────────────────────────────────────────────────╮
│                                       Viddl                                        │
│                   Download a video or a song by pasting a link.                    │
│                                                                                    │
│  Paste a video link                                                                │
│  ▊  https://www.youtube.com/watch?v=…                                             ▎
│                                                                                    │
│                          Find qualities        Quit                                │
│                                                                                    │
│  Ready to download · engine 2026.08.19 · ffmpeg ready                              │
╰────────────────────────────────────────────────────────────────────────────────────╯

╭────────────────────────────────────────────────────────────────────────────────────╮
│  Me at the zoo                                                                     │
│  0:19  ·  jawed  ·  Youtube                                                        │
│                                                                                    │
│   240p                              725.0 KB   MP4 video + audio · needs ffmpeg    │
│   144p                              492.7 KB   MP4 video + audio · needs ffmpeg    │
│   Audio only · MP3 320 kbps        ~301.0 KB   MP3 · source is 130 kbps · needs …  │
│   Audio only · MP3 128 kbps        ~296.9 KB   MP3 file · needs ffmpeg             │
│                                                                                    │
│                          Download        Set up ffmpeg        Back                 │
│                                                                                    │
│  Saves to /home/you/Downloads  ·  492.7 KB                                         │
╰────────────────────────────────────────────────────────────────────────────────────╯
```

## Install

You need Python 3.9 or newer (already installed on macOS and most Linux
systems).

```sh
git clone <this repo> viddl
cd viddl
./install.sh
```

Then:

```sh
viddl
```

That is the whole setup. The installer puts a `viddl` command on your `PATH`,
and the app looks after its own dependencies:

* **yt-dlp** (about 3 MB) is fetched into `~/.local/share/viddl/bin` the first
  time the app runs. If you already have it installed, Viddl uses yours instead.
* **ffmpeg** is only fetched when you actually need it (MP3, or a quality that
  has to be merged), and it asks before downloading anything large. If you
  already have ffmpeg, that is used.

Nothing is installed system-wide and no configuration files are created.

## Using it

| You want to… | Do this |
| --- | --- |
| Start it | `viddl` |
| Go straight to a link | `viddl https://www.youtube.com/watch?v=…` |
| Start on the MP3 options | `viddl --audio <link>` |
| See what qualities a link has, without downloading | `viddl --list-qualities <link>` |
| Save somewhere else for one run | `viddl --download-dir ~/Videos <link>` |
| Check that everything is set up | `viddl --doctor` |

In the app:

* **Mouse** — click a quality to download it, click any button. Rows highlight
  as you move over them.
* **Keyboard** — `↑`/`↓` or `j`/`k` to move, `Enter` to download, `Tab` between
  buttons, `Esc` to go back, `Ctrl+Q` to quit.
* Paste with `Ctrl+V` / `Cmd+V`, or just type the link.

Downloads go to your **Downloads** folder (`~/Downloads`, or whatever your
desktop calls it) with the video's own title, plus its id so nothing gets
overwritten by accident:

```
~/Downloads/Me at the zoo [jNQXAC9IVRw].mp4
```

When it finishes, the app shows the **full path** on screen, along with buttons
to copy it or open the folder.

## Audio only (MP3)

Pick any `Audio only · MP3` row. Viddl downloads the best audio stream the site
offers and converts it with ffmpeg. Sizes are marked `~` because MP3 encoding is
variable — the estimate is capped by the source quality, so it never promises
more than you can get.

## The browser extension

The extension adds a **Viddl** button to YouTube (next to the like/share row)
and a **right-click option everywhere else**. Clicking either hands the link to
the app on your computer — no server, no port, no account.

```sh
viddl --install-browser-extension     # copies it out and tells you where
```

Then, once:

1. Open `chrome://extensions` (or `edge://extensions`, `brave://extensions`).
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose the folder the command printed.

The extension talks to the app through a custom `viddl://` link, the same way
`mailto:` opens your mail program. The installer already registered it; if you
skipped that, run:

```sh
viddl --register-protocol
```

The first time you click the button your browser asks whether to allow
`viddl://` links — tick **always allow** and it will not ask again. A terminal
window opens with the link already loaded, ready for you to pick a quality.

The extension asks for very little: it only reads a page when you use the
button or the menu on it, and it never touches sites in the background.

## When something goes wrong

Errors are written for people, not for log files. A private video says *"That
video is private. Only the owner can download it."*, a mistyped link says
*"That page could not be found (404)."*, and a missing ffmpeg tells you to press
**Set up ffmpeg** rather than mentioning post-processing.

If you need more detail:

```sh
viddl --doctor          # versions, paths, and whether each tool works
```

That prints the app folder, the log file, and where downloads go. The log holds
the last run's details if something failed in a way the screen could not
explain.

## Removing it

```sh
./uninstall.sh            # remove the app, keep the yt-dlp/ffmpeg helpers
./uninstall.sh --purge    # remove everything
```

## How it works

| Piece | What it does |
| --- | --- |
| `viddl/tui/` | The terminal interface (Textual). Five screens: home, qualities, progress, stopped, done. |
| `viddl/engine.py` | Finds yt-dlp and ffmpeg, or downloads its own copy into `~/.local/share/viddl/bin`. |
| `viddl/formats.py` | Turns site metadata into the quality menu, with size estimates. |
| `viddl/download.py` | Runs yt-dlp, reads its progress, and works out which file was written. Stopping it stops the whole tree yt-dlp started — the ffmpeg merge included — and keeps what was fetched so it can be resumed. |
| `viddl/errors.py` | Maps raw tool output onto plain-language explanations. |
| `viddl/protocol.py` | The `viddl://` hand-off and its per-OS registration. |
| `viddl/browser_extension/` | The Chromium (Manifest V3) extension. |

Nothing is ever written outside `~/.local/share/viddl` (plus your Downloads
folder), apart from the two small system bits that make the browser button work:
a `.desktop` entry on Linux, an app bundle in `~/Applications` on macOS, or a
registry key under `HKEY_CURRENT_USER` on Windows. Viddl's own folder and the
log are readable only by you.

Any page can ask the system to open Viddl with a link, so the link is only ever
put in the box for you to look at: nothing downloads until you choose a quality,
and a link from a page can never reach the command line as an option. The whole
model — including what a page *can* do, and what this deliberately does not
defend against — is in [SECURITY.md](SECURITY.md).

## Environment variables

Four variables change where Viddl looks. None are needed for normal use; they
exist for tests, shared machines and unusual setups. `viddl --doctor` prints the
ones that are set and what became of each, so none of them is a secret.

| Variable | What it does | Why you would use it |
| --- | --- | --- |
| `VIDDL_HOME` | The folder Viddl owns: the helpers it downloads, the log, the extension it copies out. | Keep everything on another disk, or start from a clean state. |
| `VIDDL_DOWNLOAD_DIR` | Where finished videos and MP3s go (default: your Downloads folder). | Save to a media drive, or straight into a project folder. |
| `VIDDL_YTDLP` | The yt-dlp to run, instead of the one Viddl finds or downloads. | Use a build of your own, or the one your package manager installed. |
| `VIDDL_FFMPEG` | The ffmpeg to use, instead of the one Viddl finds or downloads. | Same, for ffmpeg. |

Each value is checked before it is used, and one that cannot be honoured is
ignored rather than half-applied. A value that looks like an option
(`--exec=…`) is refused, so it can never reach a command line as a flag; a
folder that is really a file is refused, rather than failing later in a way
nobody can explain; and a bare program name means your `PATH` — never the
working directory, which is not something Viddl gets to choose when a browser
starts it. `viddl --doctor` says which of the four are in use, which were
ignored, and why.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

The suite covers the quality menu and its size estimates, engine discovery and
installation (including a mirror returning an HTML error page), the download
runner and its progress parsing, error translation, `viddl://` parsing and
registration, and the whole interface driven through Textual's test pilot — with
clicks, arrow keys and the cancel path. The extension is checked too: its
manifest, every file it references, and its pure JavaScript logic via Node.

Regenerate the extension icons with `python3 tools/make_icons.py`.

## Notes

* Playlists are not supported yet: a playlist link downloads its first video and
  the app says so.
* Instagram, Threads, and sometimes Vimeo or TikTok ask for a login on some
  networks (they are stricter with datacenter connections than with home ones).
  When that happens Viddl says *"The site wants you to be signed in to watch
  this one"* rather than showing a stack trace. Signed-in downloads are not
  supported yet, so those links need a different approach.
* Everything runs through yt-dlp's own extractors, which is why the same list of
  sites works here as there — and why a link that works one week can stop
  working the next until yt-dlp updates.
* Live streams work but are recorded from the current point.
* Windows is supported by the code (registry handler, `ffmpeg.exe`,
  `%LOCALAPPDATA%`) but has been tested less than Linux and macOS. There is no
  `install.ps1` yet — `pip install .` then `viddl --register-protocol` is the
  short version.
