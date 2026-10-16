"""The quality menu: sizes, ordering, and the MP3 options."""

from __future__ import annotations

from viddl.formats import MediaOption, estimate_size, format_rows, parse_info

DURATION = 212.0  # seconds

INFO = {
    "id": "dQw4w9WgXcQ",
    "title": "A Very Real Video",
    "uploader": "Some Channel",
    "duration": DURATION,
    "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "extractor_key": "Youtube",
    "thumbnail": "https://example.invalid/t.jpg",
    "formats": [
        {
            "format_id": "140",
            "ext": "m4a",
            "audio_ext": "m4a",
            "video_ext": "none",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "abr": 129.5,
            "filesize": 3400000,
        },
        {
            "format_id": "251",
            "ext": "webm",
            "audio_ext": "webm",
            "video_ext": "none",
            "vcodec": "none",
            "acodec": "opus",
            "abr": 160.0,
            "filesize": 4200000,
        },
        {
            "format_id": "137",
            "ext": "mp4",
            "vcodec": "avc1.640028",
            "acodec": "none",
            "height": 1080,
            "fps": 30,
            "vbr": 2000,
            "filesize": 52000000,
        },
        {
            "format_id": "248",
            "ext": "webm",
            "vcodec": "vp9",
            "acodec": "none",
            "height": 1080,
            "fps": 30,
            "vbr": 1500,
            "filesize": 40000000,
        },
        {
            "format_id": "136",
            "ext": "mp4",
            "vcodec": "avc1.4d401f",
            "acodec": "none",
            "height": 720,
            "fps": 30,
            "vbr": 1200,
            "filesize": 31000000,
        },
        {
            "format_id": "18",
            "ext": "mp4",
            "vcodec": "avc1.42001E",
            "acodec": "mp4a.40.2",
            "height": 360,
            "fps": 30,
            "tbr": 700,
            "filesize": 18000000,
        },
        {
            "format_id": "160",
            "ext": "mp4",
            "vcodec": "avc1.4d400c",
            "acodec": "none",
            "height": 144,
            "fps": 15,
            "vbr": 100,
            "filesize": 2600000,
        },
    ],
}


def test_video_rows_are_one_per_height_highest_first():
    info = parse_info(INFO)
    video = [opt for opt in info.options if opt.kind == "video"]
    assert [opt.height for opt in video] == [1080, 720, 360, 144]
    assert video[0].label == "1080p"


def test_best_video_and_audio_are_merged_with_a_size():
    info = parse_info(INFO)
    top = info.options[0]
    # Best video, and m4a audio because it merges cleanly into an mp4.
    assert top.format_selector == "137+140"
    assert top.needs_ffmpeg is True
    assert top.merge_container == "mp4"
    # 52 MB video + 3.4 MB audio.
    assert top.size_bytes == 52000000 + 3400000
    assert top.size_is_estimate is False


def test_progressive_format_needs_no_ffmpeg():
    info = parse_info(INFO)
    row_360 = next(opt for opt in info.options if opt.height == 360)
    assert row_360.needs_ffmpeg is False
    assert row_360.format_selector == "18"
    assert "ready to play" in row_360.detail


def test_extensionless_h264_wins_over_smaller_vp9():
    info = parse_info(INFO)
    top = info.options[0]
    # avc1 ("137") is preferred over the smaller vp9 ("248") for compatibility.
    assert "137" in top.format_selector


def test_mp3_options_are_offered_at_three_qualities():
    info = parse_info(INFO)
    mp3s = [opt for opt in info.options if opt.key.startswith("mp3-")]
    assert [opt.label for opt in mp3s] == [
        "Audio only · MP3 320 kbps",
        "Audio only · MP3 192 kbps",
        "Audio only · MP3 128 kbps",
    ]
    assert mp3s[0].extra_args[:3] == ["--extract-audio", "--audio-format", "mp3"]
    assert mp3s[0].needs_ffmpeg is True
    assert mp3s[0].size_text.startswith("~")


def test_mp3_size_never_promises_more_than_the_source_has():
    """Encoding a 130 kbps source at "320 kbps" still lands near 130 kbps."""
    info = parse_info(INFO)
    mp3s = [opt for opt in info.options if opt.key.startswith("mp3-")]
    source_bitrate = 129.5
    capped = int(source_bitrate * 1000 / 8 * DURATION)
    assert mp3s[0].size_bytes == capped
    assert mp3s[1].size_bytes == capped
    # 128 kbps fits under the source, so it uses its own target.
    assert mp3s[2].size_bytes == int(128 * 1000 / 8 * DURATION)
    assert "source is 130 kbps" in mp3s[0].note
    assert mp3s[0].note.startswith("MP3")
    assert mp3s[2].note == "MP3 file"


def test_mp3_size_uses_the_target_when_the_source_is_better_than_it():
    info = parse_info(
        {
            "duration": 60,
            "title": "loud music",
            "formats": [
                {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus",
                 "abr": 256, "filesize": 2000000},
            ],
        }
    )
    mp3s = [opt for opt in info.options if opt.key.startswith("mp3-")]
    # A 256 kbps source caps the 320 target but not the 128 one.
    assert mp3s[0].size_bytes == int(256 * 1000 / 8 * 60)
    assert "source is 256 kbps" in mp3s[0].note
    assert mp3s[2].size_bytes == int(128 * 1000 / 8 * 60)
    assert mp3s[2].note == "MP3 file"


def test_prefers_the_stream_that_knows_its_own_size():
    """Real-world case: an unmeasurable 144p stream claimed a much higher
    bitrate than the one with an exact size, and the exact one was right."""
    info = parse_info(
        {
            "duration": 19,
            "title": "Me at the zoo",
            "formats": [
                {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
                 "abr": 129.8, "filesize": 309288},
                # 269 knows no size and overstates its bitrate.
                {"format_id": "269", "ext": "mp4", "vcodec": "avc1.4D401F", "acodec": "none",
                 "height": 144, "tbr": 139.6},
                {"format_id": "160", "ext": "mp4", "vcodec": "avc1.4D400C", "acodec": "none",
                 "height": 144, "vbr": 82.5, "filesize": 195278},
            ],
        }
    )
    row = next(opt for opt in info.options if opt.height == 144)
    assert row.format_selector == "160+140"
    assert row.size_bytes == 195278 + 309288
    assert row.size_is_estimate is False


def test_options_are_videos_then_audio():
    info = parse_info(INFO)
    kinds = [opt.kind for opt in info.options]
    assert kinds == sorted(kinds, key=lambda kind: 0 if kind == "video" else 1)


def test_playlist_uses_first_entry_and_says_so():
    info = parse_info({"entries": [INFO, INFO], "_type": "playlist"})
    assert info.title == "A Very Real Video"
    assert info.playlist_note.startswith("Only the first of 2")


def test_missing_formats_falls_back_to_best_available():
    info = parse_info({"title": "Odd one", "webpage_url": "https://example.invalid/v"})
    video = [opt for opt in info.options if opt.kind == "video"]
    assert len(video) == 1
    assert video[0].format_selector == "bv*+ba/b"
    assert video[0].needs_ffmpeg is True


def test_estimate_size_prefers_exact_then_approximate_then_bitrate():
    assert estimate_size({"filesize": 1000}, 10) == (1000, False)
    assert estimate_size({"filesize_approx": 2000}, 10) == (2000, True)
    size, estimate = estimate_size({"tbr": 800}, 10)
    assert (size, estimate) == (int(800 * 1000 / 8 * 10), True)
    assert estimate_size({}, None) == (None, True)


def test_size_text_marks_estimates():
    assert MediaOption("k", "l", 1500000, False).size_text == "1.4 MB"
    assert MediaOption("k", "l", 1500000, True).size_text.startswith("~")
    assert MediaOption("k", "l", None).size_text == "size unknown"


def test_format_rows_aligns_labels_and_sizes():
    rows = format_rows(parse_info(INFO).options)
    assert len(rows) == 7
    assert all(row.startswith("  ") for row in rows)
    assert "1080p" in rows[0]
