"""Claiming a name or a folder, so two runs cannot write to the same one.

A claim is a file created with ``O_EXCL``: creating it is the test and the claim
in one step, with no window in between for two runs to both decide the same name
is free. What the file *says* is only ever used to tell a claim still being
honoured from one a crash left behind — the process id and the moment it was
made — so a run killed between claiming a name and filling it cannot keep it
from every run after that.

Two things are claimed this way: the working folder a download writes in
(:data:`FOLDER_CLAIM`), and the name a finished file is delivered under
(:data:`NAME_CLAIM_PREFIX`, kept *beside* that name so the name itself is only
ever created by the move that fills it). The rules for both live here, apart
from the download job, because they are about the filesystem rather than about
yt-dlp: they can be tested against a temporary folder with nothing else running.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import time
from pathlib import Path

from .engine import ToolError, is_windows

#: Marks a working folder as being written in right now. Creating it is the
#: test and the claim in one step, which is what keeps two windows apart.
FOLDER_CLAIM = ".viddl-busy"

#: Marks the right to deliver a name in the downloads folder. Kept beside that
#: name rather than on it, so a run killed between claiming and moving leaves
#: nothing a person would mistake for a finished video.
NAME_CLAIM_PREFIX = ".viddl-name-"

#: How long a claim is believed when its owner cannot be checked. A process id
#: can be reused by an unrelated program, and versions before this one wrote no
#: owner at all, so age is the fallback that cannot be fooled.
CLAIM_TTL = 12 * 3600


def _windows_process_alive(pid: int) -> bool:
    """Windows' answer to ``os.kill(pid, 0)``, without the kill.

    ``OpenProcess`` succeeds on a process that has exited while its parent
    still holds a handle to it, so the exit code decides: ``STILL_ACTIVE`` is
    the only answer that means "running". A missing handle means either no such
    process or (``ERROR_ACCESS_DENIED``) one that belongs to somebody else.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists
    try:
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_alive(pid: int) -> bool:
    """Whether a process with this id still exists, on either platform.

    ``os.kill(pid, 0)`` is the POSIX way, but on Windows it is not a probe: a
    signal other than the two console events terminates the process.
    """
    if pid <= 0:
        return False
    if is_windows():
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:  # pragma: no cover - a kernel that says something else
        return True
    return True


def claim_is_stale(claim: Path) -> bool:
    """Whether a claim was left by a run that is no longer writing here.

    The claim names its process and the time it was made. One whose process has
    exited is free immediately; one that cannot be read (older versions wrote an
    empty file) or that has outlived :data:`CLAIM_TTL` is written off as well,
    because a process id may have been reused since.
    """
    try:
        pid_text, born_text = claim.read_text(encoding="ascii").split()
        pid, born = int(pid_text), float(born_text)
    except (OSError, ValueError):
        pid, born = 0, 0.0
    now = time.time()
    if pid > 0 and math.isfinite(born) and now - born <= CLAIM_TTL:
        return not process_alive(pid)
    try:
        return now - claim.stat().st_mtime > CLAIM_TTL
    except OSError:
        return False


def mark_claim(handle: int) -> None:
    """Write down who claimed it and when, for the run that comes after this one.

    The file's existence is the claim; these two numbers only let a later run
    tell a claim that is still honoured from one a crash left behind. A claim
    that cannot be written to is still a claim — the age of the file answers
    for it — so this never raises.
    """
    with contextlib.suppress(OSError):
        os.write(handle, f"{os.getpid()} {time.time():.0f}\n".encode("ascii"))


def name_claim(out_dir: Path, filename: str) -> Path:
    """Where the right to deliver ``filename`` in ``out_dir`` is written down.

    A hidden neighbour, hashed so that a long or awkward title cannot make a
    name this module has to quote or truncate, and dot-prefixed so that it is
    never the name the user is looking for.
    """
    digest = hashlib.sha1(filename.encode("utf-8")).hexdigest()[:12]
    return out_dir / f"{NAME_CLAIM_PREFIX}{digest}"


def reap_name_claim(reservation: Path) -> bool:
    """Throw away a reservation left by a run that is gone; is the name free?

    ``False`` means somebody may still be delivering that name — the run
    holding the reservation wrote it a moment ago — and it must not be taken
    from under them.
    """
    if not claim_is_stale(reservation):
        return False
    with contextlib.suppress(OSError):
        reservation.unlink()
    return True


class OutputClaimer:
    """The right to a name in one folder, and the giving back of it.

    One folder, because that is what a claim is about: the first free name is
    found by trying names in order, and the claim on each is made with
    ``O_EXCL`` so no two runs can both decide it is free. Nothing here knows
    what is being downloaded — only which name it may be delivered under.
    """

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = Path(out_dir)

    def claim(self, filename: str) -> Path:
        """Reserve the first free name for ``filename`` in the output folder.

        O_EXCL is the whole point: the check and the claim are one step, so no
        two jobs can both decide a name is free and then both write to it.

        What is created is the hidden reservation beside the name, not an empty
        file under it — see :meth:`viddl.download.DownloadJob._deliver`. A
        reservation whose owner is gone is reaped rather than respected, so a
        crash cannot block the name for the runs that come after it.
        """
        stem, suffix = os.path.splitext(filename)
        for number in range(1, 1000):
            name = filename if number == 1 else f"{stem} ({number}){suffix}"
            target = self.out_dir / name
            reservation = name_claim(self.out_dir, name)
            if target.exists():
                # A finished file owns the name. A reservation beside it was
                # left by the run that delivered it, and is litter now.
                reap_name_claim(reservation)
                continue
            try:
                handle = os.open(
                    reservation, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
            except FileExistsError:
                # Somebody is delivering this name right now, or a run that is
                # gone reserved it and never came back.
                if not reap_name_claim(reservation):
                    continue
                try:
                    handle = os.open(
                        reservation, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                    )
                except OSError:
                    # Two runs noticed the same dead reservation; the other won.
                    continue
            mark_claim(handle)
            os.close(handle)
            return target
        raise ToolError(
            "There are already too many files with that name.",
            f"Tidy up the ones you do not need in {self.out_dir} and try again.",
        )

    def release(self, target: Path) -> None:
        """Give up the reservation for ``target``: the file itself holds it now.

        Called once the move has either filled the name or failed, so a
        delivery that worked does not leave a hidden reservation behind and one
        that did not keeps the name free for the next attempt.
        """
        with contextlib.suppress(OSError):
            name_claim(self.out_dir, target.name).unlink()
