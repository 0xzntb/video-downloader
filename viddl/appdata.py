"""Where Viddl keeps its files and where downloads go.

Everything lives in one folder per user, so there is nothing to configure:

* Linux   ``~/.local/share/viddl`` (or ``$XDG_DATA_HOME/viddl``)
* macOS   ``~/Library/Application Support/viddl``
* Windows ``%LOCALAPPDATA%\\viddl``

``VIDDL_HOME`` overrides that folder and ``VIDDL_DOWNLOAD_DIR`` overrides the
output folder; ``VIDDL_YTDLP`` and ``VIDDL_FFMPEG`` point at the two programs
Viddl otherwise finds or fetches for itself, and ``VIDDL_YTDLP_ARGS`` adds
extra yt-dlp options on purpose.

All five are read through :func:`folder_override`, :func:`program_override` and
:func:`ytdlp_arguments`, which check what they are given before it is used. An
environment variable is not a command line: nobody sees it in the app, and in a
shared checkout or a CI job it can be set by something other than the person
using Viddl. So a value is treated as a request — one that can be refused, with
the refusal visible in ``viddl --doctor`` — rather than as an instruction.
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from . import APP_NAME

# The five variables Viddl reads, named once so the documentation, the doctor
# output and the tests cannot drift from the code that reads them.
ENV_HOME = "VIDDL_HOME"
ENV_DOWNLOAD_DIR = "VIDDL_DOWNLOAD_DIR"
ENV_YTDLP = "VIDDL_YTDLP"
ENV_FFMPEG = "VIDDL_FFMPEG"
ENV_YTDLP_ARGS = "VIDDL_YTDLP_ARGS"
ENVIRONMENT_VARIABLES = (
    ENV_HOME,
    ENV_DOWNLOAD_DIR,
    ENV_YTDLP,
    ENV_FFMPEG,
    ENV_YTDLP_ARGS,
)

# Options Viddl builds for itself, by short and long name. A value that sets one
# of these is refused rather than honoured: they decide where the file goes, what
# it is called and which quality is fetched — the things the qualities screen
# promised and the finished screen reports. Everything else is the user's to
# choose, which is the whole point of the escape hatch.
_OWNED_OPTIONS = {
    "-P": "--paths",
    "--paths": "--paths",
    "-o": "--output",
    "--output": "--output",
    "-f": "--format",
    "--format": "--format",
    "-a": "--batch-file",
    "--batch-file": "--batch-file",
    "-s": "--simulate",
    "--simulate": "--simulate",
    "--skip-download": "--skip-download",
    "--config-locations": "--config-locations",
    "--progress-template": "--progress-template",
}

# Viddl's own folders hold two downloaded programs, so they are for the user
# who installed them and nobody else on the machine.
_OWNER_ONLY = 0o700

# The log holds links, which can carry tokens and signed query strings, so it is
# closed to everyone but its owner as well.
_OWNER_PRIVATE = 0o600


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _is_macos() -> bool:
    return sys.platform == "darwin"


@dataclass(frozen=True)
class FolderProblem:
    """One of Viddl's own folders that could not be created, and why.

    ``line`` is for a screen, which has one line; ``details`` is for
    ``viddl --doctor``, which has room to say which folder and what to do.
    """

    path: Path
    error: OSError

    @property
    def reason(self) -> str:
        """"Permission denied", "Not a directory" — never an empty string."""
        return (self.error.strerror or str(self.error) or "unknown error").strip()

    @property
    def line(self) -> str:
        """The short version, for a screen with one line of room.

        It says what is wrong and where to read more, not how to fix it: the
        path and the way out are the doctor's job, and a sentence that fits in
        an 80-column terminal cannot hold all three.
        """
        return f"Cannot save Viddl's files ({self.reason}) — run `viddl --doctor`"

    @property
    def details(self) -> List[str]:
        """``--doctor`` lines: the folder, the reason, and the way out."""
        return [
            f"cannot create {self.path} ({self.reason})",
            "Check that you can write there, or set VIDDL_HOME to a folder you own.",
        ]


@dataclass(frozen=True)
class Override:
    """What one environment variable asked for, and what became of it."""

    name: str
    value: str
    problem: str = ""

    @property
    def used(self) -> bool:
        return not self.problem


def _asked(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _refuse(value: str) -> str:
    """Why this value cannot be used, or ``""`` when it can.

    The reasons are the same for every variable. A value that looks like an
    option could be read as one by a program Viddl hands it to — the lesson from
    the ``viddl://`` hand-off, applied to the environment — and a value carrying
    a control character would not survive being used as a path or written down.
    """
    if not value:
        return "empty"
    if value.startswith("-"):
        return "looks like an option, not a path"
    if any(character in value for character in "\x00\n\r"):
        return "contains a control character"
    return ""


def folder_override(name: str) -> Optional[Path]:
    """A folder from the environment, checked, or ``None`` to use the default.

    A relative path is read the way the person who set it would read it — from
    the working directory — and then resolved, so what the app actually uses is
    visible in ``--doctor`` instead of depending on where the app was started.
    A value naming something that exists and is not a folder is refused: it
    would fail later, and a confusing failure is worse than an ignored setting.
    """
    value = _asked(name)
    if not value or _refuse(value):
        return None
    path = Path(value).expanduser()
    try:
        if path.exists() and not path.is_dir():
            return None
        return path.resolve()
    except OSError:
        return None


def program_override(name: str) -> Optional[Path]:
    """A program from the environment, checked, or ``None``.

    A plain name means the ``PATH`` and never the working directory. That is the
    point of checking at all: an override is often set by something other than
    the person at the keyboard, and if ``yt-dlp`` silently meant ``./yt-dlp``
    then whoever could write to that directory could choose the program Viddl
    runs. A value with a directory in it is used as a path, expanded and made
    absolute, and has to be a real executable file.
    """
    value = _asked(name)
    if not value or _refuse(value):
        return None
    if not any(separator in value for separator in (os.sep, "/", "\\")):
        return which(value)
    path = Path(value).expanduser()
    try:
        path = path.resolve()
    except OSError:
        return None
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    return path


def _owned_option(token: str) -> str:
    """The option this token sets, when it is one Viddl decides for itself.

    Long options are matched by abbreviation too, because yt-dlp's parser
    accepts them: ``--path /tmp`` means ``--paths``, and a guard that only knew
    the full spelling would be a guard you can walk around by typing less.
    """
    name = token.split("=", 1)[0]
    if name in _OWNED_OPTIONS:
        return _OWNED_OPTIONS[name]
    # Short options take their value attached, as in ``-P/tmp`` or ``-fbest``.
    if len(token) > 2 and token[:2] in _OWNED_OPTIONS:
        return _OWNED_OPTIONS[token[:2]]
    if name.startswith("--"):
        for owned in sorted(set(_OWNED_OPTIONS.values())):
            if owned.startswith(name):
                return owned
    return ""


def _split(value: str) -> List[str]:
    """Split the value the way the platform's shell would, for the common cases.

    POSIX quoting everywhere would eat the backslashes of a Windows path —
    ``--cookies C:\\Users\\me\\cookies.txt`` would arrive as
    ``C:Usersmecookies.txt`` — so there the quotes are kept by ``shlex`` and
    stripped here instead. Either way nothing is expanded or run.
    """
    if _is_windows():
        return [token.strip('"').strip("'") for token in shlex.split(value, posix=False)]
    return shlex.split(value)


def _arguments(value: str) -> Tuple[List[str], str]:
    """The options ``VIDDL_YTDLP_ARGS`` asks for, and why they cannot be used.

    Split the way a shell splits them, because that is what a person writing a
    list of options expects, and checked one token at a time. Refusing the whole
    value on the first bad token is deliberate: half a command line is a
    command line nobody wrote.
    """
    if not value:
        return [], "empty"
    if any(character in value for character in "\x00\n\r"):
        return [], "contains a control character"
    try:
        tokens = _split(value)
    except ValueError:
        return [], "unbalanced quotes"
    if not tokens:
        return [], "empty"
    for token in tokens:
        if token == "--":
            return [], "would end Viddl's own options early"
        if "://" in token or token.startswith("www."):
            # A link is not a setting: it would be a second thing to download,
            # and Viddl would report one file. A word without an option in
            # front of it is left to yt-dlp to complain about, because that is
            # exactly what an option's value looks like (``--limit-rate 2M``).
            return [], f"contains a link, not an option: {token}"
        owned = _owned_option(token)
        if owned:
            return [], f"sets {owned}, which Viddl decides"
    return tokens, ""


def ytdlp_arguments() -> List[str]:
    """Extra yt-dlp options from the environment, checked; empty if refused."""
    return _arguments(_asked(ENV_YTDLP_ARGS))[0]


def ytdlp_arguments_problem() -> str:
    """Why ``VIDDL_YTDLP_ARGS`` cannot be used, or ``""`` when it can.

    Unset is not a problem: it is the ordinary case. Set and useless — empty,
    unbalanced, or asking for something Viddl decides — is.
    """
    if ENV_YTDLP_ARGS not in os.environ:
        return ""
    return _arguments(_asked(ENV_YTDLP_ARGS))[1]


def overrides() -> List[Override]:
    """Every environment variable that is set, and what became of it.

    Only the ones that are set appear: an empty list is the common case, and
    saying nothing is the honest way to say that nothing was overridden.
    """
    found: List[Override] = []
    for name in ENVIRONMENT_VARIABLES:
        if name not in os.environ:
            continue
        value = _asked(name)
        if name == ENV_YTDLP_ARGS:
            problem = ytdlp_arguments_problem()
        else:
            problem = _refuse(value)
        if not problem:
            if name in (ENV_YTDLP, ENV_FFMPEG):
                problem = "" if program_override(name) else "not a program Viddl can run"
            elif name != ENV_YTDLP_ARGS:
                problem = "" if folder_override(name) else "not a folder"
        found.append(Override(name, value, problem))
    return found


def environment_report() -> List[str]:
    """``viddl --doctor`` lines for the overrides in effect, or nothing."""
    lines: List[str] = []
    for index, entry in enumerate(overrides()):
        label = "Environment:" if index == 0 else ""
        verdict = "in use" if entry.used else f"ignored — {entry.problem}"
        lines.append(f"{label:<14}{entry.name}={entry.value} ({verdict})")
    return lines


def data_dir() -> Path:
    """The one folder Viddl owns."""
    override = folder_override(ENV_HOME)
    if override is not None:
        return override

    if _is_windows():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if _is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME

    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME


def bin_dir() -> Path:
    """Where the yt-dlp and ffmpeg helpers are kept."""
    return data_dir() / "bin"


def log_path() -> Path:
    """A plain-text log of the last run, handy when something goes wrong."""
    return data_dir() / "viddl.log"


def _not_ours_to_tighten(path: Path) -> bool:
    """Whether this folder belongs to someone else, or to everyone.

    ``VIDDL_HOME`` can point anywhere, and the two places where ``chmod 0700``
    would be a disaster rather than a precaution are the root directory and the
    user's home directory itself. Those are skipped; every other folder is
    Viddl's to lock down.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return True
    if resolved == Path(resolved.anchor):
        return True
    try:
        return resolved == Path.home().resolve()
    except (OSError, RuntimeError):
        return False


def _secure_dir(path: Path) -> None:
    """Create one of Viddl's folders, reachable only by its owner.

    The mode is applied after the folder exists, because ``mkdir``'s is masked
    by the umask and a folder made by an earlier version keeps whatever it was
    given then — so an upgrade tightens what is already on disk. Windows is
    skipped rather than pretended about: it governs access with ACLs, and the
    POSIX bits ``chmod`` accepts there do not mean this.

    A folder that cannot be created raises, as before; a mode that cannot be set
    does not, since a filesystem without permissions is not a reason to refuse
    to run.
    """
    path.mkdir(parents=True, exist_ok=True)
    if _is_windows() or _not_ours_to_tighten(path):
        return
    try:
        if stat.S_IMODE(path.stat().st_mode) != _OWNER_ONLY:
            os.chmod(path, _OWNER_ONLY)
    except OSError:
        pass


def ensure_dirs() -> Optional[FolderProblem]:
    """Create Viddl's folders, and report the one that could not be made.

    Nothing here raises: a read-only home must not crash every code path that
    only wants a path. But it must not be *forgotten* either, which is what it
    used to be — the first sign of it was a failed download talking about
    something else. The failure is returned, so ``viddl --doctor`` and the home
    screen can say what is actually wrong, and so can anyone else who cares.

    ``None`` means both folders are there. Only failing to *create* one is
    reported: a filesystem that cannot hold a permission bit is not a problem
    worth refusing to run over (see :func:`_secure_dir`).
    """
    first: Optional[FolderProblem] = None
    for path in (data_dir(), bin_dir()):
        try:
            _secure_dir(path)
        except OSError as exc:
            if first is None:
                first = FolderProblem(path, exc)
    return first


def secure_log_file() -> Optional[Path]:
    """The log file: created if it is missing, readable only by its owner.

    An existing log left looser by an earlier version is tightened here, on the
    same reasoning as the folders in :func:`ensure_dirs`. ``None`` means there
    is nowhere to put it — a missing log is a lost nicety, not a reason for
    Viddl to refuse to run, so the caller carries on without one.
    """
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _OWNER_PRIVATE)
        os.close(descriptor)
        if not _is_windows() and stat.S_IMODE(path.stat().st_mode) != _OWNER_PRIVATE:
            os.chmod(path, _OWNER_PRIVATE)
    except OSError:
        return None
    return path


def _xdg_download_dir() -> Optional[Path]:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    config_file = (Path(config_home) if config_home else Path.home() / ".config") / "user-dirs.dirs"
    try:
        text = config_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("XDG_DOWNLOAD_DIR"):
            continue
        _, _, raw = line.partition("=")
        raw = raw.strip().strip('"')
        if not raw:
            continue
        expanded = raw.replace("$HOME", str(Path.home()))
        return Path(expanded)
    return None


def downloads_dir() -> Path:
    """The user's Downloads folder, created if needed."""
    override = folder_override(ENV_DOWNLOAD_DIR)
    if override is not None:
        path = override
    elif _is_windows():
        home = os.environ.get("USERPROFILE") or str(Path.home())
        path = Path(home) / "Downloads"
    else:
        path = _xdg_download_dir() or (Path.home() / "Downloads")

    try:
        path.mkdir(parents=True, exist_ok=True)
        # Verify we can actually write there before advertising it.
        if not os.access(path, os.W_OK):
            raise OSError("not writable")
    except OSError:
        # Our own folder is the fallback, and it is ours to lock down too: what
        # is downloaded here is nobody else's business.
        fallback = data_dir() / "Downloads"
        _secure_dir(fallback)
        return fallback
    return path


def which(program: str) -> Optional[Path]:
    """``shutil.which`` that also looks in a few places GUI apps often miss."""
    found = shutil.which(program)
    if found:
        return Path(found)

    extra = [
        Path.home() / ".local" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/snap/bin"),
    ]
    for folder in extra:
        candidate = folder / program
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None
