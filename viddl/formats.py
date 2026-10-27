"""Turn yt-dlp's metadata into a short list of qualities with size estimates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .util import human_size

# Containers we like to hand back, best first: mp4 plays everywhere.
_VIDEO_EXT_RANK = {"mp4": 0, "m4v": 1, "webm": 2, "mkv": 3}
_AUDIO_EXT_RANK = {"m4a": 0, "mp4": 1, "opus": 2, "webm": 3, "mp3": 4, "ogg": 5}

# Heights we are willing to list, so the menu stays readable.
_MIN_HEIGHT = 144
_MAX_ROWS = 9

#: The key of the row that keeps the site's own audio file, untouched (F4).
#: Chosen for "audio" when no format is named, because an MP3 re-encode of a
#: source that is already m4a or opus throws away quality for nothing.
ORIGINAL_AUDIO_KEY = "orig"

#: How big an uncompressed 44.1 kHz stereo WAV of one second is: two channels,
#: two bytes a sample, 44100 samples. Only the ``~`` estimate on the WAV and
#: FLAC rows uses it — the source's own rate is not in the metadata, so a number
#: exact to the byte would be a promise Viddl cannot keep.
_WAV_BYTES_PER_SECOND = 44100 * 2 * 2

#: FLAC lands at roughly this much of the WAV it came from. A rule of thumb, and
#: marked as one: the size on the row starts with ``~`` like every other guess.
#: Measured at 0.40 for a lossy source (which has less to preserve than a
#: lossless one); 0.5 sits between that and what music usually weighs.
_FLAC_OF_WAV = 0.5


@dataclass(frozen=True)
class AudioPreset:
    """One bitrate inside a format — the numbers on the picker's second level.

    ``quality`` is what yt-dlp's ``--audio-quality`` is given. For MP3 it is a
    VBR step (0 is the best), while a number above 10 is passed to ffmpeg as an
    explicit ``-b:a`` bitrate, which is how m4a and opus are asked for a size.
    """

    bitrate: int
    quality: str
    #: True when ffmpeg is given a fixed bitrate and writes one whatever the
    #: source holds (AAC does: measured 255 kbps out of a 130 kbps source).
    #: False for encoders that spend what the audio needs — libmp3lame's VBR
    #: steps and libopus — so a small source stays a small file.
    fixed_rate: bool = False


@dataclass(frozen=True)
class AudioFormat:
    """A container the sound can be converted to, and its presets (F4)."""

    key: str
    name: str
    presets: Tuple[AudioPreset, ...] = ()
    #: FLAC and WAV have no bitrate to choose: the encoder decides, and the
    #: result is lossless (or uncompressed), so a preset row would be a knob
    #: that does nothing.
    lossless: bool = False
    uncompressed: bool = False


#: Every conversion Viddl offers, best-listening first. MP3 keeps the bitrates
#: it has always had; the rest get two presets, because one would be a choice
#: between "this format" and nothing.
AUDIO_FORMATS: Tuple[AudioFormat, ...] = (
    AudioFormat(
        "mp3",
        "MP3",
        (
            AudioPreset(320, "0"),
            AudioPreset(192, "5"),
            AudioPreset(128, "7"),
        ),
    ),
    AudioFormat(
        "m4a",
        "M4A",
        (
            AudioPreset(256, "256", fixed_rate=True),
            AudioPreset(128, "128", fixed_rate=True),
        ),
    ),
    AudioFormat("opus", "Opus", (AudioPreset(160, "160"), AudioPreset(96, "96"))),
    AudioFormat("flac", "FLAC", lossless=True),
    AudioFormat("wav", "WAV", uncompressed=True),
)

#: What the original row calls the site's own file, by the codec it holds. A
#: webm stream is named for its codec because "WEBM" says nothing about sound.
_AUDIO_CODEC_NAMES = {"opus": "Opus", "vorbis": "Vorbis", "aac": "M4A", "alac": "ALAC"}


@dataclass
class MediaOption:
    """One row in the quality menu."""

    key: str
    label: str
    size_bytes: Optional[int] = None
    size_is_estimate: bool = False
    kind: str = "video"  # "video" | "audio"
    needs_ffmpeg: bool = False
    format_selector: str = "bv*+ba/b"
    extra_args: List[str] = field(default_factory=list)
    note: str = ""
    height: Optional[int] = None
    merge_container: Optional[str] = None
    #: What the sound ends up as — "MP3", "Opus", or the name of the file the
    #: site already had ("M4A"). Empty on video rows (F4).
    audio_format: str = ""

    @property
    def keeps_source_audio(self) -> bool:
        """True for the audio row that downloads the site's own file (F4).

        It is the only audio row with nothing to convert: no ``-x``, no
        ``--audio-format``, no encoder to disagree with the source.
        """
        return self.kind == "audio" and not self.extra_args

    @property
    def size_text(self) -> str:
        if self.size_bytes is None:
            return "size unknown"
        prefix = "~" if self.size_is_estimate else ""
        return f"{prefix}{human_size(self.size_bytes)}"

    @property
    def detail(self) -> str:
        bits = [self.note] if self.note else []
        if self.needs_ffmpeg:
            bits.append("needs ffmpeg")
        return " · ".join(bits)


@dataclass(frozen=True)
class PlaylistSummary:
    """What a look-up can tell about a playlist without listing it (F2).

    The look-up only reads the first video, so this is the part that says
    "there is more here": enough to offer the choice, not enough to show the
    list. Listing it is a second, flatter question — see :class:`Playlist` —
    asked only when somebody asks for more than the first video.
    """

    title: str
    count: int
    url: str


@dataclass(frozen=True)
class PlaylistEntry:
    """One video in a playlist, as the flat listing describes it.

    The flat listing is metadata only — no full extraction per video — so a
    title can be missing or a link can be an identifier rather than an address.
    Both are handled where they are used, not papered over here.
    """

    index: int
    title: str
    url: str = ""
    duration: Optional[float] = None


@dataclass
class Playlist:
    """A playlist's videos, in the order the site keeps them."""

    title: str
    url: str
    entries: List[PlaylistEntry] = field(default_factory=list)
    #: What the playlist says it holds, which can be more than was read: the
    #: listing stops at a limit so a playlist of a thousand does not become a
    #: thousand-item menu.
    count: int = 0

    @property
    def truncated(self) -> bool:
        return self.count > len(self.entries)

    @property
    def heading(self) -> str:
        title = self.title or "Playlist"
        if self.truncated:
            return f"{title}  ·  the first {len(self.entries)} of {self.count} videos"
        return f"{title}  ·  {len(self.entries)} videos"


def _playlist_title(info: Dict[str, Any]) -> str:
    return str(info.get("title") or info.get("id") or "Playlist")


def parse_playlist(info: Dict[str, Any]) -> Playlist:
    """Build the checklist from yt-dlp's flat playlist listing.

    A flat entry can be missing a title, a length or even a usable link; each
    of those is left empty here rather than invented, so the checklist can show
    what the site actually said and the queue can decide what to do about it.
    """
    raw_entries = [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)]
    total = int(_num(info.get("playlist_count")) or 0)
    total = max(total, len(raw_entries))
    entries: List[PlaylistEntry] = []
    for position, entry in enumerate(raw_entries, start=1):
        index = int(_num(entry.get("playlist_index")) or position)
        entries.append(
            PlaylistEntry(
                index=index,
                title=str(entry.get("title") or entry.get("id") or f"Video {index}"),
                url=str(entry.get("webpage_url") or entry.get("url") or ""),
                duration=_num(entry.get("duration")),
            )
        )
    return Playlist(
        title=_playlist_title(info),
        url=str(info.get("webpage_url") or info.get("original_url") or ""),
        entries=entries,
        count=total,
    )


@dataclass
class MediaInfo:
    """What we know about a link before downloading it."""

    url: str
    title: str
    uploader: str = ""
    duration: Optional[float] = None
    thumbnail: Optional[str] = None
    extractor: str = ""
    options: List[MediaOption] = field(default_factory=list)
    is_live: bool = False
    playlist_note: str = ""
    #: Set when the link is a playlist, so the app can offer the rest of it
    #: (F2). The options above still describe the first video, which is what
    #: "download this one" means.
    playlist: Optional[PlaylistSummary] = None

    @property
    def heading(self) -> str:
        parts = [self.title or "Untitled"]
        if self.duration:
            from .util import human_duration

            parts.append(human_duration(self.duration))
        return "  ·  ".join(parts)


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _has_video(fmt: Dict[str, Any]) -> bool:
    codec = fmt.get("vcodec")
    return bool(codec) and codec != "none"


def _has_audio(fmt: Dict[str, Any]) -> bool:
    codec = fmt.get("acodec")
    return bool(codec) and codec != "none"


def estimate_size(fmt: Dict[str, Any], duration: Optional[float]) -> Tuple[Optional[int], bool]:
    """Bytes for a single format, plus whether the number is a guess."""
    exact = _num(fmt.get("filesize"))
    if exact:
        return int(exact), False

    approx = _num(fmt.get("filesize_approx"))
    if approx:
        return int(approx), True

    bitrate = _num(fmt.get("tbr")) or (
        (_num(fmt.get("vbr")) or 0) + (_num(fmt.get("abr")) or 0)
    )
    if bitrate and duration:
        # Bitrate is kilobits per second.
        return int(bitrate * 1000 / 8 * duration), True
    return None, True


def _video_sort_key(fmt: Dict[str, Any]) -> Tuple[int, int, float]:
    ext_rank = _VIDEO_EXT_RANK.get(str(fmt.get("ext") or ""), 9)
    codec = str(fmt.get("vcodec") or "")
    # Prefer widely-playable H.264 over AV1/VP9 when sizes are close.
    codec_rank = 0 if codec.startswith(("avc", "h264")) else 1
    bitrate = _num(fmt.get("vbr")) or _num(fmt.get("tbr")) or 0.0
    return (codec_rank * 10 + ext_rank, -int(_num(fmt.get("fps")) or 0), -bitrate)


def _knows_size(fmt: Dict[str, Any]) -> bool:
    """True when the site told us how big this stream actually is."""
    return bool(_num(fmt.get("filesize")) or _num(fmt.get("filesize_approx")))


def _video_bitrate(fmt: Dict[str, Any]) -> float:
    return _num(fmt.get("vbr")) or _num(fmt.get("tbr")) or 0.0


def _codec_family(fmt: Dict[str, Any]) -> str:
    codec = str(fmt.get("vcodec") or "")
    for family in ("avc", "hev", "vp09", "vp9", "vp8", "av01", "mp4v"):
        if codec.startswith(family):
            return family
    return codec


def _pick_video(formats: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Best stream for one resolution.

    Quality comes first, with one deliberate exception: when the winner has no
    size metadata, a like-for-like stream (same container and codec family) that
    does know its size wins if its bitrate is at least half of the winner's.
    Sites routinely overstate the bitrate of streams they cannot measure, and a
    size we can show the user honestly is worth more than an unverifiable guess.
    """
    ranked = sorted(formats, key=_video_sort_key)
    best = ranked[0]
    if _knows_size(best):
        return best

    best_bitrate = _video_bitrate(best)
    best_ext = str(best.get("ext") or "")
    family = _codec_family(best)
    for candidate in ranked[1:]:
        if not _knows_size(candidate):
            continue
        if str(candidate.get("ext") or "") != best_ext:
            continue
        if _codec_family(candidate) != family:
            continue
        if _video_bitrate(candidate) >= 0.5 * best_bitrate:
            return candidate
    return best


def _audio_sort_key(fmt: Dict[str, Any]) -> Tuple[int, float]:
    ext_rank = _AUDIO_EXT_RANK.get(str(fmt.get("audio_ext") or fmt.get("ext") or ""), 9)
    bitrate = _num(fmt.get("abr")) or _num(fmt.get("tbr")) or 0.0
    return (-ext_rank, bitrate)


def _label_for_height(height: int, extra: str = "") -> str:
    quality = {2160: "4K", 1440: "1440p", 1080: "1080p"}.get(height, f"{height}p")
    if extra:
        return f"{quality} {extra}"
    return quality


def parse_info(info: Dict[str, Any]) -> MediaInfo:
    """Build the quality menu for one video's metadata."""
    playlist_note = ""
    playlist: Optional[PlaylistSummary] = None
    if isinstance(info.get("entries"), list) and info["entries"]:
        entries = [entry for entry in info["entries"] if entry]
        # The probe asks yt-dlp for the first entry only, so the list here is
        # one item long however big the playlist is; the total is what the
        # playlist itself reported. A single-video "playlist" needs no note.
        total = int(_num(info.get("playlist_count")) or 0)
        total = max(total, len(entries))
        if total > 1:
            playlist = PlaylistSummary(
                title=_playlist_title(info),
                count=total,
                url=str(info.get("webpage_url") or info.get("original_url") or ""),
            )
            # Kept as metadata, not as instructions: the same sentence is read
            # by the app (where a button takes the rest) and by --plain (where
            # a flag does), so it says what the link is and lets each path say
            # what it will do about it (F2).
            playlist_note = f"This is a playlist of {total} videos: “{playlist.title}”"
        info = entries[0]

    url = str(info.get("webpage_url") or info.get("original_url") or info.get("url") or "")
    duration = _num(info.get("duration"))
    title = str(info.get("title") or info.get("id") or "Untitled video")

    media = MediaInfo(
        url=url,
        title=title,
        uploader=str(info.get("uploader") or info.get("channel") or ""),
        duration=duration,
        thumbnail=info.get("thumbnail"),
        extractor=str(info.get("extractor_key") or info.get("extractor") or ""),
        is_live=bool(info.get("is_live")),
        playlist_note=playlist_note,
        playlist=playlist,
    )

    formats: Iterable[Dict[str, Any]] = [
        fmt for fmt in (info.get("formats") or []) if isinstance(fmt, dict)
    ]

    video_only: List[Dict[str, Any]] = []
    progressive: List[Dict[str, Any]] = []
    audio_only: List[Dict[str, Any]] = []

    for fmt in formats:
        has_v, has_a = _has_video(fmt), _has_audio(fmt)
        if not fmt.get("format_id"):
            continue
        if has_v and has_a:
            progressive.append(fmt)
        elif has_v:
            video_only.append(fmt)
        elif has_a:
            audio_only.append(fmt)

    if not formats and info.get("url"):
        # Some extractors hand back a single already-resolved file.
        progressive.append(
            {
                "format_id": info.get("format_id") or "best",
                "ext": info.get("ext") or "mp4",
                "vcodec": info.get("vcodec") or "unknown",
                "acodec": info.get("acodec") or "unknown",
                "height": info.get("height"),
                "filesize": info.get("filesize"),
                "filesize_approx": info.get("filesize_approx"),
                "tbr": info.get("tbr"),
            }
        )

    best_audio = max(audio_only, key=_audio_sort_key) if audio_only else None
    audio_size: Optional[int] = None
    audio_is_estimate = True
    source_audio_bitrate: Optional[float] = None
    if best_audio is not None:
        audio_size, audio_is_estimate = estimate_size(best_audio, duration)
        source_audio_bitrate = _num(best_audio.get("abr")) or _num(best_audio.get("tbr"))

    # ---- video rows, one per resolution -------------------------------------
    video_candidates: Dict[int, List[Dict[str, Any]]] = {}
    for fmt in video_only:
        height = _num(fmt.get("height"))
        if not height or int(height) < _MIN_HEIGHT:
            continue
        video_candidates.setdefault(int(height), []).append(fmt)
    by_height = {height: _pick_video(group) for height, group in video_candidates.items()}

    progressive_candidates: Dict[int, List[Dict[str, Any]]] = {}
    for fmt in progressive:
        height = int(_num(fmt.get("height")) or 0)
        if height < _MIN_HEIGHT:
            continue
        progressive_candidates.setdefault(height, []).append(fmt)
    progressive_by_height = {
        height: _pick_video(group) for height, group in progressive_candidates.items()
    }

    heights = sorted(set(by_height) | set(progressive_by_height), reverse=True)
    if len(heights) > _MAX_ROWS:
        # Always keep the best quality, then step down through the rest.
        keep = [heights[0]]
        step = max(1, (len(heights) - 1) // (_MAX_ROWS - 1))
        keep.extend(heights[1::step][: _MAX_ROWS - 1])
        heights = sorted(set(keep), reverse=True)

    video_rows: List[MediaOption] = []
    for height in heights:
        fps = None
        if height in by_height:
            vid = by_height[height]
            vid_size, vid_estimate = estimate_size(vid, duration)
            # ``total`` already means the playlist's length here; this one is
            # what the merged file will weigh.
            combined: Optional[int] = None
            if vid_size is not None:
                combined = vid_size + (audio_size or 0)
            est = vid_estimate or audio_is_estimate
            fps = _num(vid.get("fps"))
            selector = f"{vid['format_id']}+{best_audio['format_id']}" if best_audio else str(vid["format_id"])
            ext = str(vid.get("ext") or "mp4")
            row = MediaOption(
                key=f"v{height}",
                label=_label_for_height(height, "60fps" if (fps or 0) >= 50 else ""),
                size_bytes=combined,
                size_is_estimate=est,
                kind="video",
                needs_ffmpeg=best_audio is not None,
                format_selector=selector,
                extra_args=[],
                note=f"{ext.upper()} video" + (" + audio" if best_audio else ""),
                height=height,
                merge_container=ext if ext in ("mp4", "mkv", "webm") else None,
            )
        else:
            prog = progressive_by_height[height]
            size, est = estimate_size(prog, duration)
            ext = str(prog.get("ext") or "mp4")
            row = MediaOption(
                key=f"v{height}",
                label=_label_for_height(height),
                size_bytes=size,
                size_is_estimate=est,
                kind="video",
                needs_ffmpeg=False,
                format_selector=str(prog["format_id"]),
                note=f"{ext.upper()} · ready to play",
                height=height,
            )
        video_rows.append(row)

    media.options.extend(video_rows)

    if not video_rows:
        # Unknown shape: let yt-dlp pick the best it can and say so honestly.
        media.options.append(
            MediaOption(
                key="best",
                label="Best available",
                kind="video",
                needs_ffmpeg=True,
                format_selector="bv*+ba/b",
                note="automatic",
            )
        )

    # ---- audio rows ---------------------------------------------------------
    media.options.extend(
        audio_options(
            best_audio, audio_size, audio_is_estimate, duration, source_audio_bitrate
        )
    )

    return media


def audio_name(fmt: Dict[str, Any]) -> str:
    """What to call the sound inside ``fmt``: "M4A", "Opus", … (F4).

    The codec wins over the container when it has a name of its own, because a
    webm holding opus is remembered by its listener as "an opus file", and the
    container is what the site happened to wrap it in.
    """
    codec = str(fmt.get("acodec") or "").lower()
    for family, name in _AUDIO_CODEC_NAMES.items():
        if codec.startswith(family):
            return name
    ext = str(fmt.get("audio_ext") or fmt.get("ext") or "").lower()
    return {"m4a": "M4A", "mp3": "MP3", "flac": "FLAC", "wav": "WAV", "ogg": "OGG"}.get(
        ext, ext.upper() or "audio"
    )


def _source_format(fmt: Dict[str, Any]) -> str:
    """Which of :data:`AUDIO_FORMATS` the stream already is, if any (F4).

    The codec decides, because the container can lie about the contents: a webm
    holding opus is an Opus source. A codec with no row of its own — vorbis, on
    the sites that still serve it — returns its own name, which matches nothing
    and therefore skips nothing.
    """
    codec = str(fmt.get("acodec") or "").lower()
    for family, key in (
        ("opus", "opus"),
        ("mp4a", "m4a"),
        ("aac", "m4a"),
        ("mp3", "mp3"),
        ("flac", "flac"),
        ("alac", "m4a"),
    ):
        if codec.startswith(family):
            return key
    ext = str(fmt.get("audio_ext") or fmt.get("ext") or "").lower()
    return {"m4a": "m4a", "mp3": "mp3", "flac": "flac", "wav": "wav", "webm": "opus"}.get(
        ext, ext
    )


def audio_options(
    best_audio: Optional[Dict[str, Any]],
    audio_size: Optional[int],
    audio_is_estimate: bool,
    duration: Optional[float],
    source_bitrate: Optional[float],
) -> List[MediaOption]:
    """The audio half of the menu: keep the site's file, or convert it (F4).

    The first row is the one the site already has. It is first because it is
    both the best-sounding and the cheapest choice: there is no ffmpeg step, no
    generation loss, and no chance of an encoder disagreeing with the source.
    Everything below it is a deliberate trade — a smaller file, a container a
    particular player wants, or a format somebody has always used.
    """
    rows: List[MediaOption] = []
    # The format the site already uses. Asking yt-dlp to "convert" a stream to
    # its own codec does not re-encode it — it copies it — so an "M4A 128 kbps"
    # row on an m4a source would deliver the source at 130 kbps while claiming
    # 128. Measured with yt-dlp 2026.08.19: `--audio-format m4a` on an m4a
    # source produced the same 309,205 bytes twice. The original row already is
    # that format, so the conversion is not offered at all (F4).
    source_format = _source_format(best_audio) if best_audio is not None else ""

    if best_audio is not None:
        name = audio_name(best_audio)
        rows.append(
            MediaOption(
                key=ORIGINAL_AUDIO_KEY,
                label=f"Audio only · original {name}",
                size_bytes=audio_size,
                size_is_estimate=audio_is_estimate,
                kind="audio",
                # The whole point: the stream is taken as it is.
                needs_ffmpeg=False,
                format_selector=str(best_audio["format_id"]),
                note="no re-encode",
                audio_format=name,
            )
        )

    for audio_format in AUDIO_FORMATS:
        if audio_format.key == source_format:
            continue
        for preset in audio_format.presets:
            # What the file will weigh. A variable-rate encoder given a 130 kbps
            # source at a 320 kbps setting stays near 130 kbps, so the estimate
            # takes the smaller of the two; a fixed-rate one writes 320 whatever
            # it was handed (measured: AAC at 256 from a 130 kbps source wrote
            # 255 kbps), so its row has to say the target.
            effective: float = float(preset.bitrate)
            note = f"{audio_format.name} file"
            if source_bitrate and source_bitrate < preset.bitrate:
                if not preset.fixed_rate:
                    effective = source_bitrate
                note = (
                    f"{audio_format.name} · source is "
                    f"{round(source_bitrate)} kbps"
                )
            size = int(effective * 1000 / 8 * duration) if duration else None
            rows.append(
                MediaOption(
                    key=f"{audio_format.key}-{preset.bitrate}",
                    label=f"Audio only · {audio_format.name} {preset.bitrate} kbps",
                    size_bytes=size,
                    size_is_estimate=True,
                    kind="audio",
                    needs_ffmpeg=True,
                    format_selector="ba/b",
                    extra_args=[
                        "--extract-audio",
                        "--audio-format",
                        audio_format.key,
                        "--audio-quality",
                        preset.quality,
                    ],
                    note=note,
                    audio_format=audio_format.name,
                )
            )
        if not audio_format.presets:
            # A lossless container has no bitrate to pick: converting is a
            # container change, and the size follows the source rather than a
            # number on the row.
            if audio_format.uncompressed:
                size = int(duration * _WAV_BYTES_PER_SECOND) if duration else None
                note = "uncompressed file"
            else:
                size = (
                    int(duration * _WAV_BYTES_PER_SECOND * _FLAC_OF_WAV)
                    if duration
                    else None
                )
                note = "lossless file"
            rows.append(
                MediaOption(
                    key=audio_format.key,
                    label=f"Audio only · {audio_format.name}",
                    size_bytes=size,
                    size_is_estimate=True,
                    kind="audio",
                    needs_ffmpeg=True,
                    format_selector="ba/b",
                    extra_args=["--extract-audio", "--audio-format", audio_format.key],
                    note=note,
                    audio_format=audio_format.name,
                )
            )

    return rows


def format_rows(options: List[MediaOption]) -> List[str]:
    """Plain-text version of the menu, used by ``viddl --list-qualities``."""
    if not options:
        return []
    width = max(len(opt.label) for opt in options)
    rows = []
    for opt in options:
        detail = opt.detail
        suffix = f"  ({detail})" if detail else ""
        rows.append(f"  {opt.label.ljust(width)}  {opt.size_text:>12}{suffix}")
    return rows
