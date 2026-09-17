# SPDX-FileCopyrightText: 2026 Simulacre
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""FFmpeg command construction and process execution.

The module is deliberately split into two layers so that the interesting
logic stays testable outside of Blender:

Pure layer:
    :class:`EncodeSettings`, :func:`color_to_hex`, :func:`get_resolution`,
    :func:`format_fps`, :func:`build_video_filters`,
    :func:`build_filter_chain` and :func:`build_ffmpeg_command`.
    Nothing in this layer imports ``bpy`` or touches the file system, so a
    plain CPython interpreter can import and unit test it (see ``tests/``).

Environment layer:
    :func:`resolve_ffmpeg_binary`, :func:`is_ffmpeg_available`,
    :func:`find_font_path` and :func:`run_ffmpeg`. These talk to Blender,
    the operating system or ``subprocess`` and hold no encoding policy.

``bpy`` is imported lazily inside the few functions that need it, which is
what keeps the module importable from a standalone interpreter.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import platform
import shutil
import subprocess
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Fallback binary name, resolved through ``PATH`` by the OS.
DEFAULT_FFMPEG_BINARY = "ffmpeg"

#: Output format enum -> file extension.
CONTAINER_EXTENSIONS: Dict[str, str] = {
    "MP4": ".mp4",
    "MOV": ".mov",
    "MKV": ".mkv",
    "AVI": ".avi",
    "GIF": ".gif",
    "WEBP": ".webp",
}

#: Resolution preset enum -> ``(width, height)`` in pixels.
RESOLUTION_PRESETS: Dict[str, Tuple[int, int]] = {
    "FHD": (1920, 1080),
    "HD": (1280, 720),
    "QHD": (2560, 1440),
    "SQUARE": (1080, 1080),
    "IG_STORY": (1080, 1920),
}

#: Resolution used when an unknown preset is requested.
DEFAULT_RESOLUTION: Tuple[int, int] = (1280, 720)

#: Formats that are muxed as a regular video stream.
VIDEO_FORMATS: FrozenSet[str] = frozenset({"MP4", "MOV", "MKV", "AVI"})

#: Formats that cannot carry an audio track.
AUDIO_LESS_FORMATS: FrozenSet[str] = frozenset({"GIF", "WEBP"})

#: Blender codec enum -> FFmpeg encoder name, for the lossless viewport codecs.
LOSSLESS_CODECS: Dict[str, str] = {"HUFFYUV": "huffyuv", "QTRLE": "qtrle"}

#: Encoders that accept the H.264 bitrate/pixel-format/preset options.
H264_ENCODERS: FrozenSet[str] = frozenset({"libx264", "h264_nvenc"})

#: Quality preset enum -> target video bitrate.
QUALITY_BITRATES: Dict[str, str] = {"HIGH": "6M", "MID": "3M", "LOW": "1.5M"}

#: Bitrate used when an unknown quality preset is requested.
DEFAULT_BITRATE = "3M"

#: Default audio bitrate in kbit/s.
DEFAULT_AUDIO_BITRATE_KBPS = 192

#: Size budget of the "Discord 8MB Match" preset, in mebibytes.
DISCORD_TARGET_MB = 8.0

#: Share of the size budget the streams may use; the rest absorbs container
#: overhead and rate-control overshoot.
SIZE_LIMIT_SAFETY = 0.95

#: Lowest video bitrate the size calculator will ever emit, in kbit/s.
MIN_VIDEO_BITRATE_KBPS = 120

#: VRChat world thumbnail specification (fixed by the platform).
THUMBNAIL_RESOLUTION: Tuple[int, int] = (1920, 1080)

#: Thumbnail format enum -> file extension.
THUMBNAIL_EXTENSIONS: Dict[str, str] = {"WEBP": ".webp", "JPG": ".jpg"}

#: Default labels burned into the two halves of an A/B comparison.
SIDE_BY_SIDE_LABELS: Tuple[str, str] = ("ORIGINAL", "SILHOUETTE")

#: Editing proxy specification: 720p30 H.264, small enough to scrub smoothly.
PROXY_RESOLUTION: Tuple[int, int] = (1280, 720)
PROXY_FPS = "30"
PROXY_CRF = 23
PROXY_PRESET = "veryfast"
PROXY_SUFFIX = "_proxy"

#: Formats whose muxer stores the ``-metadata`` tags reliably.
METADATA_FORMATS: FrozenSet[str] = frozenset({"MP4", "MOV", "MKV"})

#: Metadata tags the add-on writes, in the order FFmpeg receives them.
METADATA_KEYS: Tuple[str, ...] = ("title", "artist", "comment")

#: ``atempo`` only accepts this range, so other factors are chained.
MIN_ATEMPO = 0.5
MAX_ATEMPO = 2.0

#: Archive badge anchors as ``(x_expression, y_expression)`` format strings.
BADGE_POSITIONS: Dict[str, Tuple[str, str]] = {
    "BR": ("w-text_w-{margin}", "h-text_h-{margin}"),
    "BL": ("{margin}", "h-text_h-{margin}"),
    "TR": ("w-text_w-{margin}", "{margin}"),
    "TL": ("{margin}", "{margin}"),
}

#: Smallest badge inset in pixels, regardless of output height.
MIN_BADGE_MARGIN = 12

#: Characters no mainstream file system accepts in a file name.
INVALID_FILENAME_CHARS = '<>:"/\\|?*'

#: Default template for the segment exporter.
DEFAULT_SEGMENT_TEMPLATE = "{prefix}_{name}_{index}"

#: Tokens :func:`format_segment_filename` understands.
SEGMENT_TOKENS: Tuple[str, ...] = (
    "prefix",
    "name",
    "strip",
    "index",
    "scene",
    "date",
)

#: 3x3 composition markers as ``(label, x_expression, y_expression)``.
MARKER_LABELS: Tuple[Tuple[str, str, str], ...] = (
    ("RU", "w*0.15", "h*0.15"),
    ("U", "w*0.5", "h*0.15"),
    ("LU", "w*0.85", "h*0.15"),
    ("R", "w*0.15", "h*0.5"),
    ("C", "w*0.5", "h*0.5"),
    ("L", "w*0.85", "h*0.5"),
    ("RD", "w*0.15", "h*0.85"),
    ("D", "w*0.5", "h*0.85"),
    ("LD", "w*0.85", "h*0.85"),
)

_WINDOWS_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/seguiemj.ttf",
)

_MACOS_FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)

_LINUX_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)

_BLENDER_BUNDLED_FONTS = ("GeistMono-Regular.ttf", "droidsans.ttf")

# Caches for the two comparatively expensive lookups. ``draw()`` runs on every
# redraw, so the results are memoised and dropped again on add-on registration.
_ffmpeg_binary_cache: Optional[str] = None
_ffprobe_binary_cache: Optional[str] = None
_font_path_cache: Optional[str] = None
_font_path_resolved = False


# ---------------------------------------------------------------------------
# Pure layer
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class TimeRange:
    """A trim applied to the input before encoding.

    Attributes:
        start: Offset from the start of the source, in seconds.
        duration: Length to keep in seconds, or ``None`` to run to the end.
    """

    start: float = 0.0
    duration: Optional[float] = None


@dataclasses.dataclass(frozen=True)
class Segment:
    """One exportable slice of a timeline.

    Attributes:
        name: Human readable name, taken from the strip or the marker.
        start: Offset inside the source file, in seconds.
        end: End offset inside the source file, in seconds.
        index: 1-based position used by the file name template.
        source_path: Absolute path of the file the slice is cut from.
        strip_name: Name of the strip the slice belongs to. With marker
            splitting this differs from ``name`` and keeps identically named
            markers on different strips apart.
    """

    name: str
    start: float
    end: float
    index: int
    source_path: str = ""
    strip_name: str = ""

    @property
    def duration(self) -> float:
        """Length of the slice.

        Returns:
            The duration in seconds; zero when the slice is reversed or empty.
        """
        return max(0.0, self.end - self.start)

    def as_time_range(self) -> TimeRange:
        """Convert the segment into the trim the encoder consumes.

        Returns:
            A :class:`TimeRange` starting at :attr:`start` and lasting
            :attr:`duration`.
        """
        return TimeRange(start=self.start, duration=self.duration)


@dataclasses.dataclass(frozen=True)
class StripRange:
    """Where one movie strip sits on the timeline, in frames.

    This mirrors the handful of ``bpy`` strip fields the marker splitter
    needs, so the mapping itself stays pure and unit testable.

    Attributes:
        name: Strip name.
        source_path: Absolute path of the movie file behind the strip.
        frame_start: Timeline frame that holds frame 0 of the source file.
            Head-trimmed strips start playing later than this.
        frame_final_start: First timeline frame the strip actually shows.
        frame_final_end: One past the last timeline frame the strip shows.
    """

    name: str
    source_path: str
    frame_start: float
    frame_final_start: float
    frame_final_end: float

    @property
    def visible_source_start(self) -> float:
        """Start of the visible window, measured inside the source file.

        Returns:
            The offset in frames; zero for a strip with no head trim.
        """
        return self.frame_final_start - self.frame_start

    @property
    def visible_source_end(self) -> float:
        """End of the visible window, measured inside the source file.

        Returns:
            The offset in frames, one past the last visible frame.
        """
        return self.frame_final_end - self.frame_start


@dataclasses.dataclass(frozen=True)
class SpeedVariant:
    """One output of a multi-export preset.

    Attributes:
        suffix: Appended to the base file name, e.g. ``"_slow"``.
        mirror: Whether this variant is flipped horizontally.
        speed: Playback rate; ``0.75`` is 25% slower than real time.
        label: Short description shown in reports and logs.
    """

    suffix: str
    mirror: bool = False
    speed: float = 1.0
    label: str = ""


#: The "KSL Learning Pack" preset: original, mirrored and slowed down.
KSL_LEARNING_PACK: Tuple[SpeedVariant, ...] = (
    SpeedVariant(suffix="_orig", mirror=False, speed=1.0, label="원본"),
    SpeedVariant(suffix="_mirror", mirror=True, speed=1.0, label="좌우 반전"),
    SpeedVariant(suffix="_slow", mirror=False, speed=0.75, label="0.75x 슬로모션"),
)


@dataclasses.dataclass(frozen=True)
class EncodeSettings:
    """Everything :func:`build_ffmpeg_command` needs, as plain Python data.

    The dataclass is intentionally decoupled from Blender's ``PropertyGroup``
    so that command generation can be exercised in unit tests. Use
    :meth:`from_props` to convert the add-on's property group into an
    instance.

    Attributes:
        width: Target width in pixels.
        height: Target height in pixels.
        fps: Target frame rate, already formatted for FFmpeg (e.g. ``"29.97"``).
        output_format: One of the keys of :data:`CONTAINER_EXTENSIONS`.
        quality_preset: One of the keys of :data:`QUALITY_BITRATES`.
        use_nvenc: Whether the NVIDIA hardware encoder should be used.
        lossless_codec: Blender codec enum (``"HUFFYUV"``/``"QTRLE"``) used
            only when ``use_nvenc`` is ``False``; empty means H.264.
        use_mirror: Whether to flip the image horizontally.
        remove_audio: Whether to drop the audio stream.
        show_markers: Whether to draw the 3x3 composition markers.
        show_info: Whether to draw the FPS/timecode/frame HUD.
        font_path: Absolute, *unescaped* path to a ``drawtext`` font, or
            ``None`` when no font could be found (overlays are then skipped).
        marker_color: Marker text color as ``(r, g, b)`` floats in ``0..1``.
        marker_font_size: Marker font size in points.
        info_color: HUD text color as ``(r, g, b)`` floats in ``0..1``.
        info_bg_color: HUD box color as ``(r, g, b)`` floats in ``0..1``.
        info_bg_opacity: HUD box opacity in ``0..1``.
        info_font_size: HUD font size in points.
        target_size_mb: File size budget in mebibytes for the size-matching
            preset, or ``None`` to use the quality preset bitrate.
        duration_seconds: Source duration, required to turn ``target_size_mb``
            into a bitrate. Size matching is skipped when it is unknown.
        audio_bitrate_kbps: Audio bitrate in kbit/s, subtracted from the size
            budget when computing the video bitrate.
        trim: Slice of the source to encode, or ``None`` for the whole file.
        speed: Playback rate; ``1.0`` is real time, ``0.75`` is slow motion.
        metadata: Container tags to write, e.g. ``{"title": "..."}``. Ignored
            by formats outside :data:`METADATA_FORMATS`.
        badge_text: Archive badge caption; empty disables the badge.
        badge_position: A key of :data:`BADGE_POSITIONS`.
        badge_font_size: Badge font size in points.
        badge_opacity: Badge box opacity in ``0..1``.
    """

    width: int = DEFAULT_RESOLUTION[0]
    height: int = DEFAULT_RESOLUTION[1]
    fps: str = "30"
    output_format: str = "MP4"
    quality_preset: str = "MID"
    use_nvenc: bool = True
    lossless_codec: str = ""
    use_mirror: bool = False
    remove_audio: bool = False
    show_markers: bool = False
    show_info: bool = False
    font_path: Optional[str] = None
    marker_color: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    marker_font_size: int = 24
    info_color: Tuple[float, float, float] = (1.0, 0.9, 0.0)
    info_bg_color: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    info_bg_opacity: float = 0.3
    info_font_size: int = 18
    target_size_mb: Optional[float] = None
    duration_seconds: Optional[float] = None
    audio_bitrate_kbps: int = DEFAULT_AUDIO_BITRATE_KBPS
    trim: Optional[TimeRange] = None
    speed: float = 1.0
    metadata: Optional[Dict[str, str]] = None
    badge_text: str = ""
    badge_position: str = "BR"
    badge_font_size: int = 20
    badge_opacity: float = 0.45

    @classmethod
    def from_props(
        cls,
        props: Any,
        width: int,
        height: int,
        fps: str,
        font_path: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, str]] = None,
        trim: Optional[TimeRange] = None,
    ) -> "EncodeSettings":
        """Build settings from the add-on property group.

        Args:
            props: A ``GeminiProperties`` instance, or any object exposing the
                same attribute names (handy for tests).
            width: Target width in pixels, already resolved by the caller.
            height: Target height in pixels, already resolved by the caller.
            fps: Target frame rate formatted for FFmpeg.
            font_path: Unescaped font path for the overlay filters.
            duration_seconds: Source duration, needed by the size-matching
                preset; ``None`` disables size matching.
            metadata: Already resolved container tags, or ``None``.
            trim: Slice of the source to encode, or ``None`` for all of it.

        Returns:
            A fully populated :class:`EncodeSettings` instance.
        """
        lossless_codec = ""
        if props.tool_mode == "VIEWPORT" and props.vp_codec in LOSSLESS_CODECS:
            lossless_codec = props.vp_codec

        target_size_mb = None
        if getattr(props, "use_size_limit", False):
            target_size_mb = float(props.target_size_mb)

        return cls(
            width=int(width),
            height=int(height),
            fps=str(fps),
            output_format=props.output_format,
            quality_preset=props.quality_preset,
            use_nvenc=bool(props.use_nvenc),
            lossless_codec=lossless_codec,
            use_mirror=bool(props.use_mirror),
            remove_audio=bool(props.remove_audio),
            show_markers=bool(props.show_markers),
            show_info=bool(props.show_info),
            font_path=font_path,
            marker_color=tuple(props.marker_color),
            marker_font_size=int(props.marker_font_size),
            info_color=tuple(props.info_color),
            info_bg_color=tuple(props.info_bg_color),
            info_bg_opacity=float(props.info_bg_opacity),
            info_font_size=int(props.info_font_size),
            target_size_mb=target_size_mb,
            duration_seconds=duration_seconds,
            metadata=metadata,
            trim=trim,
            badge_text=(
                props.badge_text if getattr(props, "use_badge", False) else ""
            ),
            badge_position=getattr(props, "badge_position", "BR"),
            badge_font_size=int(getattr(props, "badge_font_size", 20)),
            badge_opacity=float(getattr(props, "badge_opacity", 0.45)),
        )


def color_to_hex(color: Sequence[float]) -> str:
    """Convert a float RGB color to the ``0xRRGGBB`` literal FFmpeg expects.

    Args:
        color: Sequence of at least three floats in the ``0..1`` range. Values
            outside the range are clamped.

    Returns:
        The color as ``"0xrrggbb"``.
    """
    channels = [max(0, min(255, int(float(channel) * 255))) for channel in color[:3]]
    return "0x{:02x}{:02x}{:02x}".format(*channels)


def get_resolution(preset: str) -> Tuple[int, int]:
    """Look up the pixel size of a resolution preset.

    Args:
        preset: A key of :data:`RESOLUTION_PRESETS`.

    Returns:
        ``(width, height)``, falling back to :data:`DEFAULT_RESOLUTION` for an
        unknown preset.
    """
    return RESOLUTION_PRESETS.get(preset, DEFAULT_RESOLUTION)


def get_extension(output_format: str) -> str:
    """Return the file extension for an output format.

    Args:
        output_format: A key of :data:`CONTAINER_EXTENSIONS`.

    Returns:
        The extension including the leading dot, defaulting to ``".mp4"``.
    """
    return CONTAINER_EXTENSIONS.get(output_format, ".mp4")


def format_fps(value: float) -> str:
    """Format a frame rate the way FFmpeg's ``fps`` filter likes it.

    Args:
        value: Frame rate, e.g. ``30.0`` or ``29.969999``.

    Returns:
        ``"30"`` for whole numbers, otherwise a trimmed decimal such as
        ``"29.97"``.
    """
    rounded = round(float(value), 2)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:g}"


def sanitize_filename(name: str, replacement: str = "_") -> str:
    """Strip characters no mainstream file system accepts from a name.

    Args:
        name: Raw name, typically a strip or marker name.
        replacement: Character substituted for every invalid one.

    Returns:
        A safe file name stem; ``"untitled"`` when nothing usable remains.
    """
    cleaned = "".join(
        replacement if character in INVALID_FILENAME_CHARS or ord(character) < 32
        else character
        for character in name
    ).strip(" .")
    return cleaned or "untitled"


def format_segment_filename(
    template: str,
    extension: str,
    prefix: str = "",
    name: str = "",
    index: int = 1,
    scene: str = "",
    date: str = "",
    strip: str = "",
) -> str:
    """Render a segment file name from a user supplied template.

    The template understands the tokens listed in :data:`SEGMENT_TOKENS`, for
    example ``"{prefix}_{name}_{index}"``. ``{index}`` is zero padded to three
    digits, and every substituted value is sanitised, so a marker named
    ``"take 1/2"`` cannot produce a nested path.

    Args:
        template: Template string, without the extension.
        extension: File extension including the leading dot.
        prefix: Value for ``{prefix}``.
        name: Value for ``{name}``, the strip or marker name.
        index: Value for ``{index}``, 1-based.
        scene: Value for ``{scene}``.
        date: Value for ``{date}``.
        strip: Value for ``{strip}``, the owning strip's name. Useful when
            marker splitting runs over several strips that share marker names.

    Returns:
        The rendered file name, extension included.

    Raises:
        ValueError: If the template references an unknown token or is
            malformed (for example an unbalanced brace).
    """
    tokens = {
        "prefix": sanitize_filename(prefix) if prefix else "",
        "name": sanitize_filename(name) if name else "",
        "strip": sanitize_filename(strip) if strip else "",
        "index": f"{int(index):03d}",
        "scene": sanitize_filename(scene) if scene else "",
        "date": sanitize_filename(date) if date else "",
    }

    try:
        rendered = template.format(**tokens)
    except KeyError as exc:
        raise ValueError(
            f"알 수 없는 템플릿 토큰입니다: {exc}. "
            f"사용 가능: {', '.join('{%s}' % key for key in SEGMENT_TOKENS)}"
        ) from exc
    except (IndexError, ValueError) as exc:
        raise ValueError(f"템플릿 형식이 잘못되었습니다: {exc}") from exc

    # Empty tokens leave doubled or trailing separators behind.
    while "__" in rendered:
        rendered = rendered.replace("__", "_")
    rendered = sanitize_filename(rendered.strip("_- "))
    return f"{rendered}{extension}"


def markers_within_strip(
    markers: Sequence[Tuple[float, str]], strip: StripRange
) -> List[Tuple[float, str]]:
    """Select the markers that belong to one strip, in timeline order.

    A marker belongs to a strip when it sits in ``[frame_start,
    frame_final_end)``. The lower bound is the strip's own frame zero rather
    than its first visible frame, so a marker parked in a trimmed-away head
    still opens a segment; :func:`build_marker_segments` then clamps that
    segment to the visible part.

    Args:
        markers: ``(frame, name)`` pairs, in any order.
        strip: The strip to test against.

    Returns:
        The matching markers, sorted by frame.
    """
    return sorted(
        (
            marker
            for marker in markers
            if strip.frame_start <= marker[0] < strip.frame_final_end
        ),
        key=lambda marker: marker[0],
    )


def build_marker_segments(
    strips: Sequence[StripRange],
    markers: Sequence[Tuple[float, str]],
    fps: float,
    start_index: int = 1,
) -> List[Segment]:
    """Cut every strip at the markers that fall inside it.

    Each strip is handled independently, so one press of the export button
    slices all checked strips, and markers outside a strip never affect it.
    Inside a strip a marker runs until the next marker of that same strip, or
    until the end of the strip for the last one. Segment indices continue
    across strips so a template without ``{strip}`` still yields unique names.

    Timeline frames are mapped back onto each strip's own source file with
    ``(frame - strip.frame_start) / fps`` and then clamped to the strip's
    visible window, so trimmed-away material is never exported.

    Args:
        strips: The strips to slice, in the order they should be exported.
        markers: ``(frame, name)`` pairs from the timeline.
        fps: Scene frame rate used to convert frames to seconds.
        start_index: Value for the first segment's ``index``.

    Returns:
        The segments to export; empty when no marker falls inside any strip.

    Raises:
        ValueError: If ``fps`` is not positive.
    """
    if fps <= 0:
        raise ValueError(f"FPS는 0보다 커야 합니다: {fps}")

    segments: List[Segment] = []
    index = start_index

    for strip in strips:
        inside = markers_within_strip(markers, strip)
        if not inside:
            logger.debug("스트립 '%s' 범위 안에 마커가 없습니다.", strip.name)
            continue

        visible_start = strip.visible_source_start
        visible_end = strip.visible_source_end

        for position, (frame, marker_name) in enumerate(inside):
            if position + 1 < len(inside):
                end_frame = inside[position + 1][0]
            else:
                end_frame = strip.frame_final_end

            start = min(max(frame - strip.frame_start, visible_start), visible_end)
            end = min(max(end_frame - strip.frame_start, visible_start), visible_end)
            if end <= start:
                logger.debug(
                    "길이가 0인 마커 구간을 건너뜁니다: %s (%s)",
                    marker_name,
                    strip.name,
                )
                continue

            segments.append(
                Segment(
                    name=marker_name or f"marker{position + 1}",
                    start=start / fps,
                    end=end / fps,
                    index=index,
                    source_path=strip.source_path,
                    strip_name=strip.name,
                )
            )
            index += 1

    return segments


def build_speed_filters(speed: float) -> List[str]:
    """Build the video filter that changes playback speed.

    Args:
        speed: Playback rate; ``1.0`` is real time, ``0.75`` slows down.

    Returns:
        A single ``setpts`` filter, or an empty list at real time.

    Raises:
        ValueError: If ``speed`` is not positive.
    """
    if speed <= 0:
        raise ValueError(f"재생 속도는 0보다 커야 합니다: {speed}")
    if speed == 1.0:
        return []
    return [f"setpts={1.0 / speed:g}*PTS"]


def build_atempo_filters(speed: float) -> List[str]:
    """Build the audio filters matching a speed change.

    ``atempo`` only accepts factors between :data:`MIN_ATEMPO` and
    :data:`MAX_ATEMPO`, so extreme rates are expressed as a chain.

    Args:
        speed: Playback rate; ``1.0`` is real time.

    Returns:
        The ``atempo`` filters, or an empty list at real time.

    Raises:
        ValueError: If ``speed`` is not positive.
    """
    if speed <= 0:
        raise ValueError(f"재생 속도는 0보다 커야 합니다: {speed}")
    if speed == 1.0:
        return []

    factors: List[float] = []
    remaining = speed
    while remaining < MIN_ATEMPO:
        factors.append(MIN_ATEMPO)
        remaining /= MIN_ATEMPO
    while remaining > MAX_ATEMPO:
        factors.append(MAX_ATEMPO)
        remaining /= MAX_ATEMPO
    factors.append(remaining)
    return [f"atempo={factor:g}" for factor in factors]


def build_badge_filter(settings: EncodeSettings, font_path: str) -> str:
    """Build the single-line archive badge overlay.

    Args:
        settings: The encode settings.
        font_path: Font path, already escaped by :func:`escape_font_path`.

    Returns:
        A ``drawtext`` filter anchored to the configured corner.
    """
    margin = max(MIN_BADGE_MARGIN, int(settings.height * 0.025))
    x_expression, y_expression = BADGE_POSITIONS.get(
        settings.badge_position, BADGE_POSITIONS["BR"]
    )
    # A badge is one line by definition: newlines would break the anchor maths.
    text = settings.badge_text.replace("\n", " ").replace("'", "")
    return (
        f"drawtext=fontfile='{font_path}':text='{text}'"
        f":x={x_expression.format(margin=margin)}"
        f":y={y_expression.format(margin=margin)}"
        f":fontsize={settings.badge_font_size}:fontcolor=white@0.9"
        f":box=1:boxcolor=black@{settings.badge_opacity}:boxborderw=8"
    )


def resolve_metadata_tokens(
    values: Dict[str, str], tokens: Dict[str, str]
) -> Dict[str, str]:
    """Substitute ``{name}``-style tokens inside metadata values.

    Unlike file names, a bad token here is not worth failing an export over:
    the raw text is kept and a warning is logged instead.

    Args:
        values: Tag name to raw value mapping; empty values are dropped.
        tokens: Token name to replacement mapping.

    Returns:
        The resolved mapping, without the empty entries.
    """
    resolved: Dict[str, str] = {}
    for key, raw in values.items():
        if not raw:
            continue
        try:
            resolved[key] = raw.format(**tokens)
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning(
                "메타데이터 '%s'의 토큰을 해석하지 못해 원문을 사용합니다: %s", key, exc
            )
            resolved[key] = raw
    return resolved


def build_metadata_arguments(
    metadata: Optional[Dict[str, str]], output_format: str
) -> List[str]:
    """Build the ``-metadata`` arguments for the container tags.

    Args:
        metadata: Tag name to value mapping, or ``None``.
        output_format: A key of :data:`CONTAINER_EXTENSIONS`; formats outside
            :data:`METADATA_FORMATS` get no tags because their muxers drop
            them silently.

    Returns:
        The argument list, empty when nothing should be written.
    """
    if not metadata:
        return []
    if output_format not in METADATA_FORMATS:
        logger.info("%s 컨테이너는 메타데이터를 저장하지 않아 건너뜁니다.", output_format)
        return []

    arguments: List[str] = []
    for key in METADATA_KEYS:
        value = metadata.get(key)
        if value:
            # A newline would terminate the tag early in some muxers.
            arguments.extend(["-metadata", f"{key}={value.replace(chr(10), ' ')}"])
    return arguments


def build_trim_arguments(
    trim: Optional[TimeRange],
) -> Tuple[List[str], List[str]]:
    """Split a trim into the arguments that go before and after ``-i``.

    ``-ss`` belongs before the input so FFmpeg seeks by keyframe instead of
    decoding everything up to the offset; ``-t`` belongs after it so the
    duration is measured from the seek point.

    Args:
        trim: The slice to encode, or ``None``.

    Returns:
        ``(pre_input_arguments, post_input_arguments)``.
    """
    if trim is None:
        return [], []

    pre_input = []
    if trim.start > 0:
        pre_input = ["-ss", f"{trim.start:.3f}"]

    post_input = []
    if trim.duration is not None and trim.duration > 0:
        post_input = ["-t", f"{trim.duration:.3f}"]
    return pre_input, post_input


def iter_variant_settings(
    settings: EncodeSettings, variants: Sequence[SpeedVariant] = KSL_LEARNING_PACK
) -> List[Tuple[SpeedVariant, "EncodeSettings"]]:
    """Expand one encode into the outputs of a multi-export preset.

    Mirroring is applied as an XOR against the user's own mirror toggle, so a
    clip that is already mirrored still produces one flipped and one unflipped
    variant rather than two identical files.

    Args:
        settings: The base encode settings.
        variants: The preset to expand, defaulting to
            :data:`KSL_LEARNING_PACK`.

    Returns:
        ``(variant, settings)`` pairs in preset order.
    """
    expanded = []
    for variant in variants:
        expanded.append(
            (
                variant,
                dataclasses.replace(
                    settings,
                    use_mirror=settings.use_mirror != variant.mirror,
                    speed=variant.speed,
                ),
            )
        )
    return expanded


def escape_font_path(path: str) -> str:
    """Escape a font path for use inside a ``drawtext`` filter argument.

    FFmpeg's filter parser treats backslashes and colons as syntax, so Windows
    paths such as ``C:\\Windows\\Fonts\\arial.ttf`` must be rewritten first.

    Args:
        path: Absolute path to a font file.

    Returns:
        The escaped path, e.g. ``"C\\:/Windows/Fonts/arial.ttf"``.
    """
    return path.replace("\\", "/").replace(":", "\\:")


def select_video_codec(settings: EncodeSettings) -> str:
    """Pick the FFmpeg video encoder for the given settings.

    Args:
        settings: The encode settings.

    Returns:
        ``"h264_nvenc"`` when hardware encoding is enabled, the mapped lossless
        encoder when one is requested, otherwise ``"libx264"``.
    """
    if settings.use_nvenc:
        return "h264_nvenc"
    if settings.lossless_codec:
        return LOSSLESS_CODECS.get(settings.lossless_codec, "libx264")
    return "libx264"


def calculate_video_bitrate_kbps(
    target_size_mb: float,
    duration_seconds: float,
    audio_bitrate_kbps: int = DEFAULT_AUDIO_BITRATE_KBPS,
    safety: float = SIZE_LIMIT_SAFETY,
    minimum_kbps: int = MIN_VIDEO_BITRATE_KBPS,
) -> int:
    """Work out the video bitrate that fits a clip into a size budget.

    Used by the "Discord 8MB Match" preset. The audio bitrate is subtracted
    first, and :data:`SIZE_LIMIT_SAFETY` leaves headroom for container
    overhead and rate-control overshoot.

    Args:
        target_size_mb: Size budget in mebibytes (``8.0`` means 8 * 1024 * 1024
            bytes, which is how upload limits are actually enforced).
        duration_seconds: Clip duration in seconds.
        audio_bitrate_kbps: Audio bitrate in kbit/s; pass ``0`` for silent
            output.
        safety: Fraction of the budget the streams may use, in ``0..1``.
        minimum_kbps: Floor applied to the result so an over-long clip still
            produces a playable file rather than a bitrate of zero.

    Returns:
        The video bitrate in kbit/s, never below ``minimum_kbps``.

    Raises:
        ValueError: If ``duration_seconds`` or ``target_size_mb`` is not
            positive.
    """
    if duration_seconds <= 0:
        raise ValueError("영상 길이를 알 수 없어 용량을 계산할 수 없습니다.")
    if target_size_mb <= 0:
        raise ValueError("목표 용량은 0보다 커야 합니다.")

    budget_bits = target_size_mb * 1024 * 1024 * 8 * safety
    audio_bits = audio_bitrate_kbps * 1000 * duration_seconds
    video_bits_per_second = (budget_bits - audio_bits) / duration_seconds
    return max(minimum_kbps, int(video_bits_per_second / 1000))


def resolve_video_bitrate_kbps(settings: EncodeSettings) -> Optional[int]:
    """Return the size-matched bitrate for ``settings``, if size matching applies.

    Args:
        settings: The encode settings.

    Returns:
        The computed bitrate in kbit/s, or ``None`` when the quality preset
        bitrate should be used instead (size matching disabled, or the source
        duration is unknown).
    """
    if not settings.target_size_mb or not settings.duration_seconds:
        return None

    audio_kbps = 0 if settings.remove_audio else settings.audio_bitrate_kbps
    if settings.output_format in AUDIO_LESS_FORMATS:
        audio_kbps = 0

    try:
        return calculate_video_bitrate_kbps(
            settings.target_size_mb, settings.duration_seconds, audio_kbps
        )
    except ValueError as exc:
        logger.warning("용량 맞춤 비트레이트 계산을 건너뜁니다: %s", exc)
        return None


def build_cover_scale_filters(width: int, height: int) -> List[str]:
    """Build the scale/crop pair that fills a target box without distortion.

    The image is scaled so that it *covers* the box (preserving the aspect
    ratio, rounded to even pixel counts for ``yuv420p``) and is then
    center-cropped to the exact target size.

    Args:
        width: Target width in pixels.
        height: Target height in pixels.

    Returns:
        The ``scale`` and ``crop`` filter strings, in order.
    """
    width_expr = f"trunc(iw*max({width}/iw\\,{height}/ih)/2)*2"
    height_expr = f"trunc(ih*max({width}/iw\\,{height}/ih)/2)*2"
    return [
        f"scale={width_expr}:{height_expr}:flags=lanczos",
        f"crop={width}:{height}",
    ]


def build_scale_filters(width: int, height: int, fps: str) -> List[str]:
    """Build the fps/scale/crop part of the filter chain.

    Args:
        width: Target width in pixels.
        height: Target height in pixels.
        fps: Target frame rate formatted for FFmpeg.

    Returns:
        The individual filter strings, in order.
    """
    return [f"fps=fps={fps}", *build_cover_scale_filters(width, height)]


def build_marker_filters(settings: EncodeSettings, font_path: str) -> List[str]:
    """Build the ``drawtext`` filters for the 3x3 composition markers.

    Args:
        settings: The encode settings.
        font_path: Font path, already escaped by :func:`escape_font_path`.

    Returns:
        One ``drawtext`` filter per marker defined in :data:`MARKER_LABELS`.
    """
    size = settings.marker_font_size
    color = color_to_hex(settings.marker_color)
    return [
        (
            f"drawtext=fontfile='{font_path}':text='{label}'"
            f":x={x_expr}-text_w/2:y={y_expr}-text_h/2"
            f":fontsize={size}:fontcolor={color}"
            ":shadowcolor=black@0.8:shadowx=2:shadowy=2"
        )
        for label, x_expr, y_expr in MARKER_LABELS
    ]


def build_info_filter(settings: EncodeSettings, font_path: str) -> str:
    """Build the ``drawtext`` filter for the FPS/timecode/frame HUD.

    Args:
        settings: The encode settings.
        font_path: Font path, already escaped by :func:`escape_font_path`.

    Returns:
        A single ``drawtext`` filter string.
    """
    time_expr = (
        r"%{eif\:t/60\:d\:2}\:%{eif\:mod(t,60)\:d\:2}"
        r".%{eif\:(t*10-10*floor(t))\:d}"
    )
    text = rf"FPS\: {settings.fps} | TCR\: {time_expr} | FRM\: %{{frame_num}}"
    return (
        f"drawtext=fontfile='{font_path}':text='{text}'"
        f":x=20:y=20:fontsize={settings.info_font_size}"
        f":fontcolor={color_to_hex(settings.info_color)}"
        f":box=1:boxcolor={color_to_hex(settings.info_bg_color)}"
        f"@{settings.info_bg_opacity}:boxborderw=5"
    )


def build_video_filters(settings: EncodeSettings) -> List[str]:
    """Build the complete ordered list of video filters.

    Order matters: the mirror runs first so overlay text is never flipped,
    then the speed change, then the resample/scale/crop block, and finally the
    text overlays, which must be neither stretched nor mirrored.

    Args:
        settings: The encode settings.

    Returns:
        The filter strings, in the order FFmpeg should apply them.
    """
    filters: List[str] = []
    if settings.use_mirror:
        filters.append("hflip")
    filters.extend(build_speed_filters(settings.speed))
    filters.extend(build_scale_filters(settings.width, settings.height, settings.fps))

    wants_text = bool(
        settings.show_markers or settings.show_info or settings.badge_text
    )
    if not settings.font_path:
        if wants_text:
            logger.warning(
                "오버레이가 요청되었지만 사용할 폰트를 찾지 못해 건너뜁니다."
            )
        return filters

    font_path = escape_font_path(settings.font_path)
    if settings.show_markers:
        filters.extend(build_marker_filters(settings, font_path))
    if settings.show_info:
        filters.append(build_info_filter(settings, font_path))
    if settings.badge_text:
        filters.append(build_badge_filter(settings, font_path))
    return filters


def build_filter_chain(settings: EncodeSettings) -> str:
    """Join the video filters into a single ``-vf`` argument.

    Args:
        settings: The encode settings.

    Returns:
        The comma separated filter chain.
    """
    return ",".join(build_video_filters(settings))


def build_bitrate_arguments(settings: EncodeSettings) -> List[str]:
    """Build the rate-control arguments for an H.264 encode.

    With size matching enabled the bitrate is capped hard (``-maxrate`` plus a
    two-second ``-bufsize``) so the encoder cannot overshoot the budget; the
    plain quality presets stay on a soft average bitrate.

    Args:
        settings: The encode settings.

    Returns:
        The rate-control arguments to append to the command.
    """
    size_matched_kbps = resolve_video_bitrate_kbps(settings)
    if size_matched_kbps is None:
        bitrate = QUALITY_BITRATES.get(settings.quality_preset, DEFAULT_BITRATE)
        return ["-b:v", bitrate]

    bitrate = f"{size_matched_kbps}k"
    return [
        "-b:v",
        bitrate,
        "-maxrate",
        bitrate,
        "-bufsize",
        f"{size_matched_kbps * 2}k",
    ]


def build_ffmpeg_command(
    settings: EncodeSettings,
    input_path: str,
    output_path: str,
    ffmpeg_binary: str = DEFAULT_FFMPEG_BINARY,
) -> List[str]:
    """Build the full FFmpeg argument vector for one encode.

    This is the single source of truth for the add-on's encoding policy and
    the main entry point exercised by the unit tests.

    Args:
        settings: The encode settings.
        input_path: Absolute path of the source file.
        output_path: Absolute path of the file to write.
        ffmpeg_binary: Path to (or name of) the FFmpeg executable.

    Returns:
        The argument list, ready to hand to :func:`run_ffmpeg`.

    Raises:
        ValueError: If ``settings.output_format`` is not supported.
    """
    output_format = settings.output_format
    if output_format not in CONTAINER_EXTENSIONS:
        raise ValueError(f"지원하지 않는 출력 포맷입니다: {output_format!r}")

    pre_input, post_input = build_trim_arguments(settings.trim)
    command = [ffmpeg_binary, "-y", *pre_input, "-i", input_path]
    command.extend(post_input)
    command.extend(["-threads", "0"])

    # GIF/WebP cannot carry audio; muxing AAC into them makes FFmpeg abort.
    if settings.remove_audio or output_format in AUDIO_LESS_FORMATS:
        command.append("-an")
    else:
        command.extend(["-c:a", "aac", "-b:a", f"{settings.audio_bitrate_kbps}k"])
        atempo_filters = build_atempo_filters(settings.speed)
        if atempo_filters:
            command.extend(["-af", ",".join(atempo_filters)])

    command.extend(build_metadata_arguments(settings.metadata, output_format))
    filter_chain = build_filter_chain(settings)

    if output_format in VIDEO_FORMATS:
        codec = select_video_codec(settings)
        command.extend(["-vf", filter_chain, "-c:v", codec])
        if codec in H264_ENCODERS:
            command.extend(build_bitrate_arguments(settings))
            command.extend(["-pix_fmt", "yuv420p"])
            if codec == "h264_nvenc":
                command.extend(["-preset", "p4", "-tune", "hq"])
            else:
                command.extend(["-preset", "veryfast"])
    elif output_format == "GIF":
        command.extend(
            [
                "-filter_complex",
                f"{filter_chain},split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
            ]
        )
    elif output_format == "WEBP":
        command.extend(
            ["-vf", filter_chain, "-c:v", "libwebp", "-quality", "75", "-loop", "0"]
        )

    command.append(output_path)
    return command


def build_proxy_command(
    input_path: str,
    output_path: str,
    width: int = PROXY_RESOLUTION[0],
    height: int = PROXY_RESOLUTION[1],
    fps: str = PROXY_FPS,
    crf: int = PROXY_CRF,
    ffmpeg_binary: str = DEFAULT_FFMPEG_BINARY,
) -> List[str]:
    """Build the command that generates a lightweight editing proxy.

    Proxies are meant for scrubbing a long OBS capture in the VSE, so this
    deliberately ignores the user's quality settings: constant-rate-factor
    H.264 at 720p30 with ``faststart``, which decodes cheaply and seeks well.

    Args:
        input_path: Source video.
        output_path: Proxy file to write.
        width: Proxy width in pixels.
        height: Proxy height in pixels.
        fps: Proxy frame rate formatted for FFmpeg.
        crf: ``libx264`` constant rate factor; higher means smaller.
        ffmpeg_binary: Path to (or name of) the FFmpeg executable.

    Returns:
        The argument list, ready to hand to :func:`run_ffmpeg`.
    """
    filter_chain = ",".join(
        [f"fps=fps={fps}", *build_cover_scale_filters(width, height)]
    )
    return [
        ffmpeg_binary,
        "-y",
        "-i",
        input_path,
        "-threads",
        "0",
        "-vf",
        filter_chain,
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        PROXY_PRESET,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        output_path,
    ]


def build_segment_copy_command(
    input_path: str,
    output_path: str,
    segment: Segment,
    ffmpeg_binary: str = DEFAULT_FFMPEG_BINARY,
) -> List[str]:
    """Build a stream-copy command that cuts one segment without re-encoding.

    This is near instant but the cut snaps to the nearest keyframe before the
    requested start, so the segment may begin slightly early. Re-encode
    through :func:`build_ffmpeg_command` when frame accuracy matters.

    Args:
        input_path: Source video.
        output_path: Segment file to write.
        segment: The slice to cut.
        ffmpeg_binary: Path to (or name of) the FFmpeg executable.

    Returns:
        The argument list, ready to hand to :func:`run_ffmpeg`.
    """
    pre_input, post_input = build_trim_arguments(segment.as_time_range())
    return [
        ffmpeg_binary,
        "-y",
        *pre_input,
        "-i",
        input_path,
        *post_input,
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        output_path,
    ]


def jpeg_quality_scale(quality: int) -> int:
    """Convert a 0-100 quality value to FFmpeg's MJPEG ``-q:v`` scale.

    Args:
        quality: Perceptual quality in ``0..100``; values outside are clamped.

    Returns:
        A value on FFmpeg's inverted scale, where ``2`` is best and ``31`` is
        worst.
    """
    clamped = max(0, min(100, int(quality)))
    return max(2, min(31, round(31 - (clamped / 100.0) * 29)))


def build_thumbnail_command(
    input_path: str,
    output_path: str,
    image_format: str = "WEBP",
    width: int = THUMBNAIL_RESOLUTION[0],
    height: int = THUMBNAIL_RESOLUTION[1],
    quality: int = 90,
    seek_seconds: Optional[float] = None,
    ffmpeg_binary: str = DEFAULT_FFMPEG_BINARY,
) -> List[str]:
    """Build the command that exports a single still image.

    Used by the VRChat world thumbnail export, which requires exactly
    :data:`THUMBNAIL_RESOLUTION` pixels in WebP or JPEG. The source is scaled
    to cover and then center-cropped, so the aspect ratio is never distorted.

    Args:
        input_path: Source video or image.
        output_path: Image file to write.
        image_format: A key of :data:`THUMBNAIL_EXTENSIONS`.
        width: Output width in pixels.
        height: Output height in pixels.
        quality: Perceptual quality in ``0..100``.
        seek_seconds: Timestamp to grab the frame from; ``None`` takes the
            first frame.
        ffmpeg_binary: Path to (or name of) the FFmpeg executable.

    Returns:
        The argument list, ready to hand to :func:`run_ffmpeg`.

    Raises:
        ValueError: If ``image_format`` is not supported.
    """
    if image_format not in THUMBNAIL_EXTENSIONS:
        raise ValueError(f"지원하지 않는 썸네일 포맷입니다: {image_format!r}")

    command = [ffmpeg_binary, "-y"]
    if seek_seconds:
        # Placed before -i so FFmpeg seeks by keyframe instead of decoding
        # every frame up to the timestamp.
        command.extend(["-ss", f"{max(0.0, float(seek_seconds)):.3f}"])
    command.extend(["-i", input_path])

    filter_chain = ",".join(build_cover_scale_filters(width, height))
    command.extend(["-vf", filter_chain, "-frames:v", "1", "-an"])

    if image_format == "WEBP":
        command.extend(
            ["-c:v", "libwebp", "-quality", str(max(0, min(100, int(quality))))]
        )
    else:
        command.extend(
            [
                "-c:v",
                "mjpeg",
                "-q:v",
                str(jpeg_quality_scale(quality)),
                "-pix_fmt",
                "yuvj420p",
            ]
        )

    command.append(output_path)
    return command


def build_side_by_side_filter(
    settings: EncodeSettings,
    labels: Optional[Tuple[str, str]] = SIDE_BY_SIDE_LABELS,
) -> str:
    """Build the ``filter_complex`` graph that stacks two clips horizontally.

    Each input is resampled and cropped to half the target width, optionally
    labelled, and then joined with ``hstack``. The marker and HUD overlays do
    not apply here: the burned-in labels replace them.

    Args:
        settings: The encode settings; ``width``/``height``/``fps`` describe
            the *combined* output.
        labels: ``(left, right)`` captions, or ``None`` for no captions.
            Captions are also skipped when ``settings.font_path`` is unset.

    Returns:
        The filter graph, whose final output pad is ``[v]``.
    """
    half_width = max(2, (settings.width // 2) // 2 * 2)
    font_path = escape_font_path(settings.font_path) if settings.font_path else None

    if labels is not None and font_path is None:
        logger.warning("폰트를 찾지 못해 A/B 라벨 없이 합성합니다.")

    branches = []
    for index, pad in enumerate(("left", "right")):
        chain = [f"fps=fps={settings.fps}"]
        if settings.use_mirror:
            chain.append("hflip")
        chain.extend(build_cover_scale_filters(half_width, settings.height))
        chain.append("setsar=1")
        if labels is not None and font_path is not None:
            chain.append(
                f"drawtext=fontfile='{font_path}':text='{labels[index]}'"
                f":x=(w-text_w)/2:y=h*0.04"
                f":fontsize={settings.marker_font_size}:fontcolor=white"
                ":box=1:boxcolor=black@0.5:boxborderw=8"
            )
        branches.append(f"[{index}:v]{','.join(chain)}[{pad}]")

    branches.append("[left][right]hstack=inputs=2[v]")
    return ";".join(branches)


def build_side_by_side_command(
    settings: EncodeSettings,
    left_path: str,
    right_path: str,
    output_path: str,
    labels: Optional[Tuple[str, str]] = SIDE_BY_SIDE_LABELS,
    ffmpeg_binary: str = DEFAULT_FFMPEG_BINARY,
) -> List[str]:
    """Build the command that renders an A/B comparison video.

    Args:
        settings: The encode settings describing the combined output.
        left_path: Clip shown on the left (the original).
        right_path: Clip shown on the right (the silhouette pass).
        output_path: File to write.
        labels: ``(left, right)`` captions, or ``None`` for no captions.
        ffmpeg_binary: Path to (or name of) the FFmpeg executable.

    Returns:
        The argument list, ready to hand to :func:`run_ffmpeg`.

    Raises:
        ValueError: If the output format cannot hold a stacked video stream.
    """
    if settings.output_format not in VIDEO_FORMATS:
        raise ValueError(
            "A/B 비교 출력은 MP4/MOV/MKV/AVI 에서만 사용할 수 있습니다: "
            f"{settings.output_format!r}"
        )

    command = [
        ffmpeg_binary,
        "-y",
        "-i",
        left_path,
        "-i",
        right_path,
        "-filter_complex",
        build_side_by_side_filter(settings, labels),
        "-map",
        "[v]",
    ]

    if settings.remove_audio:
        command.append("-an")
    else:
        # The left clip carries the audio; "?" keeps the map optional so a
        # silent recording does not abort the encode.
        command.extend(["-map", "0:a?", "-c:a", "aac", "-b:a", "192k"])

    command.extend(
        build_metadata_arguments(settings.metadata, settings.output_format)
    )
    codec = select_video_codec(settings)
    command.extend(["-c:v", codec])
    if codec in H264_ENCODERS:
        command.extend(build_bitrate_arguments(settings))
        command.extend(["-pix_fmt", "yuv420p"])
        if codec == "h264_nvenc":
            command.extend(["-preset", "p4", "-tune", "hq"])
        else:
            command.extend(["-preset", "veryfast"])

    command.append(output_path)
    return command


# ---------------------------------------------------------------------------
# Environment layer
# ---------------------------------------------------------------------------
def clear_caches() -> None:
    """Drop the memoised binary and font lookups.

    Called on add-on registration so that a Blender session picks up a newly
    installed FFmpeg without a restart.
    """
    global _ffmpeg_binary_cache, _ffprobe_binary_cache
    global _font_path_cache, _font_path_resolved
    _ffmpeg_binary_cache = None
    _ffprobe_binary_cache = None
    _font_path_cache = None
    _font_path_resolved = False


def _blender_install_dir() -> Optional[str]:
    """Return the directory holding the running Blender executable.

    Returns:
        The absolute directory path, or ``None`` when ``bpy`` is unavailable
        (for example when the module is imported by the test suite).
    """
    try:
        import bpy
    except ImportError:
        logger.debug("bpy를 사용할 수 없어 Blender 설치 경로 탐색을 건너뜁니다.")
        return None

    binary_path = getattr(bpy.app, "binary_path", "")
    if not binary_path:
        logger.debug("bpy.app.binary_path가 비어 있습니다.")
        return None
    return os.path.dirname(binary_path)


def resolve_ffmpeg_binary() -> str:
    """Locate the FFmpeg executable to use.

    Blender ships FFmpeg as a library rather than an executable, so a binary
    sitting next to ``blender.exe`` is only a lucky extra; the usual case is a
    system wide install found through ``PATH``.

    Returns:
        An absolute path when a bundled binary exists, otherwise the plain name
        :data:`DEFAULT_FFMPEG_BINARY`.
    """
    global _ffmpeg_binary_cache
    if _ffmpeg_binary_cache is not None:
        return _ffmpeg_binary_cache

    resolved = DEFAULT_FFMPEG_BINARY
    install_dir = _blender_install_dir()
    if install_dir:
        exe_name = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
        candidates = (
            os.path.join(install_dir, exe_name),
            os.path.join(install_dir, "bin", exe_name),
        )
        for candidate in candidates:
            try:
                if os.path.isfile(candidate):
                    resolved = candidate
                    break
            except OSError as exc:  # unreadable mount, permission issues, ...
                logger.warning("FFmpeg 경로 확인 실패 (%s): %s", candidate, exc)

    _ffmpeg_binary_cache = resolved
    logger.debug("FFmpeg 실행 파일: %s", resolved)
    return resolved


def resolve_ffprobe_binary() -> str:
    """Locate the ``ffprobe`` executable that ships next to FFmpeg.

    Returns:
        An absolute path when FFmpeg was resolved to one, otherwise the plain
        name ``"ffprobe"``.
    """
    global _ffprobe_binary_cache
    if _ffprobe_binary_cache is not None:
        return _ffprobe_binary_cache

    ffmpeg_binary = resolve_ffmpeg_binary()
    resolved = "ffprobe"
    if os.path.isfile(ffmpeg_binary):
        directory = os.path.dirname(ffmpeg_binary)
        exe_name = "ffprobe.exe" if platform.system() == "Windows" else "ffprobe"
        candidate = os.path.join(directory, exe_name)
        if os.path.isfile(candidate):
            resolved = candidate

    _ffprobe_binary_cache = resolved
    return resolved


def probe_duration(path: str, timeout: float = 30.0) -> Optional[float]:
    """Read a media file's duration with ``ffprobe``.

    Args:
        path: Absolute path of the media file.
        timeout: Limit in seconds for the probe process.

    Returns:
        The duration in seconds, or ``None`` when ``ffprobe`` is missing, fails
        or reports something unparsable. Callers treat ``None`` as "size
        matching unavailable" rather than as an error.
    """
    command = [
        resolve_ffprobe_binary(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            **_popen_keywords(low_priority=False),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffprobe로 영상 길이를 확인하지 못했습니다 (%s): %s", path, exc)
        return None

    if completed.returncode != 0:
        logger.warning(
            "ffprobe가 실패했습니다 (코드 %s): %s",
            completed.returncode,
            completed.stderr.decode("utf-8", errors="replace").strip(),
        )
        return None

    raw = completed.stdout.decode("utf-8", errors="replace").strip()
    try:
        duration = float(raw)
    except ValueError:
        logger.warning("ffprobe 출력에서 길이를 해석하지 못했습니다: %r", raw)
        return None

    return duration if duration > 0 else None


def is_ffmpeg_available(binary: Optional[str] = None) -> bool:
    """Check whether an FFmpeg executable can actually be launched.

    Args:
        binary: Executable path or name; defaults to
            :func:`resolve_ffmpeg_binary`.

    Returns:
        ``True`` when the path exists or the name resolves through ``PATH``.
    """
    binary = binary or resolve_ffmpeg_binary()
    if os.path.isfile(binary):
        return True
    return shutil.which(binary) is not None


def _font_candidates() -> Tuple[str, ...]:
    """Collect platform font paths, followed by Blender's bundled fonts.

    Returns:
        Candidate font paths in priority order.
    """
    system = platform.system()
    if system == "Windows":
        candidates = list(_WINDOWS_FONT_CANDIDATES)
    elif system == "Darwin":
        candidates = list(_MACOS_FONT_CANDIDATES)
    else:
        candidates = list(_LINUX_FONT_CANDIDATES)

    install_dir = _blender_install_dir()
    if install_dir:
        try:
            import bpy

            version_dir = f"{bpy.app.version[0]}.{bpy.app.version[1]}"
        except (ImportError, AttributeError, IndexError, TypeError) as exc:
            logger.debug("Blender 버전 디렉터리를 계산하지 못했습니다: %s", exc)
        else:
            candidates.extend(
                os.path.join(install_dir, version_dir, "datafiles", "fonts", name)
                for name in _BLENDER_BUNDLED_FONTS
            )
    return tuple(candidates)


def find_font_path() -> Optional[str]:
    """Find a TrueType font usable by FFmpeg's ``drawtext`` filter.

    Returns:
        The absolute, *unescaped* path of the first existing candidate, or
        ``None`` when no font was found. Overlays are skipped in that case.
    """
    global _font_path_cache, _font_path_resolved
    if _font_path_resolved:
        return _font_path_cache

    found: Optional[str] = None
    for candidate in _font_candidates():
        try:
            if os.path.isfile(candidate):
                found = candidate
                break
        except OSError as exc:
            logger.warning("폰트 경로 확인 실패 (%s): %s", candidate, exc)

    if found is None:
        logger.warning(
            "오버레이용 폰트를 찾지 못했습니다. 마커/정보 표시는 비활성화됩니다."
        )

    _font_path_cache = found
    _font_path_resolved = True
    return found


def _lower_process_priority() -> None:
    """Renice the child process on POSIX systems (``preexec_fn`` hook)."""
    try:
        os.nice(10)
    except (AttributeError, OSError) as exc:  # pragma: no cover - platform dependent
        logger.debug("프로세스 우선순위를 낮추지 못했습니다: %s", exc)


def _popen_keywords(low_priority: bool) -> Dict[str, Any]:
    """Build platform specific keyword arguments for :func:`subprocess.run`.

    Args:
        low_priority: Whether the encoder should yield CPU time to the rest of
            the system (the add-on's "Thermal Guard" option).

    Returns:
        Keyword arguments. On Windows this hides the console window and lowers
        the priority class; on POSIX it renices the child process.
    """
    keywords: Dict[str, Any] = {}
    if platform.system() == "Windows":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        keywords["startupinfo"] = startupinfo
        if low_priority:
            keywords["creationflags"] = subprocess.BELOW_NORMAL_PRIORITY_CLASS
    elif low_priority:
        keywords["preexec_fn"] = _lower_process_priority
    return keywords


def tail(text: str, max_lines: int = 12) -> str:
    """Return the last lines of a log, for display in a Blender report.

    Args:
        text: Full log text.
        max_lines: Maximum number of trailing lines to keep.

    Returns:
        The trimmed text; empty when ``text`` is empty.
    """
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


def run_ffmpeg(
    command: Sequence[str],
    low_priority: bool = False,
    timeout: Optional[float] = None,
) -> Tuple[bool, str]:
    """Run an FFmpeg command and capture its diagnostics.

    Args:
        command: Argument vector from :func:`build_ffmpeg_command`.
        low_priority: Run the encoder below normal priority.
        timeout: Optional limit in seconds; the process is killed when it is
            exceeded.

    Returns:
        ``(succeeded, message)``. On failure the message is FFmpeg's stderr
        tail, or a description of the exception that prevented the run.
    """
    logger.info("FFmpeg 실행: %s", " ".join(command))
    try:
        completed = subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            **_popen_keywords(low_priority),
        )
    except FileNotFoundError:
        message = (
            f"FFmpeg 실행 파일을 찾을 수 없습니다: {command[0]}\n"
            "FFmpeg를 설치하고 환경변수 PATH에 등록해 주세요."
        )
        logger.error(message)
        return False, message
    except PermissionError as exc:
        message = f"FFmpeg 실행 권한이 없습니다: {exc}"
        logger.error(message)
        return False, message
    except subprocess.TimeoutExpired:
        message = f"FFmpeg가 제한 시간({timeout}초)을 초과하여 중단되었습니다."
        logger.error(message)
        return False, message
    except OSError as exc:
        message = f"FFmpeg 프로세스를 시작하지 못했습니다: {exc}"
        logger.exception(message)
        return False, message

    stderr = ""
    if completed.stderr:
        stderr = completed.stderr.decode("utf-8", errors="replace")

    if completed.returncode != 0:
        logger.error("FFmpeg 실패 (코드 %s):\n%s", completed.returncode, stderr)
        return False, tail(stderr)

    logger.debug("FFmpeg 완료:\n%s", stderr)
    return True, ""
