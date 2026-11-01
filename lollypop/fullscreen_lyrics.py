#!/usr/bin/env python3
"""Apple Music-style fullscreen layout for Lollypop.

The native Lollypop fullscreen window (``lollypop.fullscreen.FullScreen``)
is a cover-focused screen with no lyrics.  This module keeps that window,
its background, close button, clock, navigation sidebar and playback
widgets, but replaces the centre content with a two-column layout:

    left  : artwork, track metadata, progress and transport controls
    right : the existing Apple Music-style synced lyrics view

The hook is installed at runtime from the extension bootstrap.  It never
edits Lollypop's system files, and if the expected native widgets are not
present the original fullscreen screen is left untouched.
"""

from __future__ import annotations

import os
import traceback
import types
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango

try:
    from lollypop.synced_lyrics import DEFAULT_ACCENT, SyncedLyricsView
except Exception:  # pragma: no cover - reported by install_fullscreen()
    DEFAULT_ACCENT = (0.65, 0.55, 0.98)
    SyncedLyricsView = None

PREFIX = "[Lollypop Extension]"
DEBUG = os.environ.get("LOLLYPOP_EXT_DEBUG") == "1"
CSS_PRIORITY = Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2

_css_provider = None
_css_screen = None
_css_accent = None


def _log(*args):
    print(PREFIX, *args, flush=True)


def _debug(*args):
    if DEBUG:
        _log(*args)


# ---------------------------------------------------------------- CSS


def _normalise_accent(accent):
    if not accent:
        return DEFAULT_ACCENT
    try:
        values = tuple(float(value) for value in accent[:3])
    except (TypeError, ValueError):
        return DEFAULT_ACCENT
    if max(values) > 1.0:
        values = tuple(value / 255.0 for value in values)
    return tuple(max(0.0, min(1.0, value)) for value in values)


def _hex_color(accent):
    red, green, blue = (
        max(0, min(255, int(round(value * 255))))
        for value in _normalise_accent(accent)
    )
    return f"#{red:02x}{green:02x}{blue:02x}"


def _build_css(accent):
    accent_hex = _hex_color(accent)
    return f"""
#fullscreen-lyrics-root {{
    color: #ffffff;
}}

#fullscreen-lyrics-root .fs-eyebrow {{
    color: {accent_hex};
    font-size: 11px;
    font-weight: bold;
}}

#fullscreen-lyrics-root .fs-title {{
    color: #ffffff;
    font-size: 40px;
    font-weight: 800;
}}

#fullscreen-lyrics-root .fs-artist {{
    color: rgba(255, 255, 255, 0.84);
    font-size: 21px;
    font-weight: 500;
}}

#fullscreen-lyrics-root .fs-album {{
    color: rgba(255, 255, 255, 0.52);
    font-size: 15px;
}}

#fullscreen-lyrics-root .fs-heading {{
    color: {accent_hex};
    font-size: 12px;
    font-weight: bold;
}}

#fullscreen-lyrics-root .fs-cover {{
    border-radius: 14px;
    box-shadow: 0 18px 50px rgba(0, 0, 0, 0.48);
}}

#fullscreen-lyrics-root .lyrics-row {{
    background-color: transparent;
    border-radius: 10px;
}}

#fullscreen-lyrics-root .lyrics-row.hover {{
    background-color: rgba(255, 255, 255, 0.06);
}}

#fullscreen-lyrics-root .lyrics-line {{
    color: rgba(255, 255, 255, 0.30);
    font-size: 28px;
    font-weight: 500;
    padding: 8px 0;
}}

#fullscreen-lyrics-root .lyrics-line.past {{
    color: rgba(255, 255, 255, 0.43);
}}

#fullscreen-lyrics-root .lyrics-line.active {{
    color: rgba(255, 255, 255, 0.50);
    font-size: 38px;
    font-weight: 600;
    padding: 11px 0;
}}

#fullscreen-lyrics-root .lyrics-dots {{
    color: rgba(255, 255, 255, 0.45);
    font-size: 30px;
    padding: 9px 0;
}}

#fullscreen-lyrics-root .lyrics-message {{
    color: rgba(255, 255, 255, 0.58);
    font-size: 23px;
    font-weight: 500;
    padding: 10px 0;
}}

#fullscreen-lyrics-root scrolledwindow,
#fullscreen-lyrics-root viewport {{
    background-color: transparent;
}}

#fullscreen-lyrics-root button.banner-button {{
    color: #ffffff;
}}

#fullscreen-lyrics-root.compact .fs-title {{
    font-size: 30px;
}}

#fullscreen-lyrics-root.compact .fs-artist {{
    font-size: 18px;
}}

#fullscreen-lyrics-root.compact .fs-album {{
    font-size: 13px;
}}

#fullscreen-lyrics-root.compact .fs-heading {{
    font-size: 11px;
}}

#fullscreen-lyrics-root.compact .lyrics-line {{
    font-size: 22px;
    padding: 6px 0;
}}

#fullscreen-lyrics-root.compact .lyrics-line.active {{
    font-size: 30px;
    padding: 9px 0;
}}

#fullscreen-lyrics-root.compact .lyrics-dots {{
    font-size: 23px;
    padding: 7px 0;
}}

#fullscreen-lyrics-root.compact .lyrics-message {{
    font-size: 19px;
    padding: 8px 0;
}}
"""


def _apply_css(accent=None):
    """Install/update the fullscreen-scoped stylesheet on the screen."""
    global _css_provider, _css_screen, _css_accent

    accent = _normalise_accent(accent)
    if _css_provider is not None and accent == _css_accent:
        return True

    try:
        if _css_provider is None:
            screen = Gdk.Screen.get_default()
            if screen is None:
                return False
            _css_provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_screen(
                screen, _css_provider, CSS_PRIORITY
            )
            _css_screen = screen

        css = _build_css(accent).encode("utf-8")
        _css_provider.load_from_data(css)
        _css_accent = accent
        return True
    except GLib.Error as exc:
        _log("Fullscreen CSS rejected by GTK:", exc.message)
    except Exception as exc:
        _log("Fullscreen CSS application failed:", exc)
        traceback.print_exc()
    return False


# ---------------------------------------------------------------- helpers


def _app():
    from lollypop.define import App

    return App()


def _player():
    try:
        return _app().player
    except Exception:
        return None


def _track_info(track):
    # Reuse the exact metadata/cache policy used by the side panel.
    from lollypop.widget_injector import _track_info as resolve_track_info

    return resolve_track_info(track)


def _load_lyrics_assets(info):
    """Resolve lyrics and a cover palette in a worker thread."""
    from lollypop.widget_injector import (
        _best_local_lyrics,
        _embedded_art,
        _folder_cover,
        _online_lyrics,
        extract_palette,
        has_synced_lyrics,
    )

    local = _best_local_lyrics(info)
    lyrics = local
    if not local or not has_synced_lyrics(local):
        online = _online_lyrics(info)
        if online and (not local or has_synced_lyrics(online)):
            lyrics = online

    art_path = None
    palette = None
    if info.audio_path:
        art_path = _embedded_art(info.audio_path) or _folder_cover(
            info.audio_path
        )
    if art_path and extract_palette is not None:
        palette = extract_palette(art_path)

    return lyrics, local, palette


# ---------------------------------------------------------------- panel


class _FullscreenLyricsPanel:
    """Owns the fullscreen two-column content and its lyric clock."""

    def __init__(self, window):
        self._window = window
        self._destroyed = False
        self._visible = False
        self._player = None
        self._signal_ids = []
        self._timer_id = 0
        self._executor = None
        self._token = 0
        self._last_key = None
        self._compact = None
        self._resize_idle = 0
        self._pending_allocation = None
        self._left_width = 0
        self._holder_width = 0
        self._art_size = 0
        self._text_compact = None
        self._accent = _normalise_accent(DEFAULT_ACCENT)
        self._lyrics = None

        _apply_css(self._accent)
        self._build()
        self._connect()
        self._refresh_track(force=True)

    # ---- construction ------------------------------------------------

    def _native(self, name):
        return getattr(self._window, f"_FullScreen__{name}", None)

    def _build(self):
        window = self._window
        self._grid = self._native("overlay_grid")
        self._label = self._native("label_widget")
        self._progress = self._native("progress_widget")
        self._buttons = self._native("buttons_widget")
        self._artwork = self._native("artwork_widget")

        if (
            self._grid is None
            or self._progress is None
            or self._buttons is None
            or SyncedLyricsView is None
        ):
            raise RuntimeError("native fullscreen widgets are unavailable")

        self._eventbox = (
            self._artwork.get_parent()
            if self._artwork is not None
            else None
        )
        if not isinstance(self._eventbox, Gtk.EventBox):
            raise RuntimeError("native fullscreen artwork container is missing")

        # Build the lyrics widget before touching the native tree.  A failure
        # here is then guaranteed to leave the native fullscreen screen intact.
        lyrics_view = SyncedLyricsView(
            on_seek=self._on_lyric_seek, fullscreen=True
        )

        # Keep the clock and close button in row 0.  Remove only the native
        # centre widgets, which we are about to reparent into our own layout.
        centre_widgets = {
            self._label,
            self._progress,
            self._buttons,
            self._eventbox,
        }
        for child in list(self._grid.get_children()):
            if child in centre_widgets:
                self._grid.remove(child)

        self._datetime = None
        for child in self._grid.get_children():
            if isinstance(child, Gtk.Label):
                self._datetime = child
                child.set_vexpand(False)
                child.set_valign(Gtk.Align.START)
                break

        self._grid.set_row_spacing(16)
        self._grid.set_margin_top(18)

        self._main = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=64
        )
        self._main.set_name("fullscreen-lyrics-root")
        self._main.set_hexpand(True)
        self._main.set_vexpand(True)
        self._main.set_margin_start(56)
        self._main.set_margin_end(56)
        self._main.set_margin_bottom(28)

        self._left = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=16
        )
        self._left.set_hexpand(False)
        self._left.set_vexpand(True)
        self._left.set_valign(Gtk.Align.FILL)
        self._left.set_halign(Gtk.Align.START)

        # Artwork.  The native widget remains clickable (it cycles the
        # fullscreen cover style), so we only reposition it.
        self._eventbox.set_margin_start(0)
        self._eventbox.set_margin_end(0)
        self._eventbox.set_margin_top(0)
        self._eventbox.set_margin_bottom(0)
        self._eventbox.set_halign(Gtk.Align.START)
        self._eventbox.set_valign(Gtk.Align.START)
        self._eventbox.set_hexpand(False)
        self._eventbox.set_vexpand(False)
        self._eventbox.get_style_context().add_class("fs-cover")
        if self._artwork is not None:
            self._artwork.set_margin_end(0)
            self._artwork.set_margin_bottom(0)
            self._artwork.set_valign(Gtk.Align.START)
            self._artwork.set_halign(Gtk.Align.START)
            self._artwork.set_vexpand(False)
        self._left.pack_start(self._eventbox, False, False, 0)

        # Metadata.  A custom block gives the title/artist/album hierarchy
        # that the native single-label widget cannot provide.
        self._eyebrow = Gtk.Label(label="NOW PLAYING")
        self._eyebrow.set_xalign(0)
        self._eyebrow.get_style_context().add_class("fs-eyebrow")

        self._title = Gtk.Label()
        self._title.set_xalign(0)
        self._title.set_yalign(0)
        self._title.set_justify(Gtk.Justification.LEFT)
        self._title.set_line_wrap(True)
        self._title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._title.set_lines(2)
        self._title.set_ellipsize(Pango.EllipsizeMode.END)
        self._title.set_max_width_chars(36)
        self._title.get_style_context().add_class("fs-title")

        self._artist = Gtk.Label()
        self._artist.set_xalign(0)
        self._artist.set_ellipsize(Pango.EllipsizeMode.END)
        self._artist.set_single_line_mode(True)
        self._artist.set_max_width_chars(44)
        self._artist.get_style_context().add_class("fs-artist")

        self._album = Gtk.Label()
        self._album.set_xalign(0)
        self._album.set_ellipsize(Pango.EllipsizeMode.END)
        self._album.set_single_line_mode(True)
        self._album.set_max_width_chars(52)
        self._album.get_style_context().add_class("fs-album")

        self._metadata = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=3
        )
        self._metadata.set_hexpand(True)
        self._metadata.pack_start(self._eyebrow, False, False, 0)
        self._metadata.pack_start(self._title, False, False, 0)
        self._metadata.pack_start(self._artist, False, False, 0)
        self._metadata.pack_start(self._album, False, False, 0)
        self._left.pack_start(self._metadata, False, False, 0)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        self._left.pack_start(spacer, True, True, 0)

        # Native progress and transport controls.
        self._progress.set_size_request(-1, -1)
        self._progress.set_halign(Gtk.Align.FILL)
        self._progress.set_hexpand(True)
        self._progress.set_valign(Gtk.Align.CENTER)
        self._left.pack_start(self._progress, False, False, 0)

        self._buttons.set_size_request(272, -1)
        self._buttons.set_halign(Gtk.Align.START)
        self._buttons.set_hexpand(False)
        self._buttons.set_valign(Gtk.Align.CENTER)
        self._left.pack_start(self._buttons, False, False, 0)

        # Lyrics column.  A maximum-width holder keeps line lengths readable
        # on 1440p/4K displays while leaving the rest of the pane empty.
        self._right = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10
        )
        self._right.set_hexpand(True)
        self._right.set_vexpand(True)
        self._right.set_valign(Gtk.Align.FILL)

        self._holder = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8
        )
        self._holder.set_hexpand(False)
        self._holder.set_vexpand(True)
        self._holder.set_halign(Gtk.Align.START)
        self._holder.set_valign(Gtk.Align.FILL)

        self._heading = Gtk.Label(label="LYRICS")
        self._heading.set_xalign(0)
        self._heading.get_style_context().add_class("fs-heading")
        self._holder.pack_start(self._heading, False, False, 0)

        self._lyrics = lyrics_view
        self._lyrics.set_hexpand(True)
        self._lyrics.set_vexpand(True)
        self._holder.pack_start(self._lyrics, True, True, 0)
        self._right.pack_start(self._holder, True, True, 0)

        self._main.pack_start(self._left, False, False, 0)
        self._main.pack_start(self._right, True, True, 0)
        self._grid.attach(self._main, 0, 1, 3, 1)
        self._main.show_all()

        # The user's artist-artwork setting makes the native background use
        # an artist photo.  The split-screen layout is album-centric, so force
        # this one window to use the album cover instead.  Do this last so a
        # construction failure cannot leave a half-applied monkeypatch behind.
        original_background = getattr(
            window, "_FullScreen__update_background", None
        )
        if original_background is not None:
            def album_background(_self, *args, **kwargs):
                return original_background(True)

            window._FullScreen__update_background = types.MethodType(
                album_background, window
            )

    # ---- signals -----------------------------------------------------

    def _connect(self):
        self._player = _player()
        if self._player is not None:
            self._signal_ids = [
                self._player.connect(
                    "current-changed", self._on_current_changed
                ),
                self._player.connect(
                    "status-changed", self._on_status_changed
                ),
                self._player.connect("seeked", self._on_seeked),
            ]
        self._window.connect("show", self._on_show)
        self._window.connect("hide", self._on_hide)
        self._window.connect("destroy", self._on_destroy)
        self._window.connect("size-allocate", self._on_size_allocate)
        _debug("Fullscreen lyrics panel connected")

    # ---- metadata / lyrics ------------------------------------------

    def _refresh_track(self, force=False):
        if self._destroyed:
            return

        track = None
        if self._player is not None:
            track = getattr(self._player, "current_track", None)

        info = _track_info(track)
        key = (
            info.has_track,
            info.title,
            info.artist,
            info.album,
            info.audio_path,
        )
        if not force and key == self._last_key:
            return
        self._last_key = key

        if not info.has_track:
            # Invalidate any worker still fetching the previous track and
            # stop its clock; otherwise stale lyrics/palette can land after
            # the screen already says "Nothing playing".
            self._token += 1
            self._stop_timer()
            self._eyebrow.set_visible(False)
            self._title.set_text("Nothing playing")
            self._artist.set_visible(False)
            self._album.set_visible(False)
            self._lyrics.set_message("")
            self._apply_accent(None)
            return

        self._eyebrow.set_visible(True)
        self._title.set_text(info.title or "Unknown title")
        self._artist.set_text(info.artist)
        self._artist.set_visible(bool(info.artist))

        album_text = info.album
        year = getattr(track, "year", None) if track is not None else None
        if album_text and year:
            album_text = f"{album_text} · {year}"
        self._album.set_text(album_text)
        self._album.set_visible(bool(album_text))

        self._lyrics.set_message("Loading lyrics…")
        self._token += 1
        token = self._token
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="lollypop-fs-lyrics"
            )
        try:
            self._executor.submit(self._load_worker, token, info)
        except RuntimeError:
            pass

    def _load_worker(self, token, info):
        try:
            lyrics, local, palette = _load_lyrics_assets(info)
            if not self._stale(token):
                if local:
                    GLib.idle_add(self._apply_lyrics, token, local)
                if lyrics is not local:
                    GLib.idle_add(self._apply_lyrics, token, lyrics)
                if not local and not lyrics:
                    GLib.idle_add(self._apply_lyrics, token, None)
            if not self._stale(token):
                GLib.idle_add(self._apply_palette, token, palette)
        except Exception:
            _log("Fullscreen lyrics worker failed:")
            traceback.print_exc()
            if not self._stale(token):
                GLib.idle_add(self._apply_lyrics, token, None)

    def _sync_native_progress(self, position_ms=None):
        """Refresh the native progress widget even when playback is paused.

        Lollypop's ProgressPlayerWidget ignores update_position() without an
        explicit value while paused.  FullScreen.do_show() therefore leaves
        the bar and elapsed time at 0:00 if the screen is opened on a paused
        track, while the lyrics view correctly shows the current line.  That
        contradiction looks like broken lyrics, so keep both widgets in sync.
        """
        if self._progress is None or self._player is None:
            return
        try:
            if position_ms is None:
                position_ms = getattr(self._player, "position", 0)
            position_ms = max(0, int(position_ms or 0))
        except (TypeError, ValueError):
            return
        try:
            self._progress.update_position(position_ms)
        except Exception:
            _log("Fullscreen native progress update failed:")
            traceback.print_exc()

    def _stale(self, token):
        return self._destroyed or token != self._token

    def _apply_lyrics(self, token, text):
        if self._stale(token):
            return False
        if text:
            self._lyrics.set_lyrics(text)
        else:
            self._lyrics.set_message("No lyrics available.")
        self._update_position()
        self._start_timer()
        return False

    def _apply_palette(self, token, palette):
        if self._stale(token):
            return False
        accent = None
        if palette:
            try:
                accent = palette[1]
            except (IndexError, TypeError):
                accent = None
        self._apply_accent(accent)
        return False

    def _apply_accent(self, accent):
        self._accent = _normalise_accent(accent or DEFAULT_ACCENT)
        _apply_css(self._accent)
        if self._lyrics is not None:
            self._lyrics.set_accent(self._accent)

    # ---- lyric clock -------------------------------------------------

    def _is_playing(self):
        try:
            return bool(self._player is not None and self._player.is_playing)
        except Exception:
            return False

    def _update_position(self, position_ms=None):
        if self._lyrics is None or self._player is None:
            return
        try:
            if position_ms is None:
                position_ms = getattr(self._player, "position", 0)
            position_ms = max(0, int(position_ms or 0))
            track = getattr(self._player, "current_track", None)
            duration_ms = int(getattr(track, "duration", 0) or 0)
            self._lyrics.set_position(position_ms, duration_ms)
        except Exception:
            _log("Fullscreen lyrics position update failed:")
            traceback.print_exc()

    def _start_timer(self):
        if (
            self._timer_id
            or self._destroyed
            or not self._visible
            or self._lyrics is None
            or not getattr(self._lyrics, "_synced", False)
        ):
            return
        self._update_position()
        if self._is_playing():
            self._timer_id = GLib.timeout_add(33, self._on_tick)

    def _stop_timer(self):
        if self._timer_id:
            try:
                GLib.source_remove(self._timer_id)
            except Exception:
                pass
            self._timer_id = 0

    def _on_tick(self):
        if self._destroyed or not self._visible:
            self._timer_id = 0
            return False
        if not self._is_playing():
            self._update_position()
            self._timer_id = 0
            return False
        self._update_position()
        return True

    # ---- layout / window events -------------------------------------

    def _on_size_allocate(self, _widget, allocation):
        if allocation.width <= 1 or allocation.height <= 1:
            return
        compact = allocation.width < 1500 or allocation.height < 860
        if compact != self._compact:
            self._compact = compact
            context = self._main.get_style_context()
            if compact:
                context.add_class("compact")
            else:
                context.remove_class("compact")

        self._pending_allocation = (allocation.width, allocation.height)
        if not self._resize_idle:
            self._resize_idle = GLib.idle_add(self._apply_size)

    def _apply_size(self):
        self._resize_idle = 0
        if self._destroyed or self._pending_allocation is None:
            return False

        width, height = self._pending_allocation
        if width <= 1 or height <= 1:
            return False

        if self._compact != self._text_compact:
            self._text_compact = self._compact
            self._title.set_max_width_chars(
                26 if self._compact else 36
            )
            self._artist.set_max_width_chars(
                32 if self._compact else 44
            )
            self._album.set_max_width_chars(
                38 if self._compact else 52
            )

        margin = 28 if self._compact else 56
        gap = 32 if self._compact else 64
        self._main.set_margin_start(margin)
        self._main.set_margin_end(margin)
        self._main.set_margin_bottom(18 if self._compact else 28)
        self._main.set_spacing(gap)

        target = int(min(width * 0.34, height * 0.46, 560))
        target = max(180, target)
        left_width = int(max(width * 0.32, target + 140))
        left_width = max(360, min(left_width, 1100))
        max_left = max(300, width - 2 * margin - gap - 380)
        left_width = min(left_width, max_left)
        target = max(160, min(target, left_width - 30))

        if left_width != self._left_width:
            self._left_width = left_width
            self._left.set_size_request(left_width, -1)

        right_width = max(
            300, width - 2 * margin - gap - left_width
        )
        holder_width = min(right_width, 980 if not self._compact else 820)
        if holder_width != self._holder_width:
            self._holder_width = holder_width
            self._holder.set_size_request(holder_width, -1)

        if target != self._art_size:
            self._art_size = target
            if self._artwork is not None:
                self._artwork.set_art_size(target, target)
                self._artwork.update(True)
        return False

    def _on_show(self, _widget):
        self._visible = True
        _debug("Fullscreen lyrics panel shown")
        self._start_timer()
        # FullScreen.do_show() runs update_position() without a value, which
        # is a no-op while paused.  Refresh it here once the window is shown.
        GLib.idle_add(self._sync_native_progress)

    def _on_hide(self, _widget):
        self._visible = False
        _debug("Fullscreen lyrics panel hidden")
        self._stop_timer()

    def _on_current_changed(self, _player):
        self._refresh_track()
        GLib.idle_add(self._sync_native_progress)

    def _on_status_changed(self, _player):
        if self._is_playing():
            self._start_timer()
        else:
            self._stop_timer()
            self._update_position()
        self._sync_native_progress()

    def _on_seeked(self, _player, position_ms):
        self._update_position(position_ms)
        self._sync_native_progress(position_ms)

    def _on_lyric_seek(self, position_ms):
        if self._player is None:
            return
        try:
            self._player.seek(max(0, int(position_ms)))
        except Exception:
            traceback.print_exc()

    def _on_destroy(self, _widget):
        self._destroyed = True
        _debug("Fullscreen lyrics panel destroyed")
        self._stop_timer()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        if self._player is not None:
            for signal_id in self._signal_ids:
                try:
                    self._player.disconnect(signal_id)
                except Exception:
                    pass
        self._signal_ids = []


# ---------------------------------------------------------------- install

_installed = False


def install_fullscreen():
    """Patch Lollypop's FullScreen class once.

    Call this after the application and main window exist.  It is safe to
    call repeatedly; a failure leaves the native fullscreen screen intact.
    """
    global _installed
    if _installed:
        return True
    if SyncedLyricsView is None:
        _log("Fullscreen lyrics unavailable: synced_lyrics import failed")
        return False

    try:
        from lollypop.fullscreen import FullScreen
    except Exception:
        _log("Fullscreen lyrics unavailable: cannot import FullScreen")
        traceback.print_exc()
        return False

    if getattr(FullScreen, "_lollypop_extension_fullscreen", False):
        _installed = True
        return True

    original_delayed_init = FullScreen.delayed_init

    @wraps(original_delayed_init)
    def delayed_init(self):
        original_delayed_init(self)
        if getattr(self, "_lollypop_extension_lyrics_panel", None) is not None:
            return
        try:
            panel = _FullscreenLyricsPanel(self)
            self._lollypop_extension_lyrics_panel = panel
            _debug("Fullscreen lyrics layout installed")
        except Exception:
            _log(
                "Fullscreen lyrics layout unavailable; "
                "keeping native fullscreen:"
            )
            traceback.print_exc()

    FullScreen.delayed_init = delayed_init
    FullScreen._lollypop_extension_fullscreen = True
    _installed = True
    _debug("Fullscreen lyrics hook installed")
    return True
