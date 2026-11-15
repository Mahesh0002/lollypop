# -*- coding: utf-8 -*-
"""Lollypop music player package."""

# Import C++ acceleration module if available
try:
    from . import lollypop_ext
except ImportError:
    lollypop_ext = None