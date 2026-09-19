<div align="center">

# 🎬 Quick Video Encoder

**Blender 뷰포트 녹화 · FFmpeg 일괄 변환 · VRChat/KSL 수어 아카이빙 파이프라인**

[![Blender](https://img.shields.io/badge/Blender-4.2%2B%20LTS-E87D0D?logo=blender&logoColor=white)](https://www.blender.org/download/lts/)
[![License](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Addon-v3.8.1-brightgreen)](https://github.com/LeeSimYul/blender_video_encoder)
[![Python](https://img.shields.io/badge/Tested%20Python-3.11%2B-3776AB?logo=python&logoColor=white)](#-테스트--testing)
[![FFmpeg](https://img.shields.io/badge/Requires-FFmpeg-007808?logo=ffmpeg&logoColor=white)](#-ffmpeg-설치-및-path-설정)

</div>

---

## 📖 개요 · Summary

**Quick Video Encoder** 는 Blender 4.2+ LTS 용 영상 변환 · 뷰포트 녹화 애드온입니다.
3D 뷰포트 사이드바(`N` → **Video Tool**) 한 곳에서 외부 동영상을 FFmpeg 로 일괄 변환하고,
작업 중인 뷰포트를 씬 카메라 설정 그대로 녹화할 수 있습니다.
v3.8 부터는 긴 OBS 녹화본을 **구간 단위로 잘라 수어 학습용 패키지와 메타데이터까지 한 번에 생성**하는
VRChat / KSL(한국수어) 아카이빙 파이프라인을 제공합니다.

> **Quick Video Encoder** is a Blender 4.2+ LTS add-on that batch-transcodes video with FFmpeg
> and records the 3D viewport — all from a single sidebar panel. Since v3.8 it ships an
> **archiving pipeline for VRChat / Korean Sign Language (KSL) footage**: marker-based segment
> splitting, a learning pack (original · mirrored · 0.75× slow-mo), one-click edit proxies,
> and embedded container metadata.

| | |
| --- | --- |
| 📍 **위치 / Location** | `View3D > Sidebar (N) > Video Tool` |
| 🧩 **카테고리 / Category** | Render |
| 🎞️ **출력 포맷 / Outputs** | MP4 · MOV · MKV · AVI · GIF · WebP (+ WebP/JPG 썸네일) |
| ⚙️ **인코더 / Encoders** | `libx264` · `h264_nvenc` (NVIDIA) |

---

## 📚 목차 · Table of Contents

- [✨ 핵심 기능 · Key Features](#-핵심-기능--key-features)
  - [기본 파이프라인](#1-기본-파이프라인--core-pipeline)
  - [특화 프리셋 팩](#2-특화-프리셋-팩--preset-pack)
  - [★ VRChat / KSL 수어 아카이빙 파이프라인](#3--vrchat--ksl-수어-아카이빙-파이프라인--sign-language-archiving-pipeline)
  - [보조 도구](#4-보조-도구--utilities)
- [📦 요구 사항 · Requirements](#-요구-사항--requirements)
- [🚀 설치 · Installation](#-설치--installation)
- [🎥 FFmpeg 설치 및 PATH 설정](#-ffmpeg-설치-및-path-설정)
- [🕹️ 사용법 · Usage](#️-사용법--usage)
- [🛠️ 트러블슈팅 · Troubleshooting](#️-트러블슈팅--troubleshooting)
- [🏗️ 아키텍처 · Architecture](#️-아키텍처--architecture)
- [🧪 테스트 · Testing](#-테스트--testing)
- [📜 라이선스 · License](#-라이선스--license)

---

## ✨ 핵심 기능 · Key Features

### 작업 모드 · Modes

| 모드 | 설명 | 출력 |
| --- | --- | --- |
| **영상 파일 변환** (Transcode) | 시퀀서(VSE)에 불러온 영상들을 체크박스로 골라 일괄 변환 | MP4 · MOV · MKV · AVI · GIF · WebP |
| **뷰포트 녹화** (Viewport) | OpenGL 캡처(WYSIWYG) 또는 Workbench 렌더 후 자동 변환 | 위와 동일 |

### 1. 기본 파이프라인 · Core Pipeline

| 기능 | 설명 | 적용 모드 |
| --- | --- | --- |
| 🖥️ **뷰포트 녹화 (OpenGL / Workbench)** | 화면 그대로 캡처하는 OpenGL 방식과, 셰이딩을 통일해 렌더하는 Workbench 방식 중 선택 | 뷰포트 녹화 |
| 📐 **씬 카메라 해상도 / FPS 동기화** | Output Properties 의 해상도 · FPS 를 그대로 가져와 왜곡 없이 렌더 | 뷰포트 녹화 |
| ⚡ **NVENC 터보 가속** | NVIDIA GPU 하드웨어 인코더(`h264_nvenc`) 사용. 끄면 `libx264` 로 폴백 | 양쪽 |
| 🌡️ **발열 제어 (Thermal Guard)** | FFmpeg 자식 프로세스의 우선순위를 낮춰(POSIX `nice`, Windows 우선순위 클래스) 장시간 인코딩 중에도 Blender · 시스템이 멈추지 않도록 보호 | 양쪽 |

### 2. 특화 프리셋 팩 · Preset Pack

| 프리셋 | 핵심 동작 | 적용 모드 |
| --- | --- | --- |
| 💬 **Discord 8MB Match** | 목표 용량(MB) × 영상 길이로 비트레이트를 역산하고 `-maxrate` / `-bufsize` 로 상한 고정 | 양쪽 |
| 🖼️ **VRChat Thumbnail** | VRChat 월드 썸네일 규격(1920×1080) WebP / JPG 단일 이미지 익스포트 | 양쪽 |
| 🆚 **Side-by-Side A/B Test** | 동일 구간을 원본 / 실루엣으로 2회 녹화 후 `hstack` 으로 좌우 병렬 합성 | 뷰포트 녹화 (OpenGL) |

<details>
<summary><b>💬 Discord 8MB Match</b> — 용량 제한 플랫폼용 자동 비트레이트</summary>

목표 용량과 `ffprobe` 로 측정한 영상 길이에서 비트레이트를 역산합니다.

```
video_kbps = (목표MB × 1024 × 1024 × 8 × 0.95 − audio_kbps × 1000 × 길이) / 길이 / 1000
```

- `0.95` 는 컨테이너 오버헤드와 레이트 컨트롤 오버슛을 위한 안전 마진입니다.
- 오디오를 제거하면 그만큼 영상에 배분됩니다.
- `-b:v` 뿐 아니라 `-maxrate` / `-bufsize` 를 함께 걸어 상한을 넘지 않도록 고정합니다.
- 영상이 너무 길어 계산값이 최소치(120 kbps) 아래로 떨어지면 최소치로 고정됩니다.
- `ffprobe` 로 길이를 얻지 못하면 경고를 남기고 화질 프리셋 비트레이트로 되돌아갑니다.

Discord 기준: 무료 8MB, Nitro Basic 50MB, Nitro 500MB. 목표 용량은 1~500MB 사이에서 직접 지정할 수 있습니다.
</details>

<details>
<summary><b>🖼️ VRChat Thumbnail</b> — 월드 썸네일 규격 익스포트</summary>

- **영상 파일 변환 모드**: 체크된 첫 영상에서 현재 플레이헤드 위치의 프레임을 추출합니다.
- **뷰포트 녹화 모드**: 현재 뷰포트를 1920×1080 스틸로 렌더한 뒤 변환합니다.
- 비율이 다른 소스는 가운데 기준으로 cover-crop 되어 찌그러지지 않습니다.
- WebP(권장) 또는 JPG 선택, 화질 0~100% 조절. 저장 후 파일 경로가 클립보드에 복사됩니다.
</details>

<details>
<summary><b>🆚 Side-by-Side A/B Test</b> — 원본 vs 실루엣 검수 병렬 합성</summary>

1. 1차: 사용자의 현재 셰이딩 그대로 녹화
2. 2차: 실루엣 검수 셰이딩(아바타 = 검정, 배경 = 회색)으로 녹화
3. FFmpeg `hstack` 으로 좌우 합성 후 중간 파일 삭제

- 각 화면은 출력 너비의 절반으로 cover-crop 되며 짝수 픽셀로 정렬됩니다.
- 상단에 `ORIGINAL` / `SILHOUETTE` 라벨을 넣을 수 있습니다 (폰트가 없으면 자동 생략).
- 오디오는 1차 녹화본에서 가져옵니다.
- **제약**: 렌더 방식이 `현재 화면 캡처 (Viewport)` 이고 출력이 MP4/MOV/MKV/AVI 일 때만 동작합니다. 녹화 시간은 2배가 됩니다.
</details>

### 3. ★ VRChat / KSL 수어 아카이빙 파이프라인 · Sign Language Archiving Pipeline

> [!IMPORTANT]
> v3.8 의 핵심 기능입니다. 한 시간짜리 OBS 녹화본을 VSE 에서 마커로 나눠 두기만 하면,
> **구간 분할 → 학습용 3종 변환 → 메타데이터 / 배지 주입**까지 한 번의 클릭으로 끝납니다.
>
> ```
> OBS 녹화본 ─► [VSE 프록시 720p30] ─► 마커/스트립 구간 분할 ─► ×3 학습 패키지 ─► 메타데이터 + 배지
>                                                              (orig · mirror · slow)
> ```

| 기능 | 설명 |
| --- | --- |
| ✂️ **Multi-Strip Marker Splitting** | 체크된 **모든** 스트립을 각자의 프레임 범위 안의 마커로 분할해 개별 파일로 익스포트. 스트립 단위 분할도 지원 |
| 🤟 **KSL Learning Pack** | 원본 `1.0x` + 미러링 `hflip` + `0.75x` 슬로모션을 연쇄 익스포트 (구간 분할과 함께 쓰면 **구간 × 3** 파일) |
| 🎞️ **VSE 편집용 프록시** | 720p · 30fps · CRF 23 H.264 + `faststart` 경량 프록시를 원클릭 생성 |
| 🏷️ **아카이브 배지 & 메타데이터** | 모서리 반투명 배지 오버레이 + MP4/MOV 내부 `title` · `artist` · `comment` 태그 자동 주입 |

<details>
<summary><b>✂️ Multi-Strip Marker Splitting</b> — 세그먼트 자동 분할 익스포트</summary>

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
| `{index}` | 1부터 시작하는 3자리 번호 (`001`) — 스트립이 바뀌어도 이어짐 |
| `{scene}` | 현재 씬 이름 |
| `{date}` | 실행일 (`YYYYMMDD`) |

- 여러 스트립에 같은 이름의 마커가 있다면 `{strip}_{name}` 처럼 조합해 구분합니다.
- 파일명 값은 모두 정제되므로 `take 1/2` 같은 마커 이름이 하위 폴더를 만들거나 경로를 벗어나지 않습니다.
- 오타 토큰(`{prefx}`)은 사용 가능한 토큰 목록과 함께 오류로 보고됩니다.
- 마커는 각 스트립의 `frame_start` ~ `frame_final_end` 범위로 배정되며, 어느 스트립에도 속하지 않는 마커는 무시됩니다.
- head-trim 된 스트립의 잘린 영역에 있는 마커는 보이는 시작점으로 당겨지므로, 편집에서 제외한 부분이 내보내지지 않습니다.

**무재인코딩 분할** — 스트림 복사로 즉시 자릅니다. 매우 빠르지만 키프레임 단위라 시작점이 조금 앞당겨질 수 있고, 오버레이 · 배속 · 해상도 변경은 적용되지 않습니다. 프레임 정확도가 필요하면 꺼 두세요.
</details>

<details>
<summary><b>🤟 KSL Learning Pack</b> — 수어 학습용 다중 출력</summary>

| 접미사 | 내용 | 필터 |
| --- | --- | --- |
| `_orig` | 원본 1.0x | — |
| `_mirror` | 좌우 반전 | `hflip` |
| `_slow` | 0.75배속 슬로모션 | `setpts=1.33333*PTS` + `atempo=0.75` |

- 영상과 **오디오가 함께** 느려집니다(`atempo`). 오디오를 제거한 경우 `-af` 는 붙지 않습니다.
- 좌우 반전은 사용자의 `좌우 반전` 토글과 XOR 로 적용되므로, 이미 반전된 소스에서도 반전본 / 비반전본이 하나씩 나옵니다.
- 영상 파일 변환 모드에서 동작합니다.
</details>

<details>
<summary><b>🎞️ VSE 편집용 프록시</b> — 긴 녹화본 스크러빙용</summary>

`<원본이름>_proxy.mp4` 로 720p · 30fps · CRF 23 H.264 + `faststart` 를 생성합니다.
화질 설정과 무관한 고정 사양이며, 한 시간 이상의 녹화본도 VSE 에서 가볍게 스크러빙하는 것이 목적입니다.
</details>

<details>
<summary><b>🏷️ 아카이브 메타데이터 & 워터마크 배지</b></summary>

**메타데이터** — `-metadata` 로 컨테이너 내부에 태그를 기록합니다.

- `title` / `artist` / `comment` 세 필드 지원, `{name}` `{scene}` `{date}` 토큰 사용 가능 (`{name}` 은 출력 파일 이름).
- MP4 · MOV · MKV 에서만 저장됩니다. AVI / GIF / WebP 는 먹서가 태그를 버리므로 건너뜁니다.
- 토큰 오타가 나도 내보내기는 계속되며 원문이 그대로 기록되고 경고만 남습니다.
- 확인: `ffprobe -show_format <파일>`

**배지** — 모서리에 반투명 한 줄 워터마크를 올립니다.

- 문구, 위치(4개 모서리), 글자 크기, 배경 투명도 조절.
- 여백은 출력 높이에 비례하므로 720p 와 4K 에서 같은 비율로 보입니다.
- 줄바꿈은 공백으로 치환되어 항상 한 줄 유지, 마커 · HUD 오버레이보다 **나중에** 그려져 항상 맨 위에 옵니다.
- 폰트를 찾지 못하면 다른 오버레이와 마찬가지로 조용히 생략됩니다.
</details>

### 4. 보조 도구 · Utilities

| 기능 | 설명 | 적용 모드 |
| --- | --- | --- |
| **실루엣 검수 모드** | 아바타를 검은 실루엣, 배경을 회색으로 자동 전환 (녹화 후 원상 복구) | 뷰포트 녹화 (OpenGL) |
| **3×3 위치 마커** | 구도 확인용 RU / U / LU … LD 오버레이 | 양쪽 |
| **정보(HUD) 표시** | FPS · 타임코드 · 프레임 번호를 좌측 상단에 표시 | 양쪽 |
| **좌우 반전 / 오디오 제거** | `hflip` 및 `-an` | 양쪽 |
| **레퍼런스 임포트** | 선택한 영상을 뷰포트 배경 이미지 엠프티로 추가 | 영상 파일 변환 |

---

## 📦 요구 사항 · Requirements

| 항목 | 버전 | 비고 |
| --- | --- | --- |
| Blender | **4.2 LTS 이상** | 4.5 의 `Strip` API 개명에도 대응 |
| Python | Blender 내장 (3.11+) | 별도 설치 불필요 |
| FFmpeg | 4.x 이상 권장 | **필수** — 시스템에 설치하고 `PATH` 등록 |
| ffprobe | FFmpeg 에 동봉 | Discord 용량 맞춤 기능에 사용 |
| NVIDIA GPU | 선택 | NVENC 가속 사용 시 |

> [!NOTE]
> Blender 는 FFmpeg 를 **라이브러리로만** 포함하고 실행 파일은 배포하지 않습니다.
> 변환 기능을 쓰려면 FFmpeg 를 별도로 설치해야 합니다.

---

## 🚀 설치 · Installation

### 방법 A — `.zip` 설치 (권장)

**1단계. 애드온 폴더를 압축합니다.**

> [!WARNING]
> 저장소 루트가 아니라 **안쪽의 `blender_video_encoder/` 애드온 폴더**를 압축해야 합니다.
> zip 을 열었을 때 최상위에 `blender_video_encoder/__init__.py` 가 보여야 정상입니다.

```bash
# macOS / Linux / Git Bash
zip -r blender_video_encoder.zip blender_video_encoder
```

```powershell
# Windows (PowerShell)
Compress-Archive -Path .\blender_video_encoder -DestinationPath .\blender_video_encoder.zip
```

Windows 탐색기라면 `blender_video_encoder` 폴더 우클릭 → `보내기 > 압축(ZIP) 폴더`.

**2단계.** Blender → `Edit > Preferences > Add-ons` → 우측 상단 `▼` → **`Install from Disk...`** 에서 zip 선택

**3단계.** 목록에서 **Quick Video Encoder** 를 체크해 활성화

**4단계.** 3D 뷰포트에서 `N` 키 → **Video Tool** 탭 열기

**5단계.** 패널 하단 `FFmpeg 상태: 정상 작동 중` 표시 확인 (❌ 라면 [FFmpeg 설정](#-ffmpeg-설치-및-path-설정)으로)

### 방법 B — 디렉터리에 직접 배치

이 애드온은 `bl_info` 를 사용하는 **레거시 애드온**이므로 `extensions/` 가 아닌 `addons/` 아래에 두어야 합니다.

| OS | 경로 |
| --- | --- |
| Windows | `%APPDATA%\Blender Foundation\Blender\4.2\scripts\addons\` |
| macOS | `~/Library/Application Support/Blender/4.2/scripts/addons/` |
| Linux | `~/.config/blender/4.2/scripts/addons/` |

폴더 이름은 반드시 `blender_video_encoder` 여야 합니다 (파이썬 패키지명과 일치). 경로 확인:

```python
import bpy; print(bpy.utils.user_resource('SCRIPTS', path="addons"))
```

### 방법 C — 개발용 심볼릭 링크

```powershell
# Windows (관리자 권한 PowerShell)
New-Item -ItemType SymbolicLink `
  -Path "$env:APPDATA\Blender Foundation\Blender\4.2\scripts\addons\blender_video_encoder" `
  -Target "$PWD\blender_video_encoder"
```

```bash
# macOS / Linux
ln -s "$PWD/blender_video_encoder" ~/.config/blender/4.2/scripts/addons/blender_video_encoder
```

> [!NOTE]
> `extensions/` 디렉터리에 두면 인식되지 않습니다. Blender 4.2 Extensions 플랫폼은
> `blender_manifest.toml` 을 요구하며, 이 애드온은 아직 레거시 애드온 형식입니다.

---

## 🎥 FFmpeg 설치 및 PATH 설정

패널 하단의 **FFmpeg 상태** 줄에서 인식 여부를 바로 확인할 수 있습니다.

- ✅ `FFmpeg 상태: 정상 작동 중`
- ❌ `FFmpeg 상태: 엔진 없음 (시스템 환경변수 확인)`

### Windows

**패키지 매니저 (가장 간단)**

```powershell
winget install Gyan.FFmpeg
```

**수동 설치 + 시스템 환경변수 `PATH` 등록**

1. [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 또는 [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases) 에서 `ffmpeg-release-essentials.zip` 을 받습니다.
2. `C:\ffmpeg` 처럼 **공백 없는 경로**에 풀고, `C:\ffmpeg\bin\ffmpeg.exe` 가 있는지 확인합니다.
3. `Win` 키 → **`시스템 환경 변수 편집`** 검색 → `환경 변수(N)...`
4. **사용자 변수**(또는 시스템 변수)의 `Path` 선택 → `편집` → `새로 만들기` → `C:\ffmpeg\bin` 입력
5. 열린 창을 모두 `확인` 으로 닫습니다.
6. **새** PowerShell 창에서 확인합니다.

   ```powershell
   ffmpeg -version
   where.exe ffmpeg
   ```

7. **Blender 를 완전히 재시작합니다.** (Steam 등 런처로 실행했다면 런처도 재시작)

> [!TIP]
> 실행 중인 프로세스는 시작 시점의 환경변수를 복사해 두기 때문에, `PATH` 를 바꾼 뒤에는
> 반드시 재시작해야 새 값이 반영됩니다. 재부팅하면 확실합니다.

### macOS

```bash
brew install ffmpeg
```

> Finder / Dock 에서 실행한 Blender 는 셸의 `PATH`(`/opt/homebrew/bin`)를 물려받지 못할 수 있습니다.
> 인식되지 않으면 터미널에서 `/Applications/Blender.app/Contents/MacOS/Blender` 로 실행해 보세요.

### Linux

```bash
sudo apt install ffmpeg        # Debian / Ubuntu
sudo dnf install ffmpeg        # Fedora (RPM Fusion 필요)
sudo pacman -S ffmpeg          # Arch
```

---

## 🕹️ 사용법 · Usage

1. 사이드바에서 작업 모드를 고릅니다.
2. **영상 파일 변환**: `영상 불러오기` 로 파일을 추가하고 체크박스로 대상을 고릅니다.
   **뷰포트 녹화**: 렌더 방식과 컨테이너 / 코덱을 설정합니다.
3. 출력 포맷 · 해상도 · FPS · 화질을 지정하고, 필요한 프리셋 / 아카이빙 옵션을 켭니다.
4. `저장 경로` 를 지정합니다. (필수)
5. `변환 시작` / `녹화 시작` 을 누릅니다.

작업이 끝나면 결과 경로가 클립보드에 복사되고 소요 시간이 팝업으로 표시됩니다.

---

## 🛠️ 트러블슈팅 · Troubleshooting

### FFmpeg 인식

| 증상 | 원인 및 해결 |
| --- | --- |
| `FFmpeg 상태: 엔진 없음` | FFmpeg 미설치 또는 `PATH` 미등록. [설정 절차](#-ffmpeg-설치-및-path-설정)를 따르고 **Blender 를 재시작**하세요. 터미널에서 `ffmpeg -version` 이 먼저 동작해야 합니다. |
| `PATH` 에 넣었는데도 인식되지 않음 | Blender(또는 런처)가 설정 이전에 실행된 경우입니다. 모두 종료 후 재시작하세요. `bin` 폴더가 아닌 상위 폴더를 등록하지 않았는지도 확인하세요. |
| Discord 용량 맞춤이 적용되지 않음 | `ffprobe` 로 길이를 읽지 못한 경우입니다. FFmpeg 설치본에 `ffprobe` 가 함께 있는지 확인하세요. GIF/WebP 에는 적용되지 않습니다. |
| NVENC 사용 시 인코딩 실패 | NVIDIA GPU 가 아니거나 드라이버가 오래된 경우입니다. `NVENC 터보 가속` 을 끄면 `libx264` 로 인코딩합니다. |

### 녹화 · 변환

| 증상 | 원인 및 해결 |
| --- | --- |
| `출력(저장) 폴더를 지정해주세요!` | `저장 경로` 가 비어 있습니다. |
| `활성화된 카메라가 없습니다.` | 카메라를 추가하고 `Ctrl + Numpad 0` 으로 활성화하세요. |
| `녹화 파일을 찾을 수 없습니다` | 저장 경로에 쓰기 권한이 없거나 렌더가 중간에 취소된 경우입니다. |
| 오버레이(마커 / HUD / 배지)가 없음 | `drawtext` 용 폰트를 찾지 못했습니다. Windows `arial.ttf`, macOS `Helvetica.ttc`, Linux DejaVu / Liberation 을 탐색합니다. |
| A/B 비교 옵션이 회색 처리됨 | 렌더 방식이 `워크벤치 렌더` 이거나 출력이 GIF/WebP 입니다. `현재 화면 캡처 (Viewport)` + MP4 계열로 바꾸세요. |
| 녹화 중 Blender 가 멈춘 것처럼 보임 | 정상입니다. 애니메이션 렌더는 UI 를 블록합니다. `Window > Toggle System Console` 에서 진행 상황을 확인하세요. |

### 아카이빙 파이프라인

| 증상 | 원인 및 해결 |
| --- | --- |
| `알 수 없는 템플릿 토큰입니다` | 파일명 템플릿 오타. 사용 가능 토큰: `{prefix}` `{name}` `{strip}` `{index}` `{scene}` `{date}` |
| `타임라인에 마커가 없습니다` | `M` 키로 마커를 추가하거나 분할 기준을 `스트립` 으로 바꾸세요. |
| `체크된 스트립의 프레임 범위 안에 마커가 없습니다` | 마커가 스트립 사이 빈 구간에 있습니다. 마커를 스트립 위로 옮기세요. |
| 무재인코딩 분할 결과의 시작이 밀림 | 스트림 복사는 키프레임 단위로만 자릅니다. 프레임 정확도가 필요하면 끄세요. |
| 무재인코딩 분할이 실패함 | 출력 포맷이 원본 컨테이너와 호환되지 않습니다. 같은 포맷을 고르거나 재인코딩으로 전환하세요. |
| 메타데이터가 보이지 않음 | MP4 / MOV / MKV 만 태그를 저장합니다. `ffprobe -show_format` 으로 확인하세요. |

> [!TIP]
> 자세한 로그는 `Window > Toggle System Console` (Windows) 또는 터미널에서 Blender 를 실행하면
> `[blender_video_encoder] ...` 형식으로 출력됩니다.

---

## 🏗️ 아키텍처 · Architecture

```
blender_video_encoder/              # 저장소 루트
├── blender_video_encoder/          # 애드온 패키지 (이 폴더를 zip 으로 배포)
│   ├── __init__.py                 # bl_info, register / unregister
│   ├── properties.py               # PropertyGroup, 스트립별 속성
│   ├── operators.py                # 변환 · 녹화 · 아카이빙 오퍼레이터
│   ├── ui.py                       # View3D 사이드바 패널
│   └── utils/
│       ├── __init__.py
│       └── ffmpeg.py               # FFmpeg 커맨드 생성 + subprocess 처리
├── tests/
│   ├── test_ffmpeg.py              # 커맨드 생성 · 필터 · 프리셋 단위 테스트
│   └── test_archive_pipeline.py    # 분할 · 프록시 · KSL · 메타데이터 단위 테스트
├── legacy/                         # 모듈화 이전 단일 파일 원본 (참고용)
├── LICENSE                         # GNU GPL v3 전문
└── README.md
```

### 설계 원칙 · Two-Layer Design

`utils/ffmpeg.py` 는 **"무엇을 실행할지"** 와 **"어떻게 실행할지"** 를 분리한 두 개의 층으로 나뉩니다.

| 층 | 책임 | 대표 API | `bpy` 의존 |
| --- | --- | --- | --- |
| 🧮 **Pure Layer** | 인코딩 정책 · FFmpeg 인자 생성. 부수 효과 없음 | `EncodeSettings`, `build_ffmpeg_command()`, `build_thumbnail_command()`, `build_side_by_side_command()`, `calculate_video_bitrate_kbps()`, `format_segment_filename()`, `iter_variant_settings()`, `build_metadata_arguments()` | ❌ 순수 Python |
| 🔌 **Environment Layer** | 파일 시스템 · Blender · `subprocess` 입출력. 정책 없음 | `resolve_ffmpeg_binary()`, `probe_duration()`, `find_font_path()`, `run_ffmpeg()` | 경계에서만 |

덕분에 *"어떤 FFmpeg 명령어가 만들어지는가"* 를 Blender 를 띄우지 않고 일반 CPython 에서 검증할 수 있습니다.

---

## 🧪 테스트 · Testing

```bash
python -m unittest discover -s tests -v
```

```
Ran 109 tests in 0.0XXs

OK
```

- Blender 설치 없이 실행됩니다. 테스트는 `utils/ffmpeg.py` 를 파일 경로로 직접 로드해 `bpy` 의존 패키지를 우회합니다.
- Python **3.11+** 에서 검증되었습니다 (Blender 4.2 LTS 내장 버전과 동일).

| 파일 | 범위 |
| --- | --- |
| `test_ffmpeg.py` | 커맨드 생성, 필터 체인, Discord 비트레이트, 썸네일, A/B 합성 |
| `test_archive_pipeline.py` | 마커 / 스트립 분할, 파일명 템플릿, 프록시, KSL 학습 패키지, 메타데이터 |

---

## 📜 라이선스 · License

이 애드온은 Blender 파이썬 API(`bpy`)에 종속되어 동작하므로, Blender 와 동일한 카피레프트 계열인
**GNU General Public License v3.0 or later (`GPL-3.0-or-later`)** 로 배포됩니다.

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
이 프로젝트를 포크하거나 수정본을 배포할 경우, 동일한 GPL v3 조건으로 소스코드를 공개해야 합니다.

<div align="center">

---

Made with 🤟 for the VRChat 한국수어 community · [Issues](https://github.com/LeeSimYul/blender_video_encoder/issues)

</div>
