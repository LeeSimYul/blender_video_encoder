bl_info = {
    "name": "Quick Video Encoder v3.5.2",
    "author": "Gemini & User",
    "version": (3, 5, 2),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar(N) > Video Tool",
    "description": "완전 한글화 적용, 씬(Scene) 카메라 설정 동기화 기능 추가",
    "category": "Render",
}

import bpy
import os
import subprocess
import platform
import time
import traceback
from datetime import datetime

# ------------------------------------------------------------------------
# 1. ⚙️ 데이터 및 속성
# ------------------------------------------------------------------------

def register_strip_props():
    try:
        bpy.types.Sequence.gemini_chk = bpy.props.BoolProperty(
            name="선택", description="[선택] 체크 해제 시 해당 영상은 작업에서 제외됩니다.", default=True
        )
        bpy.types.Sequence.gemini_status = bpy.props.StringProperty(
            name="상태", default=""
        )
    except: pass

def update_tool_mode(self, context):
    self.use_mirror = False
    self.remove_audio = False
    self.show_markers = False
    self.show_info = False

class GeminiProperties(bpy.types.PropertyGroup):
    tool_mode: bpy.props.EnumProperty(
        name="작업 모드",
        items=[
            ('TRANSCODE', "영상 파일 변환", "외부 동영상 파일을 변환하거나 레퍼런스로 불러옵니다."),
            ('VIEWPORT', "뷰포트 녹화", "현재 작업 중인 3D 뷰포트 화면을 녹화합니다.")
        ],
        default='TRANSCODE', update=update_tool_mode,
        description="원하는 작업 모드를 선택하세요."
    )

    output_format: bpy.props.EnumProperty(
        name="포맷",
        items=[
            ('MP4', "MP4", "가장 호환성이 좋은 표준 비디오 형식 (H.264/NVENC)"),
            ('MOV', "MOV", "Apple QuickTime 형식"),
            ('MKV', "MKV", "Matroska 컨테이너"),
            ('AVI', "AVI", "Windows 표준"),
            ('GIF', "GIF", "움직이는 이미지 (소리 없음)"),
            ('WEBP', "WebP", "구글이 개발한 고효율 웹 이미지"),
        ], default='MP4', description="최종 결과물의 파일 형식을 선택합니다."
    )
    
    # [새로운 기능] 씬 설정 동기화
    use_scene_settings: bpy.props.BoolProperty(
        name="씬(Scene) 카메라 설정 따르기",
        default=True,
        description="현재 블렌더 우측 패널(Output Properties)에 설정된 해상도와 FPS를 그대로 사용하여 왜곡 없이 렌더링합니다."
    )
    
    preset: bpy.props.EnumProperty(
        name="해상도",
        items=[
            ('FHD', "FHD (1920x1080)", ""), ('HD', "HD (1280x720)", ""),
            ('QHD', "QHD (2560x1440)", ""), ('SQUARE', "Square (1080x1080)", ""), 
            ('IG_STORY', "Vertical (1080x1920)", "")
        ], default='HD', description="영상의 해상도(크기)를 강제로 설정합니다."
    )
    
    fps_selection: bpy.props.EnumProperty(
        name="FPS",
        items=[
            ('23.98', "23.98", ""), ('24', "24", ""), ('25', "25", ""),
            ('29.97', "29.97", ""), ('30', "30", ""), ('50', "50", ""),
            ('59.94', "59.94", ""), ('60', "60", ""),
        ], default='30', description="초당 프레임 수(Frame Rate)를 강제로 설정합니다."
    )
    
    # Hardware
    use_nvenc: bpy.props.BoolProperty(
        name="NVENC 터보 가속", default=True, 
        description="[GPU 가속] NVIDIA 그래픽카드를 사용하여 인코딩 속도를 최고로 높입니다."
    )
    use_thermal_guard: bpy.props.BoolProperty(
        name="발열 제어 (Thermal Guard)", default=True,
        description="[발열 관리] 인코딩 프로세스 우선순위를 낮춰 PC 과부하와 멈춤 현상을 방지합니다."
    )
    
    # Transcode
    quality_preset: bpy.props.EnumProperty(
        name="화질",
        items=[('HIGH', "고화질 (High)", ""), ('MID', "일반 (Medium)", ""), ('LOW', "저용량 (Low)", "")],
        default='MID', description="영상의 화질 수준을 설정합니다."
    )

    # Viewport
    vp_engine: bpy.props.EnumProperty(
        name="렌더 방식",
        items=[
            ('OPENGL', "현재 화면 캡처 (Viewport)", "보고 있는 화면 그대로 캡처합니다. (WYSIWYG)"),
            ('WORKBENCH', "워크벤치 렌더 (Workbench)", "블렌더 엔진으로 깔끔하게 렌더링합니다. (추천)")
        ],
        default='OPENGL', description="렌더링 엔진을 선택합니다."
    )

    use_anim_check: bpy.props.BoolProperty(
        name="실루엣 검수 모드 (Anim Check)", default=False,
        description="[검수용] 아바타를 실루엣으로, 배경을 회색으로 자동 설정하여 동작의 흐름을 확인합니다."
    )

    use_vp_preset: bpy.props.BoolProperty(
        name="강제 렌더 모드 (Force Rendered)", default=False, 
        description="[프리뷰] 녹화 시 강제로 'Rendered' 모드로 전환하여 조명과 텍스처를 포함한 최종 결과물을 확인합니다."
    )

    vp_container: bpy.props.EnumProperty(name="컨테이너", items=[('MPEG4', "MPEG-4 (.mp4)", ""), ('QUICKTIME', "QuickTime (.mov)", ""), ('MATROSKA', "Matroska (.mkv)", ""), ('AVI', "AVI (.avi)", "")], default='MPEG4', description="녹화 원본 컨테이너")
    vp_codec: bpy.props.EnumProperty(name="코덱", items=[('H264', "H.264", ""), ('HUFFYUV', "Huffyuv (무손실)", ""), ('QTRLE', "QT Anim (알파채널)", "")], default='H264', description="영상 압축 코덱")
    vp_quality: bpy.props.EnumProperty(name="내부 화질", items=[('HIGH', "고화질 (High)", ""), ('MEDIUM', "일반 (Medium)", ""), ('LOW', "저화질 (Low)", "")], default='MEDIUM', description="블렌더 내부 렌더링 화질")
    vp_speed: bpy.props.EnumProperty(name="인코딩 속도", items=[('SLOWEST', "느림 (고품질)", ""), ('GOOD', "보통 (Good)", ""), ('REALTIME', "빠름 (Realtime)", "")], default='GOOD', description="인코딩 처리 속도")
    vp_keyframe: bpy.props.IntProperty(name="키프레임 간격", default=2, min=1, max=10, description="키프레임 간격 (탐색 속도에 영향)")

    # Overlays
    use_mirror: bpy.props.BoolProperty(name="좌우 반전 (Mirror)", default=False, description="영상을 거울처럼 좌우 반전합니다.")
    remove_audio: bpy.props.BoolProperty(name="오디오 제거", default=False, description="영상에서 소리를 완전히 제거합니다.")
    show_markers: bpy.props.BoolProperty(name="3x3 위치 마커 표시", default=False, description="화면 구도 확인용 3x3 위치 마커(RU, C, LD 등)를 오버레이합니다.")
    show_info: bpy.props.BoolProperty(name="정보(HUD) 표시", default=False, description="FPS, 타임코드, 현재 프레임을 좌측 상단에 표시합니다.")
    
    # Customization
    marker_color: bpy.props.FloatVectorProperty(name="색상", subtype='COLOR', default=(1.0, 1.0, 1.0))
    marker_font_size: bpy.props.IntProperty(name="크기", default=24, min=10)
    info_color: bpy.props.FloatVectorProperty(name="텍스트", subtype='COLOR', default=(1.0, 0.9, 0.0))
    info_bg_color: bpy.props.FloatVectorProperty(name="배경", subtype='COLOR', default=(0.0, 0.0, 0.0))
    info_bg_opacity: bpy.props.FloatProperty(name="투명도", default=0.3, min=0.0, max=1.0)
    info_font_size: bpy.props.IntProperty(name="크기", default=18, min=10)
    
    output_dir: bpy.props.StringProperty(name="저장 경로", subtype='DIR_PATH', description="결과물이 저장될 폴더 경로입니다.")

# ------------------------------------------------------------------------
# 2. 🛠️ 유틸리티 & FFmpeg
# ------------------------------------------------------------------------

def get_ffmpeg_binary():
    try:
        bin_dir = os.path.dirname(bpy.app.binary_path)
        if platform.system() == 'Windows':
            for p in [os.path.join(bin_dir, "ffmpeg.exe"), os.path.join(bin_dir, "bin", "ffmpeg.exe")]:
                if os.path.exists(p): return p
    except: pass
    return "ffmpeg"

def get_safe_font_path():
    system = platform.system()
    raw_path = None
    if system == 'Windows':
        for p in ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/seguiemj.ttf"]:
            if os.path.exists(p): raw_path = p; break
    elif system == 'Darwin': raw_path = "/System/Library/Fonts/Helvetica.ttc"
    if not raw_path:
        try:
            ver = f"{bpy.app.version[0]}.{bpy.app.version[1]}"
            base = os.path.dirname(bpy.app.binary_path)
            for c in [os.path.join(base, ver, "datafiles", "fonts", "GeistMono-Regular.ttf"),
                      os.path.join(base, ver, "datafiles", "fonts", "droidsans.ttf")]:
                if os.path.exists(c): raw_path = c; break
        except: pass
    if raw_path: return raw_path.replace("\\", "/").replace(":", "\\:")
    return None

def color_to_hex(prop):
    return f"0x{int(prop[0]*255):02x}{int(prop[1]*255):02x}{int(prop[2]*255):02x}"

def get_res_tuple(preset_enum):
    if preset_enum == 'FHD': return 1920, 1080
    elif preset_enum == 'QHD': return 2560, 1440
    elif preset_enum == 'SQUARE': return 1080, 1080
    elif preset_enum == 'IG_STORY': return 1080, 1920
    return 1280, 720

def show_message_box(message="", title="Info", icon='INFO'):
    def draw(self, context):
        for line in message.split('\n'): self.layout.label(text=line)
    bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)

def build_filter_complex(props, target_w, target_h, target_fps):
    filters = []
    if props.use_mirror: filters.append("hflip")
    filters.append(f"fps=fps={target_fps}")
    w_expr = f"trunc(iw*max({target_w}/iw\,{target_h}/ih)/2)*2"
    h_expr = f"trunc(ih*max({target_w}/iw\,{target_h}/ih)/2)*2"
    filters.append(f"scale={w_expr}:{h_expr}:flags=lanczos")
    filters.append(f"crop={target_w}:{target_h}")
    font_path = get_safe_font_path()
    if font_path:
        if props.show_markers:
            fs = props.marker_font_size; fcolor = color_to_hex(props.marker_color)
            labels = [("RU", "w*0.15", "h*0.15"), ("U", "w*0.5", "h*0.15"), ("LU", "w*0.85", "h*0.15"),
                      ("R", "w*0.15", "h*0.5"), ("C", "w*0.5", "h*0.5"), ("L", "w*0.85", "h*0.5"),
                      ("RD", "w*0.15", "h*0.85"), ("D", "w*0.5", "h*0.85"), ("LD", "w*0.85", "h*0.85")]
            for txt, x, y in labels:
                filters.append(f"drawtext=fontfile='{font_path}':text='{txt}':x={x}-text_w/2:y={y}-text_h/2:fontsize={fs}:fontcolor={fcolor}:shadowcolor=black@0.8:shadowx=2:shadowy=2")
        if props.show_info:
            fs_info = props.info_font_size; if_color = color_to_hex(props.info_color)
            bg_color = color_to_hex(props.info_bg_color); bg_alpha = props.info_bg_opacity
            time_expr = "%{eif\:t/60\:d\:2}\:%{eif\:mod(t,60)\:d\:2}.%{eif\:(t*10-10*floor(t))\:d}"
            info_txt = f"FPS\: {target_fps} | TCR\: {time_expr} | FRM\: %{{frame_num}}"
            filters.append(f"drawtext=fontfile='{font_path}':text='{info_txt}':x=20:y=20:fontsize={fs_info}:fontcolor={if_color}:box=1:boxcolor={bg_color}@{bg_alpha}:boxborderw=5")
    return ",".join(filters)

def run_ffmpeg_process(context, props, input_path, output_path, is_viewport_post=False):
    ffmpeg_bin = get_ffmpeg_binary()
    
    # [수정] 씬 설정 동기화 옵션 반영 (is_viewport_post인 경우에만 씬 정보 참조)
    if is_viewport_post and props.use_scene_settings:
        tw = int(context.scene.render.resolution_x * (context.scene.render.resolution_percentage / 100.0))
        th = int(context.scene.render.resolution_y * (context.scene.render.resolution_percentage / 100.0))
        tfps = round(context.scene.render.fps / context.scene.render.fps_base, 2)
        tfps_str = str(tfps)
    else:
        tw, th = get_res_tuple(props.preset)
        tfps_str = props.fps_selection

    vf = build_filter_complex(props, tw, th, tfps_str)
    
    cmd = [ffmpeg_bin, "-y", "-i", input_path]
    cmd.extend(["-threads", "0"]) 
    if props.remove_audio: cmd.append("-an")
    else: cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    
    fmt = props.output_format
    if fmt in ['MP4', 'MOV', 'MKV', 'AVI']:
        v_codec = "libx264"
        if props.use_nvenc: v_codec = "h264_nvenc"
        if props.tool_mode == 'VIEWPORT' and not props.use_nvenc:
            if props.vp_codec == 'HUFFYUV': v_codec = "huffyuv"
            elif props.vp_codec == 'QTRLE': v_codec = "qtrle"
        cmd.extend(["-vf", vf, "-c:v", v_codec])
        if "h264" in v_codec or "nvenc" in v_codec:
            br = "6M" if props.quality_preset=='HIGH' else "3M"
            cmd.extend(["-b:v", br, "-pix_fmt", "yuv420p"])
            if "nvenc" in v_codec: cmd.extend(["-preset", "p4", "-tune", "hq"])
            else: cmd.extend(["-preset", "veryfast"])
    elif fmt == 'GIF':
        cmd.extend(["-filter_complex", f"{vf},split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"])
    elif fmt == 'WEBP':
        cmd.extend(["-vf", vf, "-c:v", "libwebp", "-quality", "75", "-loop", "0"])
    cmd.append(output_path)
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startupinfo.wShowWindow = subprocess.SW_HIDE
        creation_flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS if (platform.system()=='Windows' and props.use_thermal_guard) else 0
        res = subprocess.run(cmd, startupinfo=startupinfo, creationflags=creation_flags, stderr=subprocess.PIPE)
        return res.returncode == 0, res.stderr.decode('utf-8', errors='ignore')
    except Exception as e: return False, str(e)

# ------------------------------------------------------------------------
# 4. 🕹️ Import & Process Operators
# ------------------------------------------------------------------------

class GEMINI_OT_ImportReference(bpy.types.Operator):
    """체크된 영상을 뷰포트 배경(Reference)으로 즉시 불러옵니다."""
    bl_idname = "gemini.import_reference"
    bl_label = "레퍼런스로 불러오기"
    bl_options = {'REGISTER', 'UNDO'} 
    
    def execute(self, context):
        se = context.scene.sequence_editor
        if not se or not se.sequences: return {'CANCELLED'}
        target_strip = None
        for s in se.sequences:
            if s.type == 'MOVIE' and s.gemini_chk: target_strip = s; break 
        if not target_strip: return {'CANCELLED'}
        fpath = bpy.path.abspath(target_strip.filepath)
        if not os.path.exists(fpath): return {'CANCELLED'}
        try:
            bpy.ops.object.empty_image_add(filepath=fpath, align='VIEW', location=(0, 0, 0))
            obj = context.active_object
            if obj:
                obj.name = f"REF_{target_strip.name}"
                obj.empty_display_type = 'IMAGE'; obj.empty_image_depth = 'BACK'; obj.empty_image_side = 'FRONT'; obj.show_in_front = False 
            self.report({'INFO'}, f"레퍼런스 임포트 완료: {target_strip.name}")
        except Exception as e: self.report({'ERROR'}, str(e))
        return {'FINISHED'}

def run_transcode_mode(context, props):
    se = context.scene.sequence_editor
    target_strips = [s for s in se.sequences if s.type == 'MOVIE' and s.gemini_chk]
    abs_out = os.path.normpath(bpy.path.abspath(props.output_dir))
    if not os.path.exists(abs_out): os.makedirs(abs_out)
    success_cnt = 0; fail_list = []
    for s in target_strips:
        s.gemini_status = "변환 중..."
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        fpath = bpy.path.abspath(s.filepath)
        fname = os.path.splitext(os.path.basename(fpath))[0]
        ext_map = {'MP4':'.mp4', 'MOV':'.mov', 'MKV':'.mkv', 'AVI':'.avi', 'GIF':'.gif', 'WEBP':'.webp'}
        ext = ext_map.get(props.output_format, '.mp4')
        out_path = os.path.join(abs_out, f"{fname}{ext}")
        if os.path.normpath(fpath) == os.path.normpath(out_path): out_path = os.path.join(abs_out, f"{fname}_conv{ext}")
        ok, err = run_ffmpeg_process(context, props, fpath, out_path)
        if ok: s.gemini_status = "완료"; success_cnt += 1
        else: s.gemini_status = "실패"; fail_list.append((fname, err))
    return success_cnt, fail_list, abs_out

def run_viewport_mode(context, props):
    scene = context.scene
    if not scene.camera: return False, "활성화된 카메라가 없습니다.", None
    found_area = None; saved_shading = {}
    
    old_use_sequencer = scene.render.use_sequencer
    scene.render.use_sequencer = False 
    
    if props.vp_engine == 'OPENGL':
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                found_area = area; space = area.spaces.active
                shading = space.shading; overlay = space.overlay
                saved_shading = {
                    'type': shading.type, 'light': shading.light, 'color_type': shading.color_type,
                    'wireframe_color_type': shading.wireframe_color_type, 'background_type': shading.background_type,
                    'background_color': shading.background_color[:], 'single_color': shading.single_color[:],
                    'show_overlays': overlay.show_overlays, 'show_shadows': shading.show_shadows, 'show_cavity': shading.show_cavity
                }
                if props.use_anim_check: 
                    shading.type = 'SOLID'; shading.light = 'FLAT'; shading.color_type = 'SINGLE'; shading.single_color = (0.0, 0.0, 0.0)
                    shading.background_type = 'VIEWPORT'; shading.background_color = (0.8, 0.8, 0.8); overlay.show_overlays = False
                elif props.use_vp_preset: shading.type = 'RENDERED' 
                area.tag_redraw(); break

    old_res_x, old_res_y = scene.render.resolution_x, scene.render.resolution_y
    old_fps = scene.render.fps; old_fmt = scene.render.image_settings.file_format; old_engine = scene.render.engine 
    
    # [수정] 씬 설정 동기화 로직
    if not props.use_scene_settings:
        target_w, target_h = get_res_tuple(props.preset)
        scene.render.resolution_x = target_w; scene.render.resolution_y = target_h
        scene.render.fps = int(float(props.fps_selection))
        if float(props.fps_selection) in [23.98, 29.97, 59.94]: scene.render.fps += 1; scene.render.fps_base = 1.001
        else: scene.render.fps_base = 1.0

    scene.render.image_settings.file_format = 'FFMPEG'; scene.render.ffmpeg.format = props.vp_container
    scene.render.ffmpeg.codec = props.vp_codec; scene.render.ffmpeg.audio_codec = 'AAC'
    scene.render.ffmpeg.constant_rate_factor = props.vp_quality; scene.render.ffmpeg.ffmpeg_preset = props.vp_speed; scene.render.ffmpeg.gopsize = props.vp_keyframe
    
    abs_out_dir = os.path.normpath(bpy.path.abspath(props.output_dir))
    if not os.path.exists(abs_out_dir): os.makedirs(abs_out_dir)
    end_frame = scene.frame_end; date_str = datetime.now().strftime("%Y%m%d")
    prefix = "Viewport"; 
    if props.use_anim_check: prefix = "Silhouette"
    elif props.use_vp_preset: prefix = "Preview"
    elif props.vp_engine == 'WORKBENCH': prefix = "Workbench"
    
    base_name = f"{prefix}_{end_frame}_{date_str}"
    ext = {'MP4':'.mp4', 'MOV':'.mov', 'MKV':'.mkv', 'AVI':'.avi', 'GIF':'.gif', 'WEBP':'.webp'}.get(props.output_format, '.mp4')
    counter = 1
    while True:
        filename = f"{base_name}_{counter:03d}"
        full_path = os.path.join(abs_out_dir, f"{filename}{ext}")
        if not os.path.exists(full_path): break
        counter += 1
        
    scene.render.filepath = os.path.join(abs_out_dir, filename)
    
    render_success = False; error_msg = ""
    try: 
        if props.vp_engine == 'WORKBENCH': scene.render.engine = 'BLENDER_WORKBENCH'; bpy.ops.render.render(animation=True) 
        else: bpy.ops.render.opengl(animation=True, view_context=True)
        render_success = True
    except Exception as e: render_success = False; error_msg = str(e)
    
    if found_area and saved_shading:
        sh = found_area.spaces.active.shading; ov = found_area.spaces.active.overlay
        sh.type = saved_shading['type']
        try:
            sh.light = saved_shading['light']; sh.color_type = saved_shading['color_type']
            sh.wireframe_color_type = saved_shading['wireframe_color_type']; sh.background_type = saved_shading['background_type']
            sh.background_color = saved_shading['background_color']; sh.single_color = saved_shading['single_color']
            sh.show_shadows = saved_shading['show_shadows']; sh.show_cavity = saved_shading['show_cavity']
            ov.show_overlays = saved_shading['show_overlays']
        except: pass
    
    scene.render.use_sequencer = old_use_sequencer
    scene.render.engine = old_engine; scene.render.resolution_x = old_res_x; scene.render.resolution_y = old_res_y
    scene.render.fps = old_fps; scene.render.image_settings.file_format = old_fmt
    if not render_success: return False, error_msg, None
    
    gen_file = f"{scene.render.filepath}.{props.vp_container.lower().replace('mpeg4','mp4').replace('matroska','mkv').replace('quicktime','mov')}"
    if not os.path.exists(gen_file): gen_file = f"{scene.render.filepath}{scene.frame_start:04d}-{scene.frame_end:04d}.{props.vp_container.lower().replace('mpeg4','mp4').replace('matroska','mkv').replace('quicktime','mov')}"
    
    final_path = gen_file
    need_post = props.show_markers or props.show_info or props.output_format in ['GIF', 'WEBP'] or props.use_mirror or props.use_nvenc
    if os.path.exists(gen_file) and need_post:
        final_path = os.path.join(abs_out_dir, f"{filename}{ext}")
        ok, err = run_ffmpeg_process(context, props, gen_file, final_path, is_viewport_post=True)
        if ok: 
            try: os.remove(gen_file)
            except: pass
        else: final_path = gen_file
    return True, "OK", final_path

class GEMINI_OT_Process(bpy.types.Operator):
    """지정된 설정으로 인코딩 및 녹화 작업을 시작합니다."""
    bl_idname = "gemini.process"; bl_label = "작업 시작"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        props = context.scene.gemini_props
        if not props.output_dir: self.report({'ERROR'}, "출력(저장) 폴더를 지정해주세요!"); return {'CANCELLED'}
        start_time = time.time(); final_msg = ""; output_loc = ""
        try:
            if props.tool_mode == 'TRANSCODE':
                se = context.scene.sequence_editor
                if not se or not se.sequences: self.report({'ERROR'}, "불러온 영상이 없습니다."); return {'CANCELLED'}
                for s in se.sequences: s.gemini_status = ""
                suc, fail, path = run_transcode_mode(context, props)
                output_loc = path; final_msg = f"성공: {suc}건" + (f"\n실패: {len(fail)}건" if fail else "")
            elif props.tool_mode == 'VIEWPORT':
                ok, msg, path = run_viewport_mode(context, props)
                output_loc = os.path.dirname(path) if path else ""
                final_msg = f"저장 완료:\n{os.path.basename(path)}" if ok else f"녹화 실패:\n{msg}"
        except Exception as e:
            err_log = traceback.format_exc(); context.window_manager.clipboard = err_log
            show_message_box("알 수 없는 오류가 발생했습니다.\n에러 로그가 클립보드에 복사되었습니다.", "치명적 오류", 'ERROR')
            return {'CANCELLED'}
        duration = time.time() - start_time; time_str = f"({int(duration//60)}분 {duration%60:.2f}초 소요됨)"
        final_msg += f"\n\n{time_str}"
        if output_loc: context.window_manager.clipboard = output_loc; show_message_box(final_msg, "작업 완료", 'CHECKMARK'); self.report({'INFO'}, f"작업 완료! 경로가 복사되었습니다. {time_str}")
        return {'FINISHED'}

class GEMINI_OT_RemoveItem(bpy.types.Operator):
    """선택한 영상을 작업 목록에서 제거합니다."""
    bl_idname = "gemini.remove_item"; bl_label = "제거"
    bl_options = {'REGISTER', 'UNDO'}
    target_name: bpy.props.StringProperty()
    def execute(self, context):
        se = context.scene.sequence_editor
        if se:
            s = se.sequences.get(self.target_name)
            if s: se.sequences.remove(s)
        return {'FINISHED'}

class GEMINI_OT_ImportMulti(bpy.types.Operator):
    """탐색기를 열어 변환할 영상 파일을 여러 개 불러옵니다."""
    bl_idname = "gemini.import_multi"; bl_label = "영상 불러오기"
    bl_options = {'REGISTER', 'UNDO'}
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    def invoke(self, context, event): context.window_manager.fileselect_add(self); return {'RUNNING_MODAL'}
    def execute(self, context):
        valid_exts = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv']
        invalid_cnt = 0; valid_cnt = 0
        scene = context.scene
        if not scene.sequence_editor: scene.sequence_editor_create()
        curr = 1
        if scene.sequence_editor.sequences: curr = scene.sequence_editor.sequences[-1].frame_final_end + 10
        for f in self.files:
            _, ext = os.path.splitext(f.name); 
            if ext.lower() not in valid_exts: invalid_cnt += 1; continue
            try:
                s = scene.sequence_editor.sequences.new_movie(name=f.name, filepath=os.path.join(self.directory, f.name), channel=1, frame_start=curr)
                s.gemini_chk = True; s.gemini_status = "대기 중"; curr += 100; valid_cnt += 1
            except: pass
        if invalid_cnt > 0:
            msg = f"{invalid_cnt}개의 지원하지 않는 파일이 제외되었습니다."
            if valid_cnt == 0: show_message_box(msg, "불러오기 실패", 'ERROR')
            else: show_message_box(msg, "알림", 'INFO')
        return {'FINISHED'}

class GEMINI_OT_ClearList(bpy.types.Operator):
    """목록에 있는 모든 영상을 한 번에 지웁니다."""
    bl_idname = "gemini.clear_list"; bl_label = "초기화"
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        se = context.scene.sequence_editor
        if se: 
            for s in se.sequences: se.sequences.remove(s)
        return {'FINISHED'}

# ------------------------------------------------------------------------
# 5. 🖥️ UI (한글화 및 배치 개선)
# ------------------------------------------------------------------------

class VIEW3D_PT_GeminiEncoderV352(bpy.types.Panel):
    bl_label = "Quick Encoder v3.5.2"
    bl_idname = "VIEW3D_PT_gemini_v352"
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'Video Tool'
    
    def draw(self, context):
        layout = self.layout
        try: props = context.scene.gemini_props
        except: return
        row = layout.row(); row.prop(props, "tool_mode", expand=True); layout.separator()
        
        def draw_overlay_settings(layout_box, props):
            col = layout_box.column(align=True) 
            col.prop(props, "show_markers", toggle=True, icon='GRID')
            if props.show_markers:
                sub = col.column(align=True)
                sub.prop(props, "marker_color")
                sub.prop(props, "marker_font_size")
            col.prop(props, "show_info", toggle=True, icon='INFO')
            if props.show_info:
                sub = col.column(align=True)
                r = sub.row(align=True); r.prop(props, "info_color"); r.prop(props, "info_bg_color")
                sub.prop(props, "info_bg_opacity"); sub.prop(props, "info_font_size")

        if props.tool_mode == 'TRANSCODE':
            box = layout.box(); se = context.scene.sequence_editor
            count = len([s for s in se.sequences if s.type=='MOVIE']) if se else 0
            box.label(text=f"작업 목록 ({count} 개)", icon='FILE_MOVIE')
            if count > 0:
                col = box.column()
                for s in se.sequences:
                    if s.type == 'MOVIE':
                        row = col.row(align=True)
                        row.prop(s, "gemini_chk", text=s.name)
                        sub = row.row(); sub.alignment = 'RIGHT'; 
                        if s.gemini_status: sub.label(text=s.gemini_status)
                        op = sub.operator("gemini.remove_item", text="", icon='X'); op.target_name = s.name
            else: box.label(text="대기 중인 영상이 없습니다.", icon='INFO')
            
            row = box.row(); row.operator("gemini.import_multi", icon='IMPORT'); row.operator("gemini.clear_list", icon='TRASH')
            if count > 0: box.operator("gemini.import_reference", icon='IMAGE_DATA')

            layout.separator(); col = layout.column(align=True)
            col.prop(props, "output_format"); col.prop(props, "preset")
            col.prop(props, "fps_selection"); col.prop(props, "quality_preset")
            
            col.separator()
            col.label(text="모디파이어 & 오버레이:")
            col.prop(props, "use_mirror", icon='MOD_MIRROR')
            col.prop(props, "remove_audio", icon='MUTE_IPO_ON')
            draw_overlay_settings(col, props)

        elif props.tool_mode == 'VIEWPORT':
            box = layout.box(); box.label(text="뷰포트 녹화 설정", icon='VIEW3D')
            
            # 1. Recording
            box_1 = box.box(); box_1.label(text="1. 녹화 (소스 설정)", icon='REC')
            box_1.prop(props, "vp_engine", text="") 
            if props.vp_engine == 'OPENGL':
                row = box_1.row(align=True)
                row.prop(props, "use_vp_preset", icon='SHADING_RENDERED')
                row.prop(props, "use_anim_check", icon='ARMATURE_DATA')
            col = box_1.column(align=False); col.separator(factor=0.5)
            col.prop(props, "vp_container"); col.prop(props, "vp_codec")
            col.prop(props, "vp_quality"); col.prop(props, "vp_speed")
            col.prop(props, "vp_keyframe")
            
            # 2. Conversion
            box_2 = box.box(); box_2.label(text="2. 변환 (출력 설정)", icon='RENDER_ANIMATION')
            col = box_2.column(align=False)
            col.prop(props, "output_format")
            if props.output_format in ['GIF', 'WEBP']: col.label(text="* 녹화 후 자동 변환됩니다.", icon='FILE_REFRESH')
            
            # [기능 추가] 씬 카메라 동기화
            col.separator(factor=0.5)
            col.prop(props, "use_scene_settings", icon='SCENE_DATA')
            
            if not props.use_scene_settings:
                sub = col.column(align=True)
                sub.prop(props, "preset")
                sub.prop(props, "fps_selection")
            else:
                sub = col.column(align=True)
                res_x = int(context.scene.render.resolution_x * (context.scene.render.resolution_percentage / 100.0))
                res_y = int(context.scene.render.resolution_y * (context.scene.render.resolution_percentage / 100.0))
                fps = round(context.scene.render.fps / context.scene.render.fps_base, 2)
                sub.label(text=f"현재 씬 설정: {res_x}x{res_y} @ {fps}fps", icon='INFO')
            
            # 3. Overlays
            box_3 = box.box(); box_3.label(text="3. 오버레이 (후처리)", icon='IMAGE_ALPHA')
            draw_overlay_settings(box_3, props)
            
            # 4. Hardware
            box_4 = box.box(); box_4.label(text="4. 하드웨어 최적화", icon='PREFERENCES')
            col_hw = box_4.column(align=True)
            col_hw.prop(props, "use_nvenc", icon='TRIA_RIGHT')
            col_hw.prop(props, "use_thermal_guard", icon='TEMP')

        layout.separator(); box = layout.box(); col = box.column(align=True)
        col.label(text="📂 저장 경로:"); col.prop(props, "output_dir", text="")
        ff_bin = get_ffmpeg_binary()
        if os.path.exists(ff_bin) and ff_bin != "ffmpeg": box.label(text="FFmpeg 상태: 정상 작동 중", icon='CHECKMARK')
        else: box.label(text="FFmpeg 상태: 엔진 없음 (시스템 환경변수 확인)", icon='ERROR')
        main_btn_text = "변환 시작" if props.tool_mode == 'TRANSCODE' else "녹화 시작"
        main_icon = 'RENDER_ANIMATION' if props.tool_mode == 'TRANSCODE' else 'REC'
        row = layout.row(); row.scale_y = 1.5; row.operator("gemini.process", text=main_btn_text, icon=main_icon)

classes = (GeminiProperties, GEMINI_OT_Process, GEMINI_OT_ImportMulti, GEMINI_OT_ClearList, GEMINI_OT_RemoveItem, GEMINI_OT_ImportReference, VIEW3D_PT_GeminiEncoderV352)
def register():
    register_strip_props(); 
    for c in classes: bpy.utils.register_class(c)
    bpy.types.Scene.gemini_props = bpy.props.PointerProperty(type=GeminiProperties)
def unregister():
    del bpy.types.Scene.gemini_props; 
    for c in classes: bpy.utils.unregister_class(c)
if __name__ == "__main__": register()