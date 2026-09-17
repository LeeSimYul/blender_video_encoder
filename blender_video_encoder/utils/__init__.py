# SPDX-FileCopyrightText: 2026 Simulacre
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Helper subpackage of the Quick Video Encoder add-on.

Modules in here must stay free of add-on state so that they can be reused and
unit tested in isolation. :mod:`.ffmpeg` in particular imports ``bpy`` lazily
and can therefore be loaded by a plain CPython interpreter.
"""
