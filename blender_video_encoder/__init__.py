# Quick Video Encoder — video conversion and viewport recording for Blender.
# Copyright (C) 2026 Simulacre
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-FileCopyrightText: 2026 Simulacre
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Quick Video Encoder — a video conversion and viewport recording add-on.

The add-on links against Blender's Python API and is therefore distributed
under the GNU General Public License v3.0 or later, the same copyleft family
Blender itself uses. See the ``LICENSE`` file at the repository root.

Package layout:

* :mod:`.properties` — the ``PropertyGroup`` and the per-strip properties.
* :mod:`.operators` — the transcode and viewport recording pipelines.
* :mod:`.ui` — the ``View3D`` sidebar panel.
* :mod:`.utils.ffmpeg` — FFmpeg command construction and process handling.

Only this module talks to Blender's add-on machinery; every submodule exposes
its own ``register()`` / ``unregister()`` pair that is orchestrated here.
"""

bl_info = {
    "name": "Quick Video Encoder",
    "author": "Gemini & User",
    "version": (3, 8, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar(N) > Video Tool",
    "description": (
        "영상 파일 변환과 뷰포트 녹화를 한 패널에서 처리합니다. "
        "구간 분할 익스포트, 편집용 프록시, 수어 다중 익스포트, "
        "아카이브 메타데이터/배지, VRChat 썸네일 지원."
    ),
    "doc_url": "https://github.com/LeeSimYul/blender_video_encoder",
    "tracker_url": "https://github.com/LeeSimYul/blender_video_encoder/issues",
    "license": "SPDX:GPL-3.0-or-later",
    "category": "Render",
}

import logging

from . import operators, properties, ui
from .utils import ffmpeg

#: Submodules reloaded when the add-on is re-run from Blender's text editor.
_SUBMODULES = (ffmpeg, properties, operators, ui)

if "_ADDON_LOADED" in locals():
    import importlib

    for _submodule in _SUBMODULES:
        importlib.reload(_submodule)

_ADDON_LOADED = True

logger = logging.getLogger(__name__)

#: Modules whose ``register()`` runs in this order (``unregister()`` reverses it).
_REGISTRABLE_MODULES = (properties, operators, ui)


def _configure_logging() -> None:
    """Attach a console handler to the add-on's logger namespace.

    Blender does not configure ``logging`` for add-ons, so without this the
    messages emitted by the submodules would never reach the system console.
    """
    package_logger = logging.getLogger(__package__)
    if not package_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("[%(name)s] %(levelname)s: %(message)s")
        )
        package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False


def register() -> None:
    """Register every class and property of the add-on.

    Raises:
        Exception: Re-raised after a partial rollback so Blender reports the
            failure instead of leaving a half-registered add-on behind.
    """
    _configure_logging()
    ffmpeg.clear_caches()

    registered = []
    try:
        for module in _REGISTRABLE_MODULES:
            module.register()
            registered.append(module)
    except Exception:
        logger.exception("애드온 등록에 실패하여 되돌립니다.")
        for module in reversed(registered):
            try:
                module.unregister()
            except Exception:  # noqa: BLE001 - keep unwinding the rest
                logger.exception("롤백 중 %s 해제에 실패했습니다.", module.__name__)
        raise

    logger.info("Quick Video Encoder %s 등록 완료.", bl_info["version"])


def unregister() -> None:
    """Unregister everything :func:`register` added, in reverse order."""
    for module in reversed(_REGISTRABLE_MODULES):
        try:
            module.unregister()
        except Exception:  # noqa: BLE001 - one failure must not block the rest
            logger.exception("%s 해제에 실패했습니다.", module.__name__)

    logger.info("Quick Video Encoder 해제 완료.")


if __name__ == "__main__":
    # Allows running the package directly from Blender's text editor.
    register()
