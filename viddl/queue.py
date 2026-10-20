"""Downloading several videos, one after another (F2).

A playlist is a list of downloads, and a list wants three things a single
download does not: somewhere to say which one is running, an honest count when
it ends, and the promise that one bad video costs one item rather than the
whole list. This module is that promise, kept in one place so the terminal app
and ``--plain`` cannot disagree about it.

The downloads are sequential on purpose. Running four at once looks faster and
is not: the site throttles the account, the disk writes four ``.part`` files at
once, and every progress line belongs to a different video. One at a time means
the screen has exactly one answer to "how long now?" — and resuming a stopped
queue costs almost nothing, because each finished file is already in place and
yt-dlp recognises it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .download import DownloadCancelled, DownloadJob, Progress
from .engine import Engine, ToolError
from .formats import MediaOption, Playlist, PlaylistEntry
from .util import safe_video_link

#: A video the queue has not reached yet.
WAITING = "waiting"
#: The video being downloaded right now.
RUNNING = "running"
#: A file was saved.
DONE = "done"
#: Viddl tried and the site said no. The queue carries on.
FAILED = "failed"
#: Never attempted: the user stopped the queue first.
SKIPPED = "skipped"
#: Attempted and stopped mid-download, like the single-download case.
STOPPED = "stopped"


@dataclass
class QueueItem:
    """One video in the queue, and what became of it."""

    entry: PlaylistEntry
    #: Where this sits in the queue, from 1. Not the same as ``entry.index``
    #: when the user picked a few out of the middle of a playlist.
    position: int
    state: str = WAITING
    path: Optional[Path] = None
    error: Optional[ToolError] = None

    @property
    def title(self) -> str:
        return self.entry.title or f"Video {self.entry.index}"

    #: How many videos are in the queue, filled in by the queue before the run
    #: so a label needs no argument.
    total: int = 0

    @property
    def label(self) -> str:
        """``3/9  Title`` — the count first, because it is what people ask."""
        return f"{self.position}/{self.total}  {self.title}"

    @property
    def finished(self) -> bool:
        return self.state in (DONE, FAILED, SKIPPED, STOPPED)


@dataclass
class QueueSummary:
    """How a queue ended, in the terms the user cares about."""

    total: int
    done: int
    failed: int
    skipped: int
    stopped: bool
    items: List[QueueItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.done > 0 and not self.failed

    @property
    def headline(self) -> str:
        """One sentence that is true whether it went well or not."""
        if self.total == 1:
            if self.done:
                return "The video was saved."
            if self.stopped:
                return "The download was stopped."
            return "The video could not be downloaded."
        if self.stopped and self.done:
            return f"Stopped after saving {self.done} of {self.total} videos."
        if self.stopped:
            return f"Stopped before saving anything of {self.total} videos."
        if self.failed and self.done:
            return f"Saved {self.done} of {self.total} videos; {self.failed} could not be downloaded."
        if self.failed:
            return f"None of the {self.total} videos could be downloaded."
        return f"Saved all {self.done} videos."


class QueueJob:
    """A list of videos, downloaded in order, stoppable at any point."""

    def __init__(
        self,
        engine: Engine,
        playlist: Playlist,
        entries: Sequence[PlaylistEntry],
        option: MediaOption,
        out_dir: Path,
        cookies: str = "",
        make_job: Callable[..., DownloadJob] = DownloadJob,
        on_start: Optional[Callable[[QueueItem], None]] = None,
        on_progress: Optional[Callable[[QueueItem, Progress], None]] = None,
        on_end: Optional[Callable[[QueueItem], None]] = None,
    ) -> None:
        self.engine = engine
        self.playlist = playlist
        self.option = option
        self.out_dir = Path(out_dir)
        self.cookies = cookies
        self._make_job = make_job
        self.on_start = on_start
        self.on_progress = on_progress
        self.on_end = on_end
        total = len(entries)
        self.items: List[QueueItem] = []
        for position, entry in enumerate(entries, start=1):
            self.items.append(QueueItem(entry=entry, position=position, total=total))
        self._cancelled = threading.Event()
        self._job: Optional[DownloadJob] = None

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    # -- stopping -----------------------------------------------------------
    def cancel(self, force: bool = False) -> None:
        """Stop the queue: the current video is cancelled, the rest not started.

        The current download keeps its ``.part`` exactly as a single download
        does, so a queue stopped at video seven resumes with six files already
        in place — and the one it was in the middle of carries on too.
        """
        self._cancelled.set()
        job = self._job
        cancel = getattr(job, "cancel", None)
        if callable(cancel):
            cancel(force=force)

    # -- running ------------------------------------------------------------
    def run(self) -> QueueSummary:
        """Download every item in order, and report how it went.

        A failed video does not end the queue. A site that has deleted one video
        of forty is not a reason to keep the other thirty-nine, and the summary
        names the ones that did not work so nothing is quietly missing.
        """
        for item in self.items:
            if self._cancelled.is_set():
                item.state = SKIPPED
                continue
            item.state = RUNNING
            self._notify(self.on_start, item)
            try:
                path = self._download(item)
            except DownloadCancelled:
                # Stopped by the user, which is not a failure to report. The
                # loop keeps going so that every video after this one is marked
                # skipped: a list with three "waiting" items at the end reads
                # as work still to come, which is not what stopping means.
                item.state = STOPPED
                self._cancelled.set()
                self._job = None
                self._notify(self.on_end, item)
                continue
            except ToolError as exc:
                item.state = FAILED
                item.error = exc
                self._job = None
                self._notify(self.on_end, item)
                continue
            item.path = path
            item.state = DONE
            self._job = None
            self._notify(self.on_end, item)
        return self.summary()

    def _download(self, item: QueueItem) -> Path:
        """Run one item, asking the playlist for it when the link is not one."""
        entry = item.entry
        link = safe_video_link(entry.url)
        kwargs = {
            "on_progress": lambda progress: self._notify(self.on_progress, item, progress),
            "cookies": self.cookies,
        }
        if link is not None:
            job = self._make_job(self.engine, link, self.option, self.out_dir, **kwargs)
        else:
            # The listing gave the site's own identifier rather than an
            # address. Asking the playlist for item N is the same video, and
            # the alternative — refusing — would fail whole sites whose
            # listings are shaped that way.
            playlist_link = safe_video_link(self.playlist.url)
            if playlist_link is None:
                raise ToolError(
                    "This video could not be downloaded by itself.",
                    "Open the playlist in Viddl again and choose it from the list.",
                )
            kwargs["playlist_item"] = entry.index
            job = self._make_job(
                self.engine, playlist_link, self.option, self.out_dir, **kwargs
            )
        self._job = job
        return job.run()

    def summary(self) -> QueueSummary:
        counts = {state: 0 for state in (DONE, FAILED, SKIPPED, STOPPED)}
        for item in self.items:
            counts[item.state] = counts.get(item.state, 0) + 1
        return QueueSummary(
            total=self.total,
            done=counts[DONE],
            failed=counts[FAILED],
            skipped=counts[SKIPPED] + counts[STOPPED],
            stopped=self._cancelled.is_set(),
            items=list(self.items),
        )

    def _notify(self, callback: Optional[Callable[..., None]], *args) -> None:
        """Call a UI callback when there is one.

        A screen that went away is not a reason to fail a download, which is
        why the callable is optional rather than required.
        """
        if callback is None:
            return
        callback(*args)
