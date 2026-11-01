#!/usr/bin/env python3
"""Runtime shell hooks for the Apple Music-inspired Lollypop UI.

This module does not edit any Lollypop system file.  It decorates the native
widgets after they are constructed:

  * sidebar / toolbar classes for the Apple-style chrome;
  * a gradient overlay on ``AlbumBannerWidget`` driven by the album cover;
  * typography classes on the hero title, artist, metadata and actions.

The album hero overlay is inserted *below* the banner content and above the
native blurred artwork, so it is purely visual and never intercepts clicks.
A failure here leaves the native banner completely intact.
"""

from __future__ import annotations

import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

PREFIX = "[Lollypop Extension]"

try:
    from lollypop.define import App
    from lollypop.widgets_banner_album import AlbumBannerWidget
except Exception:  # pragma: no cover - only available inside Lollypop
    App = None
    AlbumBannerWidget = None

try:
    from lollypop.color_extractor import NEUTRAL_PALETTE, extract_palette
except Exception:
    NEUTRAL_PALETTE = ((34, 34, 44), (176, 156, 236), (126, 176, 226))
    extract_palette = None

# Album art is small and local; one worker is enough and keeps the UI thread
# free while a large cover is decoded.
_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="apple-album-hero"
)


def _c8(value):
    return max(0, min(255, int(round(value))))


def _rgba(color, alpha):
    return "rgba({}, {}, {}, {:.3f})".format(
        *(_c8(v) for v in color), alpha
    )


@lru_cache(maxsize=64)
def _palette_for_path(path, mtime_ns):
    """Extract an album palette, cached by path + mtime."""
    if not path or not os.path.isfile(path) or extract_palette is None:
        return None
    try:
        return extract_palette(path)
    except Exception:
        traceback.print_exc()
        return None


def _album_art_path(banner):
    """Best-effort path to the banner album's current cover."""
    if App is None:
        return None
    try:
        album = getattr(banner, "_AlbumBannerWidget__album", None)
        if album is None:
            return None
        uri = App().album_art.get_uri(album)
        if not uri:
            return None
        # Lollypop returns a file:// URI here, while the palette extractor
        # needs a real filesystem path.
        if uri.startswith("file://"):
            return GLib.filename_from_uri(uri)[0]
        return uri
    except Exception:
        return None


def _decorate_banner(banner):
    """Add Apple-style classes to the native hero widgets."""
    try:
        context = banner.get_style_context()
        context.add_class("apple-album-banner")
    except Exception:
        pass

    class_map = {
        "_AlbumBannerWidget__title_label": "apple-album-title",
        "_AlbumBannerWidget__artist_label": "apple-album-artist",
        "_AlbumBannerWidget__year_label": "apple-album-meta",
        "_AlbumBannerWidget__duration_label": "apple-album-meta",
        "_AlbumBannerWidget__cover_widget": "apple-album-cover",
        "_AlbumBannerWidget__play_button": "apple-album-play",
        "_AlbumBannerWidget__middle_box": "apple-album-actions",
    }
    for attr, css_class in class_map.items():
        widget = getattr(banner, attr, None)
        if widget is None or not hasattr(widget, "get_style_context"):
            continue
        try:
            widget.get_style_context().add_class(css_class)
        except Exception:
            pass

    # The native action box is a Gtk.Box; align it like Apple Music instead of
    # letting the vertical info column stretch the icon buttons full-width.
    actions = getattr(banner, "_AlbumBannerWidget__middle_box", None)
    if actions is not None:
        try:
            actions.set_halign(Gtk.Align.START)
            actions.set_valign(Gtk.Align.START)
            actions.set_spacing(8)
        except Exception:
            pass
    play = getattr(banner, "_AlbumBannerWidget__play_button", None)
    if play is not None:
        try:
            play.set_halign(Gtk.Align.START)
            play.set_size_request(42, 42)
        except Exception:
            pass


def _install_gradient(banner):
    """Insert the gradient overlay below the native hero content."""
    if getattr(banner, "_apple_gradient", None) is not None:
        return
    overlay = getattr(banner, "_overlay", None)
    if overlay is None:
        return
    try:
        gradient = Gtk.Box()
        gradient.get_style_context().add_class("apple-album-gradient")
        gradient.set_hexpand(True)
        gradient.set_vexpand(True)
        gradient.set_halign(Gtk.Align.FILL)
        gradient.set_valign(Gtk.Align.FILL)
        # Add before AlbumBannerWidget.populate() adds the content overlay,
        # so the gradient sits above the blurred artwork and below the text.
        overlay.add_overlay(gradient)
        overlay.set_overlay_pass_through(gradient, True)
        gradient.show()
        banner._apple_gradient = gradient
        banner._apple_gradient_provider = None
    except Exception:
        traceback.print_exc()


def _apply_hero_palette(banner, palette):
    """Load the album-derived gradient into the hero overlay."""
    gradient = getattr(banner, "_apple_gradient", None)
    if gradient is None:
        return False
    try:
        # The banner can be destroyed while the palette worker is decoding
        # the cover.  Never touch a widget that has already been unparented.
        if gradient.get_parent() is None:
            return False
        if palette is None:
            palette = NEUTRAL_PALETTE
        background, accent1, _accent2 = palette
        # A soft, dark Apple Music hero.  The artwork remains visible through
        # the alpha, while the lower half fades to near-black for the title.
        css = f"""
        .apple-album-gradient {{
            background-image: linear-gradient(
                135deg,
                {_rgba(accent1, 0.58)} 0%,
                {_rgba(background, 0.74)} 48%,
                rgba(0, 0, 0, 0.90) 100%);
            border-radius: 18px;
        }}
        """
        provider = getattr(banner, "_apple_gradient_provider", None)
        if provider is None:
            provider = Gtk.CssProvider()
            # Outrank the static USER-priority base so the album palette wins
            # over the neutral hero gradient in gtk.css.
            gradient.get_style_context().add_provider(
                provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1
            )
            banner._apple_gradient_provider = provider
        provider.load_from_data(css.encode("utf-8"))
    except Exception:
        traceback.print_exc()
    return False


def _request_hero_palette(banner):
    """Resolve the cover palette off the UI thread and apply it on idle."""
    path = _album_art_path(banner)
    if not path:
        _apply_hero_palette(banner, None)
        return
    try:
        mtime = os.stat(path).st_mtime_ns
    except Exception:
        mtime = 0

    def work():
        palette = _palette_for_path(path, mtime)
        GLib.idle_add(_apply_hero_palette, banner, palette)
        return None

    try:
        _EXECUTOR.submit(work)
    except Exception:
        _apply_hero_palette(banner, None)


def _patch_album_banner():
    """Monkeypatch AlbumBannerWidget.populate once."""
    if AlbumBannerWidget is None:
        return False
    if getattr(AlbumBannerWidget, "_apple_shell_patched", False):
        return True
    original = AlbumBannerWidget.populate

    def populate(self):
        _install_gradient(self)
        result = original(self)
        _decorate_banner(self)
        _request_hero_palette(self)
        return result

    AlbumBannerWidget.populate = populate
    AlbumBannerWidget._apple_shell_patched = True
    AlbumBannerWidget._apple_original_populate = original
    return True


def _patch_album_tracks():
    """Keep the full album tracklist in a single Apple-style column.

    Lollypop's album detail view otherwise splits tracks into two columns on
    wide windows.  The compact album widgets used by artist pages already opt
    into ``SINGLE_COLUMN`` themselves, so only the full ``ALBUM`` view is
    changed here.
    """
    try:
        from lollypop.define import ViewType
        from lollypop.view_tracks_album import AlbumTracksView
    except Exception:
        return False
    if getattr(AlbumTracksView, "_apple_single_column_patched", False):
        return True
    original = AlbumTracksView.__init__

    def __init__(self, album, view_type):
        try:
            if (isinstance(view_type, int) and
                    view_type & ViewType.ALBUM and
                    not view_type & ViewType.SMALL):
                view_type |= ViewType.SINGLE_COLUMN
        except Exception:
            pass
        original(self, album, view_type)

    AlbumTracksView.__init__ = __init__
    AlbumTracksView._apple_single_column_patched = True
    return True


def install_apple_shell(container=None, toolbar=None):
    """Install all runtime shell hooks.  Safe to call more than once."""
    try:
        _patch_album_banner()
    except Exception:
        print(f"{PREFIX} Album hero hook failed:")
        traceback.print_exc()

    try:
        _patch_album_tracks()
    except Exception:
        print(f"{PREFIX} Album tracklist hook failed:")
        traceback.print_exc()

    try:
        if container is not None:
            sidebar = getattr(container, "sidebar", None)
            if sidebar is not None and hasattr(sidebar, "get_style_context"):
                sidebar.get_style_context().add_class("apple-sidebar")
            if hasattr(container, "get_style_context"):
                container.get_style_context().add_class("apple-content")
    except Exception:
        print(f"{PREFIX} Sidebar decoration failed:")
        traceback.print_exc()

    try:
        if toolbar is not None and hasattr(toolbar, "get_style_context"):
            toolbar.get_style_context().add_class("apple-toolbar")
    except Exception:
        print(f"{PREFIX} Toolbar decoration failed:")
        traceback.print_exc()
    return True
