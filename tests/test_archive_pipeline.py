# SPDX-FileCopyrightText: 2026 Simulacre
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the archiving pipeline helpers.

Covers the segment exporter's file name generation, the multi-export preset
loop, the trim/speed plumbing and the metadata parsing — all of it pure Python
that runs without Blender::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from typing import Optional

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "blender_video_encoder",
    "utils",
    "ffmpeg.py",
)


def _load_ffmpeg_module() -> types.ModuleType:
    """Import ``utils/ffmpeg.py`` standalone, without the add-on package.

    Returns:
        The imported module, reusing the instance another test file may have
        loaded already.

    Raises:
        ImportError: If the module file cannot be loaded.
    """
    if "qve_ffmpeg" in sys.modules:
        return sys.modules["qve_ffmpeg"]

    spec = importlib.util.spec_from_file_location("qve_ffmpeg", _MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"모듈을 불러올 수 없습니다: {_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ffmpeg = _load_ffmpeg_module()


class FilenameTemplateTests(unittest.TestCase):
    """Tests for :func:`format_segment_filename`."""

    def test_default_template(self) -> None:
        """The default template renders prefix, name and a padded index."""
        name = ffmpeg.format_segment_filename(
            ffmpeg.DEFAULT_SEGMENT_TEMPLATE,
            ".mp4",
            prefix="VRC",
            name="take1",
            index=3,
        )
        self.assertEqual(name, "VRC_take1_003.mp4")

    def test_index_is_zero_padded(self) -> None:
        """Indices sort correctly in a file browser."""
        name = ffmpeg.format_segment_filename("{index}", ".mp4", index=7)
        self.assertEqual(name, "007.mp4")

    def test_all_tokens_are_supported(self) -> None:
        """Every documented token can be used at once."""
        name = ffmpeg.format_segment_filename(
            "{scene}-{date}-{prefix}-{name}-{index}",
            ".mkv",
            prefix="p",
            name="n",
            index=1,
            scene="Scene",
            date="20260917",
        )
        self.assertEqual(name, "Scene-20260917-p-n-001.mkv")

    def test_path_separators_cannot_escape_the_directory(self) -> None:
        """A marker named like a path cannot create nested folders."""
        name = ffmpeg.format_segment_filename(
            "{name}", ".mp4", name="take 1/2\\3"
        )
        self.assertNotIn("/", name[:-4])
        self.assertNotIn("\\", name)
        self.assertEqual(name, "take 1_2_3.mp4")

    def test_invalid_characters_are_replaced(self) -> None:
        """Characters Windows rejects are swapped for underscores."""
        name = ffmpeg.format_segment_filename("{name}", ".mp4", name='a<b>c:d"e|f?g*h')
        self.assertEqual(name, "a_b_c_d_e_f_g_h.mp4")

    def test_empty_tokens_do_not_leave_double_separators(self) -> None:
        """An unset prefix must not produce a leading underscore."""
        name = ffmpeg.format_segment_filename(
            "{prefix}_{name}_{index}", ".mp4", prefix="", name="clip", index=2
        )
        self.assertEqual(name, "clip_002.mp4")

    def test_unknown_token_raises_with_a_hint(self) -> None:
        """A typo in the template is reported, not silently ignored."""
        with self.assertRaises(ValueError) as caught:
            ffmpeg.format_segment_filename("{prefx}_{index}", ".mp4")
        self.assertIn("prefx", str(caught.exception))

    def test_malformed_template_raises(self) -> None:
        """An unbalanced brace is a template error."""
        with self.assertRaises(ValueError):
            ffmpeg.format_segment_filename("{name", ".mp4", name="x")

    def test_sanitize_filename_never_returns_empty(self) -> None:
        """A name made only of invalid characters still yields a usable stem."""
        self.assertEqual(ffmpeg.sanitize_filename("///"), "___")
        self.assertEqual(ffmpeg.sanitize_filename("  . "), "untitled")
        self.assertEqual(ffmpeg.sanitize_filename(""), "untitled")


class SegmentModelTests(unittest.TestCase):
    """Tests for :class:`Segment` and the trim arguments."""

    def test_duration_is_derived_and_never_negative(self) -> None:
        """A reversed segment reports zero rather than a negative length."""
        self.assertEqual(ffmpeg.Segment("a", 2.0, 5.0, 1).duration, 3.0)
        self.assertEqual(ffmpeg.Segment("a", 5.0, 2.0, 1).duration, 0.0)

    def test_as_time_range(self) -> None:
        """A segment converts to the trim the encoder consumes."""
        time_range = ffmpeg.Segment("a", 1.5, 4.0, 1).as_time_range()
        self.assertEqual(time_range.start, 1.5)
        self.assertEqual(time_range.duration, 2.5)

    def test_trim_arguments_are_split_around_the_input(self) -> None:
        """``-ss`` seeks before decoding, ``-t`` limits the output."""
        pre, post = ffmpeg.build_trim_arguments(
            ffmpeg.TimeRange(start=12.0, duration=3.5)
        )
        self.assertEqual(pre, ["-ss", "12.000"])
        self.assertEqual(post, ["-t", "3.500"])

    def test_zero_start_emits_no_seek(self) -> None:
        """A segment starting at zero needs no ``-ss``."""
        pre, post = ffmpeg.build_trim_arguments(ffmpeg.TimeRange(0.0, 10.0))
        self.assertEqual(pre, [])
        self.assertEqual(post, ["-t", "10.000"])

    def test_no_trim_emits_nothing(self) -> None:
        """Encoding a whole file adds no trim arguments at all."""
        self.assertEqual(ffmpeg.build_trim_arguments(None), ([], []))

    def test_command_places_seek_before_the_input(self) -> None:
        """The full command keeps ``-ss`` ahead of ``-i``."""
        settings = ffmpeg.EncodeSettings(
            trim=ffmpeg.TimeRange(start=5.0, duration=2.0)
        )
        command = ffmpeg.build_ffmpeg_command(settings, "in.mp4", "out.mp4")
        self.assertLess(command.index("-ss"), command.index("-i"))
        self.assertGreater(command.index("-t"), command.index("-i"))

    def test_stream_copy_command_avoids_re_encoding(self) -> None:
        """The fast path copies streams and normalises timestamps."""
        segment = ffmpeg.Segment("take", 4.0, 9.0, 1, "in.mp4")
        command = ffmpeg.build_segment_copy_command("in.mp4", "out.mp4", segment)
        self.assertIn("-c", command)
        self.assertEqual(command[command.index("-c") + 1], "copy")
        self.assertEqual(command[command.index("-ss") + 1], "4.000")
        self.assertEqual(command[command.index("-t") + 1], "5.000")
        self.assertIn("make_zero", command)


def _strip(
    name: str,
    frame_start: float,
    frame_final_end: float,
    frame_final_start: Optional[float] = None,
    source_path: str = "",
) -> "ffmpeg.StripRange":
    """Build a :class:`StripRange` with sensible defaults for the tests.

    Args:
        name: Strip name.
        frame_start: Timeline frame holding source frame 0.
        frame_final_end: One past the last visible timeline frame.
        frame_final_start: First visible timeline frame; defaults to
            ``frame_start`` (an untrimmed strip).
        source_path: Movie file behind the strip; defaults to ``<name>.mp4``.

    Returns:
        The strip range.
    """
    return ffmpeg.StripRange(
        name=name,
        source_path=source_path or f"{name}.mp4",
        frame_start=frame_start,
        frame_final_start=(
            frame_start if frame_final_start is None else frame_final_start
        ),
        frame_final_end=frame_final_end,
    )


class MultiStripMarkerTests(unittest.TestCase):
    """Tests for marker splitting across every checked strip."""

    def test_every_strip_is_processed(self) -> None:
        """Two strips with their own markers both produce segments."""
        strips = [_strip("A", 0, 100), _strip("B", 100, 200)]
        markers = [(0.0, "a1"), (50.0, "a2"), (100.0, "b1"), (150.0, "b2")]

        segments = ffmpeg.build_marker_segments(strips, markers, fps=10.0)

        self.assertEqual([segment.name for segment in segments],
                         ["a1", "a2", "b1", "b2"])
        self.assertEqual(
            [segment.strip_name for segment in segments], ["A", "A", "B", "B"]
        )

    def test_markers_are_mapped_into_each_own_source(self) -> None:
        """Every strip maps timeline frames onto its own file, from zero."""
        strips = [_strip("A", 0, 100), _strip("B", 100, 200)]
        markers = [(0.0, "a1"), (100.0, "b1"), (150.0, "b2")]

        segments = ffmpeg.build_marker_segments(strips, markers, fps=10.0)

        by_name = {segment.name: segment for segment in segments}
        self.assertEqual((by_name["a1"].start, by_name["a1"].end), (0.0, 10.0))
        # B starts at timeline frame 100, so its first marker is source time 0.
        self.assertEqual((by_name["b1"].start, by_name["b1"].end), (0.0, 5.0))
        self.assertEqual((by_name["b2"].start, by_name["b2"].end), (5.0, 10.0))

    def test_each_source_path_follows_its_strip(self) -> None:
        """Segments carry the file of the strip they were cut from."""
        strips = [
            _strip("A", 0, 100, source_path="/clips/one.mp4"),
            _strip("B", 100, 200, source_path="/clips/two.mp4"),
        ]
        markers = [(10.0, "a"), (110.0, "b")]

        segments = ffmpeg.build_marker_segments(strips, markers, fps=10.0)

        self.assertEqual(segments[0].source_path, "/clips/one.mp4")
        self.assertEqual(segments[1].source_path, "/clips/two.mp4")

    def test_indices_run_continuously_across_strips(self) -> None:
        """A template without {strip} still yields unique file names."""
        strips = [_strip("A", 0, 100), _strip("B", 100, 200)]
        markers = [(0.0, "m"), (50.0, "m"), (100.0, "m")]

        segments = ffmpeg.build_marker_segments(strips, markers, fps=10.0)

        self.assertEqual([segment.index for segment in segments], [1, 2, 3])
        names = {
            ffmpeg.format_segment_filename(
                "{name}_{index}", ".mp4", name=segment.name, index=segment.index
            )
            for segment in segments
        }
        self.assertEqual(len(names), 3)

    def test_strip_token_disambiguates_shared_marker_names(self) -> None:
        """Identically named markers on different strips stay apart."""
        strips = [_strip("takeA", 0, 100), _strip("takeB", 100, 200)]
        markers = [(0.0, "intro"), (100.0, "intro")]

        segments = ffmpeg.build_marker_segments(strips, markers, fps=10.0)
        names = [
            ffmpeg.format_segment_filename(
                "{strip}_{name}",
                ".mp4",
                name=segment.name,
                strip=segment.strip_name,
            )
            for segment in segments
        ]
        self.assertEqual(names, ["takeA_intro.mp4", "takeB_intro.mp4"])

    def test_markers_outside_every_strip_are_ignored(self) -> None:
        """A marker in a gap between strips belongs to neither."""
        strips = [_strip("A", 0, 100), _strip("B", 200, 300)]
        markers = [(10.0, "inside"), (150.0, "gap"), (400.0, "after")]

        segments = ffmpeg.build_marker_segments(strips, markers, fps=10.0)

        self.assertEqual([segment.name for segment in segments], ["inside"])

    def test_last_marker_of_a_strip_runs_to_that_strip_end(self) -> None:
        """A strip's final segment stops at its own end, not the next strip."""
        strips = [_strip("A", 0, 100), _strip("B", 100, 200)]
        markers = [(80.0, "a_last"), (100.0, "b_first")]

        segments = ffmpeg.build_marker_segments(strips, markers, fps=10.0)

        self.assertEqual(segments[0].name, "a_last")
        self.assertEqual(segments[0].end, 10.0)

    def test_unsorted_markers_are_ordered_per_strip(self) -> None:
        """Marker order in the scene does not affect the result."""
        strips = [_strip("A", 0, 100)]
        markers = [(60.0, "third"), (0.0, "first"), (30.0, "second")]

        segments = ffmpeg.build_marker_segments(strips, markers, fps=10.0)

        self.assertEqual(
            [segment.name for segment in segments], ["first", "second", "third"]
        )
        self.assertEqual([segment.start for segment in segments], [0.0, 3.0, 6.0])

    def test_trimmed_head_is_never_exported(self) -> None:
        """A marker in the trimmed-away head clamps to the visible start."""
        # The file starts at timeline frame 0 but the strip only shows from 40.
        strips = [_strip("A", 0, 100, frame_final_start=40)]
        markers = [(10.0, "in_head"), (60.0, "visible")]

        segments = ffmpeg.build_marker_segments(strips, markers, fps=10.0)

        self.assertEqual(segments[0].name, "in_head")
        self.assertEqual(segments[0].start, 4.0)
        self.assertEqual(segments[0].end, 6.0)
        self.assertEqual(segments[1].start, 6.0)

    def test_zero_length_segments_are_dropped(self) -> None:
        """Two markers on the same frame do not produce an empty export."""
        strips = [_strip("A", 0, 100)]
        markers = [(50.0, "one"), (50.0, "two")]

        segments = ffmpeg.build_marker_segments(strips, markers, fps=10.0)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].duration, 5.0)

    def test_strip_without_markers_is_skipped(self) -> None:
        """A strip nobody marked contributes nothing, and does not fail."""
        strips = [_strip("A", 0, 100), _strip("B", 100, 200)]
        markers = [(100.0, "only_b")]

        segments = ffmpeg.build_marker_segments(strips, markers, fps=10.0)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].strip_name, "B")

    def test_no_markers_at_all_returns_empty(self) -> None:
        """An empty marker list is not an error, just nothing to export."""
        self.assertEqual(
            ffmpeg.build_marker_segments([_strip("A", 0, 100)], [], fps=10.0), []
        )

    def test_start_index_is_respected(self) -> None:
        """Segments can continue the numbering of an earlier batch."""
        segments = ffmpeg.build_marker_segments(
            [_strip("A", 0, 100)], [(0.0, "m")], fps=10.0, start_index=5
        )
        self.assertEqual(segments[0].index, 5)

    def test_markers_within_strip_filters_by_range(self) -> None:
        """The range is half open: the end frame belongs to the next strip."""
        strip = _strip("A", 10, 20)
        selected = ffmpeg.markers_within_strip(
            [(9.0, "before"), (10.0, "start"), (19.0, "last"), (20.0, "end")], strip
        )
        self.assertEqual([name for _, name in selected], ["start", "last"])

    def test_invalid_fps_raises(self) -> None:
        """A broken scene frame rate is reported instead of dividing by zero."""
        with self.assertRaises(ValueError):
            ffmpeg.build_marker_segments([_strip("A", 0, 100)], [(0.0, "m")], fps=0.0)

    def test_unnamed_marker_gets_a_fallback_name(self) -> None:
        """An empty marker name still produces a usable file name."""
        segments = ffmpeg.build_marker_segments(
            [_strip("A", 0, 100)], [(0.0, "")], fps=10.0
        )
        self.assertEqual(segments[0].name, "marker1")


class ProxyTests(unittest.TestCase):
    """Tests for the editing proxy builder."""

    def test_proxy_is_720p30_crf_h264(self) -> None:
        """The proxy spec is fixed and independent of the user's settings."""
        command = ffmpeg.build_proxy_command("in.mkv", "out.mp4")
        self.assertIn("libx264", command)
        self.assertEqual(command[command.index("-crf") + 1], str(ffmpeg.PROXY_CRF))
        filter_chain = command[command.index("-vf") + 1]
        self.assertIn(f"fps=fps={ffmpeg.PROXY_FPS}", filter_chain)
        width, height = ffmpeg.PROXY_RESOLUTION
        self.assertIn(f"crop={width}:{height}", filter_chain)

    def test_proxy_enables_faststart(self) -> None:
        """Scrubbing needs the MP4 index at the front of the file."""
        command = ffmpeg.build_proxy_command("in.mkv", "out.mp4")
        self.assertEqual(command[command.index("-movflags") + 1], "+faststart")


class MultiExportPresetTests(unittest.TestCase):
    """Tests for the KSL Learning Pack variant loop."""

    def test_pack_produces_three_variants(self) -> None:
        """Original, mirrored and slowed, in that order."""
        suffixes = [variant.suffix for variant in ffmpeg.KSL_LEARNING_PACK]
        self.assertEqual(suffixes, ["_orig", "_mirror", "_slow"])

    def test_variant_settings_apply_mirror_and_speed(self) -> None:
        """Each variant differs from the base only where it should."""
        base = ffmpeg.EncodeSettings()
        expanded = ffmpeg.iter_variant_settings(base)
        self.assertEqual(len(expanded), 3)

        original, mirrored, slowed = expanded
        self.assertFalse(original[1].use_mirror)
        self.assertEqual(original[1].speed, 1.0)
        self.assertTrue(mirrored[1].use_mirror)
        self.assertEqual(mirrored[1].speed, 1.0)
        self.assertFalse(slowed[1].use_mirror)
        self.assertEqual(slowed[1].speed, 0.75)

    def test_mirror_is_xor_against_the_user_toggle(self) -> None:
        """An already mirrored source still yields one of each orientation."""
        base = ffmpeg.EncodeSettings(use_mirror=True)
        orientations = [
            settings.use_mirror for _, settings in ffmpeg.iter_variant_settings(base)
        ]
        self.assertEqual(orientations, [True, False, True])

    def test_base_settings_are_not_mutated(self) -> None:
        """Expanding the preset leaves the original settings untouched."""
        base = ffmpeg.EncodeSettings(use_mirror=False, speed=1.0)
        ffmpeg.iter_variant_settings(base)
        self.assertFalse(base.use_mirror)
        self.assertEqual(base.speed, 1.0)

    def test_slow_variant_emits_setpts_and_atempo(self) -> None:
        """Slow motion must retime both the video and the audio."""
        base = ffmpeg.EncodeSettings(use_nvenc=False)
        _, slowed = ffmpeg.iter_variant_settings(base)[2]
        command = ffmpeg.build_ffmpeg_command(slowed, "in.mp4", "out_slow.mp4")
        self.assertIn("setpts=1.33333*PTS", command[command.index("-vf") + 1])
        self.assertEqual(command[command.index("-af") + 1], "atempo=0.75")

    def test_speed_filter_is_skipped_at_real_time(self) -> None:
        """Real time output carries no retiming filters."""
        self.assertEqual(ffmpeg.build_speed_filters(1.0), [])
        self.assertEqual(ffmpeg.build_atempo_filters(1.0), [])

    def test_setpts_runs_after_the_mirror(self) -> None:
        """Filter order stays mirror, retime, scale."""
        settings = ffmpeg.EncodeSettings(use_mirror=True, speed=0.5)
        filters = ffmpeg.build_video_filters(settings)
        self.assertEqual(filters[0], "hflip")
        self.assertEqual(filters[1], "setpts=2*PTS")
        self.assertTrue(filters[2].startswith("fps="))

    def test_extreme_speeds_chain_atempo(self) -> None:
        """``atempo`` clamps at 0.5, so slower rates need a chain."""
        self.assertEqual(
            ffmpeg.build_atempo_filters(0.25), ["atempo=0.5", "atempo=0.5"]
        )
        self.assertEqual(ffmpeg.build_atempo_filters(4.0), ["atempo=2", "atempo=2"])

    def test_non_positive_speed_raises(self) -> None:
        """A zero or negative rate is rejected before reaching FFmpeg."""
        with self.assertRaises(ValueError):
            ffmpeg.build_speed_filters(0.0)
        with self.assertRaises(ValueError):
            ffmpeg.build_atempo_filters(-1.0)

    def test_silent_slow_variant_has_no_audio_filter(self) -> None:
        """Dropping audio means no ``-af`` even in slow motion."""
        settings = ffmpeg.EncodeSettings(speed=0.75, remove_audio=True)
        command = ffmpeg.build_ffmpeg_command(settings, "in.mp4", "out.mp4")
        self.assertIn("-an", command)
        self.assertNotIn("-af", command)


class MetadataTests(unittest.TestCase):
    """Tests for the archive metadata tags."""

    def test_tags_are_written_in_a_stable_order(self) -> None:
        """Tag order follows :data:`METADATA_KEYS`, not dict insertion."""
        arguments = ffmpeg.build_metadata_arguments(
            {"comment": "c", "artist": "b", "title": "a"}, "MP4"
        )
        self.assertEqual(
            arguments,
            [
                "-metadata", "title=a",
                "-metadata", "artist=b",
                "-metadata", "comment=c",
            ],
        )

    def test_empty_values_are_dropped(self) -> None:
        """Blank fields produce no arguments."""
        arguments = ffmpeg.build_metadata_arguments(
            {"title": "kept", "artist": "", "comment": None}, "MOV"
        )
        self.assertEqual(arguments, ["-metadata", "title=kept"])

    def test_unsupported_containers_get_no_tags(self) -> None:
        """GIF and WebP muxers drop tags, so nothing is sent."""
        for output_format in ("GIF", "WEBP", "AVI"):
            self.assertEqual(
                ffmpeg.build_metadata_arguments({"title": "x"}, output_format), []
            )

    def test_newlines_are_flattened(self) -> None:
        """A multi-line value cannot terminate the tag early."""
        arguments = ffmpeg.build_metadata_arguments({"title": "a\nb"}, "MKV")
        self.assertEqual(arguments, ["-metadata", "title=a b"])

    def test_no_metadata_means_no_arguments(self) -> None:
        """``None`` and an empty mapping both mean "write nothing"."""
        self.assertEqual(ffmpeg.build_metadata_arguments(None, "MP4"), [])
        self.assertEqual(ffmpeg.build_metadata_arguments({}, "MP4"), [])

    def test_tokens_are_substituted(self) -> None:
        """Metadata templates share the segment token vocabulary."""
        resolved = ffmpeg.resolve_metadata_tokens(
            {"title": "{name}", "comment": "{scene} / {date}"},
            {"name": "take1", "scene": "Archive", "date": "2026-09-17"},
        )
        self.assertEqual(resolved["title"], "take1")
        self.assertEqual(resolved["comment"], "Archive / 2026-09-17")

    def test_unknown_token_keeps_the_raw_text(self) -> None:
        """A bad metadata token degrades, it does not fail the export."""
        resolved = ffmpeg.resolve_metadata_tokens({"title": "{nope}"}, {"name": "x"})
        self.assertEqual(resolved["title"], "{nope}")

    def test_empty_values_are_not_resolved(self) -> None:
        """Blank fields never reach the output mapping."""
        resolved = ffmpeg.resolve_metadata_tokens({"title": "", "artist": None}, {})
        self.assertEqual(resolved, {})

    def test_command_includes_metadata_before_the_output(self) -> None:
        """The tags land on the output file, not the input."""
        settings = ffmpeg.EncodeSettings(metadata={"title": "Archive"})
        command = ffmpeg.build_ffmpeg_command(settings, "in.mp4", "out.mp4")
        self.assertIn("title=Archive", command)
        self.assertLess(command.index("title=Archive"), command.index("out.mp4"))


class BadgeTests(unittest.TestCase):
    """Tests for the archive badge overlay."""

    def test_badge_is_appended_after_the_other_overlays(self) -> None:
        """The badge is drawn last so it sits on top."""
        settings = ffmpeg.EncodeSettings(
            show_info=True, badge_text="VRChat KSL Archive", font_path="/f/a.ttf"
        )
        filters = ffmpeg.build_video_filters(settings)
        self.assertIn("text='VRChat KSL Archive'", filters[-1])

    def test_corner_anchors(self) -> None:
        """Each position maps to the matching drawtext expression."""
        expectations = {
            "BR": ("w-text_w-", "h-text_h-"),
            "BL": (":x=", "h-text_h-"),
            "TR": ("w-text_w-", ":y="),
            "TL": (":x=", ":y="),
        }
        for position, fragments in expectations.items():
            settings = ffmpeg.EncodeSettings(
                badge_text="A", badge_position=position, font_path="/f/a.ttf"
            )
            badge = ffmpeg.build_badge_filter(settings, "/f/a.ttf")
            for fragment in fragments:
                self.assertIn(fragment, badge)

    def test_unknown_position_falls_back_to_bottom_right(self) -> None:
        """A stale enum value must not break the filter graph."""
        settings = ffmpeg.EncodeSettings(badge_text="A", badge_position="NOPE")
        self.assertIn("w-text_w-", ffmpeg.build_badge_filter(settings, "/f/a.ttf"))

    def test_badge_is_forced_to_a_single_line(self) -> None:
        """Newlines would break the corner anchor maths."""
        settings = ffmpeg.EncodeSettings(badge_text="line1\nline2")
        badge = ffmpeg.build_badge_filter(settings, "/f/a.ttf")
        self.assertIn("text='line1 line2'", badge)

    def test_badge_opacity_reaches_the_box(self) -> None:
        """The configured opacity is applied to the background box."""
        settings = ffmpeg.EncodeSettings(badge_text="A", badge_opacity=0.75)
        self.assertIn("boxcolor=black@0.75", ffmpeg.build_badge_filter(settings, "f"))

    def test_badge_needs_a_font(self) -> None:
        """Without a font the badge is skipped like the other overlays."""
        settings = ffmpeg.EncodeSettings(badge_text="A", font_path=None)
        self.assertNotIn("drawtext", ffmpeg.build_filter_chain(settings))

    def test_no_badge_text_means_no_badge(self) -> None:
        """An empty caption disables the overlay entirely."""
        settings = ffmpeg.EncodeSettings(badge_text="", font_path="/f/a.ttf")
        self.assertNotIn("drawtext", ffmpeg.build_filter_chain(settings))


if __name__ == "__main__":
    unittest.main()
