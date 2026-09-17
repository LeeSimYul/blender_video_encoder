# SPDX-FileCopyrightText: 2026 Simulacre
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar panel for the Quick Video Encoder add-on.

The panel lives in ``View3D > Sidebar (N) > Video Tool``. Drawing is split
into small helpers so that each block of the UI stays readable, and the panel
never raises: a missing property group only makes it draw a hint.
"""

from __future__ import annotations

import logging
from typing import Optional

import bpy

from . import properties
from .utils import ffmpeg

logger = logging.getLogger(__name__)


def show_message_box(
    message: str = "", title: str = "Info", icon: str = "INFO"
) -> None:
    """Show a modal popup with one label per line of ``message``.

    Args:
        message: Text to display; ``\\n`` starts a new label row.
        title: Popup title.
        icon: Blender icon identifier shown next to the title.
    """

    def draw(self: bpy.types.UIPopupMenu, context: bpy.types.Context) -> None:
        """Render one label per line of the captured message.

        Args:
            self: The popup menu Blender passes to the callback.
            context: The Blender context supplied by the popup, unused here.
        """
        del context  # Required by the draw callback signature.
        for line in message.split("\n"):
            self.layout.label(text=line)

    bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)


def _draw_overlay_settings(
    layout: bpy.types.UILayout, props: properties.GeminiProperties
) -> None:
    """Draw the marker/HUD overlay toggles and their styling options.

    Args:
        layout: Layout to draw into.
        props: The scene's property group.
    """
    column = layout.column(align=True)
    column.prop(props, "show_markers", toggle=True, icon="GRID")
    if props.show_markers:
        marker_column = column.column(align=True)
        marker_column.prop(props, "marker_color")
        marker_column.prop(props, "marker_font_size")

    column.prop(props, "show_info", toggle=True, icon="INFO")
    if props.show_info:
        info_column = column.column(align=True)
        color_row = info_column.row(align=True)
        color_row.prop(props, "info_color")
        color_row.prop(props, "info_bg_color")
        info_column.prop(props, "info_bg_opacity")
        info_column.prop(props, "info_font_size")


def _draw_size_limit(
    layout: bpy.types.UILayout, props: properties.GeminiProperties
) -> None:
    """Draw the "Discord 8MB Match" size budget controls.

    Args:
        layout: Layout to draw into.
        props: The scene's property group.
    """
    column = layout.column(align=True)
    column.prop(props, "use_size_limit", toggle=True, icon="FILE_TICK")
    if not props.use_size_limit:
        return

    column.prop(props, "target_size_mb")
    if props.output_format in ffmpeg.VIDEO_FORMATS:
        column.label(text="* 길이에 맞춰 비트레이트를 자동 계산합니다.", icon="INFO")
    else:
        column.label(text="* GIF/WebP 에는 적용되지 않습니다.", icon="ERROR")


def _draw_vrchat_thumbnail(
    layout: bpy.types.UILayout, props: properties.GeminiProperties
) -> None:
    """Draw the VRChat world thumbnail export block.

    The caller provides the container, so this works both inside a box and
    directly inside a sub-panel.

    Args:
        layout: Layout to draw into.
        props: The scene's property group.
    """
    width, height = ffmpeg.THUMBNAIL_RESOLUTION
    layout.label(text=f"월드 썸네일 규격: {width}x{height}", icon="IMAGE_DATA")
    row = layout.row(align=True)
    row.prop(props, "vrc_thumb_format", expand=True)
    layout.prop(props, "vrc_thumb_quality")
    layout.operator("gemini.export_vrchat_thumbnail", icon="RENDER_STILL")


def _draw_job_list(
    layout: bpy.types.UILayout, context: bpy.types.Context
) -> int:
    """Draw the list of movie strips queued for transcoding.

    Args:
        layout: Layout to draw into.
        context: Current Blender context.

    Returns:
        The number of movie strips found in the sequence editor.
    """
    from . import operators  # Local import avoids an import cycle at load time.

    box = layout.box()
    strips = operators.get_movie_strips(context.scene.sequence_editor)
    box.label(text=f"작업 목록 ({len(strips)} 개)", icon="FILE_MOVIE")

    if not strips:
        box.label(text="대기 중인 영상이 없습니다.", icon="INFO")
    else:
        column = box.column()
        for strip in strips:
            row = column.row(align=True)
            row.prop(strip, properties.STRIP_ENABLED_PROPERTY, text=strip.name)
            status_row = row.row()
            status_row.alignment = "RIGHT"
            status = getattr(strip, properties.STRIP_STATUS_PROPERTY, "")
            if status:
                status_row.label(text=status)
            remove_op = status_row.operator("gemini.remove_item", text="", icon="X")
            remove_op.target_name = strip.name

    button_row = box.row()
    button_row.operator("gemini.import_multi", icon="IMPORT")
    button_row.operator("gemini.clear_list", icon="TRASH")
    if strips:
        box.operator("gemini.import_reference", icon="IMAGE_DATA")
    return len(strips)


def _draw_transcode_mode(
    layout: bpy.types.UILayout,
    context: bpy.types.Context,
    props: properties.GeminiProperties,
) -> None:
    """Draw the "video file conversion" section.

    Args:
        layout: Layout to draw into.
        context: Current Blender context.
        props: The scene's property group.
    """
    _draw_job_list(layout, context)

    layout.separator()
    column = layout.column(align=True)
    column.prop(props, "output_format")
    column.prop(props, "preset")
    column.prop(props, "fps_selection")
    column.prop(props, "quality_preset")

    column.separator()
    column.label(text="용량 프리셋:")
    _draw_size_limit(column, props)

    column.separator()
    column.label(text="모디파이어 & 오버레이:")
    column.prop(props, "use_mirror", icon="MOD_MIRROR")
    column.prop(props, "remove_audio", icon="MUTE_IPO_ON")
    _draw_overlay_settings(column, props)


def _draw_viewport_mode(
    layout: bpy.types.UILayout,
    context: bpy.types.Context,
    props: properties.GeminiProperties,
) -> None:
    """Draw the "viewport recording" section.

    Args:
        layout: Layout to draw into.
        context: Current Blender context.
        props: The scene's property group.
    """
    box = layout.box()
    box.label(text="뷰포트 녹화 설정", icon="VIEW3D")

    # 1. Recording source.
    source_box = box.box()
    source_box.label(text="1. 녹화 (소스 설정)", icon="REC")
    source_box.prop(props, "vp_engine", text="")
    if props.vp_engine == "OPENGL":
        row = source_box.row(align=True)
        row.prop(props, "use_vp_preset", icon="SHADING_RENDERED")
        row.prop(props, "use_anim_check", icon="ARMATURE_DATA")

        sbs_column = source_box.column(align=True)
        sbs_column.prop(props, "use_side_by_side", toggle=True, icon="ARROW_LEFTRIGHT")
        if props.use_side_by_side:
            sbs_column.prop(props, "sbs_show_labels")
            if props.output_format in ffmpeg.VIDEO_FORMATS:
                sbs_column.label(text="* 녹화를 2회 수행합니다.", icon="INFO")
            else:
                sbs_column.label(
                    text="* MP4/MOV/MKV/AVI 에서만 동작합니다.", icon="ERROR"
                )
    source_column = source_box.column()
    source_column.separator(factor=0.5)
    source_column.prop(props, "vp_container")
    source_column.prop(props, "vp_codec")
    source_column.prop(props, "vp_quality")
    source_column.prop(props, "vp_speed")
    source_column.prop(props, "vp_keyframe")

    # 2. Output conversion.
    output_box = box.box()
    output_box.label(text="2. 변환 (출력 설정)", icon="RENDER_ANIMATION")
    output_column = output_box.column()
    output_column.prop(props, "output_format")
    if props.output_format in ffmpeg.AUDIO_LESS_FORMATS:
        output_column.label(text="* 녹화 후 자동 변환됩니다.", icon="FILE_REFRESH")

    output_column.separator(factor=0.5)
    output_column.prop(props, "use_scene_settings", icon="SCENE_DATA")
    sub_column = output_column.column(align=True)
    if props.use_scene_settings:
        render = context.scene.render
        scale = render.resolution_percentage / 100.0
        width = int(render.resolution_x * scale)
        height = int(render.resolution_y * scale)
        fps = ffmpeg.format_fps(render.fps / render.fps_base)
        sub_column.label(
            text=f"현재 씬 설정: {width}x{height} @ {fps}fps", icon="INFO"
        )
    else:
        sub_column.prop(props, "preset")
        sub_column.prop(props, "fps_selection")

    output_column.separator(factor=0.5)
    _draw_size_limit(output_column, props)

    # 3. Overlays.
    overlay_box = box.box()
    overlay_box.label(text="3. 오버레이 (후처리)", icon="IMAGE_ALPHA")
    _draw_overlay_settings(overlay_box, props)

    # 4. Hardware.
    hardware_box = box.box()
    hardware_box.label(text="4. 하드웨어 최적화", icon="PREFERENCES")
    hardware_column = hardware_box.column(align=True)
    hardware_column.prop(props, "use_nvenc", icon="TRIA_RIGHT")
    hardware_column.prop(props, "use_thermal_guard", icon="TEMP")


def _draw_output_and_run(
    layout: bpy.types.UILayout, props: properties.GeminiProperties
) -> None:
    """Draw the output directory, the FFmpeg status line and the run button.

    Args:
        layout: Layout to draw into.
        props: The scene's property group.
    """
    layout.separator()
    box = layout.box()
    column = box.column(align=True)
    column.label(text="저장 경로:", icon="FILE_FOLDER")
    column.prop(props, "output_dir", text="")

    if ffmpeg.is_ffmpeg_available():
        box.label(text="FFmpeg 상태: 정상 작동 중", icon="CHECKMARK")
    else:
        box.label(
            text="FFmpeg 상태: 엔진 없음 (시스템 환경변수 확인)", icon="ERROR"
        )

    is_transcode = props.tool_mode == "TRANSCODE"
    row = layout.row()
    row.scale_y = 1.5
    row.operator(
        "gemini.process",
        text="변환 시작" if is_transcode else "녹화 시작",
        icon="RENDER_ANIMATION" if is_transcode else "REC",
    )


class VIEW3D_PT_QuickVideoEncoder(bpy.types.Panel):
    """Main sidebar panel exposing every add-on setting."""

    bl_label = "Quick Video Encoder"
    bl_idname = "VIEW3D_PT_quick_video_encoder"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Video Tool"

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the panel contents.

        Args:
            context: Current Blender context.
        """
        layout = self.layout
        props: Optional[properties.GeminiProperties] = getattr(
            context.scene, properties.SCENE_PROPERTY, None
        )
        if props is None:
            layout.label(text="애드온 속성을 불러오지 못했습니다.", icon="ERROR")
            layout.label(text="애드온을 다시 활성화해 주세요.")
            logger.warning(
                "Scene.%s가 없어 패널을 그릴 수 없습니다.", properties.SCENE_PROPERTY
            )
            return

        row = layout.row()
        row.prop(props, "tool_mode", expand=True)
        layout.separator()

        if props.tool_mode == "TRANSCODE":
            _draw_transcode_mode(layout, context, props)
        elif props.tool_mode == "VIEWPORT":
            _draw_viewport_mode(layout, context, props)

        _draw_output_and_run(layout, props)


class _ChildPanel:
    """Mixin giving every sub-panel the same placement and default state.

    Sub-panels render as collapsible boxes under the main panel and start
    closed, so the advanced archiving tools never crowd the everyday controls.
    """

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Video Tool"
    bl_parent_id = "VIEW3D_PT_quick_video_encoder"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Hide the sub-panel when the add-on properties are unavailable.

        Args:
            context: Current Blender context.

        Returns:
            ``True`` when the property group exists on the scene.
        """
        return getattr(context.scene, properties.SCENE_PROPERTY, None) is not None


class VIEW3D_PT_QVESegmentExport(_ChildPanel, bpy.types.Panel):
    """Collapsible box holding the segment exporter and the proxy builder."""

    bl_label = "구간 분할 & 프록시"
    bl_idname = "VIEW3D_PT_qve_segment_export"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Show this box only in the file conversion mode.

        Args:
            context: Current Blender context.

        Returns:
            ``True`` in ``TRANSCODE`` mode.
        """
        props = getattr(context.scene, properties.SCENE_PROPERTY, None)
        return props is not None and props.tool_mode == "TRANSCODE"

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the segment and proxy controls.

        Args:
            context: Current Blender context.
        """
        layout = self.layout
        props = context.scene.gemini_props

        column = layout.column(align=True)
        column.prop(props, "segment_source", text="")
        column.prop(props, "segment_prefix")
        column.prop(props, "segment_template")
        column.label(
            text="토큰: {prefix} {name} {strip} {index} {scene} {date}",
            icon="SYNTAX_OFF",
        )

        if props.segment_source == "MARKERS":
            marker_count = len(context.scene.timeline_markers)
            layout.label(text=f"타임라인 마커: {marker_count}개", icon="MARKER_HLT")
            layout.label(
                text="* 체크된 모든 스트립을 각자의 마커로 자릅니다.", icon="INFO"
            )

        layout.prop(props, "segment_stream_copy", icon="SEQ_SPLITVIEW")
        if props.segment_stream_copy:
            layout.label(text="* 오버레이/배속/크기 변경은 무시됩니다.", icon="ERROR")

        layout.operator("gemini.export_segments", icon="SEQ_STRIP_DUPLICATE")

        layout.separator()
        proxy_box = layout.box()
        width, height = ffmpeg.PROXY_RESOLUTION
        proxy_box.label(
            text=f"편집용 프록시 ({width}x{height} @ {ffmpeg.PROXY_FPS}fps)",
            icon="RENDERLAYERS",
        )
        proxy_box.label(text=f"접미사: {ffmpeg.PROXY_SUFFIX}.mp4", icon="FILE_MOVIE")
        proxy_box.operator("gemini.generate_proxy", icon="FILE_REFRESH")


class VIEW3D_PT_QVESignLanguage(_ChildPanel, bpy.types.Panel):
    """Collapsible box holding the sign-language multi-export preset."""

    bl_label = "수어 다중 익스포트 (KSL)"
    bl_idname = "VIEW3D_PT_qve_sign_language"

    def draw_header(self, context: bpy.types.Context) -> None:
        """Put the preset toggle in the box header.

        Args:
            context: Current Blender context.
        """
        self.layout.prop(context.scene.gemini_props, "use_ksl_pack", text="")

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the variant list produced by the preset.

        Args:
            context: Current Blender context.
        """
        layout = self.layout
        props = context.scene.gemini_props

        column = layout.column(align=True)
        column.active = props.use_ksl_pack
        column.label(text="한 번의 처리로 다음 파일을 생성합니다:", icon="DUPLICATE")
        for variant in ffmpeg.KSL_LEARNING_PACK:
            column.label(text=f"{variant.suffix}  —  {variant.label}", icon="DOT")

        if props.use_ksl_pack:
            layout.label(text="* 영상 파일 변환 모드에서 동작합니다.", icon="INFO")


class VIEW3D_PT_QVEArchive(_ChildPanel, bpy.types.Panel):
    """Collapsible box holding the archive metadata and the badge overlay."""

    bl_label = "아카이브 메타데이터 & 배지"
    bl_idname = "VIEW3D_PT_qve_archive"

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the metadata fields and the badge options.

        Args:
            context: Current Blender context.
        """
        layout = self.layout
        props = context.scene.gemini_props

        meta_box = layout.box()
        meta_box.prop(props, "use_metadata", toggle=True, icon="TEXT")
        if props.use_metadata:
            column = meta_box.column(align=True)
            column.prop(props, "meta_title")
            column.prop(props, "meta_artist")
            column.prop(props, "meta_comment")
            column.label(text="토큰: {name} {scene} {date}", icon="SYNTAX_OFF")
            if props.output_format not in ffmpeg.METADATA_FORMATS:
                meta_box.label(
                    text="* MP4/MOV/MKV 에서만 저장됩니다.", icon="ERROR"
                )

        badge_box = layout.box()
        badge_box.prop(props, "use_badge", toggle=True, icon="FONT_DATA")
        if props.use_badge:
            column = badge_box.column(align=True)
            column.prop(props, "badge_text", text="")
            column.prop(props, "badge_position")
            row = column.row(align=True)
            row.prop(props, "badge_font_size")
            row.prop(props, "badge_opacity")


class VIEW3D_PT_QVEThumbnail(_ChildPanel, bpy.types.Panel):
    """Collapsible box holding the VRChat world thumbnail export."""

    bl_label = "VRChat 썸네일"
    bl_idname = "VIEW3D_PT_qve_thumbnail"

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the thumbnail format, quality and export button.

        Args:
            context: Current Blender context.
        """
        _draw_vrchat_thumbnail(self.layout, context.scene.gemini_props)


#: Classes registered by this module. The parent panel must come first.
classes = (
    VIEW3D_PT_QuickVideoEncoder,
    VIEW3D_PT_QVESegmentExport,
    VIEW3D_PT_QVESignLanguage,
    VIEW3D_PT_QVEArchive,
    VIEW3D_PT_QVEThumbnail,
)


def register() -> None:
    """Register the panel classes."""
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    """Unregister the panel classes in reverse order."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
