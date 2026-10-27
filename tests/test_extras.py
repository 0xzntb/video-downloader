"""The extras a download can carry: subtitles, details and a cover (F5).

Everything here is about additions to a download, not the download itself: the
three switches, the arguments each one turns into, where a cover picture is put,
and the rules that keep a picture or a caption file from being mistaken for the
video the user asked for.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from viddl import download as download_mod
from viddl.download import DownloadJob
from viddl.engine import Tool
from viddl.extras import EXTRAS, Extras
from viddl.formats import MediaOption
from viddl.queue import QueueJob


class FakeEngine:
    """The command builder only needs the tool's command prefix."""

    def __init__(self):
        self._tool = Tool("yt-dlp", ["/fake/yt-dlp"], "viddl", version="fake")

    def ensure_ytdlp(self, **kwargs):
        return self._tool

    def ffmpeg_location_args(self):
        return []

    def find_ffmpeg(self):
        return None


def video_option() -> MediaOption:
    return MediaOption(
        key="v1080",
        label="1080p",
        kind="video",
        needs_ffmpeg=True,
        format_selector="137+140",
        merge_container="mp4",
    )


def progressive_option() -> MediaOption:
    """A ready-to-play row: it needs no ffmpeg of its own."""

    return MediaOption(
        key="v360",
        label="360p",
        kind="video",
        needs_ffmpeg=False,
        format_selector="18",
        note="MP4 · ready to play",
    )


def original_audio_option() -> MediaOption:
    return MediaOption(
        key="orig",
        label="Audio only · original M4A",
        kind="audio",
        needs_ffmpeg=False,
        format_selector="140",
        audio_format="M4A",
        note="no re-encode",
    )


def make_job(tmp_path, option=None, extras=None) -> DownloadJob:
    return DownloadJob(
        FakeEngine(),
        "https://x.invalid/v",
        option or video_option(),
        tmp_path / "Downloads",
        extras=extras,
    )


# ---------------------------------------------------------------------------
# the switches
# ---------------------------------------------------------------------------


def test_nothing_is_added_unless_it_is_asked_for():
    extras = Extras()

    assert not extras
    assert extras.args_for(video_option()) == []
    assert extras.needs_ffmpeg_for(video_option()) is False
    assert extras.summary(video_option()) == "none"
    # "Nothing switched on" is an answer, not a missing one.
    assert extras.skipped() == []
    assert extras.applicable() == []


def test_the_switches_are_the_three_things_yt_dlp_can_really_do():
    """Five flags in the suggestion, three behaviours in the tool (F5).

    ``--write-subs``/``--embed-subs`` are one decision, and ``--embed-chapters``
    is already implied by ``--embed-metadata``. Offering four switches would
    mean two that do nothing on their own.
    """

    assert [extra.key for extra in EXTRAS] == ["subs", "details", "cover"]


def test_switching_one_on_and_off_leaves_the_others_alone():
    extras = Extras()

    with_subs = extras.toggled("subs")
    with_both = with_subs.toggled("details")

    assert extras.keys == frozenset()
    assert with_subs.keys == frozenset({"subs"})
    assert with_both.keys == frozenset({"subs", "details"})
    assert with_both.toggled("subs").keys == frozenset({"details"})
    assert "subs" in with_both and "cover" not in with_both


def test_a_key_that_is_not_an_extra_cannot_be_switched():
    with pytest.raises(ValueError):
        Extras().toggled("nonsense")


def test_the_command_line_words_are_forgiving():
    assert Extras.parse("subs").keys == frozenset({"subs"})
    assert Extras.parse("subs, cover").keys == frozenset({"subs", "cover"})
    assert Extras.parse("subtitles  thumbnail").keys == frozenset({"subs", "cover"})
    # The word yt-dlp uses is not the row's name, so both are accepted.
    assert Extras.parse("metadata").keys == frozenset({"details"})
    assert Extras.parse("chapters").keys == frozenset({"details"})
    assert Extras.parse("all").keys == frozenset({"subs", "details", "cover"})
    assert Extras.parse("none").keys == frozenset()
    assert Extras.parse("  ").keys == frozenset()


def test_a_word_that_is_not_an_extra_is_refused_with_the_list():
    with pytest.raises(ValueError) as caught:
        Extras.parse("subs,pictures")

    message = str(caught.value)
    assert "Unknown extra 'pictures'" in message
    assert "subs, details or cover" in message


def test_the_summary_reads_like_a_sentence():
    extras = Extras.parse("subs,details,cover")

    assert extras.summary(video_option()) == "subtitles, details and cover image"
    assert Extras.parse("cover").summary(video_option()) == "cover image"


# ---------------------------------------------------------------------------
# the arguments each switch becomes
# ---------------------------------------------------------------------------


def test_subtitles_ask_only_for_english():
    """One language, because a translation that 429s ends the whole download.

    Measured with ``--sub-langs "en.*"``: yt-dlp asked for "en", "en-en" and
    "en-de", got a 429 on the last, and stopped before fetching the video.
    """

    args = Extras.parse("subs").args_for(video_option())

    assert "--embed-subs" in args
    assert "--write-auto-subs" in args
    assert args[args.index("--sub-langs") + 1] == "en,en-US,en-orig"


def test_details_are_one_switch_because_yt_dlp_makes_them_one():
    args = Extras.parse("details").args_for(video_option())

    assert args == ["--embed-metadata"]
    assert "--embed-chapters" not in args


def test_a_cover_is_saved_beside_the_file_not_embedded():
    """The engine Viddl installs has no mutagen and no ffprobe (F5).

    Measured: ``--embed-thumbnail`` on a real MP4 fails the whole download with
    "Unable to embed using ffprobe & ffmpeg". Writing the picture beside the
    file works with the tools Viddl actually installs, so that is what the row
    promises.
    """

    args = Extras.parse("cover").args_for(video_option())

    assert "--write-thumbnail" in args
    assert args[args.index("--convert-thumbnails") + 1] == "jpg"
    assert "--embed-thumbnail" not in args


def test_subtitles_are_not_asked_for_on_an_audio_download():
    extras = Extras.parse("subs,details")
    audio = original_audio_option()

    assert extras.skipped(audio) == [EXTRAS[0]]
    assert extras.applicable(audio) == [EXTRAS[1]]
    assert "--embed-subs" not in extras.args_for(audio)
    assert "--embed-metadata" in extras.args_for(audio)
    # A cover picture is the one thing an audio file can carry, so it stays.
    assert extras.needs_ffmpeg_for(original_audio_option()) is True


def test_a_video_gets_the_subtitles_it_was_offered():
    extras = Extras.parse("subs")
    assert extras.skipped(video_option()) == []
    assert extras.needs_ffmpeg_for(video_option()) is True


# ---------------------------------------------------------------------------
# the command line a download builds
# ---------------------------------------------------------------------------


def test_the_extras_reach_the_command_line(tmp_path):
    job = make_job(tmp_path, extras=Extras.parse("subs,details,cover"))
    args = job.build_args()

    for flag in ("--embed-subs", "--embed-metadata", "--write-thumbnail"):
        assert flag in args
    # They are options, so they must sit before the end-of-options marker.
    assert args.index("--embed-subs") < args.index("--")


def test_no_extras_means_no_extra_flags(tmp_path):
    args = make_job(tmp_path).build_args()

    assert "--embed-subs" not in args
    assert "--embed-metadata" not in args
    assert "--write-thumbnail" not in args


def test_a_download_that_only_needs_ffmpeg_for_the_extras_still_needs_it(tmp_path):
    plain = make_job(tmp_path, option=progressive_option())
    with_extras = make_job(
        tmp_path, option=progressive_option(), extras=Extras.parse("details")
    )

    assert plain.needs_ffmpeg is False
    assert with_extras.needs_ffmpeg is True


def test_an_audio_download_with_only_subtitles_needs_no_ffmpeg(tmp_path):
    """The subtitle switch cannot add anything here, so it costs nothing."""

    job = make_job(
        tmp_path, option=original_audio_option(), extras=Extras.parse("subs")
    )

    assert job.needs_ffmpeg is False
    assert "--embed-subs" not in job.build_args()


def test_two_runs_with_different_extras_do_not_share_a_folder(tmp_path):
    """A picture in a folder is not a partial download to be resumed."""

    one = make_job(tmp_path, extras=Extras.parse("subs"))
    two = make_job(tmp_path, extras=Extras.parse("cover"))

    assert one.work_dir() != two.work_dir()
    assert make_job(tmp_path).work_dir() == make_job(tmp_path).work_dir()


# ---------------------------------------------------------------------------
# what counts as "the file"
# ---------------------------------------------------------------------------


def test_a_cover_image_is_never_mistaken_for_the_download(tmp_path):
    job = make_job(tmp_path, extras=Extras.parse("cover"))
    job.work_dir().mkdir(parents=True)
    video = job.work_dir() / "A Video [abc].mp4"
    cover = job.work_dir() / "A Video [abc].jpg"
    video.write_bytes(b"v")
    cover.write_bytes(b"p")

    # yt-dlp announces both with "[download] Destination:", and the picture can
    # come last — which used to be enough to be reported as the video.
    job._handle_line(f"[download] Destination: {video}")
    job._handle_line(f"[download] Destination: {cover}")

    assert job._find_output({}) == video


def test_a_folder_holding_only_a_picture_is_not_a_finished_download(tmp_path):
    job = make_job(tmp_path, extras=Extras.parse("cover"))
    job.work_dir().mkdir(parents=True)
    (job.work_dir() / "A Video [abc].jpg").write_bytes(b"p")

    assert job._find_output({}) is None


def test_captions_left_behind_are_not_the_download_either(tmp_path):
    job = make_job(tmp_path, extras=Extras.parse("subs"))
    job.work_dir().mkdir(parents=True)
    caption = job.work_dir() / "A Video [abc].en.vtt"
    caption.write_text("WEBVTT", encoding="utf-8")

    assert job._find_output({}) is None


def test_the_cover_image_lands_beside_the_finished_file(tmp_path):
    job = make_job(tmp_path, extras=Extras.parse("cover"))
    work = job.work_dir()
    work.mkdir(parents=True)
    video = work / "A Video [abc].mp4"
    cover = work / "A Video [abc].jpg"
    video.write_bytes(b"v" * 10)
    cover.write_bytes(b"p" * 5)

    finished = job._deliver(video)

    assert finished == tmp_path / "Downloads" / "A Video [abc].mp4"
    assert job.attachments == [tmp_path / "Downloads" / "A Video [abc].jpg"]
    assert job.attachments[0].read_bytes() == b"p" * 5
    # The working folder is gone: the picture was delivered, not abandoned.
    assert not work.exists()


def test_the_cover_gets_the_same_number_as_a_renamed_file(tmp_path):
    """``Title (2).mp4`` and ``Title (2).jpg`` belong together."""

    out = tmp_path / "Downloads"
    out.mkdir()
    (out / "A Video [abc].mp4").write_bytes(b"older")
    job = make_job(tmp_path, extras=Extras.parse("cover"))
    work = job.work_dir()
    work.mkdir(parents=True)
    video = work / "A Video [abc].mp4"
    cover = work / "A Video [abc].jpg"
    video.write_bytes(b"new")
    cover.write_bytes(b"picture")

    finished = job._deliver(video)

    assert finished.name == "A Video [abc] (2).mp4"
    assert [path.name for path in job.attachments] == ["A Video [abc] (2).jpg"]


def test_a_picture_that_cannot_be_moved_does_not_fail_the_download(
    tmp_path, monkeypatch
):
    job = make_job(tmp_path, extras=Extras.parse("cover"))
    work = job.work_dir()
    work.mkdir(parents=True)
    video = work / "A Video [abc].mp4"
    cover = work / "A Video [abc].jpg"
    video.write_bytes(b"v")
    cover.write_bytes(b"p")
    logged: list = []
    job.on_log = logged.append

    real_replace = download_mod.os.replace

    def refuse_pictures(source, target):
        if str(source).endswith(".jpg"):
            raise OSError("the folder is read-only")
        return real_replace(source, target)

    monkeypatch.setattr(download_mod.os, "replace", refuse_pictures)

    finished = job._deliver(video)

    assert finished.is_file(), "the video was saved anyway"
    assert job.attachments == []
    assert any("cover image" in line for line in logged)


# ---------------------------------------------------------------------------
# what the screen is told while it happens
# ---------------------------------------------------------------------------


def test_the_progress_line_says_what_the_extras_are_doing(tmp_path):
    job = make_job(tmp_path, extras=Extras.parse("subs,details,cover"))

    job._handle_line('[EmbedSubtitle] Embedding subtitles in "A Video.mp4"')
    assert "subtitles" in job.progress.status.lower()
    assert job.progress.phase == "extras"

    job._handle_line('[Metadata] Adding metadata to "A Video.mp4"')
    assert "details" in job.progress.status.lower()

    job._handle_line(
        '[ThumbnailsConvertor] Converting thumbnail "A Video.webp" to jpg'
    )
    assert "cover" in job.progress.status.lower()


# ---------------------------------------------------------------------------
# a queue carries them to every video
# ---------------------------------------------------------------------------


class RecordingJob:
    started: ClassVar[list] = []

    def __init__(self, engine, url, option, out_dir, extras=None, **kwargs):
        self.url = url
        self.extras = extras
        self.attachments: list = []
        RecordingJob.started.append(self)

    def run(self):
        target = Path(self.url.rsplit("=", 1)[-1] + ".mp4")
        return target

    def cancel(self, force=False):
        pass


def test_a_queue_gives_every_video_the_same_extras():
    from viddl.formats import Playlist, PlaylistEntry

    RecordingJob.started.clear()
    entries = [
        PlaylistEntry(index=1, title="One", url="https://x.invalid/watch?v=one"),
        PlaylistEntry(index=2, title="Two", url="https://x.invalid/watch?v=two"),
    ]
    queue = QueueJob(
        FakeEngine(),
        Playlist(title="List", url="https://x.invalid/list", entries=entries, count=2),
        entries,
        video_option(),
        Path("/tmp/viddl-extra-tests"),
        extras=Extras.parse("details,cover"),
        make_job=RecordingJob,
    )

    queue.run()

    assert len(RecordingJob.started) == 2
    for job in RecordingJob.started:
        assert job.extras.keys == frozenset({"details", "cover"})
