# SPDX-FileCopyrightText: 2026 Simulacre
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Property definitions for the Quick Video Encoder add-on.

Two kinds of properties live here:

* :class:`GeminiProperties`, the ``PropertyGroup`` attached to
  ``Scene.gemini_props`` that holds every user facing setting.
* The per-strip flags (``gemini_chk`` / ``gemini_status``) injected onto the
  sequencer strip RNA type by :func:`register_strip_properties`.
"""


# NOTE: ``from __future__ import annotations`` must NOT be added to this
# module. Blender builds its RNA properties from ``__annotations__``, and PEP
# 563 would turn every ``bpy.props.*`` annotation into a plain string, leaving
# the property group empty at registration time.

import logging
from typing import Any, Optional

import bpy

from .utils.ffmpeg import DEFAULT_SEGMENT_TEMPLATE, DISCORD_TARGET_MB

logger = logging.getLogger(__name__)

#: Attribute name of the property group on ``bpy.types.Scene``.
SCENE_PROPERTY = "gemini_props"

#: Per-strip "include in batch" flag.
STRIP_ENABLED_PROPERTY = "gemini_chk"

#: Per-strip status text rendered in the job list.
STRIP_STATUS_PROPERTY = "gemini_status"


def get_strip_rna_type() -> Optional[Any]:
    """Return the RNA base type shared by all video sequencer strips.

    Blender 4.5 renamed ``bpy.types.Sequence`` to ``bpy.types.Strip``; both
    names are probed so that the add-on keeps working on 4.2 LTS and on newer
    releases.

    Returns:
        The RNA type, or ``None`` when neither name exists.
    """
    for type_name in ("Strip", "Sequence"):
        rna_type = getattr(bpy.types, type_name, None)
        if rna_type is not None:
            return rna_type
    return None


def update_tool_mode(self: "GeminiProperties", context: bpy.types.Context) -> None:
    """Reset mode specific toggles when the user switches the work mode.

    Mirroring, audio removal and the overlays mean different things in the
    transcode and viewport pipelines, so they start from a clean state.

    Args:
        self: The property group owning the changed value.
        context: The Blender context supplied by the RNA update callback.
    """
    del context  # Required by the RNA callback signature, unused here.
    self.use_mirror = False
    self.remove_audio = False
    self.show_markers = False
    self.show_info = False


class GeminiProperties(bpy.types.PropertyGroup):
    """Every user facing setting of the add-on, stored per scene.

    The group is registered as ``bpy.types.Scene.gemini_props`` so that all
    values are saved inside the ``.blend`` file.
    """

    tool_mode: bpy.props.EnumProperty(
        name="작업 모드",
        items=[
            (
                "TRANSCODE",
                "영상 파일 변환",
                "외부 동영상 파일을 변환하거나 레퍼런스로 불러옵니다.",
            ),
            (
                "VIEWPORT",
                "뷰포트 녹화",
                "현재 작업 중인 3D 뷰포트 화면을 녹화합니다.",
            ),
        ],
        default="TRANSCODE",
        update=update_tool_mode,
        description="원하는 작업 모드를 선택하세요.",
    )

    output_format: bpy.props.EnumProperty(
        name="포맷",
        items=[
            ("MP4", "MP4", "가장 호환성이 좋은 표준 비디오 형식 (H.264/NVENC)"),
            ("MOV", "MOV", "Apple QuickTime 형식"),
            ("MKV", "MKV", "Matroska 컨테이너"),
            ("AVI", "AVI", "Windows 표준"),
            ("GIF", "GIF", "움직이는 이미지 (소리 없음)"),
            ("WEBP", "WebP", "구글이 개발한 고효율 웹 이미지"),
        ],
        default="MP4",
        description="최종 결과물의 파일 형식을 선택합니다.",
    )

    use_scene_settings: bpy.props.BoolProperty(
        name="씬(Scene) 카메라 설정 따르기",
        default=True,
        description=(
            "현재 블렌더 우측 패널(Output Properties)에 설정된 해상도와 FPS를 "
            "그대로 사용하여 왜곡 없이 렌더링합니다."
        ),
    )

    preset: bpy.props.EnumProperty(
        name="해상도",
        items=[
            ("FHD", "FHD (1920x1080)", ""),
            ("HD", "HD (1280x720)", ""),
            ("QHD", "QHD (2560x1440)", ""),
            ("SQUARE", "Square (1080x1080)", ""),
            ("IG_STORY", "Vertical (1080x1920)", ""),
        ],
        default="HD",
        description="영상의 해상도(크기)를 강제로 설정합니다.",
    )

    fps_selection: bpy.props.EnumProperty(
        name="FPS",
        items=[
            ("23.98", "23.98", ""),
            ("24", "24", ""),
            ("25", "25", ""),
            ("29.97", "29.97", ""),
            ("30", "30", ""),
            ("50", "50", ""),
            ("59.94", "59.94", ""),
            ("60", "60", ""),
        ],
        default="30",
        description="초당 프레임 수(Frame Rate)를 강제로 설정합니다.",
    )

    # --- Hardware -------------------------------------------------------
    use_nvenc: bpy.props.BoolProperty(
        name="NVENC 터보 가속",
        default=True,
        description=(
            "[GPU 가속] NVIDIA 그래픽카드를 사용하여 인코딩 속도를 최고로 높입니다."
        ),
    )

    use_thermal_guard: bpy.props.BoolProperty(
        name="발열 제어 (Thermal Guard)",
        default=True,
        description=(
            "[발열 관리] 인코딩 프로세스 우선순위를 낮춰 PC 과부하와 멈춤 현상을 "
            "방지합니다."
        ),
    )

    # --- Transcode ------------------------------------------------------
    quality_preset: bpy.props.EnumProperty(
        name="화질",
        items=[
            ("HIGH", "고화질 (High)", ""),
            ("MID", "일반 (Medium)", ""),
            ("LOW", "저용량 (Low)", ""),
        ],
        default="MID",
        description="영상의 화질 수준을 설정합니다.",
    )

    # --- Size matching (Discord 8MB Match) ------------------------------
    use_size_limit: bpy.props.BoolProperty(
        name="용량 제한 맞추기",
        default=False,
        description=(
            "[Discord 8MB] 목표 용량에 맞춰 비트레이트를 자동 계산합니다. "
            "화질 프리셋 대신 계산된 값이 사용됩니다."
        ),
    )

    target_size_mb: bpy.props.FloatProperty(
        name="목표 용량 (MB)",
        default=DISCORD_TARGET_MB,
        min=1.0,
        max=500.0,
        soft_max=100.0,
        precision=1,
        description=(
            "업로드 한도에 맞출 목표 파일 크기입니다. "
            "Discord 무료 계정은 8MB, Nitro Basic 은 50MB 입니다."
        ),
    )

    # --- Sign language multi-export -------------------------------------
    use_ksl_pack: bpy.props.BoolProperty(
        name="KSL Learning Pack",
        default=False,
        description=(
            "[수어 학습용] 한 번의 처리로 원본(_orig), 좌우 반전(_mirror), "
            "0.75배속(_slow) 세 개의 파일을 연속으로 출력합니다."
        ),
    )

    # --- Segment export & proxy ------------------------------------------
    segment_source: bpy.props.EnumProperty(
        name="분할 기준",
        items=[
            (
                "STRIPS",
                "스트립 (Strip)",
                "체크된 각 비디오 스트립의 편집 구간을 개별 파일로 내보냅니다.",
            ),
            (
                "MARKERS",
                "마커 (Marker)",
                "체크된 모든 스트립을 각 스트립 범위 안의 마커로 나누어 "
                "내보냅니다.",
            ),
        ],
        default="STRIPS",
        description="타임라인을 어떤 단위로 분할할지 선택합니다.",
    )

    segment_prefix: bpy.props.StringProperty(
        name="접두사",
        default="VRC",
        description="파일명 템플릿의 {prefix} 토큰에 들어갈 값입니다.",
    )

    segment_template: bpy.props.StringProperty(
        name="파일명 템플릿",
        default=DEFAULT_SEGMENT_TEMPLATE,
        description=(
            "확장자를 제외한 파일명 형식입니다. "
            "사용 가능 토큰: {prefix} {name} {strip} {index} {scene} {date}"
        ),
    )

    segment_stream_copy: bpy.props.BoolProperty(
        name="무재인코딩 분할 (빠름)",
        default=False,
        description=(
            "재인코딩 없이 스트림을 복사해 즉시 분할합니다. 매우 빠르지만 "
            "키프레임 단위로 잘려 시작 지점이 조금 앞당겨질 수 있고, "
            "오버레이·배속·크기 변경은 적용되지 않습니다."
        ),
    )

    # --- Archive metadata & badge ----------------------------------------
    use_metadata: bpy.props.BoolProperty(
        name="메타데이터 기록",
        default=False,
        description=(
            "출력 파일 내부에 title / artist / comment 태그를 기록합니다. "
            "MP4 · MOV · MKV 에서만 저장됩니다."
        ),
    )

    meta_title: bpy.props.StringProperty(
        name="제목 (title)",
        default="{name}",
        description="{name} {scene} {date} 토큰을 사용할 수 있습니다.",
    )

    meta_artist: bpy.props.StringProperty(
        name="제작자 (artist)",
        default="",
        description="{name} {scene} {date} 토큰을 사용할 수 있습니다.",
    )

    meta_comment: bpy.props.StringProperty(
        name="설명 (comment)",
        default="VRChat KSL Archive",
        description="{name} {scene} {date} 토큰을 사용할 수 있습니다.",
    )

    use_badge: bpy.props.BoolProperty(
        name="아카이빙 배지 표시",
        default=False,
        description="화면 모서리에 반투명 한 줄 배지를 오버레이합니다.",
    )

    badge_text: bpy.props.StringProperty(
        name="배지 문구",
        default="VRChat KSL Archive",
        description="배지에 표시할 한 줄 문구입니다.",
    )

    badge_position: bpy.props.EnumProperty(
        name="위치",
        items=[
            ("BR", "우측 하단", ""),
            ("BL", "좌측 하단", ""),
            ("TR", "우측 상단", ""),
            ("TL", "좌측 상단", ""),
        ],
        default="BR",
        description="배지를 붙일 모서리입니다.",
    )

    badge_font_size: bpy.props.IntProperty(
        name="크기", default=20, min=10, max=96, description="배지 글자 크기"
    )

    badge_opacity: bpy.props.FloatProperty(
        name="배경 투명도",
        default=0.45,
        min=0.0,
        max=1.0,
        description="배지 배경 상자의 불투명도입니다.",
    )

    # --- VRChat thumbnail ------------------------------------------------
    vrc_thumb_format: bpy.props.EnumProperty(
        name="썸네일 포맷",
        items=[
            ("WEBP", "WebP", "고효율 압축, VRChat 월드 썸네일 권장 포맷"),
            ("JPG", "JPG", "가장 호환성이 좋은 이미지 형식"),
        ],
        default="WEBP",
        description="VRChat 월드 썸네일로 저장할 이미지 형식입니다.",
    )

    vrc_thumb_quality: bpy.props.IntProperty(
        name="썸네일 화질",
        default=90,
        min=0,
        max=100,
        subtype="PERCENTAGE",
        description="썸네일 압축 품질입니다. 높을수록 용량이 커집니다.",
    )

    # --- Viewport -------------------------------------------------------
    vp_engine: bpy.props.EnumProperty(
        name="렌더 방식",
        items=[
            (
                "OPENGL",
                "현재 화면 캡처 (Viewport)",
                "보고 있는 화면 그대로 캡처합니다. (WYSIWYG)",
            ),
            (
                "WORKBENCH",
                "워크벤치 렌더 (Workbench)",
                "블렌더 엔진으로 깔끔하게 렌더링합니다. (추천)",
            ),
        ],
        default="OPENGL",
        description="렌더링 엔진을 선택합니다.",
    )

    use_anim_check: bpy.props.BoolProperty(
        name="실루엣 검수 모드 (Anim Check)",
        default=False,
        description=(
            "[검수용] 아바타를 실루엣으로, 배경을 회색으로 자동 설정하여 동작의 "
            "흐름을 확인합니다."
        ),
    )

    use_vp_preset: bpy.props.BoolProperty(
        name="강제 렌더 모드 (Force Rendered)",
        default=False,
        description=(
            "[프리뷰] 녹화 시 강제로 'Rendered' 모드로 전환하여 조명과 텍스처를 "
            "포함한 최종 결과물을 확인합니다."
        ),
    )

    vp_container: bpy.props.EnumProperty(
        name="컨테이너",
        items=[
            ("MPEG4", "MPEG-4 (.mp4)", ""),
            ("QUICKTIME", "QuickTime (.mov)", ""),
            ("MATROSKA", "Matroska (.mkv)", ""),
            ("AVI", "AVI (.avi)", ""),
        ],
        default="MPEG4",
        description="녹화 원본 컨테이너",
    )

    vp_codec: bpy.props.EnumProperty(
        name="코덱",
        items=[
            ("H264", "H.264", ""),
            ("HUFFYUV", "Huffyuv (무손실)", ""),
            ("QTRLE", "QT Anim (알파채널)", ""),
        ],
        default="H264",
        description="영상 압축 코덱",
    )

    vp_quality: bpy.props.EnumProperty(
        name="내부 화질",
        items=[
            ("HIGH", "고화질 (High)", ""),
            ("MEDIUM", "일반 (Medium)", ""),
            ("LOW", "저화질 (Low)", ""),
        ],
        default="MEDIUM",
        description="블렌더 내부 렌더링 화질",
    )

    vp_speed: bpy.props.EnumProperty(
        name="인코딩 속도",
        items=[
            ("SLOWEST", "느림 (고품질)", ""),
            ("GOOD", "보통 (Good)", ""),
            ("REALTIME", "빠름 (Realtime)", ""),
        ],
        default="GOOD",
        description="인코딩 처리 속도",
    )

    vp_keyframe: bpy.props.IntProperty(
        name="키프레임 간격",
        default=2,
        min=1,
        max=10,
        description="키프레임 간격 (탐색 속도에 영향)",
    )

    # --- A/B comparison --------------------------------------------------
    use_side_by_side: bpy.props.BoolProperty(
        name="A/B 비교 출력 (Side-by-Side)",
        default=False,
        description=(
            "[검수용] 같은 구간을 일반 화면과 실루엣 화면으로 두 번 녹화한 뒤 "
            "좌우로 나란히 합성합니다. 녹화 시간이 두 배로 늘어납니다."
        ),
    )

    sbs_show_labels: bpy.props.BoolProperty(
        name="구분 라벨 표시",
        default=True,
        description="합성 영상 상단에 ORIGINAL / SILHOUETTE 라벨을 표시합니다.",
    )

    # --- Overlays -------------------------------------------------------
    use_mirror: bpy.props.BoolProperty(
        name="좌우 반전 (Mirror)",
        default=False,
        description="영상을 거울처럼 좌우 반전합니다.",
    )

    remove_audio: bpy.props.BoolProperty(
        name="오디오 제거",
        default=False,
        description="영상에서 소리를 완전히 제거합니다.",
    )

    show_markers: bpy.props.BoolProperty(
        name="3x3 위치 마커 표시",
        default=False,
        description="화면 구도 확인용 3x3 위치 마커(RU, C, LD 등)를 오버레이합니다.",
    )

    show_info: bpy.props.BoolProperty(
        name="정보(HUD) 표시",
        default=False,
        description="FPS, 타임코드, 현재 프레임을 좌측 상단에 표시합니다.",
    )

    # --- Overlay customization -----------------------------------------
    marker_color: bpy.props.FloatVectorProperty(
        name="색상", subtype="COLOR", default=(1.0, 1.0, 1.0), min=0.0, max=1.0
    )
    marker_font_size: bpy.props.IntProperty(name="크기", default=24, min=10)
    info_color: bpy.props.FloatVectorProperty(
        name="텍스트", subtype="COLOR", default=(1.0, 0.9, 0.0), min=0.0, max=1.0
    )
    info_bg_color: bpy.props.FloatVectorProperty(
        name="배경", subtype="COLOR", default=(0.0, 0.0, 0.0), min=0.0, max=1.0
    )
    info_bg_opacity: bpy.props.FloatProperty(
        name="투명도", default=0.3, min=0.0, max=1.0
    )
    info_font_size: bpy.props.IntProperty(name="크기", default=18, min=10)

    output_dir: bpy.props.StringProperty(
        name="저장 경로",
        subtype="DIR_PATH",
        description="결과물이 저장될 폴더 경로입니다.",
    )


def register_strip_properties() -> bool:
    """Attach the add-on's per-strip properties to the strip RNA type.

    Returns:
        ``True`` when the properties were attached, ``False`` when the RNA
        type could not be resolved (the failure is logged, registration of the
        rest of the add-on continues).
    """
    strip_type = get_strip_rna_type()
    if strip_type is None:
        logger.error(
            "시퀀서 스트립 RNA 타입(bpy.types.Strip/Sequence)을 찾을 수 없어 "
            "작업 목록 속성을 등록하지 못했습니다."
        )
        return False

    strip_type.gemini_chk = bpy.props.BoolProperty(
        name="선택",
        description="[선택] 체크 해제 시 해당 영상은 작업에서 제외됩니다.",
        default=True,
    )
    strip_type.gemini_status = bpy.props.StringProperty(name="상태", default="")
    return True


def unregister_strip_properties() -> None:
    """Remove the per-strip properties added by :func:`register_strip_properties`."""
    strip_type = get_strip_rna_type()
    if strip_type is None:
        return

    for attribute in (STRIP_ENABLED_PROPERTY, STRIP_STATUS_PROPERTY):
        try:
            delattr(strip_type, attribute)
        except AttributeError:
            logger.debug("스트립 속성 '%s'이(가) 이미 제거되어 있습니다.", attribute)


def register() -> None:
    """Register the property group and the scene/strip properties."""
    bpy.utils.register_class(GeminiProperties)
    bpy.types.Scene.gemini_props = bpy.props.PointerProperty(type=GeminiProperties)
    register_strip_properties()


def unregister() -> None:
    """Unregister everything :func:`register` added, in reverse order."""
    unregister_strip_properties()
    try:
        del bpy.types.Scene.gemini_props
    except AttributeError:
        logger.debug("Scene.gemini_props가 이미 제거되어 있습니다.")
    bpy.utils.unregister_class(GeminiProperties)
