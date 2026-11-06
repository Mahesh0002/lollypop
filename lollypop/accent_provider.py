#!/usr/bin/env python3
"""Wallpaper-driven accent provider for the Apple Music-style shell.

The global chrome accent is intentionally *not* taken from album art.  It is
resolved from the current Hyprland/Quickshell wallpaper, with a deterministic
fallback chain:

    wallpaper pixels -> Matugen palette -> Wallust palette -> Apple blue

Album artwork still supplies the album-page hero and the right-panel palette;
this module only owns the app-wide accent (selection, focus, primary buttons,
the playback scrubber, tab underline and the bottom player bar).

The expensive image work runs on a single worker thread.  A tiny GLib timer
watches the wallpaper path and generated palette files; when any of them
changes the accent is recomputed and delivered on the GTK main thread.
"""

from __future__ import annotations

import colorsys
import json
import os
import re
import traceback
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

try:
    from PIL import Image
except Exception:  # pragma: no cover - Pillow is a project dependency
    Image = None

# Import design tokens
from design_tokens import FALLBACK_ACCENT

PREFIX = "[Lollypop Extension]"

WALLPAPER_PATH_FILE = os.path.expanduser(
    "~/.local/state/quickshell/user/generated/wallpaper/path.txt"
)
MATUGEN_COLORS = os.path.expanduser(
    "~/.local/state/quickshell/user/generated/colors.json"
)
WALLUST_FILES = (
    os.path.expanduser("~/.config/kitty/kitty-themes/01-Wallust.conf"),
    os.path.expanduser("~/.config/rofi/wallust/colors-rofi.rasi"),
)
WALLUST_TOML = os.path.expanduser("~/.config/wallust/wallust.toml")

_HEX_RE = re.compile(r"#([0-9a-fA-F]{6})\b")
_RGB_RE = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", re.I
)

# Matugen keys worth considering, in preference order.  `primary` is first,
# but a grayscale Material palette (a common Matugen result) is skipped.
_MATUGEN_KEYS = (
    "primary",
    "secondary",
    "tertiary",
    "primary_container",
    "secondary_container",
    "tertiary_container",
    "surface_tint",
    "inverse_primary",
)


# ---------------------------------------------------------------- colour utils

def _c8(value):
    return max(0, min(255, int(round(value))))


def _hex_to_rgb(value):
    value = value.lstrip("#")
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _to_hls(color):
    r, g, b = (c / 255 for c in color)
    return colorsys.rgb_to_hls(r, g, b)


def _from_hls(h, l, s):
    return tuple(_c8(c * 255) for c in colorsys.hls_to_rgb(h, l, s))


def _is_usable(color, min_saturation=0.18):
    if color is None:
        return False
    _h, l, s = _to_hls(color)
    # Avoid near-black/white pixels: they produce muddy or invisible accents.
    return 0.16 <= l <= 0.88 and s >= min_saturation


def normalize_accent(color):
    """Make an extracted colour readable as an Apple-style accent.

    Hues are preserved, saturation is lifted enough to read as an accent, and
    lightness is constrained so white button text stays legible on dark UI.
    Greys are rejected rather than turned into a fake hue.
    """
    if color is None:
        return None
    h, l, s = _to_hls(color)
    if s < 0.16:
        return None
    l = min(max(l, 0.50), 0.66)
    s = min(max(s, 0.52), 0.86)
    return _from_hls(h, l, s)


def _score(color, count=1):
    """Prefer frequent, saturated, mid-lightness pixels."""
    _h, l, s = _to_hls(color)
    if not (0.20 <= l <= 0.82 and s >= 0.20):
        return 0.0
    # Mid-tones are more useful than blown-out highlights or shadows.
    lightness_weight = 1.0 - abs(l - 0.52) * 1.6
    return max(0.0, count) * (s ** 1.7) * max(0.0, lightness_weight)


# ---------------------------------------------------------------- sources

def current_wallpaper_path():
    """Best-effort current wallpaper path used by the Hyprland/Quickshell dotfiles."""
    try:
        with open(WALLPAPER_PATH_FILE, "r", encoding="utf-8") as handle:
            path = handle.read().strip()
        if path:
            path = os.path.expanduser(path)
            if os.path.isfile(path):
                return path
    except Exception:
        pass

    # Fallbacks for machines without Quickshell's state file.
    candidates = (
        os.path.expanduser("~/.config/hypr/wallpaper_effects/.wallpaper_current"),
        os.path.expanduser("~/.cache/swww/current"),
        os.path.expanduser("~/.config/hypr/current_wallpaper"),
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _extract_from_image(path):
    """Quantize a downscaled wallpaper and return the most accent-like colour."""
    if Image is None or not path or not os.path.isfile(path):
        return None
    try:
        image = Image.open(path)
        image = image.convert("RGB")
        image.thumbnail((96, 96), Image.Resampling.LANCZOS)
        quantized = image.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette()
        counts = quantized.getcolors() or []
        best = None
        best_score = 0.0
        for count, index in counts:
            rgb = tuple(palette[index * 3:index * 3 + 3])
            score = _score(rgb, count)
            if score > best_score:
                best_score = score
                best = rgb
        return best
    except Exception as exc:
        print(f"{PREFIX} Wallpaper accent extraction failed: {exc}")
        return None


def _extract_from_matugen():
    try:
        with open(MATUGEN_COLORS, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None

    best = None
    best_score = 0.0
    for key in _MATUGEN_KEYS:
        value = data.get(key)
        if not isinstance(value, str):
            continue
        rgb = _hex_to_rgb(value)
        if not _is_usable(rgb):
            continue
        score = _score(rgb)
        if score > best_score:
            best_score = score
            best = rgb
    return best


def _extract_from_wallust():
    colors = []
    for path in WALLUST_FILES:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except Exception:
            continue
        for match in _HEX_RE.finditer(text):
            rgb = _hex_to_rgb(match.group(0))
            if rgb:
                colors.append(rgb)
        for match in _RGB_RE.finditer(text):
            try:
                rgb = tuple(max(0, min(255, int(v))) for v in match.groups())
            except ValueError:
                continue
            colors.append(rgb)

    best = None
    best_score = 0.0
    for rgb in colors:
        if not _is_usable(rgb, min_saturation=0.20):
            continue
        score = _score(rgb)
        if score > best_score:
            best_score = score
            best = rgb
    return best


def resolve_accent():
    """Resolve the current wallpaper accent. Safe to call from a worker thread."""
    for source in (
        lambda: _extract_from_image(current_wallpaper_path()),
        _extract_from_matugen,
        _extract_from_wallust,
    ):
        try:
            candidate = source()
        except Exception:
            candidate = None
        normalized = normalize_accent(candidate)
        if normalized is not None:
            return normalized
    return FALLBACK_ACCENT


def _source_signature():
    """Cheap change signature for the wallpaper and generated palettes."""
    parts = []
    for path in (
        WALLPAPER_PATH_FILE,
        current_wallpaper_path(),
        MATUGEN_COLORS,
        WALLUST_TOML,
        *WALLUST_FILES,
    ):
        if not path:
            continue
        try:
            stat = os.stat(path)
            parts.append((path, int(stat.st_mtime_ns), stat.st_size))
        except Exception:
            parts.append((path, 0, 0))
    return tuple(parts)


# ---------------------------------------------------------------- provider

class WallpaperAccentProvider:
    """Polling wallpaper accent provider with a worker-thread extraction.

    GTK widgets must only be touched on the main thread; the callback is always
    invoked through ``GLib.idle_add``.  Polling is deliberately simple and
    robust across Hyprland wallpaper tools (swww, hyprpaper, Quickshell).
    """

    def __init__(self, callback=None, interval=4):
        self._callback = callback
        self._interval = max(2, int(interval))
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="wallpaper-accent"
        )
        self._timer_id = 0
        self._running = False
        self._signature = None
        self._accent = FALLBACK_ACCENT
        self._pending = False
        self._generation = 0

    @property
    def accent(self):
        return self._accent

    def start(self):
        if self._running:
            return
        self._running = True
        self._signature = _source_signature()
        self.refresh(force=True)
        self._timer_id = GLib.timeout_add_seconds(
            self._interval, self._check_sources
        )

    def stop(self):
        self._running = False
        if self._timer_id:
            try:
                GLib.source_remove(self._timer_id)
            except Exception:
                pass
            self._timer_id = 0
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # Python < 3.9 compatibility
            self._executor.shutdown(wait=False)
        except Exception:
            pass

    def refresh(self, force=False):
        if not self._running or self._pending:
            return
        self._pending = True
        self._generation += 1
        generation = self._generation

        def work():
            accent = resolve_accent()
            GLib.idle_add(self._deliver, accent, generation, force)
            return None

        try:
            self._executor.submit(work)
        except Exception:
            self._pending = False
            traceback.print_exc()

    def _deliver(self, accent, generation, force):
        self._pending = False
        if not self._running or generation != self._generation:
            return False
        accent = normalize_accent(accent) or FALLBACK_ACCENT
        if force or accent != self._accent:
            self._accent = accent
            if self._callback is not None:
                try:
                    self._callback(accent)
                except Exception:
                    print(f"{PREFIX} Wallpaper accent callback failed:")
                    traceback.print_exc()
        return False

    def _check_sources(self):
        if not self._running:
            return False
        signature = _source_signature()
        if signature != self._signature:
            self._signature = signature
            self.refresh()
        return True


if __name__ == "__main__":
    print("wallpaper:", current_wallpaper_path())
    print("accent:", resolve_accent())
