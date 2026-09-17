# SPDX-FileCopyrightText: 2026 Simulacre
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the pure FFmpeg command construction layer.

The tests run in a plain CPython interpreter — no Blender required::

    python -m unittest discover -s tests -v

``blender_video_encoder/utils/ffmpeg.py`` is loaded straight from its path so
that importing it does not pull in the add-on package (whose other modules do
need ``bpy``).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "blender_video_encoder",
    "utils",
    "ffmpeg.py",
)


def _load_ffmpeg_module() -> types.ModuleType:
    """Import ``utils/ffmpeg.py`` standalone, without the add-on package.

    Returns:
        The imported module.

    Raises:
        ImportError: If the module file cannot be loaded.
    """
    spec = importlib.util.spec_from_file_location("qve_ffmpeg", _MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"모듈을 불러올 수 없습니다: {_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ffmpeg = _load_ffmpeg_module()


class FakeProps:
    """Minimal stand-in for ``GeminiProperties`` used by the tests."""

    def __init__(self, **overrides: object) -> None:
        """Initialise with add-on defaults, overridden by keyword arguments.

        Args:
            **overrides: Attribute values replacing the defaults.
        """
        self.tool_mode = "TRANSCODE"
        self.output_format = "MP4"
        self.quality_preset = "MID"
        self.use_nvenc = False
        self.vp_codec = "H264"
        self.use_mirror = False
        self.remove_audio = False
        self.show_markers = False
        self.show_info = False
        self.marker_color = (1.0, 1.0, 1.0)
        self.marker_font_size = 24
        self.info_color = (1.0, 0.9, 0.0)
        self.info_bg_color = (0.0, 0.0, 0.0)
        self.info_bg_opacity = 0.3
        self.info_font_size = 18
        self.use_size_limit = False
        self.target_size_mb = 8.0
        for key, value in overrides.items():
            setattr(self, key, value)


class PureHelperTests(unittest.TestCase):
    """Tests for the small pure helpers."""

    def test_color_to_hex(self) -> None:
        """Float RGB colors become lowercase ``0xRRGGBB`` literals."""
        self.assertEqual(ffmpeg.color_to_hex((1.0, 1.0, 1.0)), "0xffffff")
        self.assertEqual(ffmpeg.color_to_hex((0.0, 0.0, 0.0)), "0x000000")
        self.assertEqual(ffmpeg.color_to_hex((1.0, 0.0, 0.5)), "0xff007f")

    def test_color_to_hex_clamps_out_of_range(self) -> None:
        """Values outside ``0..1`` are clamped instead of overflowing."""
        self.assertEqual(ffmpeg.color_to_hex((2.0, -1.0, 0.0)), "0xff0000")

    def test_get_resolution(self) -> None:
        """Known presets resolve, unknown ones fall back to 720p."""
        self.assertEqual(ffmpeg.get_resolution("FHD"), (1920, 1080))
        self.assertEqual(ffmpeg.get_resolution("IG_STORY"), (1080, 1920))
        self.assertEqual(ffmpeg.get_resolution("NOPE"), ffmpeg.DEFAULT_RESOLUTION)

    def test_format_fps(self) -> None:
        """Whole frame rates lose their decimals, NTSC rates keep them."""
        self.assertEqual(ffmpeg.format_fps(30.0), "30")
        self.assertEqual(ffmpeg.format_fps(24), "24")
        self.assertEqual(ffmpeg.format_fps(29.97002997), "29.97")

    def test_escape_font_path(self) -> None:
        """Windows separators and drive colons are escaped for ``drawtext``."""
        self.assertEqual(
            ffmpeg.escape_font_path("C:\\Windows\\Fonts\\arial.ttf"),
            "C\\:/Windows/Fonts/arial.ttf",
        )

    def test_get_extension(self) -> None:
        """Every output format maps to an extension, unknown ones to ``.mp4``."""
        self.assertEqual(ffmpeg.get_extension("WEBP"), ".webp")
        self.assertEqual(ffmpeg.get_extension("UNKNOWN"), ".mp4")


class CodecSelectionTests(unittest.TestCase):
    """Tests for :func:`select_video_codec`."""

    def test_nvenc_wins(self) -> None:
        """Hardware encoding takes precedence over the lossless codecs."""
        settings = ffmpeg.EncodeSettings(use_nvenc=True, lossless_codec="HUFFYUV")
        self.assertEqual(ffmpeg.select_video_codec(settings), "h264_nvenc")

    def test_lossless_codec_used_without_nvenc(self) -> None:
        """Without NVENC the requested lossless encoder is used."""
        settings = ffmpeg.EncodeSettings(use_nvenc=False, lossless_codec="QTRLE")
        self.assertEqual(ffmpeg.select_video_codec(settings), "qtrle")

    def test_default_is_libx264(self) -> None:
        """Software H.264 is the fallback."""
        settings = ffmpeg.EncodeSettings(use_nvenc=False)
        self.assertEqual(ffmpeg.select_video_codec(settings), "libx264")


class FilterChainTests(unittest.TestCase):
    """Tests for the video filter chain."""

    def test_scale_and_crop_target_the_requested_size(self) -> None:
        """The chain resamples, scales to cover, then crops to the exact size."""
        settings = ffmpeg.EncodeSettings(width=1920, height=1080, fps="24")
        filters = ffmpeg.build_video_filters(settings)
        self.assertEqual(filters[0], "fps=fps=24")
        self.assertIn("flags=lanczos", filters[1])
        self.assertEqual(filters[-1], "crop=1920:1080")

    def test_mirror_runs_before_everything_else(self) -> None:
        """``hflip`` comes first so overlay text is never mirrored."""
        settings = ffmpeg.EncodeSettings(use_mirror=True)
        self.assertEqual(ffmpeg.build_video_filters(settings)[0], "hflip")

    def test_overlays_are_skipped_without_a_font(self) -> None:
        """No font means no ``drawtext`` filters, not a broken command."""
        settings = ffmpeg.EncodeSettings(
            show_markers=True, show_info=True, font_path=None
        )
        chain = ffmpeg.build_filter_chain(settings)
        self.assertNotIn("drawtext", chain)

    def test_markers_emit_one_filter_each(self) -> None:
        """Each of the nine composition markers gets its own filter."""
        settings = ffmpeg.EncodeSettings(
            show_markers=True, font_path="/fonts/arial.ttf"
        )
        filters = ffmpeg.build_video_filters(settings)
        markers = [item for item in filters if item.startswith("drawtext")]
        self.assertEqual(len(markers), len(ffmpeg.MARKER_LABELS))
        self.assertIn("text='C'", "".join(markers))

    def test_info_overlay_uses_the_configured_colors(self) -> None:
        """The HUD filter carries the text color, box color and opacity."""
        settings = ffmpeg.EncodeSettings(
            show_info=True,
            font_path="/fonts/arial.ttf",
            info_color=(1.0, 0.0, 0.0),
            info_bg_color=(0.0, 0.0, 1.0),
            info_bg_opacity=0.5,
        )
        info_filter = ffmpeg.build_video_filters(settings)[-1]
        self.assertIn("fontcolor=0xff0000", info_filter)
        self.assertIn("boxcolor=0x0000ff@0.5", info_filter)


class CommandBuildTests(unittest.TestCase):
    """Tests for :func:`build_ffmpeg_command`."""

    def test_basic_mp4_command(self) -> None:
        """A default MP4 encode carries the expected codec and bitrate flags."""
        settings = ffmpeg.EncodeSettings(use_nvenc=False, quality_preset="HIGH")
        command = ffmpeg.build_ffmpeg_command(settings, "in.mov", "out.mp4")
        self.assertEqual(command[:5], ["ffmpeg", "-y", "-i", "in.mov", "-threads"])
        self.assertEqual(command[-1], "out.mp4")
        self.assertIn("libx264", command)
        self.assertEqual(command[command.index("-b:v") + 1], "6M")
        self.assertIn("-preset", command)
        self.assertEqual(command[command.index("-preset") + 1], "veryfast")

    def test_low_quality_uses_a_lower_bitrate_than_medium(self) -> None:
        """The three quality presets map to three distinct bitrates."""
        bitrates = {
            preset: ffmpeg.QUALITY_BITRATES[preset]
            for preset in ("HIGH", "MID", "LOW")
        }
        self.assertEqual(len(set(bitrates.values())), 3)

    def test_nvenc_command_uses_hardware_preset(self) -> None:
        """NVENC encodes ask for the ``p4``/``hq`` tuning."""
        settings = ffmpeg.EncodeSettings(use_nvenc=True)
        command = ffmpeg.build_ffmpeg_command(settings, "in.mp4", "out.mp4")
        self.assertIn("h264_nvenc", command)
        self.assertEqual(command[command.index("-preset") + 1], "p4")
        self.assertEqual(command[command.index("-tune") + 1], "hq")

    def test_audio_is_copied_by_default(self) -> None:
        """Video containers keep an AAC audio track unless asked otherwise."""
        command = ffmpeg.build_ffmpeg_command(
            ffmpeg.EncodeSettings(), "in.mp4", "out.mp4"
        )
        self.assertIn("-c:a", command)
        self.assertNotIn("-an", command)

    def test_remove_audio_flag(self) -> None:
        """``remove_audio`` replaces the audio codec with ``-an``."""
        settings = ffmpeg.EncodeSettings(remove_audio=True)
        command = ffmpeg.build_ffmpeg_command(settings, "in.mp4", "out.mp4")
        self.assertIn("-an", command)
        self.assertNotIn("-c:a", command)

    def test_gif_drops_audio_and_builds_a_palette(self) -> None:
        """GIF cannot carry audio and needs the two-pass palette filter."""
        settings = ffmpeg.EncodeSettings(output_format="GIF")
        command = ffmpeg.build_ffmpeg_command(settings, "in.mp4", "out.gif")
        self.assertIn("-an", command)
        self.assertNotIn("-c:a", command)
        self.assertIn("palettegen", command[command.index("-filter_complex") + 1])

    def test_webp_drops_audio_and_loops(self) -> None:
        """WebP output uses ``libwebp`` with infinite looping and no audio."""
        settings = ffmpeg.EncodeSettings(output_format="WEBP")
        command = ffmpeg.build_ffmpeg_command(settings, "in.mp4", "out.webp")
        self.assertIn("-an", command)
        self.assertIn("libwebp", command)
        self.assertEqual(command[command.index("-loop") + 1], "0")

    def test_custom_binary_is_used(self) -> None:
        """The resolved FFmpeg path becomes ``argv[0]``."""
        command = ffmpeg.build_ffmpeg_command(
            ffmpeg.EncodeSettings(), "in.mp4", "out.mp4", "/opt/ffmpeg/bin/ffmpeg"
        )
        self.assertEqual(command[0], "/opt/ffmpeg/bin/ffmpeg")

    def test_unknown_format_raises(self) -> None:
        """An unsupported container is rejected before launching FFmpeg."""
        settings = ffmpeg.EncodeSettings(output_format="OGG")
        with self.assertRaises(ValueError):
            ffmpeg.build_ffmpeg_command(settings, "in.mp4", "out.ogg")


class EncodeSettingsFromPropsTests(unittest.TestCase):
    """Tests for :meth:`EncodeSettings.from_props`."""

    def test_transcode_mode_ignores_the_viewport_codec(self) -> None:
        """The lossless viewport codecs never apply to file conversion."""
        props = FakeProps(tool_mode="TRANSCODE", vp_codec="HUFFYUV")
        settings = ffmpeg.EncodeSettings.from_props(props, 1920, 1080, "30")
        self.assertEqual(settings.lossless_codec, "")

    def test_viewport_mode_keeps_the_lossless_codec(self) -> None:
        """In viewport mode a lossless codec choice is carried over."""
        props = FakeProps(tool_mode="VIEWPORT", vp_codec="QTRLE")
        settings = ffmpeg.EncodeSettings.from_props(props, 1920, 1080, "30")
        self.assertEqual(settings.lossless_codec, "QTRLE")
        self.assertEqual(ffmpeg.select_video_codec(settings), "qtrle")

    def test_size_and_fps_are_taken_from_the_caller(self) -> None:
        """Resolution and FPS are resolved by the caller, not by the group."""
        props = FakeProps()
        settings = ffmpeg.EncodeSettings.from_props(props, 1080, 1920, "29.97")
        self.assertEqual((settings.width, settings.height), (1080, 1920))
        self.assertEqual(settings.fps, "29.97")


class SizeLimitTests(unittest.TestCase):
    """Tests for the "Discord 8MB Match" bitrate calculator."""

    def test_bitrate_fits_the_budget(self) -> None:
        """Encoding at the computed bitrate stays under the size budget."""
        target_mb = 8.0
        duration = 60.0
        audio_kbps = 192
        video_kbps = ffmpeg.calculate_video_bitrate_kbps(
            target_mb, duration, audio_kbps
        )
        total_bytes = (video_kbps + audio_kbps) * 1000 * duration / 8
        self.assertLess(total_bytes, target_mb * 1024 * 1024)

    def test_longer_clips_get_lower_bitrates(self) -> None:
        """The same budget spread over more time means less bitrate."""
        short = ffmpeg.calculate_video_bitrate_kbps(8.0, 30.0)
        long = ffmpeg.calculate_video_bitrate_kbps(8.0, 120.0)
        self.assertGreater(short, long)

    def test_silent_output_gets_the_whole_budget(self) -> None:
        """Dropping audio frees its share of the budget for the video."""
        with_audio = ffmpeg.calculate_video_bitrate_kbps(8.0, 60.0, 192)
        without_audio = ffmpeg.calculate_video_bitrate_kbps(8.0, 60.0, 0)
        self.assertEqual(without_audio - with_audio, 192)

    def test_minimum_bitrate_is_enforced(self) -> None:
        """An over-long clip still produces a playable bitrate, not zero."""
        bitrate = ffmpeg.calculate_video_bitrate_kbps(8.0, 100000.0)
        self.assertEqual(bitrate, ffmpeg.MIN_VIDEO_BITRATE_KBPS)

    def test_zero_duration_raises(self) -> None:
        """An unknown duration is an error, not a division by zero."""
        with self.assertRaises(ValueError):
            ffmpeg.calculate_video_bitrate_kbps(8.0, 0.0)

    def test_command_caps_the_bitrate_when_size_matching(self) -> None:
        """Size matching switches rate control from average to capped."""
        settings = ffmpeg.EncodeSettings(
            use_nvenc=False, target_size_mb=8.0, duration_seconds=60.0
        )
        command = ffmpeg.build_ffmpeg_command(settings, "in.mp4", "out.mp4")
        expected = f"{ffmpeg.resolve_video_bitrate_kbps(settings)}k"
        self.assertEqual(command[command.index("-b:v") + 1], expected)
        self.assertEqual(command[command.index("-maxrate") + 1], expected)
        self.assertIn("-bufsize", command)

    def test_quality_preset_used_when_duration_is_unknown(self) -> None:
        """Without a duration the encode falls back to the quality preset."""
        settings = ffmpeg.EncodeSettings(
            use_nvenc=False, target_size_mb=8.0, duration_seconds=None
        )
        command = ffmpeg.build_ffmpeg_command(settings, "in.mp4", "out.mp4")
        self.assertEqual(command[command.index("-b:v") + 1], "3M")
        self.assertNotIn("-maxrate", command)

    def test_from_props_picks_up_the_size_budget(self) -> None:
        """The property group's size toggle reaches the settings object."""
        props = FakeProps(use_size_limit=True, target_size_mb=25.0)
        settings = ffmpeg.EncodeSettings.from_props(
            props, 1920, 1080, "30", duration_seconds=10.0
        )
        self.assertEqual(settings.target_size_mb, 25.0)
        self.assertEqual(settings.duration_seconds, 10.0)


class ThumbnailTests(unittest.TestCase):
    """Tests for the VRChat world thumbnail export."""

    def test_webp_thumbnail_command(self) -> None:
        """A WebP thumbnail is a single frame at the VRChat resolution."""
        command = ffmpeg.build_thumbnail_command("in.mp4", "out.webp", "WEBP")
        self.assertEqual(command[command.index("-frames:v") + 1], "1")
        self.assertIn("libwebp", command)
        self.assertIn("-an", command)
        width, height = ffmpeg.THUMBNAIL_RESOLUTION
        self.assertIn(f"crop={width}:{height}", command[command.index("-vf") + 1])

    def test_jpg_thumbnail_command(self) -> None:
        """A JPG thumbnail uses MJPEG with a compatible pixel format."""
        command = ffmpeg.build_thumbnail_command(
            "in.mp4", "out.jpg", "JPG", quality=100
        )
        self.assertIn("mjpeg", command)
        self.assertEqual(command[command.index("-q:v") + 1], "2")
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuvj420p")

    def test_seek_is_placed_before_the_input(self) -> None:
        """``-ss`` must precede ``-i`` so FFmpeg seeks instead of decoding."""
        command = ffmpeg.build_thumbnail_command(
            "in.mp4", "out.webp", seek_seconds=12.5
        )
        self.assertLess(command.index("-ss"), command.index("-i"))
        self.assertEqual(command[command.index("-ss") + 1], "12.500")

    def test_no_seek_argument_without_a_timestamp(self) -> None:
        """Grabbing the first frame needs no seek at all."""
        command = ffmpeg.build_thumbnail_command("in.mp4", "out.webp")
        self.assertNotIn("-ss", command)

    def test_jpeg_quality_scale_is_inverted_and_clamped(self) -> None:
        """Quality 100 maps to the best ``-q:v``, 0 to the worst."""
        self.assertEqual(ffmpeg.jpeg_quality_scale(100), 2)
        self.assertEqual(ffmpeg.jpeg_quality_scale(0), 31)
        self.assertEqual(ffmpeg.jpeg_quality_scale(150), 2)

    def test_unknown_thumbnail_format_raises(self) -> None:
        """Only the formats VRChat accepts are allowed."""
        with self.assertRaises(ValueError):
            ffmpeg.build_thumbnail_command("in.mp4", "out.png", "PNG")


class SideBySideTests(unittest.TestCase):
    """Tests for the A/B comparison compositing."""

    def test_filter_graph_stacks_two_inputs(self) -> None:
        """Both inputs are scaled to half width and joined with ``hstack``."""
        settings = ffmpeg.EncodeSettings(width=1920, height=1080)
        graph = ffmpeg.build_side_by_side_filter(settings, labels=None)
        self.assertIn("[0:v]", graph)
        self.assertIn("[1:v]", graph)
        self.assertEqual(graph.count("crop=960:1080"), 2)
        self.assertTrue(graph.endswith("[left][right]hstack=inputs=2[v]"))

    def test_odd_widths_stay_even_per_half(self) -> None:
        """Half-width is rounded down to an even number for yuv420p."""
        settings = ffmpeg.EncodeSettings(width=1278, height=720)
        graph = ffmpeg.build_side_by_side_filter(settings, labels=None)
        self.assertEqual(graph.count("crop=638:720"), 2)

    def test_labels_are_burned_in_when_a_font_exists(self) -> None:
        """Each half gets its own caption."""
        settings = ffmpeg.EncodeSettings(font_path="/fonts/arial.ttf")
        graph = ffmpeg.build_side_by_side_filter(settings)
        self.assertIn("text='ORIGINAL'", graph)
        self.assertIn("text='SILHOUETTE'", graph)

    def test_labels_are_skipped_without_a_font(self) -> None:
        """No font means no captions, not a broken graph."""
        settings = ffmpeg.EncodeSettings(font_path=None)
        graph = ffmpeg.build_side_by_side_filter(settings)
        self.assertNotIn("drawtext", graph)

    def test_command_maps_the_stacked_output(self) -> None:
        """The stacked pad is mapped and the left clip provides the audio."""
        settings = ffmpeg.EncodeSettings(use_nvenc=False)
        command = ffmpeg.build_side_by_side_command(
            settings, "a.mp4", "b.mp4", "out.mp4"
        )
        self.assertEqual(command.count("-i"), 2)
        self.assertEqual(command[command.index("-map") + 1], "[v]")
        self.assertIn("0:a?", command)
        self.assertIn("libx264", command)
        self.assertEqual(command[-1], "out.mp4")

    def test_remove_audio_drops_the_audio_map(self) -> None:
        """Silent A/B output maps no audio stream at all."""
        settings = ffmpeg.EncodeSettings(remove_audio=True)
        command = ffmpeg.build_side_by_side_command(
            settings, "a.mp4", "b.mp4", "out.mp4"
        )
        self.assertIn("-an", command)
        self.assertNotIn("0:a?", command)

    def test_image_formats_are_rejected(self) -> None:
        """GIF/WebP cannot carry an A/B comparison encode."""
        settings = ffmpeg.EncodeSettings(output_format="GIF")
        with self.assertRaises(ValueError):
            ffmpeg.build_side_by_side_command(settings, "a.mp4", "b.mp4", "out.gif")


class ProcessHelperTests(unittest.TestCase):
    """Tests for the thin ``subprocess`` wrapper."""

    def test_missing_binary_is_reported_not_raised(self) -> None:
        """A missing executable returns a readable message instead of raising."""
        succeeded, message = ffmpeg.run_ffmpeg(
            ["definitely-not-ffmpeg-12345", "-version"]
        )
        self.assertFalse(succeeded)
        self.assertIn("definitely-not-ffmpeg-12345", message)

    def test_tail_keeps_only_the_last_lines(self) -> None:
        """Long FFmpeg logs are trimmed for the Blender report."""
        text = "\n".join(f"line {index}" for index in range(50))
        self.assertEqual(ffmpeg.tail(text, max_lines=3), "line 47\nline 48\nline 49")


if __name__ == "__main__":
    unittest.main()
