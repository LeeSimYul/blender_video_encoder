# SPDX-FileCopyrightText: 2026 Simulacre
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators driving the two pipelines of the Quick Video Encoder add-on.

* ``TRANSCODE`` converts the movie strips queued in the sequence editor with
  FFmpeg.
* ``VIEWPORT`` records the 3D viewport with Blender's own renderer and then
  optionally post-processes the result with FFmpeg (overlays, GIF/WebP,
  mirroring or NVENC re-encoding).

All FFmpeg specific knowledge lives in :mod:`.utils.ffmpeg`; this module only
deals with Blender state, file naming and user feedback.
"""


# NOTE: ``from __future__ import annotations`` must NOT be added to this
# module either — the operator properties below (``files``, ``directory``,
# ``target_name``) are RNA annotations that Blender evaluates at runtime.

import logging
import os
import time
import traceback
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import bpy

from . import properties, ui
from .utils import ffmpeg

logger = logging.getLogger(__name__)

#: File extensions Blender appends to a viewport render, per container enum.
BLENDER_CONTAINER_EXTENSIONS: Dict[str, str] = {
    "MPEG4": "mp4",
    "QUICKTIME": "mov",
    "MATROSKA": "mkv",
    "AVI": "avi",
}

#: Source file extensions accepted by the import operator.
SUPPORTED_INPUT_EXTENSIONS = (
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".flv",
    ".wmv",
)

#: Viewport shading attributes saved and restored around a recording.
_SHADING_ATTRIBUTES = (
    "type",
    "light",
    "color_type",
    "wireframe_color_type",
    "background_type",
    "background_color",
    "single_color",
    "show_shadows",
    "show_cavity",
)

#: Viewport overlay attributes saved and restored around a recording.
_OVERLAY_ATTRIBUTES = ("show_overlays",)

#: ``scene.render`` attributes saved and restored around a recording.
_RENDER_ATTRIBUTES = (
    "resolution_x",
    "resolution_y",
    "resolution_percentage",
    "fps",
    "fps_base",
    "engine",
    "use_sequencer",
    "filepath",
)

#: ``scene.render.ffmpeg`` attributes saved and restored around a recording.
_FFMPEG_ATTRIBUTES = (
    "format",
    "codec",
    "audio_codec",
    "constant_rate_factor",
    "ffmpeg_preset",
    "gopsize",
)

#: Shading overrides applied by the silhouette review mode.
_ANIM_CHECK_SHADING = {
    "type": "SOLID",
    "light": "FLAT",
    "color_type": "SINGLE",
    "single_color": (0.0, 0.0, 0.0),
    "background_type": "VIEWPORT",
    "background_color": (0.8, 0.8, 0.8),
}

#: Frame rates that require Blender's NTSC ``fps_base`` of ``1.001``.
_NTSC_FRAME_RATES = frozenset({"23.98", "29.97", "59.94"})


# ---------------------------------------------------------------------------
# Sequencer helpers
# ---------------------------------------------------------------------------
def get_strip_collection(sequence_editor: Any) -> Optional[Any]:
    """Return the strip collection of a sequence editor.

    Blender 4.5 renamed ``SequenceEditor.sequences`` to ``.strips``; both are
    probed so the add-on works on 4.2 LTS and newer.

    Args:
        sequence_editor: ``scene.sequence_editor``, possibly ``None``.

    Returns:
        The strip collection, or ``None`` when there is no sequence editor.
    """
    if sequence_editor is None:
        return None
    collection = getattr(sequence_editor, "strips", None)
    if collection is None:
        collection = getattr(sequence_editor, "sequences", None)
    return collection


def get_movie_strips(sequence_editor: Any, only_enabled: bool = False) -> List[Any]:
    """Collect the movie strips of a sequence editor.

    Args:
        sequence_editor: ``scene.sequence_editor``, possibly ``None``.
        only_enabled: When ``True``, keep only strips whose ``gemini_chk``
            checkbox is ticked.

    Returns:
        The matching strips, in sequencer order; empty when there is none.
    """
    collection = get_strip_collection(sequence_editor)
    if collection is None:
        return []
    strips = [strip for strip in collection if strip.type == "MOVIE"]
    if only_enabled:
        strips = [
            strip
            for strip in strips
            if getattr(strip, properties.STRIP_ENABLED_PROPERTY, True)
        ]
    return strips


def set_strip_status(strip: Any, status: str) -> None:
    """Write the per-strip status label shown in the job list.

    Args:
        strip: The sequencer strip.
        status: Status text, or an empty string to clear it.
    """
    try:
        setattr(strip, properties.STRIP_STATUS_PROPERTY, status)
    except AttributeError as exc:
        logger.warning("스트립 상태를 기록하지 못했습니다 (%s): %s", strip.name, exc)


def refresh_ui() -> None:
    """Force a viewport redraw so job progress is visible during a batch."""
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except RuntimeError as exc:
        # Blender refuses the operator in restricted contexts (background mode,
        # inside a handler, ...). Progress simply stays invisible there.
        logger.debug("UI 갱신을 건너뜁니다: %s", exc)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def ensure_directory(path: str) -> Tuple[bool, str]:
    """Create a directory if needed.

    Args:
        path: Absolute directory path.

    Returns:
        ``(succeeded, message)``; the message is empty on success.
    """
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        message = f"저장 폴더를 만들 수 없습니다: {path}\n{exc}"
        logger.error(message)
        return False, message
    return True, ""


def unique_output_path(directory: str, base_name: str, extension: str) -> str:
    """Return a numbered path that does not exist yet.

    Args:
        directory: Target directory.
        base_name: File name without counter or extension.
        extension: Extension including the leading dot.

    Returns:
        An absolute path such as ``<directory>/<base_name>_001<extension>``.
    """
    counter = 1
    while True:
        candidate = os.path.join(directory, f"{base_name}_{counter:03d}{extension}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def _snapshot(source: Any, attribute_names: Iterable[str]) -> Dict[str, Any]:
    """Read attributes into a plain dict, copying vector values.

    Args:
        source: Object to read from (an RNA struct in practice).
        attribute_names: Attribute names to capture.

    Returns:
        Mapping of attribute name to value; attributes missing on this Blender
        version are skipped and logged at debug level.
    """
    state: Dict[str, Any] = {}
    for name in attribute_names:
        try:
            value = getattr(source, name)
        except AttributeError as exc:
            logger.debug("설정 '%s'을(를) 읽을 수 없습니다: %s", name, exc)
            continue
        if not isinstance(value, (str, bool, int, float)):
            # ``bpy_prop_array`` values are views onto Blender data and must be
            # copied before the original is modified.
            try:
                value = tuple(value)
            except TypeError:
                pass
        state[name] = value
    return state


def _restore(target: Any, state: Dict[str, Any]) -> None:
    """Write back a snapshot produced by :func:`_snapshot`.

    Failures are logged instead of raised: a partially restored viewport is
    always better than an aborted unwind that leaves the scene mid-recording.

    Args:
        target: Object to write to.
        state: Mapping produced by :func:`_snapshot`.
    """
    for name, value in state.items():
        try:
            setattr(target, name, value)
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning("설정 '%s' 복원 실패: %s", name, exc)


def resolve_target_size(
    context: bpy.types.Context, props: properties.GeminiProperties, use_scene: bool
) -> Tuple[int, int, str]:
    """Resolve the output resolution and frame rate.

    Args:
        context: Current Blender context.
        props: The scene's property group.
        use_scene: When ``True``, mirror the scene's Output properties instead
            of the add-on presets.

    Returns:
        ``(width, height, fps)`` with ``fps`` formatted for FFmpeg.
    """
    if use_scene:
        render = context.scene.render
        scale = render.resolution_percentage / 100.0
        width = int(render.resolution_x * scale)
        height = int(render.resolution_y * scale)
        return width, height, ffmpeg.format_fps(render.fps / render.fps_base)

    width, height = ffmpeg.get_resolution(props.preset)
    return width, height, props.fps_selection


def resolve_source_duration(
    props: properties.GeminiProperties, input_path: str
) -> Optional[float]:
    """Look up the source duration, but only when size matching needs it.

    Args:
        props: The scene's property group.
        input_path: Absolute path of the source file.

    Returns:
        The duration in seconds, or ``None`` when size matching is disabled or
        ``ffprobe`` could not report one. A ``None`` result simply falls back
        to the quality preset bitrate.
    """
    if not props.use_size_limit:
        return None

    duration = ffmpeg.probe_duration(input_path)
    if duration is None:
        logger.warning(
            "영상 길이를 확인하지 못해 용량 맞춤 대신 화질 프리셋을 사용합니다: %s",
            input_path,
        )
    return duration


def resolve_metadata(
    context: bpy.types.Context,
    props: properties.GeminiProperties,
    name: str = "",
) -> Optional[Dict[str, str]]:
    """Resolve the archive metadata tags for one output.

    Args:
        context: Current Blender context.
        props: The scene's property group.
        name: Value for the ``{name}`` token, usually the source file stem.

    Returns:
        The resolved tags, or ``None`` when metadata writing is disabled or
        every field is empty.
    """
    if not props.use_metadata:
        return None

    tokens = {
        "name": name,
        "scene": context.scene.name,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }
    resolved = ffmpeg.resolve_metadata_tokens(
        {
            "title": props.meta_title,
            "artist": props.meta_artist,
            "comment": props.meta_comment,
        },
        tokens,
    )
    return resolved or None


def build_encode_settings(
    context: bpy.types.Context,
    props: properties.GeminiProperties,
    input_path: str,
    use_scene_settings: bool = False,
    metadata: Optional[Dict[str, str]] = None,
    trim: Optional[ffmpeg.TimeRange] = None,
) -> ffmpeg.EncodeSettings:
    """Assemble the pure :class:`~.utils.ffmpeg.EncodeSettings` for a source.

    Args:
        context: Current Blender context.
        props: The scene's property group.
        input_path: Absolute path of the source file.
        use_scene_settings: Take resolution and FPS from the scene's Output
            properties instead of the add-on presets.
        metadata: Already resolved container tags, or ``None``.
        trim: Slice of the source to encode, or ``None`` for all of it.

    Returns:
        The settings describing this encode.
    """
    width, height, fps = resolve_target_size(context, props, use_scene_settings)
    if trim is not None and trim.duration:
        # Size matching must budget for the slice, not the whole recording,
        # and the slice length is already known without probing.
        duration = trim.duration if props.use_size_limit else None
    else:
        duration = resolve_source_duration(props, input_path)

    return ffmpeg.EncodeSettings.from_props(
        props,
        width=width,
        height=height,
        fps=fps,
        font_path=ffmpeg.find_font_path(),
        duration_seconds=duration,
        metadata=metadata,
        trim=trim,
    )


def encode_file(
    context: bpy.types.Context,
    props: properties.GeminiProperties,
    input_path: str,
    output_path: str,
    use_scene_settings: bool = False,
) -> Tuple[bool, str]:
    """Encode one file with FFmpeg using the current add-on settings.

    Args:
        context: Current Blender context.
        props: The scene's property group.
        input_path: Absolute path of the source file.
        output_path: Absolute path of the file to write.
        use_scene_settings: Take resolution and FPS from the scene's Output
            properties instead of the add-on presets.

    Returns:
        ``(succeeded, message)``; the message carries the FFmpeg error on
        failure.
    """
    settings = build_encode_settings(
        context,
        props,
        input_path,
        use_scene_settings,
        metadata=resolve_metadata(
            context, props, os.path.splitext(os.path.basename(output_path))[0]
        ),
    )

    try:
        command = ffmpeg.build_ffmpeg_command(
            settings, input_path, output_path, ffmpeg.resolve_ffmpeg_binary()
        )
    except ValueError as exc:
        logger.error("FFmpeg 명령어 생성 실패: %s", exc)
        return False, str(exc)

    return ffmpeg.run_ffmpeg(command, low_priority=props.use_thermal_guard)


# ---------------------------------------------------------------------------
# Transcode pipeline
# ---------------------------------------------------------------------------
def encode_source(
    context: bpy.types.Context,
    props: properties.GeminiProperties,
    source_path: str,
    output_dir: str,
    base_name: str,
    extension: str,
    trim: Optional[ffmpeg.TimeRange] = None,
) -> List[Tuple[str, bool, str]]:
    """Encode one source into one or more output files.

    A single file comes out normally; with the KSL Learning Pack enabled the
    same source fans out into the original, mirrored and slowed variants, each
    carrying its own suffix.

    Args:
        context: Current Blender context.
        props: The scene's property group.
        source_path: Absolute path of the source file.
        output_dir: Directory the outputs are written to.
        base_name: Output file stem, without suffix or extension.
        trim: Slice of the source to encode, or ``None`` for all of it.
        extension: Output extension including the leading dot.

    Returns:
        One ``(output_path, succeeded, message)`` tuple per produced file.
    """
    settings = build_encode_settings(
        context,
        props,
        source_path,
        metadata=resolve_metadata(context, props, base_name),
        trim=trim,
    )

    if props.use_ksl_pack:
        variants = ffmpeg.iter_variant_settings(settings)
    else:
        variants = [(None, settings)]

    binary = ffmpeg.resolve_ffmpeg_binary()
    results: List[Tuple[str, bool, str]] = []
    for variant, variant_settings in variants:
        suffix = variant.suffix if variant is not None else ""
        output_path = os.path.join(output_dir, f"{base_name}{suffix}{extension}")
        if os.path.normpath(source_path) == os.path.normpath(output_path):
            # Never let FFmpeg read and write the same file.
            output_path = os.path.join(
                output_dir, f"{base_name}{suffix}_conv{extension}"
            )

        try:
            command = ffmpeg.build_ffmpeg_command(
                variant_settings, source_path, output_path, binary
            )
        except ValueError as exc:
            logger.error("FFmpeg 명령어 생성 실패: %s", exc)
            results.append((output_path, False, str(exc)))
            continue

        if variant is not None:
            logger.info("KSL 변형 인코딩: %s (%s)", variant.label, output_path)
        succeeded, error = ffmpeg.run_ffmpeg(
            command, low_priority=props.use_thermal_guard
        )
        results.append((output_path, succeeded, error))

    return results


def run_transcode_mode(
    context: bpy.types.Context, props: properties.GeminiProperties
) -> Tuple[int, List[Tuple[str, str]], str]:
    """Convert every ticked movie strip with FFmpeg.

    Args:
        context: Current Blender context.
        props: The scene's property group.

    Returns:
        ``(success_count, failures, output_directory)`` where ``failures`` is a
        list of ``(file_name, error_message)`` pairs.
    """
    output_dir = os.path.normpath(bpy.path.abspath(props.output_dir))
    created, message = ensure_directory(output_dir)
    if not created:
        return 0, [("출력 폴더", message)], output_dir

    extension = ffmpeg.get_extension(props.output_format)
    success_count = 0
    failures: List[Tuple[str, str]] = []

    for strip in get_movie_strips(context.scene.sequence_editor, only_enabled=True):
        set_strip_status(strip, "변환 중...")
        refresh_ui()

        source_path = bpy.path.abspath(strip.filepath)
        base_name = os.path.splitext(os.path.basename(source_path))[0]

        if not os.path.isfile(source_path):
            message = f"원본 파일을 찾을 수 없습니다: {source_path}"
            logger.error(message)
            set_strip_status(strip, "실패")
            failures.append((base_name, message))
            continue

        results = encode_source(
            context, props, source_path, output_dir, base_name, extension
        )
        produced = sum(1 for _, succeeded, _ in results if succeeded)
        success_count += produced
        failures.extend(
            (os.path.basename(path), error)
            for path, succeeded, error in results
            if not succeeded
        )
        set_strip_status(strip, _batch_status(produced, len(results)))

    return success_count, failures, output_dir


def _batch_status(produced: int, expected: int) -> str:
    """Return the job-list label for a strip that produced ``produced`` files.

    Args:
        produced: Number of successful outputs.
        expected: Number of outputs that were attempted.

    Returns:
        A short Korean status label.
    """
    if produced == 0:
        return "실패"
    if produced < expected:
        return f"일부 완료 ({produced}/{expected})"
    if expected > 1:
        return f"완료 ({produced}개)"
    return "완료"


# ---------------------------------------------------------------------------
# Segment export & editing proxy
# ---------------------------------------------------------------------------
def scene_fps(scene: bpy.types.Scene) -> float:
    """Return the scene frame rate as a float.

    Args:
        scene: The scene to read.

    Returns:
        Frames per second; ``24.0`` if the scene reports something unusable.
    """
    try:
        fps = scene.render.fps / scene.render.fps_base
    except (AttributeError, ZeroDivisionError):
        logger.warning("씬 FPS를 읽지 못해 24fps로 가정합니다.")
        return 24.0
    return fps if fps > 0 else 24.0


def collect_strip_segments(
    context: bpy.types.Context,
) -> Tuple[List[ffmpeg.Segment], str]:
    """Turn every ticked movie strip into a segment of its own source file.

    The trimmed in/out points of the strip are honoured, so a long OBS capture
    cut into several strips exports exactly those pieces.

    Args:
        context: Current Blender context.

    Returns:
        ``(segments, error_message)``; the message is empty on success.
    """
    strips = get_movie_strips(context.scene.sequence_editor, only_enabled=True)
    if not strips:
        return [], "체크된 영상 스트립이 없습니다."

    fps = scene_fps(context.scene)
    segments = []
    for index, strip in enumerate(strips, start=1):
        source_path = bpy.path.abspath(strip.filepath)
        start = max(0.0, strip.frame_offset_start / fps)
        duration = max(0.0, strip.frame_final_duration / fps)
        if duration <= 0:
            logger.warning("길이가 0인 스트립을 건너뜁니다: %s", strip.name)
            continue
        segments.append(
            ffmpeg.Segment(
                name=strip.name,
                start=start,
                end=start + duration,
                index=index,
                source_path=source_path,
                strip_name=strip.name,
            )
        )

    if not segments:
        return [], "내보낼 수 있는 구간이 없습니다."
    return segments, ""


def build_strip_ranges(strips: List[Any]) -> List[ffmpeg.StripRange]:
    """Convert sequencer strips into the pure model the splitter consumes.

    Args:
        strips: Movie strips from the sequence editor.

    Returns:
        One :class:`~.utils.ffmpeg.StripRange` per strip, in the same order.
    """
    return [
        ffmpeg.StripRange(
            name=strip.name,
            source_path=bpy.path.abspath(strip.filepath),
            frame_start=strip.frame_start,
            frame_final_start=strip.frame_final_start,
            frame_final_end=strip.frame_final_end,
        )
        for strip in strips
    ]


def collect_marker_segments(
    context: bpy.types.Context,
) -> Tuple[List[ffmpeg.Segment], str]:
    """Split every ticked strip at the markers that fall inside it.

    All checked strips are processed, not just the first one: each strip is
    matched against the markers inside its own frame range, so a timeline
    holding several recordings exports each of them cut at its own markers.

    Args:
        context: Current Blender context.

    Returns:
        ``(segments, error_message)``; the message is empty on success.
    """
    scene = context.scene
    markers = [(marker.frame, marker.name) for marker in scene.timeline_markers]
    if not markers:
        return [], "타임라인에 마커가 없습니다."

    strips = get_movie_strips(scene.sequence_editor, only_enabled=True)
    if not strips:
        return [], "체크된 영상 스트립이 없습니다."

    segments = ffmpeg.build_marker_segments(
        build_strip_ranges(strips), markers, scene_fps(scene)
    )
    if not segments:
        return [], (
            "체크된 스트립의 프레임 범위 안에 마커가 없습니다. "
            "마커를 스트립 위로 옮기거나 분할 기준을 '스트립'으로 바꿔 주세요."
        )
    return segments, ""


def collect_segments(
    context: bpy.types.Context, props: properties.GeminiProperties
) -> Tuple[List[ffmpeg.Segment], str]:
    """Collect the segments to export, per the configured source.

    Args:
        context: Current Blender context.
        props: The scene's property group.

    Returns:
        ``(segments, error_message)``; the message is empty on success.
    """
    if props.segment_source == "MARKERS":
        return collect_marker_segments(context)
    return collect_strip_segments(context)


def run_segment_export(
    context: bpy.types.Context, props: properties.GeminiProperties
) -> Tuple[int, List[Tuple[str, str]], str]:
    """Export every timeline segment as its own file.

    Args:
        context: Current Blender context.
        props: The scene's property group.

    Returns:
        ``(success_count, failures, output_directory)``.
    """
    output_dir = os.path.normpath(bpy.path.abspath(props.output_dir))
    created, message = ensure_directory(output_dir)
    if not created:
        return 0, [("출력 폴더", message)], output_dir

    segments, error = collect_segments(context, props)
    if error:
        return 0, [("구간 수집", error)], output_dir

    extension = ffmpeg.get_extension(props.output_format)
    date_string = datetime.now().strftime("%Y%m%d")
    binary = ffmpeg.resolve_ffmpeg_binary()

    success_count = 0
    failures: List[Tuple[str, str]] = []

    for segment in segments:
        if not os.path.isfile(segment.source_path):
            message = f"원본 파일을 찾을 수 없습니다: {segment.source_path}"
            logger.error(message)
            failures.append((segment.name, message))
            continue

        try:
            filename = ffmpeg.format_segment_filename(
                props.segment_template,
                extension,
                prefix=props.segment_prefix,
                name=segment.name,
                index=segment.index,
                scene=context.scene.name,
                date=date_string,
                strip=segment.strip_name,
            )
        except ValueError as exc:
            # A broken template breaks every segment, so stop right away.
            logger.error("파일명 템플릿 오류: %s", exc)
            failures.append(("파일명 템플릿", str(exc)))
            break

        refresh_ui()
        if props.segment_stream_copy:
            command = ffmpeg.build_segment_copy_command(
                segment.source_path,
                os.path.join(output_dir, filename),
                segment,
                binary,
            )
            succeeded, error = ffmpeg.run_ffmpeg(
                command, low_priority=props.use_thermal_guard
            )
            results = [(filename, succeeded, error)]
        else:
            results = encode_source(
                context,
                props,
                segment.source_path,
                output_dir,
                os.path.splitext(filename)[0],
                extension,
                trim=segment.as_time_range(),
            )

        success_count += sum(1 for _, succeeded, _ in results if succeeded)
        failures.extend(
            (os.path.basename(path), error)
            for path, succeeded, error in results
            if not succeeded
        )

    return success_count, failures, output_dir


def run_proxy_generation(
    context: bpy.types.Context, props: properties.GeminiProperties
) -> Tuple[int, List[Tuple[str, str]], str]:
    """Generate a 720p30 editing proxy for every ticked strip.

    Args:
        context: Current Blender context.
        props: The scene's property group.

    Returns:
        ``(success_count, failures, output_directory)``.
    """
    output_dir = os.path.normpath(bpy.path.abspath(props.output_dir))
    created, message = ensure_directory(output_dir)
    if not created:
        return 0, [("출력 폴더", message)], output_dir

    strips = get_movie_strips(context.scene.sequence_editor, only_enabled=True)
    if not strips:
        return 0, [("프록시", "체크된 영상 스트립이 없습니다.")], output_dir

    binary = ffmpeg.resolve_ffmpeg_binary()
    success_count = 0
    failures: List[Tuple[str, str]] = []

    for strip in strips:
        set_strip_status(strip, "프록시 생성 중...")
        refresh_ui()

        source_path = bpy.path.abspath(strip.filepath)
        base_name = os.path.splitext(os.path.basename(source_path))[0]
        if not os.path.isfile(source_path):
            message = f"원본 파일을 찾을 수 없습니다: {source_path}"
            logger.error(message)
            set_strip_status(strip, "실패")
            failures.append((base_name, message))
            continue

        output_path = os.path.join(
            output_dir, f"{base_name}{ffmpeg.PROXY_SUFFIX}.mp4"
        )
        command = ffmpeg.build_proxy_command(
            source_path, output_path, ffmpeg_binary=binary
        )
        succeeded, error = ffmpeg.run_ffmpeg(
            command, low_priority=props.use_thermal_guard
        )
        if succeeded:
            success_count += 1
            set_strip_status(strip, "프록시 완료")
        else:
            set_strip_status(strip, "실패")
            failures.append((base_name, error))

    return success_count, failures, output_dir


# ---------------------------------------------------------------------------
# Viewport pipeline
# ---------------------------------------------------------------------------
def find_view3d_space(context: bpy.types.Context) -> Optional[Any]:
    """Return the first 3D viewport space of the current screen.

    Args:
        context: Current Blender context.

    Returns:
        A ``SpaceView3D``, or ``None`` when the screen shows no 3D viewport.
    """
    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()
            return area.spaces.active
    return None


def apply_viewport_overrides(
    space: Any,
    props: properties.GeminiProperties,
    force_anim_check: Optional[bool] = None,
) -> None:
    """Apply the silhouette or forced-rendered shading overrides.

    Args:
        space: The ``SpaceView3D`` being recorded.
        props: The scene's property group.
        force_anim_check: Overrides ``props.use_anim_check`` for this pass.
            The A/B comparison uses it to record the same scene twice, once
            with the silhouette shading and once without.
    """
    anim_check = props.use_anim_check if force_anim_check is None else force_anim_check
    if anim_check:
        _restore(space.shading, dict(_ANIM_CHECK_SHADING))
        _restore(space.overlay, {"show_overlays": False})
    elif props.use_vp_preset:
        _restore(space.shading, {"type": "RENDERED"})


def apply_forced_scene_settings(
    scene: bpy.types.Scene, props: properties.GeminiProperties
) -> None:
    """Override the scene resolution and frame rate with the add-on presets.

    Args:
        scene: The scene being rendered.
        props: The scene's property group.
    """
    width, height = ffmpeg.get_resolution(props.preset)
    scene.render.resolution_x = width
    scene.render.resolution_y = height

    fps_value = float(props.fps_selection)
    if props.fps_selection in _NTSC_FRAME_RATES:
        scene.render.fps = int(fps_value) + 1
        scene.render.fps_base = 1.001
    else:
        scene.render.fps = int(fps_value)
        scene.render.fps_base = 1.0


def viewport_output_prefix(props: properties.GeminiProperties) -> str:
    """Return the file name prefix describing the recording mode.

    Args:
        props: The scene's property group.

    Returns:
        One of ``"ABTest"``, ``"Silhouette"``, ``"Preview"``, ``"Workbench"``
        or ``"Viewport"``.
    """
    if props.use_side_by_side:
        return "ABTest"
    if props.use_anim_check:
        return "Silhouette"
    if props.use_vp_preset:
        return "Preview"
    if props.vp_engine == "WORKBENCH":
        return "Workbench"
    return "Viewport"


def viewport_render_candidates(
    base_path: str, container: str, frame_start: int, frame_end: int
) -> Tuple[str, ...]:
    """List the paths Blender may have written the recording to.

    Blender appends either just the container extension or the rendered frame
    range, depending on the output settings.

    Args:
        base_path: ``scene.render.filepath`` without extension.
        container: Blender container enum, e.g. ``"MPEG4"``.
        frame_start: First rendered frame.
        frame_end: Last rendered frame.

    Returns:
        Candidate paths in priority order.
    """
    extension = BLENDER_CONTAINER_EXTENSIONS.get(container, "mp4")
    return (
        f"{base_path}.{extension}",
        f"{base_path}{frame_start:04d}-{frame_end:04d}.{extension}",
    )


def apply_recording_settings(
    scene: bpy.types.Scene, props: properties.GeminiProperties
) -> None:
    """Apply the render settings shared by every recording pass.

    Args:
        scene: The scene being rendered.
        props: The scene's property group.
    """
    scene.render.use_sequencer = False
    if not props.use_scene_settings:
        apply_forced_scene_settings(scene, props)

    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = props.vp_container
    scene.render.ffmpeg.codec = props.vp_codec
    scene.render.ffmpeg.audio_codec = "AAC"
    scene.render.ffmpeg.constant_rate_factor = props.vp_quality
    scene.render.ffmpeg.ffmpeg_preset = props.vp_speed
    scene.render.ffmpeg.gopsize = props.vp_keyframe


def invoke_render(
    scene: bpy.types.Scene, props: properties.GeminiProperties
) -> None:
    """Run one animation render with the configured engine.

    Args:
        scene: The scene being rendered.
        props: The scene's property group.

    Raises:
        RuntimeError: Propagated from Blender when the render is refused or
            cancelled.
    """
    if props.vp_engine == "WORKBENCH":
        scene.render.engine = "BLENDER_WORKBENCH"
        bpy.ops.render.render(animation=True)
    else:
        bpy.ops.render.opengl(animation=True, view_context=True)


def remove_files(paths: Iterable[str]) -> None:
    """Delete intermediate files, logging rather than raising on failure.

    Args:
        paths: Absolute paths to delete.
    """
    for path in paths:
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning("중간 파일을 삭제하지 못했습니다 (%s): %s", path, exc)


def validate_side_by_side(props: properties.GeminiProperties) -> Tuple[bool, str]:
    """Check that the A/B comparison can run with the current settings.

    Args:
        props: The scene's property group.

    Returns:
        ``(is_valid, message)``; the message explains the refusal.
    """
    if props.vp_engine != "OPENGL":
        return False, (
            "A/B 비교 출력은 '현재 화면 캡처(Viewport)' 방식에서만 사용할 수 "
            "있습니다. 실루엣 셰이딩이 뷰포트 설정이기 때문입니다."
        )
    if props.output_format not in ffmpeg.VIDEO_FORMATS:
        return False, (
            "A/B 비교 출력은 MP4/MOV/MKV/AVI 포맷에서만 사용할 수 있습니다."
        )
    return True, ""


def encode_side_by_side(
    context: bpy.types.Context,
    props: properties.GeminiProperties,
    left_path: str,
    right_path: str,
    output_path: str,
) -> Tuple[bool, str]:
    """Stack two recordings horizontally into one comparison video.

    Args:
        context: Current Blender context.
        props: The scene's property group.
        left_path: Recording shown on the left (original shading).
        right_path: Recording shown on the right (silhouette shading).
        output_path: File to write.

    Returns:
        ``(succeeded, message)``; the message carries the FFmpeg error on
        failure.
    """
    settings = build_encode_settings(
        context,
        props,
        left_path,
        use_scene_settings=props.use_scene_settings,
        metadata=resolve_metadata(
            context, props, os.path.splitext(os.path.basename(output_path))[0]
        ),
    )
    labels = ffmpeg.SIDE_BY_SIDE_LABELS if props.sbs_show_labels else None

    try:
        command = ffmpeg.build_side_by_side_command(
            settings,
            left_path,
            right_path,
            output_path,
            labels,
            ffmpeg.resolve_ffmpeg_binary(),
        )
    except ValueError as exc:
        logger.error("A/B 합성 명령어 생성 실패: %s", exc)
        return False, str(exc)

    return ffmpeg.run_ffmpeg(command, low_priority=props.use_thermal_guard)


def needs_post_processing(props: properties.GeminiProperties) -> bool:
    """Tell whether the recording must be piped through FFmpeg afterwards.

    Args:
        props: The scene's property group.

    Returns:
        ``True`` when overlays, mirroring, NVENC or a GIF/WebP output make a
        second pass necessary.
    """
    return bool(
        props.show_markers
        or props.show_info
        or props.use_mirror
        or props.use_nvenc
        or props.output_format in ffmpeg.AUDIO_LESS_FORMATS
    )


def run_viewport_mode(
    context: bpy.types.Context, props: properties.GeminiProperties
) -> Tuple[bool, str, Optional[str]]:
    """Record the 3D viewport and optionally post-process the result.

    The scene render settings and the viewport shading are snapshotted before
    the recording and restored afterwards, including on failure.

    Args:
        context: Current Blender context.
        props: The scene's property group.

    Returns:
        ``(succeeded, message, output_path)``. ``output_path`` is ``None`` when
        the recording failed.
    """
    scene = context.scene
    if scene.camera is None:
        return False, "활성화된 카메라가 없습니다.", None

    if props.use_side_by_side:
        is_valid, message = validate_side_by_side(props)
        if not is_valid:
            return False, message, None

    output_dir = os.path.normpath(bpy.path.abspath(props.output_dir))
    created, message = ensure_directory(output_dir)
    if not created:
        return False, message, None

    space = find_view3d_space(context) if props.vp_engine == "OPENGL" else None
    shading_state = _snapshot(space.shading, _SHADING_ATTRIBUTES) if space else {}
    overlay_state = _snapshot(space.overlay, _OVERLAY_ATTRIBUTES) if space else {}
    render_state = _snapshot(scene.render, _RENDER_ATTRIBUTES)
    ffmpeg_state = _snapshot(scene.render.ffmpeg, _FFMPEG_ATTRIBUTES)
    image_format = scene.render.image_settings.file_format

    extension = ffmpeg.get_extension(props.output_format)
    base_name = (
        f"{viewport_output_prefix(props)}_{scene.frame_end}_"
        f"{datetime.now().strftime('%Y%m%d')}"
    )
    final_path = unique_output_path(output_dir, base_name, extension)

    # When a second FFmpeg pass follows, Blender must write to a different name
    # than the final file: FFmpeg cannot read and write the same path.
    post_processing = needs_post_processing(props)
    stem = os.path.splitext(final_path)[0]
    if props.use_side_by_side:
        # Two passes: the original shading, then the silhouette.
        render_passes = ((f"{stem}_a", False), (f"{stem}_b", True))
    elif post_processing:
        render_passes = ((f"{stem}_raw", None),)
    else:
        render_passes = ((stem, None),)

    render_succeeded = False
    error_message = ""
    try:
        apply_recording_settings(scene, props)
        for render_base, force_anim_check in render_passes:
            if space:
                # Start each pass from the user's own shading so the overrides
                # of the previous pass never leak into the next one.
                _restore(space.shading, shading_state)
                _restore(space.overlay, overlay_state)
                apply_viewport_overrides(space, props, force_anim_check)
            scene.render.filepath = render_base
            invoke_render(scene, props)
        render_succeeded = True
    except RuntimeError as exc:
        # Raised by the render operators when Blender refuses the job, e.g.
        # missing context, cancelled render or an unwritable output path.
        error_message = str(exc)
        logger.error("뷰포트 녹화 실패: %s", error_message)
    except OSError as exc:
        error_message = f"출력 파일을 쓸 수 없습니다: {exc}"
        logger.error(error_message)
    finally:
        if space:
            _restore(space.shading, shading_state)
            _restore(space.overlay, overlay_state)
        _restore(scene.render.ffmpeg, ffmpeg_state)
        _restore(scene.render, render_state)
        scene.render.image_settings.file_format = image_format

    if not render_succeeded:
        return False, error_message or "알 수 없는 오류로 녹화가 중단되었습니다.", None

    recorded_paths = []
    for render_base, _ in render_passes:
        found = next(
            (
                candidate
                for candidate in viewport_render_candidates(
                    render_base, props.vp_container, scene.frame_start, scene.frame_end
                )
                if os.path.isfile(candidate)
            ),
            None,
        )
        if found is None:
            message = (
                "녹화 파일을 찾을 수 없습니다. 저장 경로와 쓰기 권한을 확인해 주세요."
            )
            logger.error("%s (base=%s)", message, render_base)
            return False, message, None
        recorded_paths.append(found)

    if props.use_side_by_side:
        succeeded, error = encode_side_by_side(
            context, props, recorded_paths[0], recorded_paths[1], final_path
        )
        if not succeeded:
            logger.warning("A/B 합성 실패, 녹화 원본을 유지합니다: %s", error)
            return True, "A/B 합성 실패 (녹화 원본 유지)", recorded_paths[0]
        remove_files(recorded_paths)
        return True, "OK", final_path

    recorded_path = recorded_paths[0]
    if not post_processing:
        return True, "OK", recorded_path

    succeeded, error = encode_file(
        context,
        props,
        recorded_path,
        final_path,
        use_scene_settings=props.use_scene_settings,
    )
    if not succeeded:
        logger.warning("후처리 실패, 녹화 원본을 유지합니다: %s", error)
        return True, "후처리 실패 (녹화 원본 유지)", recorded_path

    remove_files([recorded_path])
    return True, "OK", final_path


# ---------------------------------------------------------------------------
# VRChat thumbnail pipeline
# ---------------------------------------------------------------------------
def render_viewport_still(
    context: bpy.types.Context,
    props: properties.GeminiProperties,
    output_dir: str,
) -> Tuple[Optional[str], str]:
    """Render a single frame of the viewport at thumbnail resolution.

    Args:
        context: Current Blender context.
        props: The scene's property group.
        output_dir: Directory the temporary PNG is written to.

    Returns:
        ``(png_path, message)``; ``png_path`` is ``None`` when the render
        failed and ``message`` then explains why.
    """
    scene = context.scene
    render_state = _snapshot(scene.render, _RENDER_ATTRIBUTES)
    image_format = scene.render.image_settings.file_format
    temp_base = os.path.join(output_dir, f".qve_thumbnail_{os.getpid()}")

    error_message = ""
    try:
        scene.render.use_sequencer = False
        scene.render.resolution_x = ffmpeg.THUMBNAIL_RESOLUTION[0]
        scene.render.resolution_y = ffmpeg.THUMBNAIL_RESOLUTION[1]
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = temp_base

        if props.vp_engine == "WORKBENCH":
            scene.render.engine = "BLENDER_WORKBENCH"
            bpy.ops.render.render(write_still=True)
        else:
            bpy.ops.render.opengl(write_still=True, view_context=True)
    except RuntimeError as exc:
        error_message = f"스틸 렌더에 실패했습니다: {exc}"
        logger.error(error_message)
    except OSError as exc:
        error_message = f"임시 파일을 쓸 수 없습니다: {exc}"
        logger.error(error_message)
    finally:
        _restore(scene.render, render_state)
        scene.render.image_settings.file_format = image_format

    if error_message:
        return None, error_message

    rendered = f"{temp_base}.png"
    if not os.path.isfile(rendered):
        message = "렌더된 스틸 이미지를 찾을 수 없습니다."
        logger.error("%s (%s)", message, rendered)
        return None, message
    return rendered, ""


def resolve_thumbnail_source(
    context: bpy.types.Context,
    props: properties.GeminiProperties,
    output_dir: str,
) -> Tuple[Optional[str], float, List[str], str]:
    """Pick the image source for the VRChat thumbnail.

    In transcode mode the frame is grabbed from the first ticked movie strip,
    at the position of the playhead inside that strip. Otherwise the viewport
    itself is rendered to a temporary still.

    Args:
        context: Current Blender context.
        props: The scene's property group.
        output_dir: Directory temporary files may be written to.

    Returns:
        ``(source_path, seek_seconds, temporary_files, message)``.
        ``source_path`` is ``None`` when no source could be produced, and
        ``message`` then explains why.
    """
    scene = context.scene
    strips = get_movie_strips(scene.sequence_editor, only_enabled=True)

    if props.tool_mode == "TRANSCODE" and strips:
        strip = strips[0]
        source_path = bpy.path.abspath(strip.filepath)
        if not os.path.isfile(source_path):
            return None, 0.0, [], f"원본 파일을 찾을 수 없습니다: {source_path}"

        fps = scene.render.fps / scene.render.fps_base
        offset_frames = max(0, scene.frame_current - strip.frame_final_start)
        return source_path, offset_frames / fps if fps else 0.0, [], ""

    rendered, message = render_viewport_still(context, props, output_dir)
    if rendered is None:
        return None, 0.0, [], message
    return rendered, 0.0, [rendered], ""


def export_vrchat_thumbnail(
    context: bpy.types.Context, props: properties.GeminiProperties
) -> Tuple[bool, str, Optional[str]]:
    """Export a VRChat world thumbnail at the platform's fixed resolution.

    Args:
        context: Current Blender context.
        props: The scene's property group.

    Returns:
        ``(succeeded, message, output_path)``.
    """
    output_dir = os.path.normpath(bpy.path.abspath(props.output_dir))
    created, message = ensure_directory(output_dir)
    if not created:
        return False, message, None

    source_path, seek_seconds, temporary_files, message = resolve_thumbnail_source(
        context, props, output_dir
    )
    if source_path is None:
        return False, message, None

    extension = ffmpeg.THUMBNAIL_EXTENSIONS.get(props.vrc_thumb_format, ".webp")
    base_name = f"VRC_Thumbnail_{datetime.now().strftime('%Y%m%d')}"
    output_path = unique_output_path(output_dir, base_name, extension)

    try:
        command = ffmpeg.build_thumbnail_command(
            source_path,
            output_path,
            image_format=props.vrc_thumb_format,
            quality=props.vrc_thumb_quality,
            seek_seconds=seek_seconds,
            ffmpeg_binary=ffmpeg.resolve_ffmpeg_binary(),
        )
    except ValueError as exc:
        remove_files(temporary_files)
        logger.error("썸네일 명령어 생성 실패: %s", exc)
        return False, str(exc), None

    succeeded, error = ffmpeg.run_ffmpeg(
        command, low_priority=props.use_thermal_guard
    )
    remove_files(temporary_files)

    if not succeeded:
        return False, error, None
    return True, "OK", output_path


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
class GEMINI_OT_Process(bpy.types.Operator):
    """지정된 설정으로 인코딩 및 녹화 작업을 시작합니다."""

    bl_idname = "gemini.process"
    bl_label = "작업 시작"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set:
        """Run the pipeline selected by ``props.tool_mode``.

        Args:
            context: Current Blender context.

        Returns:
            ``{'FINISHED'}`` on success, ``{'CANCELLED'}`` otherwise.
        """
        props = getattr(context.scene, properties.SCENE_PROPERTY, None)
        if props is None:
            self.report({"ERROR"}, "애드온 속성을 찾을 수 없습니다. 재활성화해 주세요.")
            return {"CANCELLED"}

        if not props.output_dir:
            self.report({"ERROR"}, "출력(저장) 폴더를 지정해주세요!")
            return {"CANCELLED"}

        start_time = time.time()
        try:
            if props.tool_mode == "TRANSCODE":
                result = self._run_transcode(context, props)
            else:
                result = self._run_viewport(context, props)
        except Exception:  # noqa: BLE001 - last resort guard for the UI thread
            error_log = traceback.format_exc()
            logger.exception("작업 중 예기치 못한 오류가 발생했습니다.")
            context.window_manager.clipboard = error_log
            ui.show_message_box(
                "알 수 없는 오류가 발생했습니다.\n"
                "에러 로그가 클립보드에 복사되었습니다.",
                "치명적 오류",
                "ERROR",
            )
            self.report({"ERROR"}, "작업 실패: 에러 로그를 클립보드에 복사했습니다.")
            return {"CANCELLED"}

        if result is None:
            return {"CANCELLED"}

        summary, output_location = result
        duration = time.time() - start_time
        elapsed = f"({int(duration // 60)}분 {duration % 60:.2f}초 소요됨)"
        ui.show_message_box(f"{summary}\n\n{elapsed}", "작업 완료", "CHECKMARK")

        if output_location:
            context.window_manager.clipboard = output_location
            self.report({"INFO"}, f"작업 완료! 경로가 복사되었습니다. {elapsed}")
        else:
            self.report({"INFO"}, f"작업 완료! {elapsed}")
        return {"FINISHED"}

    def _run_transcode(
        self, context: bpy.types.Context, props: properties.GeminiProperties
    ) -> Optional[Tuple[str, str]]:
        """Run the transcode pipeline and build its summary.

        Args:
            context: Current Blender context.
            props: The scene's property group.

        Returns:
            ``(summary, output_directory)``, or ``None`` when the job could not
            start (the reason is already reported).
        """
        strips = get_movie_strips(context.scene.sequence_editor)
        if not strips:
            self.report({"ERROR"}, "불러온 영상이 없습니다.")
            return None

        enabled = [
            strip
            for strip in strips
            if getattr(strip, properties.STRIP_ENABLED_PROPERTY, True)
        ]
        if not enabled:
            self.report({"ERROR"}, "선택(체크)된 영상이 없습니다.")
            return None

        for strip in strips:
            set_strip_status(strip, "")

        success_count, failures, output_dir = run_transcode_mode(context, props)
        summary = f"성공: {success_count}건"
        if failures:
            summary += f"\n실패: {len(failures)}건"
            for name, error in failures:
                logger.error("변환 실패 (%s): %s", name, error)
            self.report(
                {"WARNING"},
                f"{len(failures)}건 실패했습니다. 콘솔 로그를 확인해 주세요.",
            )
        return summary, output_dir

    def _run_viewport(
        self, context: bpy.types.Context, props: properties.GeminiProperties
    ) -> Optional[Tuple[str, str]]:
        """Run the viewport pipeline and build its summary.

        Args:
            context: Current Blender context.
            props: The scene's property group.

        Returns:
            ``(summary, output_directory)``, or ``None`` when the recording
            failed (the reason is already reported).
        """
        succeeded, message, output_path = run_viewport_mode(context, props)
        if not succeeded or output_path is None:
            self.report({"ERROR"}, f"녹화 실패: {message}")
            return None

        summary = f"저장 완료:\n{os.path.basename(output_path)}"
        if message != "OK":
            summary += f"\n{message}"
            self.report({"WARNING"}, message)
        return summary, os.path.dirname(output_path)


class GEMINI_OT_ImportMulti(bpy.types.Operator):
    """탐색기를 열어 변환할 영상 파일을 여러 개 불러옵니다."""

    bl_idname = "gemini.import_multi"
    bl_label = "영상 불러오기"
    bl_options = {"REGISTER", "UNDO"}

    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype="DIR_PATH")

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set:
        """Open the file browser.

        Args:
            context: Current Blender context.
            event: The invoking event.

        Returns:
            ``{'RUNNING_MODAL'}``.
        """
        del event  # Required by the operator signature.
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set:
        """Add the selected video files to the sequence editor.

        Args:
            context: Current Blender context.

        Returns:
            ``{'FINISHED'}``, even when some files were skipped.
        """
        scene = context.scene
        if scene.sequence_editor is None:
            scene.sequence_editor_create()

        collection = get_strip_collection(scene.sequence_editor)
        if collection is None:
            self.report({"ERROR"}, "시퀀서를 초기화하지 못했습니다.")
            return {"CANCELLED"}

        frame_start = 1
        if len(collection) > 0:
            frame_start = max(strip.frame_final_end for strip in collection) + 10

        skipped = 0
        imported = 0
        for file_entry in self.files:
            extension = os.path.splitext(file_entry.name)[1].lower()
            if extension not in SUPPORTED_INPUT_EXTENSIONS:
                skipped += 1
                continue

            filepath = os.path.join(self.directory, file_entry.name)
            try:
                strip = collection.new_movie(
                    name=file_entry.name,
                    filepath=filepath,
                    channel=1,
                    frame_start=frame_start,
                )
            except RuntimeError as exc:
                skipped += 1
                logger.error("영상을 불러오지 못했습니다 (%s): %s", filepath, exc)
                continue

            setattr(strip, properties.STRIP_ENABLED_PROPERTY, True)
            set_strip_status(strip, "대기 중")
            frame_start += 100
            imported += 1

        if skipped:
            message = f"{skipped}개의 파일을 불러오지 못했습니다."
            if imported == 0:
                ui.show_message_box(message, "불러오기 실패", "ERROR")
                self.report({"ERROR"}, message)
            else:
                ui.show_message_box(message, "알림", "INFO")
                self.report({"WARNING"}, message)
        else:
            self.report({"INFO"}, f"{imported}개의 영상을 불러왔습니다.")
        return {"FINISHED"}


class GEMINI_OT_RemoveItem(bpy.types.Operator):
    """선택한 영상을 작업 목록에서 제거합니다."""

    bl_idname = "gemini.remove_item"
    bl_label = "제거"
    bl_options = {"REGISTER", "UNDO"}

    target_name: bpy.props.StringProperty(
        name="대상", description="제거할 스트립의 이름"
    )

    def execute(self, context: bpy.types.Context) -> set:
        """Remove the named strip from the sequence editor.

        Args:
            context: Current Blender context.

        Returns:
            ``{'FINISHED'}`` when the strip was removed, ``{'CANCELLED'}``
            when it no longer exists.
        """
        collection = get_strip_collection(context.scene.sequence_editor)
        if collection is None:
            self.report({"WARNING"}, "시퀀서가 비어 있습니다.")
            return {"CANCELLED"}

        strip = collection.get(self.target_name)
        if strip is None:
            self.report({"WARNING"}, f"'{self.target_name}'을(를) 찾을 수 없습니다.")
            return {"CANCELLED"}

        collection.remove(strip)
        return {"FINISHED"}


class GEMINI_OT_ClearList(bpy.types.Operator):
    """목록에 있는 모든 영상을 한 번에 지웁니다."""

    bl_idname = "gemini.clear_list"
    bl_label = "초기화"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set:
        """Remove every strip from the sequence editor.

        Args:
            context: Current Blender context.

        Returns:
            ``{'FINISHED'}``.
        """
        collection = get_strip_collection(context.scene.sequence_editor)
        if collection is None:
            return {"FINISHED"}

        # The collection shrinks while removing, so iterate over a copy.
        for strip in list(collection):
            collection.remove(strip)
        return {"FINISHED"}


class GEMINI_OT_ImportReference(bpy.types.Operator):
    """체크된 영상을 뷰포트 배경(Reference)으로 즉시 불러옵니다."""

    bl_idname = "gemini.import_reference"
    bl_label = "레퍼런스로 불러오기"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set:
        """Add the first ticked movie strip as an image empty.

        Args:
            context: Current Blender context.

        Returns:
            ``{'FINISHED'}`` on success, ``{'CANCELLED'}`` otherwise.
        """
        strips = get_movie_strips(context.scene.sequence_editor, only_enabled=True)
        if not strips:
            self.report({"WARNING"}, "체크된 영상이 없습니다.")
            return {"CANCELLED"}

        target = strips[0]
        filepath = bpy.path.abspath(target.filepath)
        if not os.path.isfile(filepath):
            self.report({"ERROR"}, f"파일을 찾을 수 없습니다: {filepath}")
            return {"CANCELLED"}

        try:
            bpy.ops.object.empty_image_add(
                filepath=filepath, align="VIEW", location=(0.0, 0.0, 0.0)
            )
        except RuntimeError as exc:
            logger.error("레퍼런스 임포트 실패 (%s): %s", filepath, exc)
            self.report({"ERROR"}, f"레퍼런스를 불러오지 못했습니다: {exc}")
            return {"CANCELLED"}

        obj = context.active_object
        if obj is not None:
            obj.name = f"REF_{target.name}"
            obj.empty_display_type = "IMAGE"
            obj.empty_image_depth = "BACK"
            obj.empty_image_side = "FRONT"
            obj.show_in_front = False

        self.report({"INFO"}, f"레퍼런스 임포트 완료: {target.name}")
        return {"FINISHED"}


class GEMINI_OT_ExportSegments(bpy.types.Operator):
    """스트립 또는 마커 구간을 개별 파일로 일괄 분할 익스포트합니다."""

    bl_idname = "gemini.export_segments"
    bl_label = "구간 분할 익스포트"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set:
        """Export each timeline segment and report the outcome.

        Args:
            context: Current Blender context.

        Returns:
            ``{'FINISHED'}`` when at least one file was written, otherwise
            ``{'CANCELLED'}``.
        """
        return _run_batch_operator(self, context, run_segment_export, "분할 익스포트")


class GEMINI_OT_GenerateProxy(bpy.types.Operator):
    """편집용 720p 30fps H.264 경량 프록시를 생성합니다."""

    bl_idname = "gemini.generate_proxy"
    bl_label = "편집용 프록시 생성"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set:
        """Generate an editing proxy for every ticked strip.

        Args:
            context: Current Blender context.

        Returns:
            ``{'FINISHED'}`` when at least one proxy was written, otherwise
            ``{'CANCELLED'}``.
        """
        return _run_batch_operator(self, context, run_proxy_generation, "프록시 생성")


def _run_batch_operator(
    operator: bpy.types.Operator,
    context: bpy.types.Context,
    pipeline: Callable[
        [bpy.types.Context, properties.GeminiProperties],
        Tuple[int, List[Tuple[str, str]], str],
    ],
    job_name: str,
) -> set:
    """Run a batch pipeline with the shared guards, reporting and timing.

    Args:
        operator: The operator to report through.
        context: Current Blender context.
        pipeline: Callable returning ``(success_count, failures, directory)``.
        job_name: Human readable job name used in the messages.

    Returns:
        ``{'FINISHED'}`` when at least one file was written, otherwise
        ``{'CANCELLED'}``.
    """
    props = getattr(context.scene, properties.SCENE_PROPERTY, None)
    if props is None:
        operator.report({"ERROR"}, "애드온 속성을 찾을 수 없습니다. 재활성화해 주세요.")
        return {"CANCELLED"}

    if not props.output_dir:
        operator.report({"ERROR"}, "출력(저장) 폴더를 지정해주세요!")
        return {"CANCELLED"}

    start_time = time.time()
    try:
        success_count, failures, output_dir = pipeline(context, props)
    except Exception:  # noqa: BLE001 - last resort guard for the UI thread
        logger.exception("%s 중 예기치 못한 오류가 발생했습니다.", job_name)
        context.window_manager.clipboard = traceback.format_exc()
        operator.report(
            {"ERROR"}, f"{job_name} 실패: 에러 로그를 클립보드에 복사했습니다."
        )
        return {"CANCELLED"}

    duration = time.time() - start_time
    elapsed = f"({int(duration // 60)}분 {duration % 60:.2f}초 소요됨)"

    for name, error in failures:
        logger.error("%s 실패 (%s): %s", job_name, name, error)

    if success_count == 0:
        reason = failures[0][1] if failures else "생성된 파일이 없습니다."
        ui.show_message_box(f"{job_name} 실패:\n{reason}", "작업 실패", "ERROR")
        operator.report({"ERROR"}, f"{job_name} 실패: {reason}")
        return {"CANCELLED"}

    summary = f"{job_name} 완료\n성공: {success_count}건"
    if failures:
        summary += f"\n실패: {len(failures)}건"
    ui.show_message_box(f"{summary}\n\n{elapsed}", "작업 완료", "CHECKMARK")

    context.window_manager.clipboard = output_dir
    if failures:
        operator.report(
            {"WARNING"},
            f"{job_name}: {success_count}건 성공, {len(failures)}건 실패. "
            "콘솔 로그를 확인해 주세요.",
        )
    else:
        operator.report(
            {"INFO"}, f"{job_name} 완료! 경로가 복사되었습니다. {elapsed}"
        )
    return {"FINISHED"}


class GEMINI_OT_ExportVRChatThumbnail(bpy.types.Operator):
    """VRChat 월드 썸네일 규격(1920x1080)으로 이미지를 저장합니다."""

    bl_idname = "gemini.export_vrchat_thumbnail"
    bl_label = "VRChat 썸네일 저장"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set:
        """Export the thumbnail and report where it landed.

        Args:
            context: Current Blender context.

        Returns:
            ``{'FINISHED'}`` on success, ``{'CANCELLED'}`` otherwise.
        """
        props = getattr(context.scene, properties.SCENE_PROPERTY, None)
        if props is None:
            self.report({"ERROR"}, "애드온 속성을 찾을 수 없습니다. 재활성화해 주세요.")
            return {"CANCELLED"}

        if not props.output_dir:
            self.report({"ERROR"}, "출력(저장) 폴더를 지정해주세요!")
            return {"CANCELLED"}

        try:
            succeeded, message, output_path = export_vrchat_thumbnail(context, props)
        except Exception:  # noqa: BLE001 - last resort guard for the UI thread
            logger.exception("썸네일 저장 중 예기치 못한 오류가 발생했습니다.")
            context.window_manager.clipboard = traceback.format_exc()
            self.report({"ERROR"}, "썸네일 저장 실패: 로그를 클립보드에 복사했습니다.")
            return {"CANCELLED"}

        if not succeeded or output_path is None:
            self.report({"ERROR"}, f"썸네일 저장 실패: {message}")
            return {"CANCELLED"}

        context.window_manager.clipboard = output_path
        width, height = ffmpeg.THUMBNAIL_RESOLUTION
        self.report(
            {"INFO"},
            f"썸네일 저장 완료 ({width}x{height}): {os.path.basename(output_path)}",
        )
        return {"FINISHED"}


#: Classes registered by this module.
classes = (
    GEMINI_OT_Process,
    GEMINI_OT_ImportMulti,
    GEMINI_OT_RemoveItem,
    GEMINI_OT_ClearList,
    GEMINI_OT_ImportReference,
    GEMINI_OT_ExportSegments,
    GEMINI_OT_GenerateProxy,
    GEMINI_OT_ExportVRChatThumbnail,
)


def register() -> None:
    """Register the operator classes."""
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    """Unregister the operator classes in reverse order."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
