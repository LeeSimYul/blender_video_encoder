# Quick Video Encoder

Blender 4.2+ LTS 용 영상 변환 · 뷰포트 녹화 애드온입니다.
3D 뷰포트 사이드바(`N`) 한 곳에서 외부 동영상 파일을 FFmpeg로 일괄 변환하고,
작업 중인 뷰포트를 그대로 녹화할 수 있습니다.

> A Blender 4.2+ LTS add-on that batch-converts video files with FFmpeg and
> records the 3D viewport, all from one sidebar panel.

![license](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)
![blender](https://img.shields.io/badge/blender-4.2%2B%20LTS-orange)

---

## 목차

- [주요 기능](#주요-기능)
- [요구 사항](#요구-사항)
- [설치](#설치)
- [FFmpeg 설치 및 PATH 설정](#ffmpeg-설치-및-path-설정)
- [사용법](#사용법)
- [Troubleshooting](#troubleshooting)
- [프로젝트 구조](#프로젝트-구조)
- [테스트](#테스트)
- [라이선스](#라이선스)

---

## 주요 기능

### 작업 모드

| 모드 | 설명 | 출력 |
| --- | --- | --- |
| **영상 파일 변환** (Transcode) | 시퀀서에 불러온 영상들을 일괄 변환 | MP4 · MOV · MKV · AVI · GIF · WebP |
| **뷰포트 녹화** (Viewport) | OpenGL 캡처(WYSIWYG) 또는 Workbench 렌더 | 위와 동일 (녹화 후 자동 변환) |

### 프리셋 및 옵션

| 기능 | 설명 | 사용 위치 |
| --- | --- | --- |
| **씬 카메라 설정 동기화** | Output Properties 의 해상도 · FPS 를 그대로 사용해 왜곡 없이 렌더링 | 뷰포트 녹화 |
| **Discord 8MB Match** | 목표 용량(MB)과 영상 길이로 비트레이트를 자동 계산, `-maxrate` 로 상한 고정 | 양쪽 모드 |
| **VRChat Thumbnail** | VRChat 월드 썸네일 규격(1920x1080) WebP/JPG 단일 버튼 익스포트 | 양쪽 모드 |
| **Side-by-Side (A/B Test)** | 동일 구간을 일반/실루엣으로 2회 녹화 후 `hstack` 으로 좌우 합성 | 뷰포트 녹화 (OpenGL) |
| **구간 분할 익스포트** | VSE 스트립 또는 타임라인 마커 구간을 개별 파일로 일괄 분할, 파일명 템플릿 지원 | 영상 파일 변환 |
| **편집용 프록시** | 720p 30fps H.264 경량 프록시 원클릭 생성 | 영상 파일 변환 |
| **KSL Learning Pack** | 원본 · 좌우 반전 · 0.75배속을 한 번에 연속 출력 | 영상 파일 변환 |
| **아카이브 메타데이터** | 출력 파일 내부에 `title` / `artist` / `comment` 태그 기록 | 양쪽 모드 |
| **아카이빙 배지** | 모서리에 반투명 한 줄 배지 오버레이 | 양쪽 모드 |
| **실루엣 검수 모드** | 아바타를 검은 실루엣, 배경을 회색으로 자동 전환 (녹화 후 원상 복구) | 뷰포트 녹화 (OpenGL) |
| **3x3 위치 마커** | 구도 확인용 RU/U/LU … LD 오버레이 | 양쪽 모드 |
| **정보(HUD) 표시** | FPS · 타임코드 · 프레임 번호를 좌측 상단에 표시 | 양쪽 모드 |
| **좌우 반전 / 오디오 제거** | `hflip` 및 `-an` | 양쪽 모드 |
| **NVENC 터보 가속** | NVIDIA GPU 하드웨어 인코딩(`h264_nvenc`) | 양쪽 모드 |
| **발열 제어** | 인코딩 프로세스 우선순위를 낮춰 작업 중 멈춤 방지 | 양쪽 모드 |
| **레퍼런스 임포트** | 선택한 영상을 뷰포트 배경 이미지 엠프티로 추가 | 영상 파일 변환 |

### 프리셋 상세

<details>
<summary><b>Discord 8MB Match</b> — 용량 제한 플랫폼용 자동 비트레이트</summary>

목표 용량과 `ffprobe` 로 측정한 영상 길이에서 비트레이트를 역산합니다.

```
video_kbps = (목표MB × 1024 × 1024 × 8 × 0.95 − audio_kbps × 1000 × 길이) / 길이 / 1000
```

- `0.95` 는 컨테이너 오버헤드와 레이트 컨트롤 오버슛을 위한 안전 마진입니다.
- 오디오를 제거하면 그만큼 영상에 배분됩니다.
- `-b:v` 뿐 아니라 `-maxrate` / `-bufsize` 를 함께 걸어 상한을 넘지 않도록 고정합니다.
- 영상이 너무 길어 계산값이 최소치(120 kbps) 아래로 떨어지면 최소치로 고정됩니다.
- `ffprobe` 로 길이를 얻지 못하면 경고를 남기고 화질 프리셋 비트레이트로 되돌아갑니다.

Discord 기준: 무료 8MB, Nitro Basic 50MB, Nitro 500MB. 목표 용량은 1~500MB 사이에서
직접 지정할 수 있습니다.
</details>

<details>
<summary><b>VRChat Thumbnail</b> — 월드 썸네일 규격 익스포트</summary>

VRChat 월드 썸네일이 요구하는 1920x1080 규격으로 이미지 한 장을 저장합니다.

- **영상 파일 변환 모드**: 체크된 첫 영상에서 현재 플레이헤드 위치의 프레임을 추출합니다.
- **뷰포트 녹화 모드**: 현재 뷰포트를 1920x1080 스틸로 렌더한 뒤 변환합니다.
- 비율이 다른 소스는 가운데를 기준으로 cover-crop 되어 찌그러지지 않습니다.
- WebP(권장) 또는 JPG 를 선택할 수 있고, 화질은 0~100% 로 조절합니다.
- 저장 후 파일 경로가 클립보드에 복사됩니다.
</details>

<details>
<summary><b>Side-by-Side (A/B Test)</b> — 원본 / 실루엣 비교 출력</summary>

같은 구간을 두 번 녹화해 좌우로 합성합니다.

1. 1차: 사용자의 현재 셰이딩 그대로 녹화
2. 2차: 실루엣 검수 셰이딩으로 녹화
3. FFmpeg `hstack` 으로 좌우 합성 후 중간 파일 삭제

- 각 화면은 출력 너비의 절반으로 cover-crop 되며, 짝수 픽셀로 정렬됩니다.
- 상단에 `ORIGINAL` / `SILHOUETTE` 라벨을 넣을 수 있습니다 (폰트가 없으면 자동 생략).
- 오디오는 1차 녹화본에서 가져옵니다.
- **제약**: 렌더 방식이 `현재 화면 캡처 (Viewport)` 이고 출력이 MP4/MOV/MKV/AVI 일 때만
  동작합니다. 실루엣이 뷰포트 셰이딩 설정이기 때문입니다. 녹화 시간은 2배가 됩니다.
</details>

<details>
<summary><b>OBS Long-Video Proxy & Segment Exporter</b> — 긴 녹화본 분할 아카이빙</summary>

한 시간짜리 OBS 녹화본을 VSE 에서 잘라두고, 각 구간을 개별 파일로 한 번에 내보냅니다.

**분할 기준**

| 기준 | 동작 |
| --- | --- |
| 스트립 (Strip) | 체크된 각 비디오 스트립의 편집 구간(in/out)을 그대로 잘라 냅니다. 스트립마다 원본 파일이 달라도 됩니다. |
| 마커 (Marker) | **체크된 모든 스트립**을 각자의 프레임 범위 안에 있는 마커로 나눕니다. 스트립마다 독립적으로 처리되며, 각 스트립의 마지막 마커는 그 스트립의 끝까지가 한 구간이 됩니다. |

**파일명 템플릿** — 기본값 `{prefix}_{name}_{index}`

| 토큰 | 값 |
| --- | --- |
| `{prefix}` | 패널에서 지정한 접두사 |
| `{name}` | 스트립 이름 또는 마커 이름 |
| `{strip}` | 구간이 속한 스트립 이름 |
| `{index}` | 1부터 시작하는 3자리 번호 (`001`) |
| `{scene}` | 현재 씬 이름 |
| `{date}` | 실행일 (`YYYYMMDD`) |

여러 스트립에 같은 이름의 마커가 있다면 `{strip}_{name}` 처럼 조합해 구분할 수 있습니다.
`{index}` 는 스트립이 바뀌어도 계속 이어지므로, `{index}` 만 써도 파일명은 겹치지 않습니다.

파일명에 들어가는 값은 모두 정제되므로, `take 1/2` 같은 마커 이름이 하위 폴더를 만들거나
경로를 벗어나는 일은 없습니다. 오타가 난 토큰(`{prefx}`)은 사용 가능한 토큰 목록과 함께
오류로 보고됩니다.

마커는 각 스트립의 `frame_start` ~ `frame_final_end` 범위로 배정되며, 어느 스트립에도
속하지 않는 마커(스트립 사이의 빈 구간 등)는 무시됩니다. 앞부분이 잘린(head-trim) 스트립의
잘려나간 영역에 마커가 있으면 구간 시작이 보이는 지점으로 당겨지므로, 편집에서 제외한
부분이 내보내지는 일은 없습니다.

**무재인코딩 분할** — 스트림을 그대로 복사해 즉시 자릅니다. 매우 빠르지만 키프레임
단위로 잘려 시작 지점이 조금 앞당겨질 수 있고, 오버레이·배속·해상도 변경은 적용되지
않습니다. 프레임 정확도가 필요하면 꺼두세요.

**편집용 프록시** — `<원본이름>_proxy.mp4` 로 720p30 CRF 23 H.264 + `faststart` 를
생성합니다. 화질 설정과 무관하게 고정된 사양이며, 스크러빙이 가벼운 것이 목적입니다.
</details>

<details>
<summary><b>Sign Language Multi-Export (KSL Learning Pack)</b> — 수어 학습용 다중 출력</summary>

한 번의 처리로 학습용 세 가지 버전을 연속 출력합니다.

| 접미사 | 내용 | 필터 |
| --- | --- | --- |
| `_orig` | 원본 | — |
| `_mirror` | 좌우 반전 | `hflip` |
| `_slow` | 0.75배속 슬로모션 | `setpts=1.33333*PTS` + `atempo=0.75` |

- 영상과 **오디오가 함께** 느려집니다 (`atempo`). 오디오를 제거한 경우 `-af` 는 붙지 않습니다.
- 좌우 반전은 사용자의 `좌우 반전` 토글과 XOR 로 적용되므로, 이미 반전된 소스에서도
  반전본과 비반전본이 하나씩 나옵니다.
- 구간 분할 익스포트와 함께 쓰면 **구간 × 3개** 파일이 생성됩니다.
- 영상 파일 변환 모드에서 동작합니다.
</details>

<details>
<summary><b>VRChat Archive Metadata & Watermark Badge</b> — 아카이빙 태그와 배지</summary>

**메타데이터** — `-metadata` 로 컨테이너 내부에 태그를 기록합니다.

- `title` / `artist` / `comment` 세 필드를 지원하며, `{name}` `{scene}` `{date}` 토큰을
  사용할 수 있습니다 (`{name}` 은 출력 파일 이름).
- MP4 · MOV · MKV 에서만 저장됩니다. AVI/GIF/WebP 는 먹서가 태그를 버리므로 건너뜁니다.
- 토큰 오타가 나도 내보내기는 계속되고, 원문이 그대로 기록되며 경고만 남습니다.
- 확인: `ffprobe -show_format <파일>`

**배지** — 모서리에 반투명 한 줄 워터마크를 올립니다.

- 문구, 위치(4개 모서리), 글자 크기, 배경 투명도를 조절할 수 있습니다.
- 여백은 출력 높이에 비례해 계산되므로 720p 와 4K 에서 같은 비율로 보입니다.
- 줄바꿈은 공백으로 치환되어 항상 한 줄로 유지됩니다.
- 마커·HUD 오버레이보다 **나중에** 그려져 항상 맨 위에 옵니다.
- 폰트를 찾지 못하면 다른 오버레이와 마찬가지로 조용히 생략됩니다.
</details>

---

## 요구 사항

| 항목 | 버전 | 비고 |
| --- | --- | --- |
| Blender | 4.2 LTS 이상 | 4.5 의 `Strip` API 개명에도 대응 |
| Python | Blender 내장 (3.11+) | 별도 설치 불필요 |
| FFmpeg | 4.x 이상 권장 | **필수** — 시스템에 설치하고 `PATH` 등록 |
| ffprobe | FFmpeg 에 동봉 | Discord 용량 맞춤 기능에만 사용 |

> Blender 는 FFmpeg 를 **라이브러리로만** 포함하고 실행 파일은 배포하지 않습니다.
> 따라서 변환 기능을 쓰려면 FFmpeg 를 별도로 설치해야 합니다.

---

## 설치

### 방법 A — Preferences 에서 zip 설치 (권장)

1. `blender_video_encoder/` 폴더를 zip 으로 압축합니다.

   ```bash
   zip -r blender_video_encoder.zip blender_video_encoder
   ```

   Windows 탐색기라면 `blender_video_encoder` 폴더를 우클릭 →
   `보내기 > 압축(ZIP) 폴더` 를 선택합니다.
   **저장소 루트가 아니라 안쪽의 애드온 폴더를 압축해야 합니다.**

2. Blender → `Edit > Preferences > Add-ons` → 우측 상단 `▼` → `Install from Disk...`
   에서 zip 을 선택합니다.
3. 목록에서 **Quick Video Encoder** 를 체크해 활성화합니다.
4. 3D 뷰포트에서 `N` 키 → **Video Tool** 탭을 엽니다.

### 방법 B — 디렉터리에 직접 배치

Blender 4.2 부터 사용자 스크립트 경로가 `extensions/` 와 `addons/` 로 나뉩니다.
이 애드온은 `bl_info` 를 사용하는 **레거시 애드온** 이므로 `addons/` 아래에 두어야 합니다.

| OS | 경로 |
| --- | --- |
| Windows | `%APPDATA%\Blender Foundation\Blender\4.2\scripts\addons\` |
| macOS | `~/Library/Application Support/Blender/4.2/scripts/addons/` |
| Linux | `~/.config/blender/4.2/scripts/addons/` |

해당 경로에 `blender_video_encoder` 폴더째로 복사하면 됩니다.

```bash
# Windows (PowerShell)
Copy-Item -Recurse blender_video_encoder "$env:APPDATA\Blender Foundation\Blender\4.2\scripts\addons\"
```

폴더 이름은 반드시 `blender_video_encoder` 여야 합니다 (파이썬 패키지명과 일치).
경로를 직접 확인하려면 Blender 파이썬 콘솔에서:

```python
import bpy; print(bpy.utils.user_resource('SCRIPTS', path="addons"))
```

### 방법 C — 개발용 심볼릭 링크

저장소를 수정하면서 바로 테스트하려면 링크를 걸어두는 편이 편합니다.

```bash
# Windows (관리자 권한 PowerShell)
New-Item -ItemType SymbolicLink `
  -Path "$env:APPDATA\Blender Foundation\Blender\4.2\scripts\addons\blender_video_encoder" `
  -Target "$PWD\blender_video_encoder"
```

```bash
# macOS / Linux
ln -s "$PWD/blender_video_encoder" \
  ~/.config/blender/4.2/scripts/addons/blender_video_encoder
```

> `extensions/` 디렉터리에 두면 인식되지 않습니다. Blender 4.2 의 Extensions 플랫폼은
> `bl_info` 대신 `blender_manifest.toml` 을 요구하며, 이 애드온은 아직 레거시 애드온
> 형식을 유지하고 있습니다.

---

## FFmpeg 설치 및 PATH 설정

패널 하단의 **FFmpeg 상태** 줄에서 인식 여부를 바로 확인할 수 있습니다.

- ✅ `FFmpeg 상태: 정상 작동 중`
- ❌ `FFmpeg 상태: 엔진 없음 (시스템 환경변수 확인)`

### Windows

가장 간단한 방법은 패키지 매니저입니다. **설치 후 Blender 를 완전히 재시작해야** 합니다
(실행 중인 프로세스는 갱신된 `PATH` 를 물려받지 못합니다).

```powershell
winget install Gyan.FFmpeg
```

수동 설치라면:

1. [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 또는
   [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases) 에서
   `ffmpeg-release-essentials.zip` 을 받습니다.
2. `C:\ffmpeg` 등 공백 없는 경로에 풀고, 안쪽의 `bin` 폴더 위치를 확인합니다
   (`C:\ffmpeg\bin\ffmpeg.exe` 가 있어야 합니다).
3. `시스템 환경 변수 편집` → `환경 변수(N)...` → **사용자 변수** 의 `Path` → `편집` →
   `새로 만들기` → `C:\ffmpeg\bin` 추가 → 모든 창을 `확인` 으로 닫습니다.
4. **새** PowerShell 창에서 확인:

   ```powershell
   ffmpeg -version
   ```

5. Blender 를 재시작합니다.

### macOS

```bash
brew install ffmpeg
```

### Linux

```bash
sudo apt install ffmpeg        # Debian / Ubuntu
sudo dnf install ffmpeg        # Fedora (RPM Fusion 필요)
sudo pacman -S ffmpeg          # Arch
```

---

## 사용법

1. 사이드바에서 작업 모드를 고릅니다.
2. **영상 파일 변환**: `영상 불러오기` 로 파일을 추가하고 체크박스로 대상을 고릅니다.
   **뷰포트 녹화**: 렌더 방식과 컨테이너/코덱을 설정합니다.
3. 출력 포맷 · 해상도 · FPS · 화질을 지정합니다.
4. `저장 경로` 를 지정합니다. (필수)
5. `변환 시작` / `녹화 시작` 을 누릅니다.

작업이 끝나면 결과 경로가 클립보드에 복사되고 소요 시간이 팝업으로 표시됩니다.

---

## Troubleshooting

| 증상 | 원인 및 해결 |
| --- | --- |
| `FFmpeg 상태: 엔진 없음` | FFmpeg 미설치 또는 `PATH` 미등록. 위 [설정 절차](#ffmpeg-설치-및-path-설정) 를 따르고 **Blender 를 재시작**하세요. 터미널에서 `ffmpeg -version` 이 동작하는지 먼저 확인합니다. |
| `PATH` 에 넣었는데도 인식되지 않음 | Blender 가 설정 이전에 실행된 경우입니다. 프로세스는 시작 시점의 환경변수를 복사하므로 재시작이 필요합니다. 런처(Steam 등)로 실행한다면 런처도 재시작하세요. |
| `출력(저장) 폴더를 지정해주세요!` | `저장 경로` 가 비어 있습니다. 패널 하단에서 폴더를 지정하세요. |
| `활성화된 카메라가 없습니다.` | 씬에 활성 카메라가 없습니다. 카메라를 추가하고 `Ctrl+숫자패드 0` 으로 활성화하세요. |
| `녹화 파일을 찾을 수 없습니다` | 저장 경로에 쓰기 권한이 없거나 렌더가 중간에 취소된 경우입니다. 다른 폴더로 지정해 보세요. |
| 변환은 됐는데 오버레이(마커/HUD)가 없음 | `drawtext` 용 폰트를 찾지 못한 경우입니다. 시스템 콘솔에 경고가 남습니다. Windows 는 `arial.ttf`, macOS 는 `Helvetica.ttc`, Linux 는 DejaVu/Liberation 폰트를 탐색합니다. |
| NVENC 사용 시 인코딩 실패 | NVIDIA GPU 가 아니거나 드라이버가 오래된 경우입니다. `NVENC 터보 가속` 을 끄면 `libx264` 로 인코딩합니다. |
| Discord 용량 맞춤이 적용되지 않음 | `ffprobe` 로 길이를 읽지 못한 경우입니다. 콘솔 경고를 확인하고, FFmpeg 설치본에 `ffprobe` 가 함께 있는지 확인하세요. GIF/WebP 에는 적용되지 않습니다. |
| A/B 비교 출력이 회색 처리됨 | 렌더 방식이 `워크벤치 렌더` 이거나 출력이 GIF/WebP 입니다. `현재 화면 캡처 (Viewport)` + MP4 계열로 바꾸세요. |
| `알 수 없는 템플릿 토큰입니다` | 파일명 템플릿에 오타가 있습니다. 사용 가능 토큰은 `{prefix}` `{name}` `{index}` `{scene}` `{date}` 입니다. |
| `타임라인에 마커가 없습니다` | 마커 분할을 선택했지만 마커가 없습니다. `M` 키로 마커를 추가하거나 분할 기준을 `스트립` 으로 바꾸세요. |
| `체크된 스트립의 프레임 범위 안에 마커가 없습니다` | 마커가 스트립 바깥(스트립 사이의 빈 구간 등)에 있습니다. 마커를 스트립 위로 옮기세요. |
| 무재인코딩 분할 결과의 시작이 밀림 | 스트림 복사는 키프레임 단위로만 자를 수 있습니다. 프레임 정확도가 필요하면 `무재인코딩 분할` 을 끄세요. |
| 무재인코딩 분할이 실패함 | 출력 포맷이 원본 컨테이너와 호환되지 않는 경우입니다. 원본과 같은 포맷을 고르거나 재인코딩으로 전환하세요. |
| 메타데이터가 파일에 보이지 않음 | 출력이 MP4/MOV/MKV 인지 확인하세요. AVI·GIF·WebP 는 태그를 저장하지 않습니다. `ffprobe -show_format` 으로 확인할 수 있습니다. |
| 배지가 보이지 않음 | 폰트를 찾지 못했거나 배지 문구가 비어 있습니다. 콘솔 경고를 확인하세요. |
| 녹화 중 Blender 가 멈춘 것처럼 보임 | 정상입니다. 애니메이션 렌더는 UI 를 블록합니다. 진행 상황은 시스템 콘솔(`Window > Toggle System Console`)에서 확인할 수 있습니다. |
| 에러 로그를 자세히 보고 싶음 | `Window > Toggle System Console` (Windows) 또는 터미널에서 Blender 를 실행하면 애드온 로그가 `[blender_video_encoder] ...` 형식으로 출력됩니다. |

---

## 프로젝트 구조

```
blender_video_encoder/          # 저장소 루트
├── blender_video_encoder/      # 애드온 패키지 (이 폴더를 zip 으로 배포)
│   ├── __init__.py             # bl_info, register / unregister
│   ├── properties.py           # GeminiProperties, 스트립 속성
│   ├── operators.py            # GEMINI_OT_* 오퍼레이터, 파이프라인
│   ├── ui.py                   # VIEW3D_PT_* 패널
│   └── utils/
│       ├── __init__.py
│       └── ffmpeg.py           # FFmpeg 커맨드 생성 + subprocess 처리
├── tests/
│   ├── test_ffmpeg.py          # 커맨드 생성 · 필터 · 프리셋 단위 테스트
│   └── test_archive_pipeline.py  # 분할/프록시/KSL/메타데이터 단위 테스트
├── legacy/                     # 모듈화 이전의 단일 파일 원본 (참고용)
├── LICENSE                     # GNU GPL v3 전문
└── README.md
```

### 설계 원칙

`utils/ffmpeg.py` 는 두 개의 층으로 나뉩니다.

- **Pure layer** — `EncodeSettings`, `build_ffmpeg_command()`,
  `build_thumbnail_command()`, `build_side_by_side_command()`,
  `calculate_video_bitrate_kbps()`, `format_segment_filename()`,
  `iter_variant_settings()`, `build_metadata_arguments()` 등. `bpy` 를 import 하지 않고 부수 효과도 없어
  일반 CPython 에서 그대로 테스트할 수 있습니다.
- **Environment layer** — `resolve_ffmpeg_binary()`, `probe_duration()`,
  `find_font_path()`, `run_ffmpeg()`. 파일 시스템 · Blender · `subprocess` 만 다루고
  인코딩 정책은 갖지 않습니다.

덕분에 "어떤 FFmpeg 명령어가 만들어지는가" 는 Blender 를 띄우지 않고도 검증할 수 있습니다.

---

## 테스트

```bash
python -m unittest discover -s tests -v
```

Blender 가 설치되어 있지 않아도 됩니다. 테스트는 `utils/ffmpeg.py` 를 파일 경로로
직접 로드해 애드온 패키지(`bpy` 의존)를 우회합니다.

---

## 라이선스

이 애드온은 Blender 파이썬 API(`bpy`) 에 종속되어 동작하므로,
Blender 와 동일한 카피레프트 계열인 **GNU General Public License v3.0 이상
(GPL-3.0-or-later)** 으로 배포됩니다.

```
Copyright (C) 2026 Simulacre

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <https://www.gnu.org/licenses/>.
```

전문은 [LICENSE](LICENSE) 파일을 참조하세요.

이 프로젝트를 포크하거나 수정본을 배포할 경우, 동일한 GPL v3 조건으로 소스코드를
공개해야 합니다.
