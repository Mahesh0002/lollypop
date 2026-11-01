#!/usr/bin/env python3
"""Album-art palette extraction and adaptive GTK 3 CSS.

Three screen-level CssProviders, all reloaded in place (no provider pile-up,
no file watching, no gsettings hacks):

  * ``PaletteStyler`` -- album-art CSS scoped to ``#right-panel``.
  * ``PageStyler``    -- wallpaper-accent overrides for the Songs page.
  * ``AmbientStyler`` -- wallpaper-accent rules for the global chrome
    (selection, focus, primary buttons, playback scrubber, active tab and
    the bottom player bar).

The global chrome accent comes from the machine wallpaper via
``accent_provider.WallpaperAccentProvider``.  Album artwork remains the
source for the now-playing panel and the album-page hero, so browsing the
library feels personal while the shell keeps one coherent system accent.

All providers register at ``STYLE_PROVIDER_PRIORITY_USER + 1`` so they
outrank the static ``~/.config/gtk-3.0/gtk.css`` base (loaded at USER
priority).
"""

import colorsys
import os
import traceback

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

try:
    from lollypop.accent_provider import FALLBACK_ACCENT
except Exception:  # pragma: no cover - preview scripts can import standalone
    FALLBACK_ACCENT = (10, 132, 255)

# GTK loads ~/.config/gtk-3.0/gtk.css at STYLE_PROVIDER_PRIORITY_USER (800).
# Registering our in-process providers just above that guarantees the
# track-reactive rules always win over the static neutral base on disk, so the
# panel's scoped/dimmed text and the accent layer are never flattened by a
# generic "label { color: #fff }" in the base file.
_PRIORITY = Gtk.STYLE_PROVIDER_PRIORITY_USER + 1

# Used when there is no artwork, so the panel is never unstyled/unreadable.
NEUTRAL_PALETTE = ((34, 34, 44), (176, 156, 236), (126, 176, 226))

# Panel text is light, so the background must stay dark and accents bright.
_BG_L_MIN, _BG_L_MAX, _BG_S_MAX = 0.11, 0.34, 0.75
_ACC_L_MIN, _ACC_L_MAX, _ACC_S_MIN = 0.52, 0.76, 0.40


# ---------------------------------------------------------------- colour maths

def _c8(value):
    return max(0, min(255, int(round(value))))


def _to_hls(color):
    r, g, b = (c / 255 for c in color)
    return colorsys.rgb_to_hls(r, g, b)


def _from_hls(h, l, s):
    return tuple(_c8(c * 255) for c in colorsys.hls_to_rgb(h, l, s))


def _distance(a, b):
    return sum((int(a[i]) - int(b[i])) ** 2 for i in range(3)) ** 0.5


def _mix(a, b, t):
    """Linear RGB blend: t=1 -> all a, t=0 -> all b (ColorUtils.mix semantics)."""
    return tuple(_c8(t * a[i] + (1.0 - t) * b[i]) for i in range(3))


def _adapt_to_accent(base, seed):
    """Hue and saturation from the seed, lightness from the base colour.

    Mirrors Quickshell's ColorUtils.adaptToAccent(base, seed), which is what
    AdaptedMaterialScheme uses to bend the theme primary toward the artwork.
    """
    _bh, bl, _bs = _to_hls(base)
    sh, _sl, ss = _to_hls(seed)
    return _from_hls(sh, bl, ss)


def _background(color):
    """Keep hue, force a dark, not-neon background (light art would make
    the white panel text unreadable)."""
    h, l, s = _to_hls(color)
    return _from_hls(h, min(max(l, _BG_L_MIN), _BG_L_MAX), min(s, _BG_S_MAX))


def _accent(color):
    """Keep hue, force a bright, visible accent. Greys stay grey."""
    h, l, s = _to_hls(color)
    l = min(max(l, _ACC_L_MIN), _ACC_L_MAX)
    if s >= 0.12:
        s = max(s, _ACC_S_MIN)
    return _from_hls(h, l, s)


def _hue_shift(color, delta):
    h, l, s = _to_hls(color)
    return _from_hls((h + delta) % 1.0, l, s)


def _lighten(color, amount):
    """Blend toward white by `amount` (0..1) — used for hover brightening."""
    return _mix(color, (255, 255, 255), 1.0 - amount)


# ---------------------------------------------------------------- extraction

# Base colours the artwork seed is blended into, mirroring Quickshell's
# Appearance.colors roles (a dark layer0 + a primary accent).
_BASE_BG = (24, 24, 34)
_BASE_ACCENT = (176, 156, 236)


def _average_color(image_path):
    """Average colour of the whole artwork, or None.

    This is what Quickshell's ColorQuantizer with depth=0 returns: a single
    colour computed by averaging the (downscaled) image's pixels.  Safe to
    call from a worker thread (GdkPixbuf file loads do not touch GTK state).
    """
    if not image_path or not os.path.isfile(image_path):
        return None
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(image_path)
    except Exception as exc:
        print(f"[Lollypop Extension] Color extraction failed: {exc}")
        return None

    try:
        # Downscale first: the average is stable and it keeps the pixel walk
        # cheap.  64px matches Quickshell's recommended rescaleSize.
        small = pixbuf.scale_simple(64, 64, GdkPixbuf.InterpType.BILINEAR)
        channels = small.get_n_channels()
        stride = small.get_rowstride()
        pixels = small.get_pixels()
        tr = tg = tb = count = 0
        for y in range(64):
            row = y * stride
            for x in range(64):
                i = row + x * channels
                if channels == 4 and pixels[i + 3] == 0:
                    continue  # transparent pixel, same as ColorQuantizer
                tr += pixels[i]
                tg += pixels[i + 1]
                tb += pixels[i + 2]
                count += 1
        if count == 0:
            return None
        return (tr / count, tg / count, tb / count)
    except Exception:
        return None
    finally:
        try:
            small = None  # release the scaled pixbuf promptly
        except Exception:
            pass


def derive_scheme(seed):
    """Turn the single artwork seed into a (background, accent1, accent2).

    Mirrors AdaptedMaterialScheme: the seed is blended ~50% into each base
    role, so every colour tracks the album art coherently rather than being
    three independently-picked swatches.
    """
    if seed is None:
        return NEUTRAL_PALETTE

    # Background: dark base tinted by the seed, forced dark so light text
    # stays readable (light artwork would otherwise wash the panel out).
    background = _background(_mix(_BASE_BG, seed, 0.5))

    # Accent 1: seed hue + saturation at the base accent's lightness, then
    # blended back toward the seed (adaptToAccent + mix, as in colPrimary).
    adapted = _adapt_to_accent(_BASE_ACCENT, seed)
    accent1 = _accent(_mix(adapted, seed, 0.5))

    # Accent 2: a hue-shifted companion for selection highlights, kept
    # distinct from accent1 so selected rows remain legible.
    accent2 = _accent(_hue_shift(_mix(adapted, seed, 0.5), 0.08))
    if _distance(accent2, accent1) < 40:
        accent2 = _hue_shift(accent1, 0.12)

    return background, accent1, accent2


def extract_palette(image_path):
    """Return (background, accent1, accent2) as RGB tuples, or None.

    Quickshell-style: average the artwork down to one seed colour, then mix
    that seed into fixed base roles (AdaptedMaterialScheme) so the whole
    panel tracks the art.  Safe to call from a worker thread.
    """
    seed = _average_color(image_path)
    if seed is None:
        return None
    return derive_scheme(seed)


# ---------------------------------------------------------------- CSS

def _rgb(color):
    return "rgb({}, {}, {})".format(*(_c8(v) for v in color))


def _rgba(color, alpha):
    return "rgba({}, {}, {}, {:.3f})".format(
        *(_c8(v) for v in color), alpha)


def generate_panel_css(primary, accent1, accent2):
    """GTK 3 CSS for the right panel and the full-page Songs browser."""
    p, a1, a2 = map(_rgb, (primary, accent1, accent2))
    a1_hover = _rgb(_lighten(accent1, 0.14))
    a1_soft = _rgba(accent1, 0.34)
    a1_wash = _rgba(accent1, 0.22)
    a1_faint = _rgba(accent1, 0.10)
    a1_glow = _rgba(accent1, 0.30)
    a2_soft = _rgba(accent2, 0.28)

    return f"""
#right-panel {{
    color: #f7f7fb;
    background-color: {p};
    transition: background-color 350ms ease-in-out;
}}

#right-panel #right-panel-header,
#right-panel #right-panel-lyrics-container,
#right-panel #right-panel-queue-container {{
    color: #f7f7fb;
    background-color: rgba(10, 10, 16, 0.52);
    border-radius: 12px;
    padding: 8px;
}}

/* Metadata has no card background; it sits directly on the panel while
   keeping the same 8px inset as every other section.  No extra padding: the
   8px section gap alone sets the vertical rhythm above/below. */
#right-panel #right-panel-track-info {{
    color: #f7f7fb;
    background-color: transparent;
    background-image: none;
    border-radius: 0;
    padding: 0;
}}

/* Three-step hierarchy: title is the bright focal point, artist sits a step
   below it, album recedes into a quieter grey. */
#right-panel #right-panel-title {{
    color: #ffffff;
    font-size: 15px;
    font-weight: 600;
}}

#right-panel #right-panel-media {{
    background-color: rgba(10, 10, 16, 0.58);
    border-radius: 12px;
}}

/* Material 3 progress bar: transparent box, compact tabular time labels. */
#right-panel #right-panel-progress {{
    background-color: transparent;
}}

#right-panel #right-panel-progress-time {{
    color: rgba(255, 255, 255, 0.62);
    font-size: 11px;
}}

#right-panel #right-panel-artist {{
    color: rgba(255, 255, 255, 0.80);
    font-size: 13px;
}}

#right-panel #right-panel-album {{
    color: rgba(255, 255, 255, 0.52);
    font-size: 12px;
}}

#right-panel #right-panel-heading {{
    color: {a1};
    font-size: 11px;
    font-weight: 700;
}}

#right-panel #right-panel-artwork-frame {{
    background-color: rgba(0, 0, 0, 0.16);
    border-radius: 12px;
}}

#right-panel #right-panel-lyrics {{
    color: rgba(255, 255, 255, 0.92);
    font-size: 14px;
}}

/* Synced lyrics: one row per line.  Inactive lines sit back in the dim
   range; the active line jumps to a bright, larger weight and the sung
   portion is painted by the Cairo/Pango overlay. */
#right-panel #right-panel-lyrics-container .lyrics-row {{
    background-color: transparent;
    border-radius: 8px;
}}

#right-panel #right-panel-lyrics-container .lyrics-row.hover {{
    background-color: rgba(255, 255, 255, 0.07);
}}

#right-panel #right-panel-lyrics-container .lyrics-line {{
    color: rgba(255, 255, 255, 0.38);
    font-size: 14px;
    padding: 4px 0;
}}

#right-panel #right-panel-lyrics-container .lyrics-line.past {{
    color: rgba(255, 255, 255, 0.55);
}}

#right-panel #right-panel-lyrics-container .lyrics-line.active {{
    color: rgba(255, 255, 255, 0.92);
    font-size: 18px;
    font-weight: 600;
    padding: 8px 0;
}}

#right-panel #right-panel-lyrics-container .lyrics-dots {{
    color: rgba(255, 255, 255, 0.45);
    font-size: 16px;
    padding: 8px 0;
}}

#right-panel scrolledwindow,
#right-panel viewport {{
    background-color: transparent;
}}

#right-panel button {{
    color: #ffffff;
    background-image: none;
    background-color: rgba(18, 18, 26, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 8px;
    box-shadow: none;
}}

#right-panel button:hover {{
    background-color: rgba(255, 255, 255, 0.16);
    border-color: {a1};
}}

#right-panel #right-panel-queue-container list,
#right-panel #right-panel-queue-container row {{
    background-color: transparent;
}}

#right-panel #right-panel-queue-container row:hover {{
    background-color: rgba(255, 255, 255, 0.08);
}}

/* NOTE: row-scoped on purpose — a bare `label` selector would out-rank the
   heading rule above and flatten the accent-coloured section headings. */
#right-panel #right-panel-queue-container row label {{
    color: #f5f5f8;
}}

#right-panel #right-panel-queue-container row:selected,
#right-panel #right-panel-queue-container row:active {{
    background-color: {a2};
}}

/* Custom Up Next list.  Rows are compact so the panel can show a useful
   preview of the queue plus the current playback context. */
#right-panel #up-next-list {{
    background-color: transparent;
}}

#right-panel #right-panel-queue-container row.up-next-row {{
    background-color: transparent;
    border-radius: 8px;
    padding: 0;
    min-height: 0;
}}

#right-panel #right-panel-queue-container row.up-next-row:hover {{
    background-color: rgba(255, 255, 255, 0.08);
}}

#right-panel #right-panel-queue-container label.up-next-number {{
    color: rgba(255, 255, 255, 0.38);
    font-size: 11px;
}}

#right-panel #right-panel-queue-container row.queued label.up-next-number {{
    color: {a1};
}}

#right-panel #right-panel-queue-container label.up-next-title {{
    color: #ffffff;
    font-size: 13px;
    font-weight: 600;
}}

#right-panel #right-panel-queue-container label.up-next-artist {{
    color: rgba(255, 255, 255, 0.55);
    font-size: 11px;
}}

#right-panel #right-panel-queue-container label.up-next-duration {{
    color: rgba(255, 255, 255, 0.42);
    font-size: 11px;
}}

#right-panel #right-panel-queue-container label.up-next-badge {{
    color: {a1};
    font-size: 9px;
    font-weight: bold;
}}

#right-panel #right-panel-queue-container label.up-next-empty {{
    color: rgba(255, 255, 255, 0.42);
    font-size: 12px;
}}

#right-panel #right-panel-queue-container button.up-next-remove {{
    color: rgba(255, 255, 255, 0.45);
    background-color: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    padding: 0;
    min-width: 18px;
    min-height: 18px;
    border-radius: 9px;
}}

#right-panel #right-panel-queue-container button.up-next-remove:hover {{
    color: #ffffff;
    background-color: rgba(255, 255, 255, 0.12);
}}

/* Full-page Songs browser, shown in Lollypop's main stack below the
   sidebar "Songs" row.  A tinted hero introduces the library and its
   controls; the track list below stays quiet and scannable. */
#songs-page {{
    background-color: transparent;
}}

#songs-page #songs-page-hero {{
    background-image: linear-gradient(
        135deg, {a1_soft} 0%, {a1_faint} 58%, rgba(255, 255, 255, 0.025) 100%);
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 22px;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.18);
    padding: 18px 20px;
}}

#songs-page #songs-page-cover {{
    background-color: rgba(0, 0, 0, 0.24);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 19px;
    padding: 2px;
    box-shadow: 0 10px 26px rgba(0, 0, 0, 0.34);
}}

#songs-page #songs-page-cover.no-art {{
    background-image: linear-gradient(135deg, {a1_wash}, {a2_soft});
}}

#songs-page #songs-page-cover image {{
    color: rgba(255, 255, 255, 0.82);
}}

#songs-page label.songs-page-eyebrow {{
    color: {a1};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.6px;
}}

#songs-page label.songs-page-title {{
    color: #ffffff;
    font-size: 38px;
    font-weight: 800;
}}

#songs-page label.songs-page-count {{
    color: rgba(255, 255, 255, 0.64);
    font-size: 13px;
    font-weight: 500;
}}

/* Page-level playback controls: a compact Spotify/Apple Music-style row
   under the title.  The primary action is the only accent-filled pill;
   shuffle/repeat light up while active. */
#songs-page #songs-page-actions {{
    background-color: transparent;
}}

#songs-page button.songs-page-control {{
    color: #ffffff;
    background-color: rgba(255, 255, 255, 0.07);
    background-image: none;
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 19px;
    box-shadow: none;
    min-height: 34px;
    padding: 0 14px;
    transition: background-color 160ms ease-in-out,
                border-color 160ms ease-in-out;
}}

#songs-page button.songs-page-control:hover {{
    background-color: rgba(255, 255, 255, 0.14);
    border-color: rgba(255, 255, 255, 0.12);
}}

#songs-page button.songs-page-control:checked,
#songs-page button.songs-page-control.active {{
    background-color: {a1};
    background-image: linear-gradient(135deg, {a1}, {a1_hover});
    border-color: rgba(255, 255, 255, 0.14);
    color: #ffffff;
    box-shadow: 0 4px 14px {a1_glow};
}}

#songs-page button.songs-page-control:checked:hover,
#songs-page button.songs-page-control.active:hover {{
    background-color: {a1_hover};
    background-image: linear-gradient(135deg, {a1_hover}, {a1});
}}

#songs-page button.songs-page-play {{
    background-color: {a1};
    background-image: linear-gradient(135deg, {a1}, {a1_hover});
    border-color: rgba(255, 255, 255, 0.14);
    min-height: 42px;
    padding: 0 20px;
    border-radius: 21px;
    box-shadow: 0 8px 22px {a1_glow};
    font-weight: 700;
}}

#songs-page button.songs-page-play:hover {{
    background-color: {a1_hover};
    background-image: linear-gradient(135deg, {a1_hover}, {a1});
}}

#songs-page button.songs-page-play label {{
    color: #ffffff;
    font-size: 13px;
    font-weight: 700;
}}

#songs-page button.songs-page-transport {{
    color: rgba(255, 255, 255, 0.82);
    background-color: rgba(255, 255, 255, 0.06);
    background-image: none;
    border: 1px solid rgba(255, 255, 255, 0.05);
    box-shadow: none;
    min-width: 36px;
    min-height: 36px;
    padding: 0;
    border-radius: 18px;
}}

#songs-page button.songs-page-transport:hover {{
    color: #ffffff;
    background-color: rgba(255, 255, 255, 0.14);
    border-color: rgba(255, 255, 255, 0.12);
}}

#songs-page separator.songs-page-action-divider {{
    background-color: rgba(255, 255, 255, 0.10);
    min-width: 1px;
    margin-top: 7px;
    margin-bottom: 7px;
}}

#songs-page label.songs-page-control-label {{
    color: inherit;
    font-size: 12px;
    font-weight: 600;
}}

#songs-page #songs-page-column-head {{
    padding-bottom: 7px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}}

#songs-page label.songs-page-column-label {{
    color: rgba(255, 255, 255, 0.34);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.2px;
}}

#songs-page #songs-page-scroller {{
    background-color: transparent;
}}

#songs-page #songs-page-list {{
    background-color: transparent;
    margin: 4px 24px 24px 24px;
}}

#songs-page row.song-row {{
    background-color: transparent;
    border-radius: 10px;
    padding: 0;
    min-height: 0;
}}

#songs-page row.song-row:hover {{
    background-color: rgba(255, 255, 255, 0.065);
}}

#songs-page row.song-row.playing {{
    background-image: linear-gradient(
        90deg, {a1_wash} 0%, {a1_faint} 62%, transparent 100%);
    box-shadow: inset 3px 0 0 {a1};
}}

#songs-page row.song-row.playing label.song-number,
#songs-page row.song-row.playing label.song-title {{
    color: {a1};
}}

#songs-page row.song-row:selected,
#songs-page row.song-row:active {{
    background-color: {a1_faint};
}}

#songs-page #songs-page-list box.song-art-frame {{
    background-color: rgba(255, 255, 255, 0.045);
    border-radius: 9px;
}}

#songs-page #songs-page-list image.song-art-placeholder {{
    color: rgba(255, 255, 255, 0.26);
}}

#songs-page label.song-number {{
    color: rgba(255, 255, 255, 0.32);
    font-size: 11px;
    font-weight: 600;
}}

#songs-page label.song-title {{
    color: #ffffff;
    font-size: 14px;
    font-weight: 600;
}}

#songs-page label.song-artist {{
    color: rgba(255, 255, 255, 0.55);
    font-size: 12px;
}}

#songs-page label.song-album {{
    color: rgba(255, 255, 255, 0.40);
    font-size: 12px;
}}

#songs-page label.song-duration {{
    color: rgba(255, 255, 255, 0.38);
    font-size: 12px;
}}

#songs-page label.song-empty {{
    color: rgba(255, 255, 255, 0.42);
    font-size: 13px;
}}
"""


def generate_global_accent_css(background, accent1=None, accent2=None):
    """GLOBAL wallpaper-accent layer applied in-process.

    Accepts either a single accent colour or the legacy
    ``(background, accent1, accent2)`` palette shape.  The accent is always
    the wallpaper-derived colour; album backgrounds are deliberately not
    painted across the shell.

    The neutral canvas, cards and typography live in the static gtk.css base
    written by ``theme_generator.py``.  This provider only adds the adaptive
    accent points that should follow the wallpaper: selection, focus, primary
    actions, the playback scrubber and the bottom player bar.
    """
    accent = accent1 if accent1 is not None else background
    if accent is None:
        accent = FALLBACK_ACCENT
    a1 = _rgb(accent)
    a1_hover = _rgb(_lighten(accent, 0.16))
    a1_soft = _rgba(accent, 0.20)
    a1_faint = _rgba(accent, 0.10)
    a1_glow = _rgba(accent, 0.28)
    ease_slow = "280ms ease-in-out"  # matches theme_generator's _EASE_SLOW

    return f"""
/* ==========================================================
   Adaptive wallpaper accent layer (Apple Music shell)
   Loaded in-process by AmbientStyler — do not put in gtk.css.
   ========================================================== */

/* Selection uses a soft wash so rows read as selected without drowning
   their text.  The canvas itself stays true black. */
*:selected,
row:selected,
row:selected label,
treeview:selected,
treeview:selected label {{
    background-color: {a1_soft};
    color: #ffffff;
    transition: background-color {ease_slow};
}}

/* Sidebar selection is a little stronger than generic list selection. */
.sidebar row:selected,
.sidebar row:selected:hover {{
    background-color: {a1_soft};
}}

/* Faint accent focus ring on interactive widgets. */
button:focus,
scale:focus,
entry:focus {{
    outline-color: {a1};
}}

/* Primary actions: the loud call-to-action, brightening on hover. */
.suggested-action {{
    background-color: {a1};
    background-image: none;
    color: #ffffff;
    border: none;
    transition: background-color {ease_slow}, box-shadow {ease_slow};
}}

.suggested-action:hover {{
    background-color: {a1_hover};
}}

.banner-button-big,
.banner-button {{
    background-color: {a1};
    background-image: none;
    color: #ffffff;
    border: none;
    transition: background-color {ease_slow};
}}

.banner-button-big:hover,
.banner-button:hover {{
    background-color: {a1_hover};
}}

/* Playback scrubbers: the played portion follows the wallpaper accent. */
.progress-bottom scale highlight,
headerbar scale highlight {{
    background-color: {a1};
    transition: background-color {ease_slow};
}}

/* Bottom player bar: accent transport play button and active toggles. */
#apple-player-bar button.apple-player-play {{
    background-color: {a1};
    color: #ffffff;
    box-shadow: 0 4px 16px {a1_glow};
}}

#apple-player-bar button.apple-player-play:hover {{
    background-color: {a1_hover};
    color: #ffffff;
}}

#apple-player-bar button.apple-player-play image {{
    color: #ffffff;
}}

#apple-player-bar scale highlight {{
    background-color: {a1};
}}

#apple-player-bar button:checked,
#apple-player-bar button.active {{
    background-color: {a1_faint};
    color: {a1};
}}

/* Queued-track marker tint. */
row.queued {{
    color: {a1};
}}

/* Keep the Songs page's accent in sync with the wallpaper.  PageStyler
   emits the full override set; this is the small fallback for previews. */
#songs-page label.songs-page-eyebrow {{
    color: {a1};
}}
"""


def generate_page_css(accent):
    """Wallpaper-accent overrides for the full-page Songs browser.

    The panel palette still supplies the Songs page's neutral structure; this
    provider owns only the accent points so the library page follows the
    desktop wallpaper rather than the currently playing album.
    """
    if accent is None:
        accent = FALLBACK_ACCENT
    a1 = _rgb(accent)
    a1_hover = _rgb(_lighten(accent, 0.14))
    a1_soft = _rgba(accent, 0.30)
    a1_wash = _rgba(accent, 0.18)
    a1_faint = _rgba(accent, 0.08)
    a1_glow = _rgba(accent, 0.28)

    return f"""
#songs-page #songs-page-hero {{
    background-image: linear-gradient(
        135deg, {a1_soft} 0%, {a1_faint} 58%,
        rgba(255, 255, 255, 0.018) 100%);
}}

#songs-page #songs-page-cover.no-art {{
    background-image: linear-gradient(135deg, {a1_wash}, {a1_faint});
}}

#songs-page label.songs-page-eyebrow {{
    color: {a1};
}}

#songs-page button.songs-page-control.active,
#songs-page button.songs-page-control:checked {{
    background-color: {a1};
    background-image: linear-gradient(135deg, {a1}, {a1_hover});
    border-color: rgba(255, 255, 255, 0.14);
    color: #ffffff;
    box-shadow: 0 4px 14px {a1_glow};
}}

#songs-page button.songs-page-control.active:hover,
#songs-page button.songs-page-control:checked:hover {{
    background-color: {a1_hover};
    background-image: linear-gradient(135deg, {a1_hover}, {a1});
}}

#songs-page button.songs-page-play {{
    background-color: {a1};
    background-image: linear-gradient(135deg, {a1}, {a1_hover});
    border-color: rgba(255, 255, 255, 0.14);
    box-shadow: 0 8px 22px {a1_glow};
}}

#songs-page button.songs-page-play:hover {{
    background-color: {a1_hover};
    background-image: linear-gradient(135deg, {a1_hover}, {a1});
}}

#songs-page row.song-row.playing {{
    background-image: linear-gradient(
        90deg, {a1_wash} 0%, {a1_faint} 62%, transparent 100%);
    box-shadow: inset 3px 0 0 {a1};
}}

#songs-page row.song-row.playing label.song-number,
#songs-page row.song-row.playing label.song-title {{
    color: {a1};
}}

#songs-page row.song-row:selected,
#songs-page row.song-row:active {{
    background-color: {a1_faint};
}}
"""


class _ScreenStyler:
    """Owns a single screen-level CssProvider that can be reloaded in place.

    Both PaletteStyler and AmbientStyler are instances of this; subclasses
    override ``_render`` to produce the CSS text for a given palette.
    """

    def _render(self, palette):
        raise NotImplementedError

    def __init__(self):
        self._provider = None
        self._screen = None

    def apply(self, palette=None):
        """Apply a palette (None -> neutral). Main thread only."""
        return self.apply_css(self._render(palette or NEUTRAL_PALETTE))

    def apply_css(self, css):
        """Load raw CSS into this provider, creating it on first use."""
        try:
            if self._provider is None:
                screen = Gdk.Screen.get_default()
                if screen is None:
                    return False
                self._provider = Gtk.CssProvider()
                Gtk.StyleContext.add_provider_for_screen(
                    screen, self._provider, _PRIORITY
                )
                self._screen = screen
            self._provider.load_from_data(css.encode("utf-8"))
            return True
        except GLib.Error as exc:
            print(f"[Lollypop Extension] CSS rejected by GTK: {exc.message}")
        except Exception as exc:
            print(f"[Lollypop Extension] CSS application failed: {exc}")
            traceback.print_exc()
        return False

    def remove(self):
        if self._provider is not None and self._screen is not None:
            try:
                Gtk.StyleContext.remove_provider_for_screen(
                    self._screen, self._provider
                )
            except Exception:
                pass
        self._provider = None
        self._screen = None


class PaletteStyler(_ScreenStyler):
    """Screen-level CssProvider for the right panel (#right-panel scope)."""

    def _render(self, palette):
        return generate_panel_css(*palette)


class PageStyler(_ScreenStyler):
    """Wallpaper-accent overrides for the Songs page.

    Registered after ``PaletteStyler`` so its scoped accent rules win on the
    Songs page while the panel keeps the album-art palette.
    """

    def apply_accent(self, accent):
        return self.apply_css(generate_page_css(accent))


class AmbientStyler(_ScreenStyler):
    """Screen-level CssProvider for the global wallpaper accent layer.

    Applies only the handful of rules that should track the machine wallpaper
    across the whole app.  The neutral window/card base lives in gtk.css
    (theme_generator.py); this provider tints selection, focus, primary
    buttons, the playback scrubber and the bottom player bar.  Reloaded in
    place when the wallpaper changes.
    """

    def _render(self, palette):
        # Legacy preview path: a 3-tuple palette uses accent1 (the middle
        # colour), not the album background.
        return generate_global_accent_css(*palette)

    def apply_accent(self, accent):
        return self.apply_css(generate_global_accent_css(accent))


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python color_extractor.py /path/to/cover-image")
        raise SystemExit(2)

    result = extract_palette(sys.argv[1])
    if result is None:
        print("Could not extract palette; check the image path.")
        raise SystemExit(1)

    for name, value in zip(("Background", "Accent 1", "Accent 2"), result):
        print(f"{name}: {value}")
    print()
    print(generate_panel_css(*result))
    print()
    print(generate_global_accent_css(result[1]))
    print()
    print(generate_page_css(result[1]))