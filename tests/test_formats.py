"""The quality menu: sizes, ordering, and the audio options (F4)."""

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


# ---------------------------------------------------------------------------
# more than MP3 (F4)
# ---------------------------------------------------------------------------


def test_the_audio_menu_starts_with_the_file_the_site_already_has():
    """The best sound is the one nobody re-encoded.

    It is first because it wins on every count at once: better than any
    conversion can be, and the only audio row that needs no ffmpeg.
    """
    info = parse_info(INFO)
    first_audio = next(opt for opt in info.options if opt.kind == "audio")
    assert first_audio.key == "orig"
    assert first_audio.label == "Audio only · original M4A"
    # 140 is the m4a stream, which comes before opus in the container ranking.
    assert first_audio.format_selector == "140"
    assert first_audio.needs_ffmpeg is False
    assert first_audio.extra_args == []
    # The site gave an exact size, so the row repeats that rather than a guess.
    assert first_audio.size_text == "3.2 MB"
    assert first_audio.keeps_source_audio is True


def test_every_conversion_is_on_the_menu_best_first():
    info = parse_info(INFO)
    audio = [opt for opt in info.options if opt.kind == "audio"]
    assert [opt.key for opt in audio] == [
        "orig",
        "mp3-320",
        "mp3-192",
        "mp3-128",
        "opus-160",
        "opus-96",
        "flac",
        "wav",
    ]
    assert [opt.audio_format for opt in audio] == [
        "M4A",
        "MP3",
        "MP3",
        "MP3",
        "Opus",
        "Opus",
        "FLAC",
        "WAV",
    ]


def test_the_format_the_link_already_has_is_not_offered_as_a_conversion():
    """yt-dlp copies a stream into its own codec instead of re-encoding it.

    Measured with 2026.08.19: `--audio-format m4a` on an m4a source wrote the
    same 309,205 bytes as the untouched download. An "M4A 128 kbps" row would
    therefore be the original file under a bitrate nobody honours, so the row
    is not offered — the original row is that format (F4).
    """
    by_key = {opt.key: opt for opt in parse_info(INFO).options}
    assert by_key["orig"].audio_format == "M4A"
    assert "m4a-256" not in by_key and "m4a-128" not in by_key


def test_a_link_that_does_not_have_m4a_is_offered_it():
    by_key = {opt.key: opt for opt in parse_info(opus_source()).options}
    assert by_key["orig"].audio_format == "Opus"
    assert by_key["m4a-256"].extra_args == [
        "--extract-audio",
        "--audio-format",
        "m4a",
        "--audio-quality",
        "256",
    ]
    # And the source's own format is now the one that is not offered.
    assert "opus-160" not in by_key


def test_each_conversion_asks_for_the_container_it_names():
    by_key = {opt.key: opt for opt in parse_info(INFO).options}
    assert by_key["opus-160"].extra_args == [
        "--extract-audio",
        "--audio-format",
        "opus",
        "--audio-quality",
        "160",
    ]
    # No bitrate knob for a lossless container: the encoder decides.
    assert by_key["flac"].extra_args == ["--extract-audio", "--audio-format", "flac"]
    assert by_key["wav"].extra_args == ["--extract-audio", "--audio-format", "wav"]
    converting = [opt for opt in by_key.values() if opt.kind == "audio" and not opt.keeps_source_audio]
    assert all(opt.needs_ffmpeg for opt in converting)


def opus_source(bitrate: float = 130) -> dict:
    """A link whose own sound is opus, so M4A is a real conversion (F4)."""
    return {
        "title": "opus only",
        "duration": DURATION,
        "formats": [
            {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus",
             "abr": bitrate, "filesize": 3400000},
        ],
    }


def test_a_variable_rate_encoder_cannot_be_made_to_outweigh_its_source():
    """The same rule as MP3: a 130 kbps source caps a 160 kbps Opus target."""
    info = parse_info(
        {
            "title": "m4a, for a change",
            "duration": DURATION,
            "formats": [
                {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
                 "abr": 130, "filesize": 3400000},
            ],
        }
    )
    by_key = {opt.key: opt for opt in info.options}
    assert by_key["opus-160"].size_bytes == int(130 * 1000 / 8 * DURATION)
    assert "source is 130 kbps" in by_key["opus-160"].note
    # 96 kbps is below the source, so the target is what it will weigh.
    assert by_key["opus-96"].size_bytes == int(96 * 1000 / 8 * DURATION)
    assert by_key["opus-96"].note == "Opus file"


def test_a_fixed_rate_encoder_writes_its_target_even_from_a_small_source():
    """Measured: AAC asked for 256 kbps from a 130 kbps source wrote 255 kbps.

    So the row shows what the file will weigh, and the note says where the sound
    came from — the honest pair: a bigger file that cannot sound better.
    """
    by_key = {opt.key: opt for opt in parse_info(opus_source()).options}
    assert by_key["m4a-256"].size_bytes == int(256 * 1000 / 8 * DURATION)
    assert "source is 130 kbps" in by_key["m4a-256"].note
    assert by_key["m4a-128"].size_bytes == int(128 * 1000 / 8 * DURATION)
    assert by_key["m4a-128"].note == "M4A file"


def test_lossless_rows_are_sized_from_the_clock_not_the_source():
    """A 130 kbps source becomes a WAV several times its size, and says so."""
    by_key = {opt.key: opt for opt in parse_info(INFO).options}
    wav, flac = by_key["wav"], by_key["flac"]
    # 44.1 kHz stereo, 16-bit: 176400 bytes for every second.
    assert wav.size_bytes == int(DURATION * 176400)
    assert 0 < flac.size_bytes < wav.size_bytes
    assert wav.size_is_estimate is True and flac.size_is_estimate is True
    assert "uncompressed" in wav.note
    assert "lossless" in flac.note
    assert wav.size_text.startswith("~")


def test_the_original_row_is_named_for_the_sound_inside_the_container():
    info = parse_info(
        {
            "title": "opus only",
            "duration": 60,
            "formats": [
                {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus",
                 "abr": 160, "filesize": 1200000},
            ],
        }
    )
    original = next(opt for opt in info.options if opt.kind == "audio")
    # "WEBM" says nothing about sound, so the codec names the row.
    assert original.key == "orig"
    assert original.label == "Audio only · original Opus"
    assert original.format_selector == "251"


def test_a_link_with_no_separate_audio_offers_no_original_row():
    info = parse_info(
        {
            "title": "odd",
            "duration": 30,
            "formats": [
                {"format_id": "hls-1", "ext": "mp4", "vcodec": "avc1",
                 "acodec": "mp4a", "tbr": 600},
            ],
        }
    )
    audio = [opt for opt in info.options if opt.kind == "audio"]
    assert "orig" not in [opt.key for opt in audio]
    # The conversions are still offered: -x works on a file that has sound in it.
    assert audio[0].key == "mp3-320"


def test_options_are_videos_then_audio():
    info = parse_info(INFO)
    kinds = [opt.kind for opt in info.options]
    assert kinds == sorted(kinds, key=lambda kind: 0 if kind == "video" else 1)


def test_playlist_uses_first_entry_and_says_so():
    info = parse_info({"entries": [INFO, INFO], "_type": "playlist"})
    assert info.title == "A Very Real Video"
    assert info.playlist_note.startswith("This is a playlist of 2 videos")
    # The rest of the list is on offer (F2), and the summary is what makes the
    # choice possible without listing anything yet.
    assert info.playlist is not None
    assert info.playlist.count == 2


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
    # Four video rows, then the original and the seven conversions left for an
    # m4a source (F4): MP3 x3, Opus x2, FLAC, WAV.
    assert len(rows) == 4 + 8
    assert all(row.startswith("  ") for row in rows)
    assert "1080p" in rows[0]
