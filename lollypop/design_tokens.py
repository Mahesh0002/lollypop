#!/usr/bin/env python3
"""Design tokens for Lollypop Apple Music UI.

Single source of truth for spacing, typography, color, elevation, motion, and sizing.
All UI modules should import from here instead of hardcoding values.
"""

from dataclasses import dataclass
from typing import Tuple


def _c8(value):
    """Clamp value to 0-255 integer."""
    return max(0, min(255, int(round(value))))


# ============================================================
# SPACING — 4px base unit scale
# ============================================================
SPACING = {
    "0": 0,
    "1": 4,
    "2": 8,
    "3": 12,
    "4": 16,
    "5": 20,
    "6": 24,
    "8": 32,
    "10": 40,
    "12": 48,
    "16": 64,
}

def sp(*keys: str) -> int:
    """Sum multiple spacing tokens."""
    return sum(SPACING[k] for k in keys)


# ============================================================
# BORDER RADIUS
# ============================================================
RADIUS = {
    "none": 0,
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 24,
    "pill": 9999,
    "circle": 9999,
}


# ============================================================
# TYPOGRAPHY — Semantic type scale
# ============================================================
@dataclass(frozen=True)
class TypeToken:
    size: int
    weight: int
    line_height: float = 1.4
    letter_spacing: str = "0"

TYPE = {
    "caption": TypeToken(11, 400, 1.3, "0"),
    "body-sm": TypeToken(12, 400, 1.4, "0"),
    "body": TypeToken(13, 400, 1.4, "0"),
    "body-emphasis": TypeToken(13, 600, 1.4, "0"),
    "subhead": TypeToken(14, 600, 1.35, "0"),
    "title-sm": TypeToken(15, 600, 1.3, "0"),
    "title": TypeToken(18, 600, 1.3, "0"),
    "title-lg": TypeToken(21, 500, 1.25, "0"),
    "headline": TypeToken(34, 800, 1.15, "-0.02em"),
    "headline-lg": TypeToken(40, 800, 1.1, "-0.02em"),
    "display": TypeToken(48, 800, 1.05, "-0.02em"),
}

# Opacity tokens for text hierarchy
TEXT_OPACITY = {
    "disabled": 0.12,
    "subtle": 0.32,
    "muted": 0.48,
    "medium": 0.58,
    "strong": 0.72,
    "emphasis": 0.86,
    "high": 0.92,
    "max": 1.0,
}

# Tracking (letter-spacing) tokens
TRACKING = {
    "tight": "-0.02em",
    "normal": "0",
    "wide": "0.08em",
    "wider": "0.16em",
}


# ============================================================
# COLOR — Semantic color roles (RGB 0-255)
# ============================================================
# Base neutrals (true black canvas, graphite, elevated cards)
NEUTRAL = {
    "canvas": (0, 0, 0),           # #000000 - window background
    "sidebar": (28, 28, 30),       # #1C1C1E - sidebar background
    "card": (34, 34, 44),          # #22222C - elevated card
    "card-hover": (44, 44, 56),    # #2C2C38 - card hover
    "border": (255, 255, 255),     # white for hairlines (with opacity)
    "divider": (255, 255, 255),    # white for dividers (with opacity)
}

# Text colors (on dark backgrounds)
TEXT = {
    "primary": (245, 245, 247),    # #F5F5F7
    "secondary": (235, 235, 245),  # #EBEBF5
    "tertiary": (199, 199, 204),   # #C7C7CC
    "quaternary": (142, 142, 147), # #8E8E93
    "disabled": (88, 88, 96),      # #585860
    "inverse": (10, 10, 12),       # #0A0A0C - on light accents
}

# Accent color (resolved at runtime from wallpaper/album art)
# FALLBACK_ACCENT = (10, 132, 255)  # Apple blue #0A84FF


# ============================================================
# ELEVATION — Box shadows
# ============================================================
ELEVATION = {
    "0": "none",
    "1": "0 2px 8px rgba(0, 0, 0, 0.15)",      # cards
    "2": "0 8px 24px rgba(0, 0, 0, 0.20)",     # panels
    "3": "0 16px 40px rgba(0, 0, 0, 0.25)",    # modals/banners
    "4": "0 24px 56px rgba(0, 0, 0, 0.30)",    # fullscreen hero
    "inner": "inset 0 2px 4px rgba(0, 0, 0, 0.2)",  # pressed state
}

# Hairline border (1px at device pixel ratio)
HAIRLINE = "1px solid rgba(255, 255, 255, 0.08)"


# ============================================================
# MOTION — Easing curves and durations
# ============================================================
EASE = {
    "fast": "120ms ease-in-out",
    "normal": "160ms ease-in-out",
    "slow": "280ms ease-in-out",
    "slower": "400ms ease-in-out",
    "spring": "cubic-bezier(0.34, 1.56, 0.64, 1)",  # overshoot spring
    "damped": "cubic-bezier(0.4, 0.0, 0.2, 1)",     # critical damping
}

# Motion tokens for specific interactions
MOTION = {
    "panel-reveal": "280ms cubic-bezier(0.4, 0.0, 0.2, 1)",
    "panel-dismiss": "200ms cubic-bezier(0.4, 0.0, 1, 1)",
    "track-crossfade": "400ms ease-in-out",
    "accent-shift": "350ms ease-in-out",
    "artwork-crossfade": "400ms ease-in-out",
    "button-press": "50ms cubic-bezier(0.4, 0.0, 1, 1)",
    "visualizer-frame": "16ms linear",  # 60fps
    "lyrics-scroll": "320ms cubic-bezier(0.34, 1.56, 0.64, 1)",
    "queue-reorder": "200ms ease-in-out",
    "seek-scrub": "16ms linear",
}


# ============================================================
# SIZING — Component dimensions
# ============================================================
SIZE = {
    # Button heights
    "btn-sm": 28,
    "btn-md": 32,
    "btn-lg": 40,
    "btn-xl": 48,
    "btn-transport": 36,
    "btn-play": 44,

    # Icon sizes (match button sizes)
    "icon-xs": 16,
    "icon-sm": 20,
    "icon-md": 24,
    "icon-lg": 32,
    "icon-xl": 40,
    "icon-2xl": 48,
    "icon-3xl": 64,

    # Track/row heights
    "row-compact": 40,
    "row-comfortable": 48,
    "sidebar-row": 32,

    # Progress/slider track
    "slider-track": 4,
    "slider-track-hover": 6,
    "slider-thumb": 10,
    "slider-thumb-hover": 12,

    # Panel
    "panel-width": 320,
    "panel-gap": 8,

    # Sidebar
    "sidebar-min-width": 218,

    # Headerbar
    "headerbar-height": 50,

    # Player bar
    "player-bar-height": 62,
    "player-bar-compact": 56,

    # Fullscreen
    "fs-art-max": 560,
    "fs-left-min": 360,
    "fs-left-max": 1100,
    "fs-holder-max": 980,
}


# ============================================================
# Z-INDEX — Layering
# ============================================================
Z_INDEX = {
    "base": 0,
    "dropdown": 100,
    "sticky": 200,
    "overlay": 300,
    "modal": 400,
    "popover": 500,
    "tooltip": 600,
    "toast": 700,
}


# ============================================================
# BREAKPOINTS — Responsive thresholds
# ============================================================
BREAKPOINT = {
    "compact": 820,      # hide left panel labels
    "very-compact": 620, # hide extra buttons
    "fullscreen": 1500,  # compact fullscreen layout
    "mobile": 360,       # phone size
}


# ============================================================
# CSS PRIORITY — GTK StyleProvider priorities
# ============================================================
CSS_PRIORITY = {
    "user": 800,           # GTK loads ~/.config/gtk-3.0/gtk.css here
    "extension-base": 801, # theme_generator static base
    "extension-panel": 802, # PaletteStyler (album art palette)
    "extension-page": 803,  # PageStyler (wallpaper accent for Songs page)
    "extension-ambient": 804, # AmbientStyler (wallpaper accent global)
    "application": 600,    # GTK_STYLE_PROVIDER_PRIORITY_APPLICATION
    "fallback": 400,       # GTK_STYLE_PROVIDER_PRIORITY_FALLBACK
}


# ============================================================
# FONT STACK
# ============================================================
FONT_STACK = (
    '"Google Sans Flex", '
    '"Google Sans", '
    '"SF Pro Display", '
    '"SF Pro Text", '
    '"Inter Variable", '
    '"Inter", '
    '"Noto Sans", '
    'sans-serif'
)

# Monospace for numeric display
MONO_STACK = (
    '"SF Mono", '
    '"JetBrains Mono", '
    '"Fira Code", '
    '"Noto Sans Mono", '
    'monospace'
)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _c8(value):
    """Clamp value to 0-255 integer."""
    return max(0, min(255, int(round(value))))


def rgba(color: Tuple[int, int, int], alpha: float) -> str:
    """Format RGB tuple as rgba() CSS string."""
    r, g, b = color
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


def rgb(color: Tuple[int, int, int]) -> str:
    """Format RGB tuple as rgb() CSS string."""
    r, g, b = color
    return f"rgb({r}, {g}, {b})"


def hex_color(color: Tuple[int, int, int]) -> str:
    """Format RGB tuple as #rrggbb CSS string."""
    r, g, b = color
    return f"#{r:02x}{g:02x}{b:02x}"


def css_var(name: str, fallback: str = "") -> str:
    """Reference a CSS custom property (for future GTK4 migration)."""
    return f"var(--{name}{', ' + fallback if fallback else ''})"


# Font feature settings for tabular numerals
FONT_FEATURE_TNUM = "'tnum'"


if __name__ == "__main__":
    # Quick verification
    print("Design tokens loaded successfully")
    print(f"Spacing scale: {SPACING}")
    print(f"Type tokens: {list(TYPE.keys())}")
    print(f"Elevation levels: {list(ELEVATION.keys())}")
    print(f"CSS priorities: {CSS_PRIORITY}")