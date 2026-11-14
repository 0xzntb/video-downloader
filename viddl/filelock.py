"""One writer's turn at a file, across processes and not only across threads.

Two Viddl windows are a supported arrangement (R6, concurrent downloads), and
the recent list and the settings file are both read-modify-written: read the
whole file, change it, write the whole thing back. A ``threading.Lock`` orders
that inside one process and says nothing about the other, so two windows can
each read the same old contents and the second write silently drops the first
one's row.

The lock is the operating system's own, taken on a sibling of the file it
guards: the data file is replaced by ``os.replace``, which gives it a new
identity, so a lock taken on the file itself would guard an object no other
process can see. It is held for the whole read-modify-write, not for the write
alone — a write that starts from stale contents loses them just as surely.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Iterator

#: The mode the lock file is opened at. It holds no data of its own; the mode
#: matches the files beside it, because everything in these folders is the
#: owner's.
_OWNER_PRIVATE = 0o600

#: How long a Windows attempt waits before trying again. ``msvcrt`` has no
#: blocking byte-range lock, so waiting is a poll: short enough that nobody
#: feels it, long enough not to spin a core.
_RETRY_SECONDS = 0.02

_WINDOWS = os.name == "nt"


@contextlib.contextmanager
def held(path: Path) -> Iterator[None]:
    """Hold this process's turn at ``path`` until the block ends.

    The folder is made first, so the first write of a fresh install can take
    its turn like any other. A folder or lock file that cannot be made raises
    to the caller: a writer that cannot take its turn must not write and
    pretend it did.

    The lock file stays behind on purpose. Unlinking it while another process
    is waiting would turn that wait into a lock on a name nobody else will ever
    take, and the second writer would then write beside the first instead of
    after it — the bug this exists to stop.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, _OWNER_PRIVATE)
    try:
        if _WINDOWS:
            _take_on_windows(descriptor)
        else:
            _take_on_posix(descriptor)
        yield
    finally:
        if _WINDOWS:
            _drop_on_windows(descriptor)
        else:
            _drop_on_posix(descriptor)
        os.close(descriptor)


def _take_on_posix(descriptor: int) -> None:
    import fcntl

    # Blocking: the other window is finishing a write, not broken, and waiting
    # a moment beats losing the row it is about to save.
    fcntl.flock(descriptor, fcntl.LOCK_EX)  # type: ignore[attr-defined]


def _drop_on_posix(descriptor: int) -> None:
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _take_on_windows(descriptor: int) -> None:
    import msvcrt

    # ``msvcrt`` locks a byte range and will not lock a range that is not
    # there, so an empty lock file is given one byte on its first use. The
    # byte is never read; only its existence makes the lock possible.
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\0")
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            # LK_NBLCK gives up immediately rather than blocking, and another
            # window's write is a reason to wait rather than to fail.
            time.sleep(_RETRY_SECONDS)


def _drop_on_windows(descriptor: int) -> None:
    import msvcrt

    os.lseek(descriptor, 0, os.SEEK_SET)
    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
