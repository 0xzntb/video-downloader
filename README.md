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
│   Audio only · original M4A         302.0 KB   no re-encode                        │
│   Audio only · MP3 320 kbps        ~301.0 KB   MP3 · source is 130 kbps · needs …  │
│   Audio only · Opus 160 kbps       ~301.0 KB   Opus · source is 130 kbps · needs … │
│   Audio only · FLAC                  ~1.6 MB   lossless file · needs ffmpeg        │
│                                                                                    │
│  Extras  [ ] Subtitles   [ ] Details   [ ] Cover image                             │
│                                                                                    │
│                          Download        Set up ffmpeg        Back                 │
│                                                                                    │
│  Saves to /home/you/Downloads  ·  492.7 KB                                         │
╰────────────────────────────────────────────────────────────────────────────────────╯
```

## Install

You need Python 3.9 or newer (already installed on macOS and most Linux
systems).

On Linux and macOS:

```sh
git clone <this repo> viddl
cd viddl
./install.sh
```

On Windows, in the folder you downloaded:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

It does the same things, for your account only: a private environment in
`%LOCALAPPDATA%\viddl`, a `viddl.cmd` launcher on your user `PATH`, and the
`viddl://` handler. Open a new terminal window afterwards. `install.ps1
-NoPath -NoProtocol` skips the two steps that touch the rest of your account.

Then:

```sh
viddl
```

That is the whole setup. The installer puts a `viddl` command on your `PATH`,
and the app looks after its own dependencies:

* **yt-dlp** (about 3 MB) is fetched into `~/.local/share/viddl/bin` the first
  time the app runs. If you already have one, Viddl uses yours instead — and if
  several exist it uses the **newest** one, whichever folder it is in, so a copy
  Viddl fetched months ago cannot shadow a newer one you installed since.
* **ffmpeg** is only fetched when you actually need it (a conversion to MP3,
  M4A, Opus, FLAC or WAV, or a quality whose video and audio have to be merged),
  and it asks before downloading anything large — the
  qualities screen says how big it is first (*"The download is about 149.2 MB"*),
  so the number is on screen before you click. If you already have ffmpeg, that
  is used — unless you have asked Viddl to fetch its own, which is then the one
  it uses (and what `viddl --update-ffmpeg` replaces).

That ffmpeg download is a big one, so a connection that drops is a pause rather
than a restart: Viddl keeps what arrived, asks the server for the rest, and
carries on from there — even in a later session, since the half-file lives in
Viddl's own folder rather than somewhere temporary. Close the app mid-download
and run it again and it picks up where it stopped; if it was killed after the
download finished but before unpacking, the archive it already has is used
instead of fetched again. Nothing half-finished ever ends up where it is run
from: the bytes are checked against the published checksum first, and a part that
cannot be continued is thrown away rather than joined to the wrong file. On
Apple silicon, if Rosetta 2 is not installed, the Intel fallback is skipped —
saying *"run `softwareupdate --install-rosetta`"* — rather than failing with a
message nobody can act on.

Nothing is installed system-wide and no configuration files are created.

## Using it

| You want to… | Do this |
| --- | --- |
| Start it | `viddl` |
| Go straight to a link | `viddl https://www.youtube.com/watch?v=…` |
| Start on the audio options | `viddl --audio <link>` |
| Download something that needs a sign-in | Choose **Sign in with my browser** on the qualities screen, or `viddl --cookies-from-browser auto <link>` |
| Use it in a script, an old console or over SSH | `viddl --plain --yes <link>` |
| Pick the quality without a menu | `viddl --plain --quality 720 <link>` |
| Take a whole playlist, or just some of it | Choose **Choose videos…** on the qualities screen (`p`), or `viddl --plain --playlist --yes <link>` |
| Add subtitles, details or a cover image | Tick them in the **Extras** row on the qualities screen (`s`, `m`, `t`), or `viddl --extras subs,details,cover <link>` |
| See what qualities a link has, without downloading | `viddl --list-qualities <link>` |
| Save somewhere else for one run | `viddl --download-dir ~/Videos <link>` |
| Skip videos you have already downloaded | `viddl --download-archive <link>` (see [What is remembered](#what-is-remembered-between-runs)) |
| See what you have downloaded lately | Press `r` (or **Recent**) on the home screen |
| Use the link you already copied | Click **Paste link** on the home screen (or press `p` when the box is not focused) |
| Download several links in one go | Paste them into the link box separated by spaces, then **Download all** on the links screen |
| Check that everything is set up | `viddl --doctor` |
| Fetch the newest video engine (the usual fix for a link that stopped working) | Press **Update engine** on the home or failure screen, or `viddl --update` |
| Fetch the newest ffmpeg build | `viddl --update-ffmpeg` |

In the app:

* **Mouse** — click a quality to download it, click any button. Rows highlight
  as you move over them.
* **Keyboard** — `↑`/`↓` or `j`/`k` to move, `Enter` to download, `Tab` between
  buttons, `Esc` to go back, `Ctrl+Q` to quit. On the qualities screen `s`,
  `m` and `t` switch subtitles, details and the cover image on and off.
* Paste with `Ctrl+V` / `Cmd+V`, or just type the link. If the clipboard already
  holds a link when Viddl opens, the home screen says so and offers **Paste
  link**; it never fills the box or starts anything by itself.

When the link is a **playlist**, the qualities screen says so and offers
**Choose videos…** (`p`). That opens a checklist of every video in it, all
ticked: click one, or press `Space`, to leave it out; `a` ticks all and `n` none.
The quality under the cursor when you open the list is the quality used for
every video in it. **Download** without opening the list takes the first video
only, which is what a playlist link did before — a click never starts forty
downloads.

Several links at once become a **queue**: paste them into the link box separated
by spaces (or one per line, if your terminal pastes that way) and the list opens
in the order you pasted it. Rows can be **Removed** (`Delete`), the whole list
**Cleared** (`c`), and another link added in the box at the top — paste it and
press `Enter`. **Download all** then asks for one quality, used for every link,
and takes them one at a time; the screen at the end names each file saved and
any that did not work, and one bad link never stops the rest. A word pasted in
between two links is left out and said so, and the same link twice is one
download.

Downloads go to your **Downloads** folder (`~/Downloads`, or whatever your
desktop calls it) with the video's own title, plus its id so nothing gets
overwritten by accident:

```
~/Downloads/Me at the zoo [jNQXAC9IVRw].mp4
```

When it finishes, the app shows the **full path** on screen, along with buttons
to copy it or open the folder.

Every finished download is also written to a small **recent list** in Viddl's
own folder. **Recent** on the home screen (`r`) shows the last fifty: the
title, the size, when it was saved and where. A row's folder can be opened
(`o`, **Open folder**), its full path copied (`c`, **Copy path**), or the file
taken again (`Enter`, **Download again**) — which puts the link back in the box
and asks the site for its qualities, exactly as if it had been pasted. A file
that has since been deleted stays in the list, marked as gone, rather than
quietly disappearing.

If that name is already taken, the new file becomes `Me at the zoo [jNQXAC9IVRw]
(2).mp4` rather than replacing what is there — including when two Viddl windows
download the same video at the same time. Each download writes in a hidden
folder of its own inside Downloads and only the finished file is moved out, so
two windows can never write over each other's bytes. That folder is named after
the link and quality, which is also what lets a stopped download be resumed: the
half-finished file is waiting there — out of sight, not gone — and picking the
same video again carries on from it, even in a later session. Choose
**Discard** on the stopped screen and the folder goes too.

## Without the full-screen app

Some consoles cannot draw the app: a legacy Windows console, some SSH
sessions, a log file, a screen reader. `--plain` prints one ASCII line at a
time, keeps progress on a single line, and puts **the saved path alone on
standard output**, so it composes:

```sh
path=$(viddl --plain --yes --quality 720 "https://www.youtube.com/watch?v=...")
```

The title, uploader, quality list and progress go to stderr; only the path is
on stdout. `--quality` takes a height (`720`, `1080`), `best`, or an audio
choice: `audio` (the best sound the link has), a format (`mp3`, `m4a`, `opus`,
`flac`, `wav`), or one preset (`mp3-192`).
Without `--yes` Viddl asks before downloading, and refuses to start when there
is no terminal to ask on — the safe default for a script. `Ctrl+C` stops the
download and keeps what was fetched, exactly as in the app, and running the
same command again carries on.

`--playlist` takes every video of a playlist, one after another, at the chosen
quality:

```sh
viddl --plain --playlist --yes --quality 720 "https://www.youtube.com/playlist?list=..."
```

Each video gets its own `[3/9] Title` line and progress, and **one path per line**
lands on stdout as it is saved — so a script gets the list in the site's own
order, and files already downloaded are reported rather than fetched again.
The run ends with `Saved all 9 videos.`; if one video fails the rest are still
tried, its error is printed, and the exit code is 1 rather than 0 — a partial
result never looks like a complete one. On a link that is a single video,
`--playlist` changes nothing.

## Audio only

The first audio row is the site's own file — **`Audio only · original M4A`**
(or `Opus`, whichever the site serves). Nothing is converted: it is the exact
stream the site sent, at the quality it sent it, and it needs no ffmpeg at all.
For most videos this is the best answer, which is why it is first and why
`--quality audio` means it.

Below it are conversions, for when you want a particular container or a smaller
file. Each names its format — `MP3`, `M4A`, `Opus`, `FLAC`, `WAV` — and the
lossy ones have bitrate presets. They all need ffmpeg, and Viddl offers to fetch
it once.

One format is deliberately missing from the list: the one your link already
uses. Asking yt-dlp to convert a stream to its own codec does not re-encode it,
it copies it — an `M4A 128 kbps` row on an m4a source would hand you the source
file under a bitrate nobody honours. The original row is that format already, so
on such a link `--quality m4a` gets you the untouched file.

Sizes are marked `~` because encoders are variable. Where it can, the estimate is
capped by the source quality, so a 130 kbps source never shows as 320 kbps just
because you asked for it. AAC is the exception: it is asked for a fixed rate and
writes one, so an M4A row shows what the file will weigh while its note says
where the sound came from — a bigger file that cannot sound better. FLAC and WAV
take their size from the length of the audio rather than the source bitrate, so a
130 kbps source becomes a much bigger file; neither adds quality that was never
there.

## Subtitles, details and cover images

Three extra things can go with a download. All are **off** until they are
clicked, on the qualities screen, because a download should not quietly contain
more than it said it would.

| Extra | What it adds |
| --- | --- |
| **Subtitles** (`s`) | English captions, including the auto-generated ones, embedded in the video. |
| **Details** (`m`) | The title, channel, date, description and chapters, embedded as tags. |
| **Cover image** (`t`) | The site's picture, saved as a `.jpg` **beside** the file. |

Subtitles are only added to a video: an audio download has nowhere to show
them, and the screen says so instead of pretending. All three need ffmpeg, so
the same **Set up ffmpeg** button appears when one is ticked and ffmpeg is not
there yet. From a script, name them with `--extras`:

```sh
viddl --plain --yes --extras subs,cover "https://www.youtube.com/watch?v=..."
```

`--extras all` takes the three; without `--plain`, the same words open the app
with those switches already on. A cover lands beside the file under the same
name, `Me at the zoo [jNQXAC9IVRw].jpg`, and the result screen says so.

Two notes about the shape of this feature, both from measuring the toolchain
Viddl installs. The cover is a file of its own rather than art inside the media
because the yt-dlp release Viddl downloads carries no mutagen and the ffmpeg it
downloads carries no ffprobe: yt-dlp's attempt to embed a picture in an MP4 then
fails the *whole download* with "Unable to embed using ffprobe & ffmpeg".
Writing the picture beside the file works for every link and every container.
And **Details** is one switch, not two, because yt-dlp always embeds chapters
when it embeds metadata — a separate chapters switch would do nothing while
details were on.

## Sites that want you to sign in

Instagram, Threads, and sometimes Vimeo or TikTok answer only to a signed-in
session, and so do age-restricted or bot-checked videos. Viddl's qualities
screen offers a **Sign in with my browser** row for these — one row per browser
it finds on your computer (Chrome, Firefox, Edge, Brave and, on a Mac, Safari).
Choosing it asks the site again, this time through the cookies that browser
already has, and every later download on that screen uses them too. Choose the
row again to stop.

What that means, in full, because it is the only part of Viddl that touches an
account's files:

* **Viddl never reads a cookie itself, and never sees a password.** It tells
yt-dlp to read the browser's own store, in place, and *only* the cookies for the
site you are downloading.
* **Nothing is copied, exported or saved.** No cookie file is written, no
credential is stored, and the log never contains one. Choosing the row starts
nothing by itself — it applies to the link on that screen and to downloads you
start from it.
* **It is per download, not per account.** Nothing is remembered next time you
open Viddl; the app starts signed out every time.
* **An open browser can hold the store locked.** If the read fails, Viddl says
so and suggests closing the browser, signing in to the site first, or choosing a
different browser — rather than showing yt-dlp's error.

From the command line the same choice is `--cookies-from-browser`:

```sh
viddl --cookies-from-browser auto <link>      # whichever browser is installed
viddl --plain --yes --cookies-from-browser firefox <link>
viddl --list-qualities --cookies-from-browser chrome <link>
```

`auto` picks the first browser found; a name it does not know is refused before
anything runs. `viddl --doctor` prints a `Cookies:` line naming the browsers it
could see, which is the first thing to check if a sign-in did not help.

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

**A link that takes a while to read.** Working out what a link offers means
asking the site, and some sites are slow. The qualities screen counts the
seconds while it waits, and after five of them it says so plainly — *"Still
looking — some sites are slow to answer. Press “Stop looking” to give up."* —
with a button that ends it there and then; the yt-dlp process is stopped, not
left running out of sight. **Back** (or `Esc`) does the same. If you go back and
open the same link again within a few minutes, the answer is remembered, so it
appears at once instead of asking the site a second time. A link that offers a
whole playlist is only read up to its first video, so a long one cannot turn
into minutes of waiting and a menu that never arrives; the list of its videos is
asked for separately — titles and links only, no video page read — and only
when you press **Choose videos…**.

**"It worked last week."** A link that stops working is usually a site that has
changed, and the engine is the part that learns the change. When the engine is
more than a month old, the home screen says so, and a link that fails says it
too — with an **Update engine** button beside the words rather than an
instruction to leave the app. Pressing it fetches the newest release with the
same checksum check as the first install, says what it moved from and to
(*"yt-dlp 2026.08.19 -> 2026.09.01"*), and then **asks the link again by
itself**, because making *this* link work is the whole point. If there was
nothing newer, it says *"already the newest release"*, does not ask the link a
second time, and does not keep offering the update for the rest of the session.
A failed update says why and leaves the failure screen as it was. `--plain` and
`--list-qualities` add the same advice to a failure when the engine is old,
pointing at `viddl --update`.

A failure that might not have happened — the site rate-limiting you, a server
having a bad minute, a connection that dropped — is tried again by itself: after
4 seconds, then after 8, three attempts in all. The progress screen says so
(*"Retrying in 4s (attempt 2 of 3)…"*) rather than looking stuck, and the second
attempt continues from the half-finished file instead of starting over. A
failure that will not change on its own — private, removed, region-blocked, a
full disk — is reported at once, because waiting for the same answer is just a
longer way of saying no. If all three attempts fail, the message says how many
times Viddl tried.

If you need more detail:

```sh
viddl --doctor          # versions, paths, and whether each tool works
```

That prints the app folder, the log file, and where downloads go. The log holds
the last run's details if something failed in a way the screen could not
explain.

If Viddl cannot create its own folder — a read-only home, or one owned by
another account — the app says so on its first screen (*"Cannot save Viddl's
files (Permission denied) — run `viddl --doctor`"*) instead of leaving you to
find out from a download that fails for what looks like an unrelated reason. The
doctor names the folder and both ways out: fix its permissions, or point
`VIDDL_HOME` at somewhere you own. It is a warning, not a refusal: downloads
still work if an engine is already installed.

The first thing to try when a link that used to work stops working is:

```sh
viddl --update           # newest yt-dlp; says which version it moved from and to
viddl --update-ffmpeg    # newest ffmpeg build, if merging or audio is the trouble
```

When the engine is more than a month old, the app says so on its first screen
and `viddl --doctor` repeats it. `VIDDL_YTDLP` points Viddl at a particular copy
of yt-dlp when you want to manage that yourself.

## Removing it

```sh
./uninstall.sh            # remove the app, keep the yt-dlp/ffmpeg helpers
./uninstall.sh --purge    # remove everything
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
.\uninstall.ps1 -Purge
```

## How it works

| Piece | What it does |
| --- | --- |
| `viddl/tui/` | The terminal interface (Textual). Screens for home, qualities, the playlist checklist, progress, stopped, and the result. |
| `viddl/engine.py` | Finds yt-dlp and ffmpeg, or downloads its own copy into `~/.local/share/viddl/bin` — in pieces that survive an interruption, checked against a published checksum and only then unpacked. |
| `viddl/formats.py` | Turns site metadata into the quality menu, with size estimates. |
| `viddl/extras.py` | The optional additions — subtitles, details, cover image — what each one really asks yt-dlp for, and where it goes. |
| `viddl/download.py` | Reads what a link offers — in a process of its own, so a slow site can be stopped and its answer is remembered for a few minutes — and runs yt-dlp in a per-download working folder, reads its progress, and moves the finished file out under a name reserved before the move, so two windows cannot collide. A failure worth a second attempt is retried after a doubling wait; stopping it stops the whole tree yt-dlp started — the ffmpeg merge included — and keeps what was fetched so it can be resumed. |
| `viddl/queue.py` | Several videos, one at a time: which one is running, a count that is honest at the end, and a bad video that costs one item rather than the list. |
| `viddl/plain.py` | The line-oriented `--plain` path: ASCII-only text, one progress line, the saved path alone on stdout. |
| `viddl/errors.py` | Maps raw tool output onto plain-language explanations, and says whether the failure is one another attempt may fix. |
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

## What is remembered between runs

Two small files in Viddl's own folder, both readable by your account alone:

* **The recent list** (`recent.json`) — the last fifty downloads: link, path,
  title, size and when. This is what the **Recent** screen shows, and it is
  always on.
* **The download archive** (`download-archive.txt`) — one line per video
  yt-dlp has finished. This one is off unless you ask for it:
  `viddl --download-archive <link>` turns it on for that run (or set
  `VIDDL_ARCHIVE=1`), and every download that run — app or `--plain` — records
  itself and skips a video that is already in it. Re-running a playlist takes
  only the videos it has not fetched yet.

What the archive does, said plainly: asking for a video you already have
hands back the file already in your Downloads folder instead of downloading it
again. It recognises the *video*, not the file — so if you deleted that file,
the next run notices and downloads it again (the line is dropped from the
archive); and if you want the same video at a *different* quality while the
archive is on, it will still hand back the file you have, because that is what
it was asked to do. Leave it off, as by default, and every click downloads.

## Environment variables

Five variables change where Viddl looks, one changes what it asks for, one
changes how long it waits for the network, and one says what is remembered
between runs. None are needed for normal use; they
exist for tests, shared machines, slow links and unusual setups. `viddl --doctor` prints the ones that are set and what became of
each, so none of them is a secret.

| Variable | What it does | Why you would use it |
| --- | --- | --- |
| `VIDDL_HOME` | The folder Viddl owns: the helpers it downloads, the log, the extension it copies out. | Keep everything on another disk, or start from a clean state. |
| `VIDDL_DOWNLOAD_DIR` | Where finished videos and audio files go (default: your Downloads folder). | Save to a media drive, or straight into a project folder. |
| `VIDDL_YTDLP` | The yt-dlp to run, instead of the one Viddl finds or downloads. | Use a build of your own, or the one your package manager installed. |
| `VIDDL_FFMPEG` | The ffmpeg to use, instead of the one Viddl finds or downloads. | Same, for ffmpeg. |
| `VIDDL_YTDLP_ARGS` | Extra options for yt-dlp, split like a shell splits them: `--limit-rate 2M --cookies-from-browser chrome`. | Slow a link down, use your browser's cookies for a site that asks you to sign in, or pass anything else yt-dlp understands. |
| `VIDDL_TIMEOUT` | How many seconds to wait for the network, per request: `45` or `45s`, between 1 and 3600. | Give a slow link more patience, or make a site that never answers fail in seconds instead of minutes. |
| `VIDDL_ARCHIVE` | Keep a download archive and skip videos already in it: `1`/`on`/`yes`, or `0`/`off`/`no` to say so out loud. | Take a playlist again and only get the new videos; never fetch the same link twice. |

Each value is checked before it is used, and one that cannot be honoured is
ignored rather than half-applied. A value that looks like an option
(`--exec=…`) is refused, so it can never reach a command line as a flag; a
folder that is really a file is refused, rather than failing later in a way
nobody can explain; and a bare program name means your `PATH` — never the
working directory, which is not something Viddl gets to choose when a browser
starts it. `viddl --doctor` says which of the seven are in use, which were
ignored, and why. `VIDDL_ARCHIVE` is the one with no path to check — a value
that is neither a yes nor a no is refused (and shown as such by the doctor)
rather than quietly read as "off".

`VIDDL_TIMEOUT` moves every network wait together, because they only make sense
as a set: yt-dlp's socket timeout (20 s by default), the longer bound on a whole
look-up (seven and a half socket waits, 150 s), the few seconds allowed to read
a checksum list or a mirror's size, and the check that a freshly downloaded
ffmpeg actually runs. Left unset, each keeps the wait it was designed with; set,
one number applies to all of them. It does not limit how long a *download* may
take — a large file is allowed to take as long as it takes — and it does not
change the pauses between retries. `--doctor` prints a `Network:` line either
way, so the wait in effect is never a guess.

`VIDDL_YTDLP_ARGS` is the deliberate way to ask for something unusual, because
yt-dlp's own config file is not read at all: a line in `~/.config/yt-dlp/config`
can change where files go, rename them, or run a command afterwards, and a
setting you forgot about is a strange failure a year later. Your options are
added to both the download and the look-up that builds the quality menu, and
they come last, so where they and Viddl's defaults disagree yours win. A few
are refused whole rather than half-applied — abbreviated or not, since yt-dlp
accepts `--form` and `--path` — because they decide something Viddl has already
promised: `--paths`, `--output`, `--format` and `--batch-file` (where the file
goes and what is fetched — what the quality you clicked said, and what the
finished screen reports), `--simulate` and `--skip-download` (which would
download nothing), `--progress-template` (the progress Viddl reads) and
`--config-locations` (which would read the config file this variable exists to
replace). A link in the value is refused too, since it would be a second thing
to download while Viddl reports one.

Refusal is all-or-nothing: one bad option drops the whole value, because the
rest of a command line nobody finished writing is not something to guess at.
If a setting seems not to have taken, `viddl --doctor` says which part of the
value was refused and why.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

On Windows the same three lines live in `.venv\Scripts\`. CI runs the suite on
Linux, macOS and Windows for Python 3.9, 3.12 and 3.13, lints with `pyflakes`,
and runs `install.ps1` and the launcher it writes in a Windows job of its own.

The suite covers the quality menu and its size estimates, engine discovery and
installation (including a mirror returning an HTML error page, and a resuming
download driven against a real server on localhost — a `Range` request is a
conversation, so a stand-in would only test the stand-in), the download
runner and its progress parsing, error translation, playlist listings and the
sequential queue behind them, `viddl://` parsing and registration, the plain
`--plain` path, and the whole interface driven through Textual's test pilot —
with clicks, arrow keys, ticking a checklist and the cancel path. The
extension is checked too: its manifest, every file it references, and its pure
JavaScript logic via Node. The Windows installer files are checked from any
platform, and the suite runs on Windows in CI.

Regenerate the extension icons with `python3 tools/make_icons.py`.

## Notes

* A playlist link downloads its first video unless you ask for more: **Choose
  videos…** in the app, `--playlist` in `--plain`. The checklist lists up to 200
  videos; a longer playlist shows the first 200 and says how many were left out
  rather than pretending to be complete. Videos are taken one at a time on
  purpose — four at once is not faster on a throttled account, and it makes
  "which one is this?" unanswerable. A stopped queue keeps what it has, and
  **Download the rest** carries on without fetching the finished files again.
* Instagram, Threads, and sometimes Vimeo or TikTok ask for a login on some
  networks (they are stricter with datacenter connections than with home ones).
  When that happens Viddl says *"The site wants you to be signed in to watch
  this one"* and points at the **Sign in with my browser** row, which is how you
  get those links — see *Sites that want you to sign in* above.
* Everything runs through yt-dlp's own extractors, which is why the same list of
  sites works here as there — and why a link that works one week can stop
  working the next until yt-dlp updates. That is what `viddl --update` is for;
  the app mentions it once the engine it is running is over a month old.
* Live streams work but are recorded from the current point.
* Windows is supported and exercised: the suite runs there in CI, the installer
  (`install.ps1`) is run for real in the Windows job, and every fake shell
  script the tests use is given the shebang Windows does not understand. If a
  console cannot draw the full-screen app, `viddl --plain` is the ASCII path.
