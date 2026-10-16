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

MP3_BITRATES: Tuple[Tuple[int, str], ...] = ((320, "0"), (192, "5"), (128, "7"))


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
    if isinstance(info.get("entries"), list) and info["entries"]:
        entries = [entry for entry in info["entries"] if entry]
        playlist_note = f"Only the first of {len(entries)} videos in this playlist."
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
            total: Optional[int] = None
            if vid_size is not None:
                total = vid_size + (audio_size or 0)
            est = vid_estimate or audio_is_estimate
            fps = _num(vid.get("fps"))
            selector = f"{vid['format_id']}+{best_audio['format_id']}" if best_audio else str(vid["format_id"])
            ext = str(vid.get("ext") or "mp4")
            row = MediaOption(
                key=f"v{height}",
                label=_label_for_height(height, "60fps" if (fps or 0) >= 50 else ""),
                size_bytes=total,
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
    for bitrate, quality in MP3_BITRATES:
        # Transcoding cannot invent quality: encoding a 130 kbps source at a
        # 320 kbps target still lands near the source, so estimate from the
        # smaller of the two. It keeps the number honest for the common case.
        effective = bitrate
        note = "MP3 file"
        if source_audio_bitrate and source_audio_bitrate < bitrate:
            effective = source_audio_bitrate
            note = f"MP3 · source is {int(round(source_audio_bitrate))} kbps"
        size = int(effective * 1000 / 8 * duration) if duration else None
        estimate = True
        media.options.append(
            MediaOption(
                key=f"mp3-{bitrate}",
                label=f"Audio only · MP3 {bitrate} kbps",
                size_bytes=size,
                size_is_estimate=estimate,
                kind="audio",
                needs_ffmpeg=True,
                format_selector="ba/b",
                extra_args=["--extract-audio", "--audio-format", "mp3", "--audio-quality", quality],
                note=note,
            )
        )

    return media


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
