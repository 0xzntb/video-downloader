# Viddl

![Viddl: a video or an MP3, from a link you paste into a terminal](docs/banner.png)

Download a video (or just the audio) by pasting a link — or typing its name —
into a terminal app that behaves like a real little program: a clean screen, a
list of qualities with estimated sizes, a progress bar, and the full path of the
file when it is done.

Works with YouTube, X, Instagram, Threads, TikTok, Vimeo and the hundreds of
other sites [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) supports. Mouse and
keyboard both work, everywhere.

There is no server, no account, nothing to configure, and no data leaves your
machine except the download itself.

![A recording of the app: a link is pasted, 720p is picked, and the file lands in Downloads](docs/demo/viddl.gif)

*Eleven seconds of the real app — a link pasted, a quality picked, the file
saved — recorded in a pseudo-terminal and replayed through a terminal emulator,
so the colours are the ones a terminal would have shown. The text version is
below, for anywhere a GIF cannot follow.*

```
╭────────────────────────────────────────────────────────────────────────────────────╮
│                                       Viddl                                        │
│         Download a video or a song by pasting a link or typing its name.           │
│                                                                                    │
│  Paste a link (or several, separated by spaces) — or type a name to search for     │
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
| Only know the name of what you want | `viddl "the song you mean"` (see [Search by name](#search-by-name)) |
| Start on the audio options | `viddl --audio <link>` |
| Download something that needs a sign-in | Choose **Sign in with my browser** on the qualities screen, or `viddl --cookies-from-browser auto <link>` |
| Use it in a script, an old console or over SSH | `viddl --plain --yes <link>` |
| Use it with a screen reader | `viddl --accessible --yes <link>` (see [Accessibility](#accessibility)) |
| Get only the saved path, for a script | `viddl --plain --yes --quiet <link>` |
| See a link's qualities as JSON | `viddl --json --list-qualities <link>` |
| Check the setup as JSON | `viddl --doctor --json` |
| Pick the quality without a menu | `viddl --plain --quality 720 <link>` |
| Take a whole playlist, or just some of it | Choose **Choose videos…** on the qualities screen (`p`), or `viddl --plain --playlist --yes <link>` |
| Add subtitles, details or a cover image | Tick them in the **Extras** row on the qualities screen (`s`, `m`, `t`), or `viddl --extras subs,details,cover <link>` |
| See what qualities a link has, without downloading | `viddl --list-qualities <link>` |
| Save somewhere else for one run | `viddl --download-dir ~/Videos <link>` |
| Skip videos you have already downloaded | `viddl --download-archive <link>` (see [What is remembered](#what-is-remembered-between-runs)) |
| Keep a call, a game or a stream usable while it downloads | `viddl --limit-rate 2M <link>` (see [A slow connection, a busy one, or a proxy](#a-slow-connection-a-busy-one-or-a-proxy)) |
| Download through a proxy | `viddl --proxy socks5://127.0.0.1:1080 <link>` |
| Record a live stream from its beginning | Press **Record from the start** on the qualities screen (`l`), or `viddl --live-from-start <link>` (see [Live streams](#live-streams)) |
| Remember a folder, quality, archive or theme between runs | `viddl --set quality 720` (see [Settings](#settings-optional)) |
| Make it easier to read on a bright or dim screen | `viddl --theme light <link>` (see [Colours and contrast](#colours-and-contrast)) |
| Read the explanations in Spanish | `viddl --lang es <link>` (see [Other languages](#other-languages)) |
| See how far a download has got from another window | Nothing to do: the title bar says it (see [The window title](#the-window-title)) |
| See what you have downloaded lately | Press `r` (or **Recent**) on the home screen |
| Find out which key does what | Press `F1` in the app (or `?` where you are not typing a link) |
| Try a failed download again | Press **Try again** (`r`) on the failure screen |
| Use the link you already copied | Click **Paste link** on the home screen (or press `p` when the box is not focused) |
| Download several links in one go | Paste them into the link box separated by spaces, then **Download all** on the links screen |
| Download from a browser tab instead of a terminal | `viddl --web` (see [A page in your browser](#a-page-in-your-browser)) |
| Check that everything is set up | `viddl --doctor` |
| Fetch the newest video engine (the usual fix for a link that stopped working) | Press **Update engine** on the home or failure screen, or `viddl --update` |
| Fetch the newest ffmpeg build | `viddl --update-ffmpeg` |

In the app:

* **Mouse** — click a quality to download it, click any button. Rows highlight
  as you move over them.
* **Keyboard** — `↑`/`↓` or `j`/`k` to move, `Enter` to download, `Tab` between
  buttons, `Esc` to go back, `Ctrl+Q` to quit. On the qualities screen `s`,
  `m` and `t` switch subtitles, details and the cover image on and off.
  On the home screen `Ctrl+T` changes the colour scheme. On the done screen `p`
  plays the file, `o` shows it in its folder and `c` copies its path; on the
  failure screen `d` opens the details, `c` copies them and `r` tries the same
  link again.
* **Every key, on one page** — press `F1`, or `?` anywhere that is not the link
  box, for the keys that work where you are standing: the whole list, including
  the ones the footer has no room for, gathered from the app itself so it cannot
  go stale. `Esc` closes it.
* **An empty box says what to do** — until the first character, the line above
  the box is a full example link (`No link yet — try
  https://www.youtube.com/watch?v=dQw4w9WgXcQ`); typing turns it back into the
  instruction. Nothing has to be guessed at, and nothing is added to the screen
  that would push the buttons off a small terminal.
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

When it finishes, the app shows the **full path** on screen, with what landed
next to it — the quality you picked, the file's own container, how long the video
is and how big the file turned out:

```
1080p  ·  MP4  ·  3:32  ·  24.1 MB
```

and buttons that act on it: **Play** (in whatever your desktop plays video with),
**Show in folder** — which selects the file on Windows and macOS, and opens its
folder where Linux has no such thing — **Copy path**, and **Download another**.

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

## Search by name

You do not always have the link. When the box — or the command line — gets a
name instead, Viddl searches for it, using the engine's own search (which is
YouTube's), and takes the **first** result:

```sh
viddl "the song you mean"
```

The qualities screen then shows *which* video that was — its title and uploader,
just as a pasted link would — before anything is downloaded, and `--plain` says
it out loud before the menu:

```
Searching for "the song you mean" - the first result is used.
```

`--list-qualities` and `--json --list-qualities` take a name too, so a script
can ask what a name resolves to without downloading it:

```sh
viddl --json --list-qualities "the song you mean" | jq -r '.url, .title'
```

Once the search has an answer it is treated as **that video**, not as the query:
the file is named after the video, the [download
archive](#what-is-remembered-between-runs) records the video, the recent list
holds the video's own link, and running the same name again with the archive on
hands back the file you already have. A search is a way in, not a new kind of
download.

What a name may be: anything that is not a link — `some song`, `AC/DC: live`,
`café del mar`. What it may not be: something that starts with a dash (the
downloader would read it as an option), a path (`/tmp/a.mp4`, `~/Videos/x.mp4`,
`./x`), another scheme (`file:`, `javascript:`), a paste longer than 200
characters, or nothing at all. Those are refused with a sentence rather than
searched for, because each of them is a mistake worth naming.

A link is still the precise answer — a name can pick the wrong video — and the
title on the qualities screen is how you notice. Press `Escape` and edit the name
in the box to try again. Names work wherever one video is expected: the home box,
`viddl`, `--plain`, `--playlist` and `--list-qualities`. They are not part of a
pasted batch of links, where each entry is downloaded without a look-up first.

## Without the full-screen app

Some consoles cannot draw the app: a legacy Windows console, some SSH
sessions, a log file, a screen reader. `--plain` prints one ASCII line at a
time, keeps progress on a single line, and puts **the saved path alone on
standard output**, so it composes:

```sh
path=$(viddl --plain --yes --quality 720 "https://www.youtube.com/watch?v=...")
```

The title, uploader, quality list and progress go to stderr; only the path is
on stdout. If a screen reader is what will be reading it, `--accessible` is the
same path with its progress written as whole lines (see
[Accessibility](#accessibility)). `--quality` takes a height (`720`, `1080`), `best`, or an audio
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

`--quiet` keeps that contract and drops the rest: the menu, the notes and the
progress line go nowhere, so **stdout is the path and stderr is empty** — no
filtering needed:

```sh
path=$(viddl --plain --yes --quiet "https://www.youtube.com/watch?v=...")
```

What went wrong still goes to stderr, so a failure can be read afterwards; the
exit code stays 0/1/130 as above.

### For scripts: JSON

`--json` prints one object instead of lined-up sentences. It works with the two
read-only answers — `viddl --json --list-qualities <link>` and
`viddl --doctor --json` — and is refused for anything else, because a download
already has `--quiet`.

```sh
viddl --json --list-qualities "https://www.youtube.com/watch?v=..." | jq -r '.qualities[].key'
```

The shapes, with every key always present:

| Object | Keys |
| --- | --- |
| qualities | `url`, `title`, `uploader`, `duration` (seconds, or `null`), `extractor`, `live`, `playlist` (`title`, `count`, `url`, `note`, or `null`), `qualities` |
| each quality | `key` (what `--quality` accepts), `label`, `kind` (`video`/`audio`), `height`, `size_bytes` (`null` when unknown — never `0`), `size_is_estimate`, `needs_ffmpeg`, `format_selector`, `merge_container`, `audio_format`, `keeps_source_audio`, `note` |
| doctor | `name`, `version`, `folders` (`app`, `downloads`, `log`), `problem`, `ytdlp` and `ffmpeg` (`path`, `version`, `origin`, `origin_text`, or `null`), `ytdlp_note`, `settings` (`path`, `file`, `values`, `problems`), `environment` (`name`, `value`, `problem`), `timeout_seconds` (`null` when unset), `archive` (`enabled`, `path`), `recent` (`path`), `cookies` (`browsers`: `key`, `label`), `platform` |

The rows are in the order the app shows them, so `qualities[0]` is what pressing
Enter would pick. Failures are still human sentences on stderr with exit code 1,
and nothing is printed on stdout when there is no answer.

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

## A slow connection, a busy one, or a proxy

Four flags describe how a download may use the network. They work in the app
and with `--plain`, cover every video of a playlist or a batch, and are off
unless you pass one.

| You want to… | Do this |
| --- | --- |
| Leave room for everything else on the connection | `viddl --limit-rate 2M <link>` |
| Download through a proxy | `viddl --proxy socks5://127.0.0.1:1080 <link>` |
| Ask for fewer pieces of a video at once | `viddl --concurrent-fragments 2 <link>` |
| Give up on a source that crawls, and try another | `viddl --throttled-rate 100K <link>` |

A speed is what yt-dlp reads: a number with an optional `K`, `M` or `G` —
`500K`, `2M`, `1.5M` — or plain bytes per second. A proxy needs its scheme
(`http`, `https`, `socks4`, `socks4a`, `socks5`, `socks5h`) and a host; an
address that needs a sign-in keeps it (`http://you:secret@proxy.example:3128`),
but the sentence Viddl writes names only `proxy.example:3128`, so a password
never reaches a log. All four are checked before anything runs: `--limit-rate
fast` is refused with the shape it wants, and `--proxy 127.0.0.1:8080` — a
value that would otherwise look like an option or an argument to the engine —
is refused rather than passed on. The default fragment count is 4.

The app repeats whatever you set on the qualities screen and on the playlist
checklist (*Network: through 127.0.0.1:1080, no faster than 2M/s, 4 pieces at a
time*), and `--plain` prints one `Network:` line before the download starts, so
a log records the terms and not only the result. `--quiet` leaves that line out
with the rest of the notes. For anything not covered here, `VIDDL_YTDLP_ARGS`
still passes options straight to yt-dlp (see [Environment
variables](#environment-variables)).


## Live streams

A live stream is the one download that does not end by itself, so Viddl says so
before you start one. The qualities screen reads *Live stream. Recording starts
now, not from the beginning. Stop when you have enough: what has been recorded
is saved*, and the button beside it — **Record from the start** (`l`) — asks the
site for the part it has already played instead. Not every site can still serve
that part, and recording from the beginning takes as long as the stream has
been running so far.

**Stop** is how a recording ends. In the app the button on the progress screen
says **Stop recording** rather than **Cancel**; with `--plain` it is `Ctrl+C`.
What had been recorded is kept and delivered as a real file in your Downloads
folder, not left as a `.part` inside a hidden working folder: the screen that
follows says *Recording stopped* with the file's path and size, and `--plain`
writes the path on stdout and exits **130**. A script can tell a stopped
recording from a finished download by the exit code and still use the file.

| You want to… | Do this |
| --- | --- |
| Record a live stream from its beginning | **Record from the start** on the qualities screen (`l`), or `viddl --live-from-start <link>` |
| Record from now (the default) | Do nothing: the recording starts when the download does |
| Stop a recording and keep what arrived | **Stop recording** in the app, or `Ctrl+C` with `--plain` |
| Record a live stream from a script | `viddl --plain --yes --live-from-start <link>` |

A live stream is one video, not a list: `--live-from-start` with `--playlist`
is refused rather than half-applied, because a queue has no way to ask each item
whether it is live before deciding. A live link inside a playlist or a batch of
links is recorded from the current point, which is the default.


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

It is one set of files with two manifests, because the browsers disagree about
exactly one thing: Chromium runs the background code as a service worker, and
Firefox refuses that key outright, running an event page instead.

```sh
viddl --install-browser-extension            # both builds, and where they are
viddl --install-browser-extension firefox    # just one; also chrome, edge, brave
```

**Chrome, Edge or Brave** — the folder ending in `chromium`:

1. Open `chrome://extensions` (or `edge://extensions`, `brave://extensions`).
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose that folder.

**Firefox** — the folder ending in `firefox`:

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on…**.
3. Choose `manifest.json` inside that folder.

Firefox forgets a temporary add-on when it restarts, and release builds only
run signed add-ons, so that is a way to try it rather than to live with it. For
a permanent install, load it again each time or sign it (see below).

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
button or the menu on it, and it never touches sites in the background. The
Firefox build also names YouTube in `host_permissions` — the same site its
content script already runs on — because without it the button on a watch page
could not hand the link over without a second click on the toolbar icon.

### Putting it in a store

```sh
viddl --extension-zip              # both builds
viddl --extension-zip firefox      # one
```

That writes `viddl-firefox-1.0.0.zip` (and the Chromium one) beside the install
folders. The zip is already the shape both stores take — `manifest.json` at the
root — so what is left is an account and a review, not a build:

* **Chrome Web Store** — upload the Chromium zip in the developer dashboard.
  A one-off registration fee and a review later, installs are a single click
  and developer mode is no longer part of the instructions.
* **addons.mozilla.org** — upload the Firefox zip to have it signed. Firefox
  only runs signed add-ons in release builds, so this is also what makes the
  extension survive a restart.

Neither submission is done by this project: both need an account, a listing
(description, screenshots, a privacy statement) and someone to answer a review.
The `id` in `manifest.firefox.json` is what keeps an add-on's identity across
updates, and it cannot be changed after the first submission — set your own
before uploading if you would rather it were not the placeholder.

## A page in your browser

`viddl --web` serves the same downloads as a page, for when a browser tab is
easier than a terminal:

```sh
viddl --web
# Viddl is open at http://127.0.0.1:51337/abCdEf.../
```

Paste a link or a name, pick a quality, watch the bytes, and the saved path is
shown when it ends. **Stop** keeps the part that arrived, exactly as `Esc` does
in the app, and stopping a live stream that way hands over the recording. The
page uses the session you started it with:

```sh
viddl --web --cookies-from-browser firefox --extras subs --download-archive --limit-rate 2M
```

Those flags then mean the same thing for every download begun there, and
`--live-from-start` records whole streams.

It is deliberately small and local:

* **Loopback only.** It binds `127.0.0.1`, never `0.0.0.0`, so nothing on your
  network can reach it. A request whose `Host` header is some other name — a
  domain pointed at 127.0.0.1 — is not answered at all.
* **A token in the URL.** Every request must carry the random token printed
  when the page started. Without it the server answers 404, which is the same
  answer a wrong token gets, so guessing learns nothing. The token is never
  written into the page's own files.
* **No CORS, and no cross-site writes.** No `Access-Control-Allow-Origin` is
  ever sent, so another site cannot read the answers. A request carrying a
  foreign `Origin` is refused, a POST has to be `application/json` (a plain
  HTML form cannot send that), and bodies are capped at 64 KB.
* **Nothing loaded from elsewhere.** The page fetches only its own script and
  stylesheet, under a `default-src 'none'` content policy.

Closing the terminal stops whatever is still downloading; a download that had
already finished is in your downloads folder either way.

What the page does not do: no playlists, no settings screen, and no ffmpeg
setup prompt — a quality that needs an ffmpeg you do not have fails with the
reason, and `viddl --update-ffmpeg` is the fix. It also cannot be hosted: yt-dlp
and ffmpeg have to run on your computer, which is the whole point.

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

**While it works.** The look-up line carries a mark that turns — `|`, `/`, `-`,
`\` — so a screen that is waiting looks like it is working before there is any
number to show, and then the seconds it has taken. On the progress screen the
bar stops at 99% until the download really has finished: a size the site only
estimated can be too small, and a bar that says 100% and then falls back to 41%
is the worst way to learn that. A total that is only an estimate is written with
a `~` (*"12.4 MB of ~48.0 MB"*), the way the quality menu already marks its own
estimates, so the moment the real size arrives is a change you were expecting
rather than a glitch. And a download that finishes before the progress screen has
been drawn once — a file that was already there, a tiny clip on a fast link —
lands on the done screen instead of flashing past a screen nobody could read.

**Trying again.** A failure is often a moment's bad luck rather than a dead
link, so the screen's first button is **Try again** (`r`): the same link at the
same quality, once more. It does not start from nothing — the half-finished file
is where the first attempt left it, so this continues from there, the same way
**Resume** does after a stop.

**Why it failed.** The screen says what happened in one sentence, and **Show
details** (or `d`) opens the rest: what Viddl made of the failure, then what the
downloader itself said, line for line — the last forty lines of it, with the
signed parameters in any link redacted and control characters removed, because
that output can quote a site's own video name. **Copy details** (or `c`) puts
the whole thing on the clipboard for a bug report, and the log file is named at
the bottom once there is something in it to read.

If you need more detail:

```sh
viddl --doctor          # versions, paths, and whether each tool works
```

That prints the app folder, the log file, and where downloads go — and says how
much the log holds and when it was last written, so an empty one is obvious
before anybody has to ask for it.

When a download, a look-up or the install of a helper fails, what happened is
written there: the command that was run, its exit code, and the last forty lines
of what the tool itself said. That is most of what a bug report otherwise has to
ask for. Links are redacted on the way in, so a signed address is not written
down; a run that worked writes nothing at all, because a line per download would
bury the failure the file exists for. The file rolls over at 256 KB and keeps
one older copy, so this is a last run rather than a history.

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
| `viddl/tui/title.py` | The window title: what a download says in the task bar, the escape sequence that sets it, the cleaning a site's name has to survive, and the stack that puts the shell's own title back. |
| `viddl/engine.py` | Finds yt-dlp and ffmpeg, or downloads its own copy into `~/.local/share/viddl/bin` — in pieces that survive an interruption, checked against a published checksum and only then unpacked. |
| `viddl/formats.py` | Turns site metadata into the quality menu, with size estimates. |
| `viddl/extras.py` | The optional additions — subtitles, details, cover image — what each one really asks yt-dlp for, and where it goes. |
| `viddl/download.py` | Reads what a link offers — in a process of its own, so a slow site can be stopped and its answer is remembered for a few minutes — and runs yt-dlp in a per-download working folder, reads its progress, and moves the finished file out under a name reserved before the move, so two windows cannot collide. A failure worth a second attempt is retried after a doubling wait; stopping it stops the whole tree yt-dlp started — the ffmpeg merge included — and keeps what was fetched so it can be resumed. |
| `viddl/queue.py` | Several videos, one at a time: which one is running, a count that is honest at the end, and a bad video that costs one item rather than the list. |
| `viddl/plain.py` | The line-oriented `--plain` path: ASCII-only text, one progress line, the saved path alone on stdout. |
| `viddl/config.py` | The optional settings file: a small TOML subset it can read on every Python it supports, and the order that decides whether a flag, a variable, the file or the default wins. |
| `viddl/theme.py` | The three colour schemes: the palettes, the WCAG contrast maths that proves they are readable, and the order that decides whether the flag, the variable, the file or the terminal picks one. |
| `viddl/web.py` | The local page behind `--web`: a loopback server with a token in the URL, the same look-up and download behind it, and no way in from another machine or another site. |
| `viddl/search.py` | What text typed where a link goes actually is: a link, a name to search for, or neither — and the link a search turned out to mean, so the download names a video rather than a query. |
| `viddl/errors.py` | Maps raw tool output onto plain-language explanations, and says whether the failure is one another attempt may fix. The patterns are here; the sentences are catalogue keys (U7). |
| `viddl/i18n/` | The sentences themselves, one plain dict per language, and the order that decides whether `--lang` or the machine's own locale picks one. No build step and no compiled catalogue: a missing translation falls back to English, key by key. |
| `viddl/protocol.py` | The `viddl://` hand-off and its per-OS registration. |
| `viddl/browser_extension/` | The browser extension: one set of files, a manifest for Chromium (service worker) and one for Firefox (event page). |

Nothing is ever written outside `~/.local/share/viddl` (plus your Downloads
folder, and the optional settings file in `~/.config/viddl` described under
[Settings](#settings-optional)), apart from the two small system bits that make the browser button work:
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
  `VIDDL_ARCHIVE=1`, or `viddl --set archive on` to remember it), and every
  download that run — app or `--plain` — records
  itself and skips a video that is already in it. Re-running a playlist takes
  only the videos it has not fetched yet.

The optional settings file is not one of these: it lives with your other
configuration (`~/.config/viddl`) and is described under
[Settings](#settings-optional).

What the archive does, said plainly: asking for a video you already have
hands back the file already in your Downloads folder instead of downloading it
again. It recognises the *video*, not the file — so if you deleted that file,
the next run notices and downloads it again (the line is dropped from the
archive); and if you want the same video at a *different* quality while the
archive is on, it will still hand back the file you have, because that is what
it was asked to do. Leave it off, as by default, and every click downloads.

## Settings (optional)

Viddl works with nothing configured, and that stays the default: no file is
read, none is written, and every choice is the one the app shipped with. When
you do want to remember something — a folder on another disk, the quality you
always take, an archive so repeats are skipped, the colours you can read — one
command writes it:

```sh
viddl --set download-dir ~/Videos
viddl --set quality 720
viddl --set archive on
viddl --set theme light
```

The file is `~/.config/viddl/config.toml` (`$XDG_CONFIG_HOME/viddl/config.toml`
when that is set, and `%APPDATA%\viddl\config.toml` on Windows), readable only
by you, and written only by `--set` and `--unset` — a machine that never ran
either has no file at all. `viddl --unset quality` forgets one preference;
removing the last one deletes the file, which puts the zero-config default back.

| Setting | Values | Flag and variable that override it |
| --- | --- | --- |
| `download-dir` | a folder | `--download-dir`, `VIDDL_DOWNLOAD_DIR` |
| `quality` | `720`, `1080p`, `best`, `audio`, `mp3-192` … | `--quality`, `--audio`, `VIDDL_QUALITY` |
| `archive` | `on` or `off` | `--download-archive`, `VIDDL_ARCHIVE` |
| `theme` | `dark`, `light`, `high-contrast` or `auto` | `--theme`, `VIDDL_THEME` |

**What wins:** a flag on the command line, then an environment variable, then
this file, then the built-in default. That order is the point of a preference
being a default rather than an instruction: `viddl --quality 480` takes 480 for
one run while the file keeps saying 720, and `--audio` beats a remembered
quality because it is a flag. `--set` checks the value before writing it — a
folder that is really a file, a quality the download path would refuse, or a
"yes" that is not a yes is refused with a sentence rather than written down and
ignored later. A value that gets into the file by hand and cannot be used is
ignored and named in `viddl --doctor`, which also prints the file and which
settings it holds.

The file is a small TOML subset — `key = "value"` and `key = true` — because
every Python Viddl supports has to read it back (3.9 has no `tomllib`). A key
from a newer version is left alone when Viddl rewrites the file, and comments
are the one thing it does not keep.

`quality` is where the app *starts*, not a decision it makes for you: the
qualities screen opens with that row under the cursor (or the best row, when
this link does not have it), and `--plain` downloads it when `--quality` is
silent.

## Colours and contrast

The app draws in one of three colour schemes. The home screen says which one is
in force — a quiet line above the buttons, `Theme: Dark` — and `Ctrl+T` (or a
click on it) moves to the next one.

| Scheme | What it is for |
| --- | --- |
| `dark` | Light text on a near-black page. The default, and what the app drew before there was a choice. |
| `light` | Dark text on a pale page, for a bright room or a terminal with a white background. |
| `high-contrast` | Black and white with bright edges, every piece of text at 7:1 or better. For a projector, a washed-out laptop screen, or eyes that want the extra separation. |
| `auto` | Not a fourth scheme: look at what the terminal says about its own background (`COLORFGBG`) and pick `dark` or `light`. A terminal that says nothing gets `dark`. |

```sh
viddl --theme light <link>          # this run
VIDDL_THEME=high-contrast viddl     # this shell
viddl --set theme light             # remembered, until --unset theme
```

A flag beats the variable, the variable beats the file, the file beats the
default — the same order every other preference follows (see
[Settings](#settings-optional)). `Ctrl+T` changes the scheme for that run only:
writing the file is `--set`'s job, and a click should not leave one behind on a
machine that never asked for it.

**The colours are measured, not eyeballed.** Every scheme names the pairs that
have to be readable — text on the panel, a muted note, a warning, a saved path,
the chosen quality's own label sitting on a bright background, the border — and
the WCAG contrast ratio is computed for each: 4.5:1 for text, 3:1 for borders
and disabled text, and 7:1 and 4.5:1 for high contrast. The test suite fails if
a palette drifts below its own bar, and `viddl --doctor` prints the smallest
text ratio of the scheme in force:

```
Theme:        light (the settings file; smallest text contrast 6.4:1)
```

**If colour is a problem.** With `NO_COLOR` set, Textual draws the screen in
monochrome, and the home row says `NO_COLOR is set, so colour is off` instead of
looking like a theme that did nothing. A terminal set to `TERM=dumb` cannot draw
the app at all, so Viddl says so and names the `viddl --plain --yes` command
that does work, rather than painting a full-screen app into a stream. `--plain`
has no colours to choose, and `--theme` is refused with it rather than accepted
and ignored; the `--web` page carries its own stylesheet, and is refused the
same way.

## Accessibility

The app is written to be read, not only looked at: every piece of state is a
word or a character rather than a colour.

* The quality under the cursor carries `›` in its first column, and the row is
  reversed while the list has the focus — so the cursor is still findable with
  `NO_COLOR` set, or on a terminal with no colour at all.
* The extras and the playlist say `[x] Subtitles` and `[ ] Subtitles`; statuses
  are sentences (`Download failed`, `yt-dlp 2099.01.01 ready`) rather than a
  colour that has to be learned.
* The focused button is bold and underlined rather than only outlined in a
different colour: those are attributes, so `NO_COLOR` cannot take them away.
* The theme row names the scheme (`Theme: Light`), and the home screen says when
  `NO_COLOR` is in force instead of looking like a setting that did nothing.
* `F1` (or `?`) is the page of keys that work where you are standing, including
  the ones the footer has no room for.

**For a screen reader, use `viddl --accessible`.** It is `--plain` with two
changes of its own: no line is ever written over, and the ending rings the
terminal's bell.

```sh
viddl --accessible --yes "https://www.youtube.com/watch?v=..."
```

Every progress update becomes a line of its own, one per whole percentage —
written once and read once — and the saved path is still the only thing on
standard output, so it composes exactly as `--plain` does. Measured on a real
terminal, the same download came out as one line holding 203 carriage returns
in `--plain`, and 101 lines holding none at all in `--accessible`, with one
bell at the end. `--quality`, `--yes`, `--playlist` and `--quiet` all work, and
`--theme` and `--web` are refused with it for the same reason they are refused
with `--plain`.

**What a terminal cannot do.** There is no spoken announcement: a full-screen
app redraws itself, and a screen reader reads the drawing. That is why the
accessible mode prints instead of drawing, and why the only thing it can add at
the end is the bell.

## Other languages

The explanations are written in English and Spanish. On a Spanish computer
nothing needs configuring: Viddl reads `LC_ALL`, then `LC_MESSAGES`, then
`LANG`, and speaks the language it finds.

```sh
viddl --lang es "https://www.youtube.com/watch?v=..."   # this run
LANG=es_ES.UTF-8 viddl --plain --yes <link>             # already the machine's setting
```

A language typed on the command line is checked: `viddl --lang fr` refuses with
*"'fr' is not a language Viddl speaks yet — try en, es"* instead of quietly
answering in English. A computer set to French is answered in English and keeps
working, and `viddl --doctor` says which language went unanswered:

```
Language:     English (en, from LANG=fr_FR.UTF-8) — no translation for fr_FR.UTF-8 yet, so English
```

**What speaks Spanish so far.** The explanations — the twenty-one rules that
turn an engine's own words into a sentence a person can act on — and the advice
about an old engine that gets appended to them, because that is part of the
same answer. A private video reads *"Ese vídeo es privado. Solo puede
descargarlo su dueño…"* with its advice in Spanish too, and `viddl --plain`
folds the accents to ASCII the way it folds every other text, so a legacy
console shows `Ese video es privado` and still composes as a script expects.

**What does not, yet.** The screens' own words, the `--plain` progress lines and
`viddl --doctor`'s sentences are English, so a Spanish failure appears inside an
English screen. That is a half-measure, and it is named here rather than implied
to be finished. Advice that points at a control quotes it as the screen has it
(*"choose “Set up ffmpeg”"*), so the words match what is actually on screen.

**Adding a language.** Copy `viddl/i18n/en.py`, translate the values, keep the
`{...}` placeholders and their names, and add the module to `_MODULES` in
`viddl/i18n/__init__.py`. Nothing is compiled: the suite fails if a catalogue is
missing a key, invents one, or fills in a different set of placeholders — and at
runtime a key a translation has not got yet falls back to English, one sentence
at a time.

## The window title

A download runs for minutes, in a window you have usually clicked away from. So
while it runs, the title bar says where it is:

```
Viddl — 42% — Killing Time.mp4
Viddl — 42% — 3 of 9 — Killing Time.mp4      a playlist, or a batch of links
```

The percentage comes before the name on purpose: a task bar or a narrow title
bar cuts off the end, and the number is the part worth keeping. A live
recording has no percentage — it has no end — so its title names what is being
recorded and stops there. When the download finishes, fails, or is stopped, the
title goes back to `Viddl`.

On the way out, the shell's own title comes back where the terminal keeps a
title stack (xterm and most things that copy it: `ESC [ 22 ; 0 t` pushed when
the app starts, `ESC [ 23 ; 0 t` popped when it closes). A terminal without a
stack is left with `Viddl` rather than `42%`, because the app's name is written
before the pop.

The name in the title is the video's, so it came from a website — and a title
*is* an escape sequence, so an ESC inside the name would end it early and start
something else in the terminal. Names are cleaned first: control characters,
the characters that reverse the direction text is drawn in, and line breaks go.
Nothing else is changed — the name is not transliterated, and the file on disk
keeps its full name.

`--plain` sets no title: its output is often piped into something that would
receive the sequence instead of a terminal. The `--web` page has a browser tab
rather than a title bar, which is the page's own business.

## Environment variables

Five variables change where Viddl looks, one changes what it asks for, one
changes how long it waits for the network, one says what is remembered
between runs, one sets a default quality and one picks the colours. None are
needed for normal use; they exist for tests, shared machines, slow links and
unusual setups, and every one of them outranks the settings file above.
`viddl --doctor` prints the ones that are set and what became of each, so none
of them is a secret.

| Variable | What it does | Why you would use it |
| --- | --- | --- |
| `VIDDL_HOME` | The folder Viddl owns: the helpers it downloads, the log, the extension it copies out. | Keep everything on another disk, or start from a clean state. |
| `VIDDL_DOWNLOAD_DIR` | Where finished videos and audio files go (default: your Downloads folder). | Save to a media drive, or straight into a project folder. |
| `VIDDL_YTDLP` | The yt-dlp to run, instead of the one Viddl finds or downloads. | Use a build of your own, or the one your package manager installed. |
| `VIDDL_FFMPEG` | The ffmpeg to use, instead of the one Viddl finds or downloads. | Same, for ffmpeg. |
| `VIDDL_YTDLP_ARGS` | Extra options for yt-dlp, split like a shell splits them: `--limit-rate 2M --cookies-from-browser chrome`. | Slow a link down, use your browser's cookies for a site that asks you to sign in, or pass anything else yt-dlp understands. |
| `VIDDL_TIMEOUT` | How many seconds to wait for the network, per request: `45` or `45s`, between 1 and 3600. | Give a slow link more patience, or make a site that never answers fail in seconds instead of minutes. |
| `VIDDL_ARCHIVE` | Keep a download archive and skip videos already in it: `1`/`on`/`yes`, or `0`/`off`/`no` to say so out loud. | Take a playlist again and only get the new videos; never fetch the same link twice. |
| `VIDDL_QUALITY` | The quality `--plain` uses when `--quality` is not given, and the row the app starts on: `720`, `best`, `audio`, `mp3-192`. | Keep a default for this shell without writing a settings file; override one command. |
| `VIDDL_THEME` | The colour scheme the app draws in: `dark`, `light`, `high-contrast`, or `auto` to follow the terminal's own background. | Try a scheme without writing a settings file, or change a plain terminal's mind for one shell. |

Each value is checked before it is used, and one that cannot be honoured is
ignored rather than half-applied. A value that looks like an option
(`--exec=…`) is refused, so it can never reach a command line as a flag; a
folder that is really a file is refused, rather than failing later in a way
nobody can explain; and a bare program name means your `PATH` — never the
working directory, which is not something Viddl gets to choose when a browser
starts it. `viddl --doctor` says which of the nine are in use, which were
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
Linux, macOS and Windows for Python 3.9, 3.12 and 3.13, checks the source with
`ruff` and `mypy`, and runs `install.ps1` and the launcher it writes in a
Windows job of its own.

`uv.lock` is committed and pins every version the suite is known to pass with.
To get exactly those, rather than whatever is newest today:

```sh
uv sync --locked --extra dev
uv run python -m pytest
```

The `locked` job in CI does the same and refuses to re-resolve, so a dependency
change that is not followed by `uv lock` fails there — on the pull request —
rather than in somebody else's install weeks later. Textual is held to a range
rather than to one version, `>=3.0,<9`: 3.0.1 is the oldest release the whole
suite passes on and 8.2.8 is the one it is developed against, so a 9.0 has to be
tried before it is allowed. Moving that number is a measurement, not an edit.

Coverage is measured in one cell of that matrix — Linux on Python 3.12 — and
held to a floor of **94%** (94.3% when the floor was last raised). The command
is the one CI runs, and it fails the run rather than printing a number nobody
reads:

```sh
.venv/bin/python -m pytest --cov=viddl --cov-fail-under=94
```

The floor only ever moves up: it is set a little under what the suite actually
covers, so a patch of new code fits and a hole does not. Raising it is part of
landing coverage, not a separate chore.

One limit worth knowing about: the suite runs on Linux, macOS and Windows,
which is what proves the app works on all three, but the registration behind
the browser button is only *executed* on Linux. The macOS app bundle and the
Windows registry key are produced by pretending to be that platform and
inspecting what comes out, because a test cannot install a URL handler for a
whole machine. Treat those two paths as checked by construction rather than
exercised end to end.

Alongside the examples, `tests/test_properties.py` states the rules the parsers
have to keep for *every* input and lets `hypothesis` generate it: a `viddl://`
URL from any page, whatever a site says its file weighs, whatever the engine
printed on stderr. It runs as part of the suite, needs nothing beyond the `dev`
extra, and its generators are seeded — a failure is the same failure tomorrow,
with the smallest input that caused it.

Two static checks come with the `dev` extra and read the same source on every
runner, so CI answers them in one cell rather than nine:

```sh
.venv/bin/python -m ruff check viddl tests
.venv/bin/python -m mypy
```

`ruff` carries pyflakes' rules and the ones pyflakes never had — unused code,
shadowed names, mutable defaults, import order, the small simplifications — and
the set is *selected* rather than everything ruff knows, because a rule set
nobody reads is a rule set everybody suppresses. `mypy` checks `viddl/` only:
that is where the annotations are, and the tests are checked by being run.
Neither runs through `ruff format` yet — 47 files would be rewritten, and that
is a separate change from having the findings.

The cheap part of all that can run itself before each commit. Opt in once per
clone; there is nothing to install, because the hook is a shell script that calls
the tools you already have:

```sh
git config core.hooksPath .githooks
```

`.githooks/pre-commit` checks the staged files for trailing whitespace, runs
`ruff` on the Python among them, runs `mypy` when anything under `viddl/`
changed, and runs 225 tests in about a second — the six pure modules, the ones
that decide what a link is, what a quality means and what a failure says.
Everything that spawns a subprocess takes minutes and stays on the runner, since
a five-minute hook is a hook everybody commits past with `--no-verify`. That is
documented as a normal thing to do here, because the hook is a convenience and
CI is the gate. It reads the files as they are on disk rather than the staged
copy, so a partial stage can still fail on the runner, and it has only been
exercised where `sh` is the shell executing it — on Windows, Git Bash can run
`sh .githooks/pre-commit` by hand.

The browser extension has two layers of tests. `tests/extension.test.js` is its
logic with the browser stubbed out and runs with the suite. `tests/extension.browser.test.js`
loads the unpacked extension into a real Chromium and clicks it: the popup
renders, the button appears on a watch page, and the `viddl://` link that comes
out is the page address, encoded, with the audio flag only when audio was asked
for. That one needs Playwright and a browser of its own, which is why it is not
part of the `dev` extra:

```sh
npm install
npx playwright install chromium
node --test tests/extension.browser.test.js
```

Without them the Python run **skips** it rather than pretending; the `extension`
job in CI installs both and runs the file directly, where a skip is a failure.
Nothing in it touches YouTube — every `youtube.com` request is answered from a
page written in the test — and the extension the app ships asks for `activeTab`
and no host permission, so the one test that reads the link out of the page
loads a copy with that permission added, which is what a click on the toolbar
would grant.

`tests/test_integration.py` is the one part of the suite that uses the real
thing rather than a stand-in engine: it serves a file from `127.0.0.1` and
downloads it through the app's own `DownloadJob` with whatever `yt-dlp` it can
find, because the contract with that tool — the progress template, the output
template, the “already downloaded” lines — is the part that breaks when yt-dlp
changes and cannot be checked by a fake. It is marked `integration` and
**skips**, never passes, on a machine with no engine; a yt-dlp installed as a
Python package is hidden from every *other* test, so the suite does not depend
on what the machine happens to have. One of them goes further and covers the
merge: `ffmpeg` writes a one-second DASH manifest offering a video stream and an
audio stream, `yt-dlp` downloads both and joins them, and the app has to report
the joined file rather than one of the halves it came from — the seam where the
last two bugs that reached users lived. That one needs a real `ffmpeg`, so it
skips without one. The weekly `engine` workflow installs the newest release,
runs those tests, and fails if one of them skipped; it can also be started by
hand from the Actions tab.

The suite covers the quality menu and its size estimates, engine discovery and
installation (including a mirror returning an HTML error page, and a resuming
download driven against a real server on localhost — a `Range` request is a
conversation, so a stand-in would only test the stand-in), the download
runner and its progress parsing, error translation, playlist listings and the
sequential queue behind them, `viddl://` parsing and registration, the plain
`--plain` path, and the whole interface driven through Textual's test pilot —
with clicks, arrow keys, ticking a checklist and the cancel path. The
extension is checked too: both manifests, every file each references, and its
JavaScript via Node — twice over, once through Chromium's `chrome.*` API and
once through Firefox's `browser.*` promises. The local page (`--web`) is driven over real HTTP on a loopback socket: the
token, the Host and Origin checks, the headers, the refusals, and a download
that comes out the other end. The Windows installer files are checked from any
platform, and the suite runs on Windows in CI.

Regenerate the extension icons with `python3 tools/make_icons.py`.

The GIF and the banner at the top of this README are generated, not drawn by
hand. `docs/demo/record.py` starts the real `viddl` in a pseudo-terminal against
a stand-in engine (`docs/demo/fake_ytdlp.py`) in a throwaway home, types a link,
waits for the screen that invites the next key, and writes an asciinema v2
recording; `docs/demo/render.py` replays that recording through a terminal
emulator and draws the frames, using the app's own palette and the monospace
font matplotlib ships. The engine is the only stand-in: every screen in the GIF
is the app's. Regenerate both with `pip install pillow pyte matplotlib` and

```sh
python3 docs/demo/record.py
python3 docs/demo/render.py gif
python3 docs/demo/render.py banner
```

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
* Live streams are recorded from the current point unless you ask for the
  beginning (`--live-from-start`, or **Record from the start** in the app), and
  stopping a recording keeps what arrived as a real file. A live link inside a
  playlist or a batch of links is always taken from the current point.
* Windows is supported and exercised: the suite runs there in CI, the installer
  (`install.ps1`) is run for real in the Windows job, and every fake shell
  script the tests use is given the shebang Windows does not understand. If a
  console cannot draw the full-screen app, `viddl --plain` is the ASCII path.

## Contributing

Bug reports, translations and patches are all welcome. [`CONTRIBUTING.md`](CONTRIBUTING.md)
has the setup commands, the four commands CI runs, and a map of which file does
what. [`CHANGELOG.md`](CHANGELOG.md) says what has changed — nothing is tagged
yet, so it describes what this version does rather than what it added.

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).
Anything that looks like a security problem belongs in a private advisory, as
[`SECURITY.md`](SECURITY.md) describes, rather than in a public issue.

## Licence

MIT — the text is in [`LICENSE`](LICENSE). Use it, change it, ship it; keep the
copyright notice and this permission notice with any copy or substantial part.
