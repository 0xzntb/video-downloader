# Viddl's security model

Viddl is a local program: no server, no account, no telemetry. It talks to the
site you point it at, and to the mirrors it downloads its own helpers from.
Nothing else leaves the machine.

The interesting part of the model is the hand-off. The browser extension gives
Viddl a link by opening a `viddl://` URL, which the operating system passes to
the app as an argument — the same arrangement as `mailto:` opening a mail
program. That means **any page can ask the operating system to open Viddl**, and
the design assumes a page may be hostile. This file states what such a page can
and cannot achieve, and where each guarantee is enforced.

## What a web page can do

* **Open Viddl, with a link of its choosing.** Viddl opens with the link in the
  box, ready to look at. Nothing is downloaded: the link is *shown*, never acted
  on. Browsers normally put a prompt in the way ("open Viddl?"), though how
  often it appears depends on the browser and on whether "always allow" was
  ticked — Viddl does not depend on it, and nothing here assumes a page is
  polite.
* **Make Viddl fetch that link's metadata.** Opening a hand-off probes the link
  with yt-dlp, which is one HTTP(S) request to the address the page chose. That
  is worth knowing because it is a request to a URL someone else picked: a page
  can use a hand-off to make your machine touch an address exactly as it could by
  getting you to click a link. It cannot choose the address of anything else,
  and it cannot make Viddl send a body, headers of its own choosing, or
  credentials.
* **Make Viddl open more than once.** Repeated hand-offs are each a fresh
  process. Viddl limits how many of them it *acts on* (below), but whether extra
  windows appear is the desktop environment's decision, not Viddl's.

## What it cannot do

| Attempt | Why it fails |
| --- | --- |
| Start a download | A hand-off only pre-fills the link box. A download begins when a quality is chosen, by a person, on the qualities screen. Held by `test_a_hand_off_never_starts_a_download`. |
| Pass an option where a link belongs | The link is the last argument and follows a `--` separator, so `--exec=…` and `--config-locations=…` arrive as links, not flags — and a value that is not a plain `http(s)` URL is refused before it gets that far, in `util.safe_video_link()`. |
| Use another scheme (`file:`, `javascript:`, `data:`) | Only `http` and `https` are accepted, at every entry point: the CLI, the `viddl://` parser, the app's link box, and the link the app starts with. |
| Smuggle settings in through a config file | Both yt-dlp invocations pass `--ignore-config`. Note this flag alone does not neutralise an explicit `--config-locations`; the `--` separator is what does. |
| Get a second hand-off acted on straight away | Hand-offs closer together than a few seconds are collapsed into one: the app still opens, without a link. Bursts are what a page in a loop looks like. |
| Slip something into the log | Every log line, tracebacks included, is passed through `util.redact()`, which removes credentials and signature parameters from links. |
| Read what Viddl installed or logged | The app's own folder and the folder holding yt-dlp and ffmpeg are `0700`, the log is `0600`, and the downloaded programs themselves are `0700`. |
| Have unverified bytes run | Every download is checked against a published SHA-256 (yt-dlp's `SHA2-256SUMS`, the ffmpeg builds' `checksums.sha256`, or a hash pinned in the source) *before* it is written to its final path. A mismatch is refused and deleted. A mirror that publishes only an MD5 is not used at all. |

Read from the other side, that is the promise: a link from a web page is only
ever *pre-filled*, and the worst a hostile page achieves is a window with a link
in it that you can ignore.

## Not defended against

* **The sites themselves, or what you download.** Viddl delegates all extraction
  to yt-dlp; a malicious page or a malicious video is yt-dlp's surface, not this
  program's.
* **Anything already running as you.** Viddl keeps its helpers and log private
  from *other accounts*; a process running as your user can read them, run them,
  and replace them.
* **A compromised release, rather than a tampered one.** Checksums prove the
  bytes are the bytes the project published — they do not make those bytes
  trustworthy. Where the download is pinned in the source (the macOS and fallback
  Linux ffmpeg binaries), it is fixed to one exact build.
* **A hostile page choosing the link is not a data leak by itself** — but the
  link is visible on screen, and the probe goes to whoever chose it. Do not click
  a hand-off you did not ask for.
* **Windows and macOS registration** are implemented but exercised only through
  simulated platform branches in tests; the running app's behaviour on those
  systems is not verified here.

## Deliberately not done

**Single-instance behaviour.** The obvious nuisance fix — a second hand-off
focusing the window that is already open — is not implemented, on purpose. The
cheap version needs a lock file with a process id, and after a crash or a reused
pid it can silently swallow a click, which is a worse failure than an extra
window. The reliable version needs either platform window APIs or a local
listening socket, and a socket is a new attack surface for a nuisance-level
problem. What exists instead is the cooldown: it limits how many links a page can
have acted on, and says plainly that it does not limit how many windows appear.

## Reporting

Open a private advisory through the repository's **Security → Report a
vulnerability** page, or an issue if it is not sensitive. Include the version
from `viddl --version`, the platform, and — if the log is involved — the lines
around the failure, which are redacted already but worth reading before posting.
