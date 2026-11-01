#!/usr/bin/env python3
"""GTK3 static base theme generator — Apple Music desktop edition.

Writes the colour-free, dark-only GTK3 base to ``~/.config/gtk-3.0/gtk.css``.
The global accent is applied in-process from the current wallpaper by
``accent_provider.WallpaperAccentProvider`` and ``color_extractor.AmbientStyler``;
album art still supplies the right-panel palette and album-page hero.

Design language:
    * True black canvas, graphite sidebar, elevated cards.
    * SF Pro-like hierarchy using Google Sans Flex when installed.
    * Pill primary actions, circular transport controls, thin hairline borders.
    * Dense but calm library rows; no light mode and no web/account chrome.
    * GTK3-safe CSS only: no custom properties, transforms, filters or
      backdrop blur.  Blurred artwork is produced as a pixbuf by Lollypop.

Output:
    ~/.config/gtk-3.0/gtk.css
"""

from pathlib import Path

# Marks the generated static base so scripts/doctor.py can distinguish a
# healthy generated theme from a stale/hand-written file.  Accent rules are
# deliberately not written to disk.
BASE_MARKER = "/* --- LOLLYPOP EXTENSION STATIC BASE (generated) --- */"

_EASE = "160ms ease-in-out"
_EASE_SLOW = "280ms ease-in-out"


def generate_global_base_css() -> str:
    """Return the dark-only Apple Music-inspired GTK3 base stylesheet."""
    return f"""{BASE_MARKER}
/* ==========================================================
   Lollypop static base — Apple Music desktop (generated)
   Geometry + typography + motion + neutral greys only.
   Global accent: in-process wallpaper accent layer.
   ========================================================== */

/* ---------------------------------------------------- 1. Canvas */
* {{
    font-family: "Google Sans Flex", "Google Sans", "SF Pro Display",
                 "SF Pro Text", "Inter", "Noto Sans", sans-serif;
    -gtk-icon-style: symbolic;
}}

window,
.background {{
    background-color: #000000;
    color: #f5f5f7;
}}

list,
treeview,
viewport,
scrolledwindow,
stack {{
    background-color: transparent;
}}

separator {{
    background-color: rgba(255, 255, 255, 0.08);
    min-width: 1px;
    min-height: 1px;
}}

/* ---------------------------------------------------- 2. Type */
label {{
    color: #f5f5f7;
    font-weight: 400;
}}

label.bold,
.bold {{
    font-weight: 700;
}}

label.large,
.large {{
    font-size: 1.15em;
    font-weight: 700;
}}

.dim-label,
label.dim-label {{
    color: rgba(235, 235, 245, 0.58);
}}

label.small,
.small {{
    font-size: 0.86em;
}}

/* ---------------------------------------------------- 3. Header bar */
headerbar,
headerbar:backdrop {{
    background-color: rgba(0, 0, 0, 0.94);
    background-image: none;
    color: #f5f5f7;
    border: none;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    box-shadow: none;
    min-height: 50px;
    padding: 4px 10px;
}}

headerbar button,
headerbar button:hover,
headerbar button:active,
headerbar button:checked {{
    background-image: none;
    border: none;
    box-shadow: none;
    color: rgba(245, 245, 247, 0.86);
    border-radius: 10px;
    min-width: 30px;
    min-height: 30px;
    padding: 0;
    transition: background-color {_EASE}, color {_EASE};
}}

headerbar button:hover {{
    background-color: rgba(255, 255, 255, 0.09);
    color: #ffffff;
}}

headerbar button:active,
headerbar button:checked {{
    background-color: rgba(255, 255, 255, 0.14);
    color: #ffffff;
}}

headerbar .title {{
    font-weight: 700;
    font-size: 14px;
}}

headerbar .subtitle {{
    color: rgba(235, 235, 245, 0.55);
    font-size: 11px;
}}

/* Lollypop's custom toolbar widgets should not draw extra plates. */
headerbar box,
headerbar .linked {{
    background-color: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
}}

/* ---------------------------------------------------- 4. Sidebar */
.sidebar,
.sidebar list,
.sidebar scrolledwindow,
.sidebar viewport {{
    background-color: #1c1c1e;
    background-image: none;
    border: none;
    box-shadow: none;
    color: #f5f5f7;
}}

.sidebar {{
    min-width: 218px;
    border-right: 1px solid rgba(255, 255, 255, 0.06);
}}

.sidebar row,
.sidebar row.row,
.sidebar row.row-big {{
    background-color: transparent;
    background-image: none;
    border: none;
    border-radius: 9px;
    margin: 1px 8px;
    padding: 5px 9px;
    min-height: 30px;
    transition: background-color {_EASE}, color {_EASE};
}}

.sidebar row:hover {{
    background-color: rgba(255, 255, 255, 0.065);
}}

.sidebar row:selected,
.sidebar row:selected:hover {{
    background-color: rgba(255, 255, 255, 0.11);
    color: #ffffff;
}}

.sidebar row label {{
    color: rgba(245, 245, 247, 0.86);
    font-size: 13px;
    font-weight: 500;
}}

.sidebar row:selected label {{
    color: #ffffff;
    font-weight: 600;
}}

.sidebar row image {{
    color: rgba(245, 245, 247, 0.62);
}}

.sidebar row:selected image {{
    color: #ffffff;
}}

.sidebar separator {{
    background-color: transparent;
    min-height: 0;
    margin: 0 16px;
}}

/* Rounded artist thumbnails in the sidebar. */
.circle-icon,
.circle-icon-large,
.sidebar .circle-icon {{
    border-radius: 999px;
}}

/* ---------------------------------------------------- 5. Buttons */
button {{
    background-color: rgba(255, 255, 255, 0.07);
    background-image: none;
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 9px;
    box-shadow: none;
    padding: 5px 12px;
    min-height: 28px;
    transition: background-color {_EASE}, color {_EASE},
                border-color {_EASE}, box-shadow {_EASE};
}}

button:hover {{
    background-color: rgba(255, 255, 255, 0.12);
    border-color: rgba(255, 255, 255, 0.12);
}}

button:active,
button:checked {{
    background-color: rgba(255, 255, 255, 0.16);
    border-color: rgba(255, 255, 255, 0.14);
}}

button:disabled {{
    color: rgba(235, 235, 245, 0.30);
    background-color: rgba(255, 255, 255, 0.035);
}}

button.image-button,
button.flat {{
    background-color: transparent;
    border-color: transparent;
    box-shadow: none;
}}

button.image-button:hover,
button.flat:hover {{
    background-color: rgba(255, 255, 255, 0.10);
}}

.linked > button,
.linked > button:hover {{
    background-color: transparent;
    border: none;
    border-radius: 999px;
    box-shadow: none;
    min-width: 32px;
    min-height: 32px;
    padding: 0;
}}

.linked > button:hover {{
    background-color: rgba(255, 255, 255, 0.09);
}}

/* Primary action: pill.  The accent fill comes from AmbientStyler. */
.suggested-action {{
    background-color: rgba(255, 255, 255, 0.92);
    background-image: none;
    color: #0b0b0c;
    border: none;
    border-radius: 999px;
    padding: 8px 18px;
    font-weight: 700;
    box-shadow: none;
}}

.suggested-action label {{
    color: inherit;
    font-weight: 700;
}}

.suggested-action:hover {{
    background-color: #ffffff;
}}

/* Circular transport and icon hits. */
.transport-button,
button.transport-button {{
    background-color: transparent;
    border: none;
    border-radius: 999px;
    min-width: 34px;
    min-height: 34px;
    padding: 0;
}}

/* ---------------------------------------------------- 6. Entries */
entry,
searchentry {{
    background-color: rgba(255, 255, 255, 0.07);
    background-image: none;
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 9px;
    box-shadow: none;
    padding: 6px 10px;
    min-height: 28px;
    transition: background-color {_EASE}, border-color {_EASE};
}}

entry:focus,
searchentry:focus {{
    background-color: rgba(255, 255, 255, 0.10);
    border-color: rgba(255, 255, 255, 0.22);
}}

entry selection,
entry selection:focus {{
    background-color: rgba(255, 255, 255, 0.22);
    color: #ffffff;
}}

/* ---------------------------------------------------- 7. Cards */
.cover-frame,
.small-cover-frame {{
    background-color: rgba(255, 255, 255, 0.035);
    background-image: none;
    border-radius: 10px;
    box-shadow: 0 8px 22px rgba(0, 0, 0, 0.28);
    padding: 0;
    transition: background-color {_EASE}, box-shadow {_EASE};
}}

.cover-frame image,
.small-cover-frame image {{
    border-radius: 10px;
}}

flowboxchild {{
    background-color: transparent;
    border-radius: 12px;
    padding: 3px;
    transition: background-color {_EASE};
}}

flowboxchild:hover {{
    background-color: rgba(255, 255, 255, 0.055);
}}

flowboxchild:selected {{
    background-color: rgba(255, 255, 255, 0.075);
}}

flowboxchild label {{
    color: #f5f5f7;
    font-size: 13px;
    font-weight: 600;
}}

flowboxchild .dim-label,
flowboxchild label.dim-label {{
    color: rgba(235, 235, 245, 0.56);
    font-size: 12px;
    font-weight: 400;
}}

/* Artist cards are circles; never put a rounded square frame behind them. */
flowboxchild .circle-icon-large {{
    border-radius: 999px;
    box-shadow: 0 8px 22px rgba(0, 0, 0, 0.30);
}}

/* ---------------------------------------------------- 8. Banners */
.album-banner,
.default-banner {{
    background-color: transparent;
    background-image: none;
    border-radius: 0;
    padding: 0;
}}

/* Album hero: the gradient overlay is inserted by apple_shell.py and tinted
   from the album artwork.  This is the neutral fallback. */
.apple-album-banner {{
    border-radius: 18px;
    padding: 0;
    box-shadow: 0 18px 48px rgba(0, 0, 0, 0.30);
}}

.apple-album-gradient {{
    background-image: linear-gradient(
        135deg, rgba(88, 88, 96, 0.82) 0%,
        rgba(36, 36, 42, 0.90) 58%,
        rgba(10, 10, 12, 0.96) 100%);
    border-radius: 18px;
}}

.apple-album-cover {{
    border-radius: 12px;
    box-shadow: 0 16px 38px rgba(0, 0, 0, 0.42);
}}

.apple-album-title {{
    color: #ffffff;
    font-size: 34px;
    font-weight: 800;
}}

.apple-album-artist {{
    color: rgba(255, 255, 255, 0.88);
    font-size: 18px;
    font-weight: 600;
}}

.apple-album-meta {{
    color: rgba(255, 255, 255, 0.58);
    font-size: 12px;
    font-weight: 500;
}}

.apple-album-actions {{
    margin-top: 10px;
}}

.apple-album-play {{
    border-radius: 999px;
    min-width: 42px;
    min-height: 42px;
    padding: 0;
}}

.banner-button,
.banner-button-big {{
    border-radius: 999px;
    min-height: 34px;
    padding: 0 16px;
    font-weight: 700;
}}

.banner-button-big {{
    min-height: 40px;
    padding: 0 20px;
}}

/* ---------------------------------------------------- 9. Track rows */
row.trackrow,
.trackrow {{
    background-color: transparent;
    background-image: none;
    border-radius: 8px;
    padding: 2px 8px;
    min-height: 36px;
    transition: background-color {_EASE};
}}

row.trackrow:hover,
.trackrow:hover {{
    background-color: rgba(255, 255, 255, 0.055);
}}

row.trackrow:selected,
.trackrow:selected {{
    background-color: rgba(255, 255, 255, 0.08);
}}

row.trackrow label {{
    font-size: 13px;
}}

row.trackrow label.dim-label {{
    color: rgba(235, 235, 245, 0.50);
    font-size: 12px;
}}

.trackrow .queued,
label.queued {{
    color: #ffffff;
}}

/* Row play indicator: the native indicator already toggles play/pause. */
.indicator,
.trackrow .indicator {{
    color: rgba(255, 255, 255, 0.72);
}}

/* ---------------------------------------------------- 10. Right panel */
#right-panel {{
    background-color: #1c1c1e;
    color: #f5f5f7;
    border-left: 1px solid rgba(255, 255, 255, 0.06);
}}

#right-panel #right-panel-header,
#right-panel #right-panel-lyrics-container,
#right-panel #right-panel-queue-container {{
    background-color: rgba(44, 44, 46, 0.72);
    border: 1px solid rgba(255, 255, 255, 0.055);
    border-radius: 14px;
    padding: 10px;
}}

#right-panel #right-panel-track-info {{
    background-color: transparent;
    background-image: none;
    padding: 0;
}}

#right-panel #right-panel-title {{
    color: #ffffff;
    font-size: 15px;
    font-weight: 700;
}}

#right-panel #right-panel-artist {{
    color: rgba(235, 235, 245, 0.72);
    font-size: 13px;
}}

#right-panel #right-panel-album {{
    color: rgba(235, 235, 245, 0.48);
    font-size: 12px;
}}

#right-panel #right-panel-heading {{
    color: rgba(235, 235, 245, 0.58);
    font-size: 10px;
    font-weight: 700;
}}

#right-panel #right-panel-artwork-frame {{
    background-color: rgba(255, 255, 255, 0.035);
    border-radius: 14px;
}}

#right-panel #right-panel-media {{
    background-color: rgba(44, 44, 46, 0.72);
    border-radius: 14px;
}}

#right-panel #right-panel-progress-time {{
    color: rgba(235, 235, 245, 0.54);
    font-size: 10px;
}}

#right-panel scrolledwindow,
#right-panel viewport {{
    background-color: transparent;
}}

#right-panel button {{
    color: #f5f5f7;
    background-color: rgba(255, 255, 255, 0.07);
    background-image: none;
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 8px;
    box-shadow: none;
}}

#right-panel button:hover {{
    background-color: rgba(255, 255, 255, 0.13);
}}

/* Tabbed Lyrics / Up Next area. */
#right-panel notebook,
#right-panel notebook > header,
#right-panel notebook > stack {{
    background-color: transparent;
    border: none;
    box-shadow: none;
}}

#right-panel notebook > header {{
    background-color: transparent;
    padding: 0;
}}

#right-panel notebook > header tab {{
    background-color: transparent;
    background-image: none;
    border: none;
    border-bottom: 2px solid transparent;
    color: rgba(235, 235, 245, 0.50);
    font-size: 12px;
    font-weight: 600;
    min-height: 32px;
    padding: 4px 14px;
    transition: color {_EASE}, border-color {_EASE};
}}

#right-panel notebook > header tab:hover {{
    color: rgba(235, 235, 245, 0.82);
}}

#right-panel notebook > header tab:checked {{
    color: #ffffff;
    border-bottom-color: rgba(255, 255, 255, 0.30);
}}

#right-panel notebook > header tab label {{
    color: inherit;
    font-size: 12px;
    font-weight: 600;
}}

#right-panel notebook > stack > box {{
    background-color: transparent;
}}

#right-panel #right-panel-lyrics {{
    color: rgba(255, 255, 255, 0.90);
    font-size: 14px;
}}

#right-panel #right-panel-lyrics-container .lyrics-line {{
    color: rgba(235, 235, 245, 0.34);
    font-size: 14px;
    padding: 4px 0;
}}

#right-panel #right-panel-lyrics-container .lyrics-line.past {{
    color: rgba(235, 235, 245, 0.52);
}}

#right-panel #right-panel-lyrics-container .lyrics-line.active {{
    color: #ffffff;
    font-size: 18px;
    font-weight: 700;
    padding: 8px 0;
}}

#right-panel #right-panel-lyrics-container .lyrics-row.hover {{
    background-color: rgba(255, 255, 255, 0.06);
    border-radius: 8px;
}}

#right-panel #right-panel-queue-container list,
#right-panel #right-panel-queue-container row {{
    background-color: transparent;
}}

#right-panel #right-panel-queue-container row:hover {{
    background-color: rgba(255, 255, 255, 0.07);
}}

/* ---------------------------------------------------- 11. Songs page */
#songs-page {{
    background-color: transparent;
}}

#songs-page #songs-page-hero {{
    background-color: rgba(28, 28, 30, 0.84);
    background-image: linear-gradient(
        135deg, rgba(255, 255, 255, 0.10) 0%,
        rgba(255, 255, 255, 0.035) 58%,
        rgba(255, 255, 255, 0.015) 100%);
    border: 1px solid rgba(255, 255, 255, 0.065);
    border-radius: 18px;
    box-shadow: 0 18px 42px rgba(0, 0, 0, 0.22);
    padding: 20px 22px;
}}

#songs-page label.songs-page-title {{
    color: #ffffff;
    font-size: 40px;
    font-weight: 800;
}}

#songs-page label.songs-page-count {{
    color: rgba(235, 235, 245, 0.58);
    font-size: 13px;
    font-weight: 500;
}}

#songs-page label.songs-page-eyebrow {{
    color: rgba(235, 235, 245, 0.52);
    font-size: 10px;
    font-weight: 700;
}}

#songs-page button.songs-page-play {{
    border-radius: 999px;
    min-height: 38px;
    padding: 0 20px;
    font-weight: 700;
}}

#songs-page button.songs-page-control,
#songs-page button.songs-page-transport {{
    border-radius: 999px;
    min-height: 34px;
    padding: 0 14px;
}}

#songs-page button.songs-page-transport {{
    min-width: 36px;
    padding: 0;
}}

#songs-page #songs-page-column-head {{
    padding-bottom: 7px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.065);
}}

#songs-page label.songs-page-column-label {{
    color: rgba(235, 235, 245, 0.32);
    font-size: 10px;
    font-weight: 700;
}}

#songs-page #songs-page-scroller,
#songs-page #songs-page-list {{
    background-color: transparent;
}}

#songs-page row.song-row {{
    background-color: transparent;
    border-radius: 9px;
    padding: 0;
    min-height: 0;
    transition: background-color {_EASE};
}}

#songs-page row.song-row:hover {{
    background-color: rgba(255, 255, 255, 0.055);
}}

#songs-page row.song-row.playing {{
    background-color: rgba(255, 255, 255, 0.06);
}}

#songs-page label.song-number {{
    color: rgba(235, 235, 245, 0.30);
    font-size: 11px;
    font-weight: 600;
}}

#songs-page label.song-title {{
    color: #ffffff;
    font-size: 14px;
    font-weight: 600;
}}

#songs-page label.song-artist {{
    color: rgba(235, 235, 245, 0.54);
    font-size: 12px;
}}

#songs-page label.song-album {{
    color: rgba(235, 235, 245, 0.40);
    font-size: 12px;
}}

#songs-page label.song-duration {{
    color: rgba(235, 235, 245, 0.36);
    font-size: 12px;
}}

#songs-page label.song-empty {{
    color: rgba(235, 235, 245, 0.42);
    font-size: 13px;
}}

#songs-page #songs-page-list box.song-art-frame {{
    background-color: rgba(255, 255, 255, 0.04);
    border-radius: 8px;
}}

/* ---------------------------------------------------- 12. Bottom player */
#apple-player-bar {{
    background-color: rgba(22, 22, 24, 0.98);
    background-image: linear-gradient(
        180deg, rgba(28, 28, 30, 0.98), rgba(18, 18, 20, 0.99));
    border-top: 1px solid rgba(255, 255, 255, 0.075);
    box-shadow: 0 -10px 30px rgba(0, 0, 0, 0.24);
    min-height: 62px;
    padding: 7px 16px;
}}

#apple-player-bar .apple-player-art {{
    border-radius: 8px;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.32);
}}

#apple-player-bar .apple-player-label {{
    color: #f5f5f7;
    font-size: 12px;
}}

#apple-player-bar .apple-player-label b {{
    font-weight: 700;
}}

#apple-player-bar button {{
    background-color: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    color: rgba(245, 245, 247, 0.76);
    border-radius: 999px;
    min-width: 30px;
    min-height: 30px;
    padding: 0;
    transition: background-color {_EASE}, color {_EASE};
}}

#apple-player-bar button:hover {{
    background-color: rgba(255, 255, 255, 0.09);
    color: #ffffff;
}}

#apple-player-bar button:checked,
#apple-player-bar button.active {{
    background-color: rgba(255, 255, 255, 0.13);
    color: #ffffff;
}}

#apple-player-bar .linked > button {{
    background-color: transparent;
    border: none;
    border-radius: 999px;
    min-width: 34px;
    min-height: 34px;
}}

#apple-player-bar .linked > button:hover {{
    background-color: rgba(255, 255, 255, 0.09);
}}

#apple-player-bar button.apple-player-play {{
    background-color: #f5f5f7;
    color: #0a0a0b;
    min-width: 38px;
    min-height: 38px;
    border-radius: 999px;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.30);
}}

#apple-player-bar button.apple-player-play:hover {{
    background-color: #ffffff;
    color: #000000;
}}

#apple-player-bar button.apple-player-play image {{
    color: #0a0a0b;
}}

#apple-player-bar .apple-player-progress {{
    background-color: transparent;
    padding: 0 4px;
}}

#apple-player-bar .apple-player-progress label {{
    color: rgba(235, 235, 245, 0.48);
    font-size: 10px;
}}

#apple-player-bar scale {{
    background-color: transparent;
    padding: 0;
}}

#apple-player-bar scale trough {{
    background-color: rgba(255, 255, 255, 0.14);
    border: none;
    border-radius: 999px;
    min-height: 4px;
}}

#apple-player-bar scale highlight {{
    background-color: rgba(255, 255, 255, 0.72);
    border-radius: 999px;
    min-height: 4px;
}}

#apple-player-bar scale slider {{
    background-color: #ffffff;
    background-image: none;
    border: none;
    border-radius: 999px;
    min-width: 10px;
    min-height: 10px;
    margin: -3px;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.30);
    transition: background-color {_EASE};
}}

#apple-player-bar scale slider:hover {{
    background-color: #ffffff;
}}

#apple-player-bar .apple-player-volume scale {{
    min-width: 84px;
}}

/* ---------------------------------------------------- 13. Popovers */
popover,
popover.background,
menu {{
    background-color: #242426;
    background-image: none;
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 12px;
    box-shadow: 0 18px 44px rgba(0, 0, 0, 0.46);
    padding: 6px;
}}

popover modelbutton,
menu menuitem {{
    background-color: transparent;
    border-radius: 7px;
    padding: 6px 10px;
    min-height: 26px;
}}

popover modelbutton:hover,
menu menuitem:hover {{
    background-color: rgba(255, 255, 255, 0.09);
}}

popover separator,
menu separator {{
    background-color: rgba(255, 255, 255, 0.08);
    margin: 4px 8px;
}}

tooltip {{
    background-color: #2c2c2e;
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 7px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.40);
}}

/* ---------------------------------------------------- 14. Scrollbars */
scrollbar {{
    background-color: transparent;
    border: none;
    box-shadow: none;
}}

scrollbar trough {{
    background-color: transparent;
    border: none;
}}

scrollbar slider {{
    background-color: rgba(255, 255, 255, 0.18);
    background-image: none;
    border: none;
    border-radius: 999px;
    min-width: 7px;
    min-height: 7px;
    transition: background-color {_EASE};
}}

scrollbar slider:hover {{
    background-color: rgba(255, 255, 255, 0.30);
}}

scrollbar.overlay-indicator:not(.dragging):not(.hovering) slider {{
    background-color: rgba(255, 255, 255, 0.16);
}}

/* ---------------------------------------------------- 15. Misc */
checkbutton check,
radiobutton radio {{
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.14);
    box-shadow: none;
}}

checkbutton check:checked,
radiobutton radio:checked {{
    background-color: #f5f5f7;
    border-color: #f5f5f7;
    color: #0a0a0b;
}}

progressbar trough {{
    background-color: rgba(255, 255, 255, 0.10);
    border: none;
    border-radius: 999px;
    min-height: 4px;
}}

progressbar progress {{
    background-color: rgba(255, 255, 255, 0.72);
    border: none;
    border-radius: 999px;
    min-height: 4px;
}}

/* Library scan progress is not playback; keep it quiet. */
.progress-bottom {{
    background-color: transparent;
}}

.progress-bottom progress {{
    background-color: rgba(255, 255, 255, 0.34);
}}

/* No light mode: force a dark base even if the desktop theme is light. */
.gtk-light,
.light {{
    background-color: #000000;
    color: #f5f5f7;
}}
"""


def write_gtk_css() -> Path:
    """Generate and atomically write the dark static base."""
    output_path = Path.home() / ".config" / "gtk-3.0" / "gtk.css"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    css = generate_global_base_css()
    temporary_path = output_path.with_suffix(".css.tmp")
    temporary_path.write_text(css, encoding="utf-8")
    temporary_path.replace(output_path)
    return output_path


if __name__ == "__main__":
    output = write_gtk_css()
    print(f"Apple Music static GTK3 base written to: {output}")
    print("Global accent is applied in-process from the wallpaper "
          "(accent_provider + AmbientStyler).")
