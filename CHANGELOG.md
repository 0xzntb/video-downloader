# Changelog

What changed in Viddl, newest first, written for someone deciding whether to
update: what it does now, what it did before, and what that costs.

The format is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
numbers are the `version` in `pyproject.toml`.

**Nothing has been tagged yet.** Everything the current version does sits under
*Unreleased*. The first release will move that section down under its number and
date, and later work will collect under a fresh *Unreleased* heading until the
next one — so a reader can always tell what they have from what they do not.

## [Unreleased]

### Added

- **The app.** A full-screen terminal program that turns a pasted link into a
  file: a link box, a qualities list with a size next to each row, a progress bar
  with speed and time left, and the saved file's full path at the end. Mouse and
  keyboard both work everywhere. It is one runtime dependency, `textual` — the
  rest is the standard library.
- **A quality list you can read before choosing.** Rows are grouped by what they
  are (video with sound, video alone, sound alone), and a size is marked as an
  estimate (`~1.6 MB`) when it is one — a figure that a conversion will change.
- **Downloads that survive a bad connection.** An interrupted download resumes
  where it stopped instead of starting again from zero, and *Cancel* really
  stops: the engine is ended and the partial file is left as a file rather than
  keep being written by a process nobody is watching.
- **The engine finds, installs and updates itself.** `viddl --update` fetches a
  newer yt-dlp, which is the answer to "this link worked last week". Stale
  engines are mentioned on screen once they are a month old.
- **Nothing downloaded is trusted blindly.** Every helper Viddl fetches — yt-dlp,
  ffmpeg — is checked against a published SHA-256 before it is written to its
  final path; a mismatch is refused and deleted, and a mirror that publishes only
  an MD5 is not used at all. The app's own folder is `0700` and its log is
  `0600`.
- **ffmpeg, offered rather than demanded.** Rows that need it say so, and a
  button installs it if it is missing; the copy Viddl installs is verified and
  kept private like the engine.
- **Audio only**, as the original M4A with no re-encode, or as MP3, Opus or FLAC
  at chosen bitrates.
- **Subtitles, details and cover images** can be ticked on the qualities screen
  and land beside the file.
- **Playlists.** A playlist link offers a checklist of its videos (up to 200, and
  it says how many were left out rather than pretending to be complete), then
  downloads them one at a time — four at once is not faster on a throttled
  account, and it makes "which one is this?" unanswerable. A stopped queue keeps
  what it has and *Download the rest* carries on without fetching the finished
  files again.
- **Search by name.** Type words instead of a link and Viddl looks them up, so
  the app is not a box that rejects everything that is not a URL.
- **Sites that want you signed in.** A *Sign in with my browser* row lets yt-dlp
  use the cookie store the browser already has, for one site, for that download.
  Viddl never reads a cookie and writes nothing down.
- **Live streams** are recorded from the current point, or from the start when
  asked; stopping keeps what arrived as a real file.
- **A browser hand-off.** An extension (Chrome/Chromium and Firefox, both bundled
  and installable from the app) sends the page's link to the app. A hand-off only
  *pre-fills* the link box: it never starts a download, and repeated hand-offs in
  a burst are collapsed into one. `SECURITY.md` states what a hostile page can
  and cannot achieve, and where each guarantee is enforced.
- **`viddl --web`**: a local page, bound to `127.0.0.1`, behind a random token
  that is compared with `compare_digest`, refusing foreign `Origin`s and any
  `Host` but loopback. A convenience for a browser tab, not a service.
- **A scriptable path.** `--plain` prints line-by-line progress with no
  full-screen drawing, `--yes` takes the best row without asking, and `--json`
  answers in a shape another program can read. Accents are folded to ASCII so a
  legacy console can show them.
- **Explanations, not exit codes.** Twenty-one rules turn the engine's own words
  into a sentence with something to do next — private video, deleted video, a
  login the site wants, an address that does not resolve, a full disk — and
  `viddl --doctor` prints the version, the engines, the folders, the language,
  the network waits and which environment variables are in force.
- **English and Spanish.** A Spanish computer gets Spanish explanations without
  configuring anything, `--lang` chooses per run, and an unsupported language is
  refused rather than quietly answered in English. The screens' own words are
  still English, which the README says plainly rather than implying otherwise.
- **Three colour schemes** — dark, light and high-contrast, or `auto` to follow
  the terminal — with contrast measured rather than eyeballed. `NO_COLOR` and
  `TERM=dumb` are obeyed, and the full-screen app has a screen-reader-friendly
  path.
- **It remembers the useful things and no more.** The last fifty completed
  downloads, the folder you chose, and settings only if you write the optional
  config file. A link is written down only when a download of it finishes, since
  links can carry signed tokens.
- **A window title that says how it is going** — percentage, position in a
  playlist and the video's name, cleaned of anything that could reorder or
  escape the title.
- **Installers for both platforms** that work for one account: a private virtual
  environment, a launcher on the user `PATH`, and the `viddl://` handler.
  `uninstall.sh` / `uninstall.ps1` take it all back out again.
