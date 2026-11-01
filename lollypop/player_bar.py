#!/usr/bin/env python3
"""Apple Music-style bottom playback bar for Lollypop.

The bar is assembled from Lollypop's own player widgets:

    ArtworkPlayerWidget   album cover
    LabelPlayerWidget     title / artist
    ProgressPlayerWidget  seekable native scrubber
    ButtonsPlayerWidget   previous / play-pause / next

That means the bar is not a mock: play/pause, next, previous, seeking,
shuffle, repeat, volume and MPRIS all keep using Lollypop's native player.
If construction fails, the caller leaves the original headerbar controls
visible, so playback can never become unreachable.
"""

from __future__ import annotations

import traceback
import types

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gtk, Pango

from lollypop.logger import Logger

PREFIX = "[Lollypop Extension]"

try:
    from lollypop.define import App, ArtBehaviour, Repeat
    from lollypop.widgets_player_artwork import ArtworkPlayerWidget
    from lollypop.widgets_player_buttons import ButtonsPlayerWidget
    from lollypop.widgets_player_label import LabelPlayerWidget
    from lollypop.widgets_player_progress import ProgressPlayerWidget
    from lollypop.utils import ms_to_string
except Exception:  # pragma: no cover - only available inside Lollypop
    App = None
    ArtBehaviour = None
    Repeat = None
    ArtworkPlayerWidget = None
    ButtonsPlayerWidget = None
    LabelPlayerWidget = None
    ProgressPlayerWidget = None
    ms_to_string = None


def _icon(name, fallback="audio-x-generic-symbolic"):
    """Return an installed icon name, or a safe fallback."""
    try:
        theme = Gtk.IconTheme.get_default()
        if theme is not None and theme.has_icon(name):
            return name
    except Exception:
        pass
    return fallback


def _settings():
    try:
        return App().settings
    except Exception:
        return None


def _player():
    try:
        return App().player
    except Exception:
        return None


def _repeat_value(mode):
    return {
        Repeat.NONE: "none",
        Repeat.AUTO_SIMILAR: "auto_similar",
        Repeat.AUTO_RANDOM: "auto_random",
        Repeat.TRACK: "track",
        Repeat.ALL: "all",
    }.get(mode, "none")


def _repeat_mode(settings):
    try:
        return settings.get_enum("repeat")
    except Exception:
        return Repeat.NONE


def _set_repeat(settings, mode):
    try:
        settings.set_value("repeat", GLib.Variant("s", _repeat_value(mode)))
    except Exception:
        traceback.print_exc()


def _patch_progress_label():
    """Keep the native elapsed-time label readable while paused.

    Lollypop's ``_on_current_changed`` builds its markup with a conditional
    expression that evaluates to an empty string when the widget is not in
    fullscreen mode.  The label therefore stays blank until playback starts
    and the periodic position timer writes it again.  This wrapper restores
    the elapsed value immediately after a track change.
    """
    if ProgressPlayerWidget is None or ms_to_string is None:
        return False
    if getattr(ProgressPlayerWidget, "_apple_progress_patched", False):
        return True
    original = ProgressPlayerWidget._on_current_changed

    def _on_current_changed(self, player):
        result = original(self, player)
        try:
            if player.current_track.id is not None:
                label = getattr(
                    self, "_ProgressPlayerWidget__time_label", None
                )
                if label is not None:
                    fullscreen = getattr(
                        self, "_ProgressPlayerWidget__fullscreen", False
                    )
                    color = " color='white'" if fullscreen else ""
                    position = max(0, int(player.position or 0))
                    label.set_markup(
                        "<span font_features='tnum'%s>%s</span>" % (
                            color, ms_to_string(position))
                    )
        except Exception:
            pass
        return result

    ProgressPlayerWidget._on_current_changed = _on_current_changed
    ProgressPlayerWidget._apple_progress_patched = True
    return True


class PlayerBar(Gtk.Box):
    """Bottom transport bar built from native Lollypop player widgets."""

    def __init__(self, toolbar=None, container=None, panel=None,
                 revealer=None, toggle=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.set_name("apple-player-bar")
        self.get_style_context().add_class("apple-player-bar")
        self.set_vexpand(False)
        self.set_size_request(-1, 62)
        self._toolbar = toolbar
        self._container = container
        self._panel = panel
        self._revealer = revealer
        self._toggle = toggle
        self._signal_ids = []
        self._settings_ids = []
        self._updating_volume = False
        self._updating_shuffle = False
        self._updating_repeat = False
        self._destroyed = False
        self._headerbar_wrapped = False

        self._build_left()
        self._build_center()
        self._build_right()
        self._connect_player()
        self._sync_visibility()
        self.connect("destroy", self._on_destroy)
        self.connect("size-allocate", self._on_size_allocate)
        # Lollypop's player widgets cache signals while unmapped.  Refresh the
        # native scrubber once the bar is actually on screen so the elapsed
        # time label is populated even when the track was already playing
        # before the bar was constructed.
        self.connect("map", self._on_map)

    # ------------------------------------------------------------ layout

    def _build_left(self):
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        left.get_style_context().add_class("apple-player-left")
        left.set_valign(Gtk.Align.CENTER)
        left.set_size_request(230, -1)

        try:
            self.artwork = ArtworkPlayerWidget(
                ArtBehaviour.CROP_SQUARE | ArtBehaviour.ROUNDED
            )
            self.artwork.set_art_size(54, 54)
            self.artwork.set_size_request(54, 54)
            self.artwork.get_style_context().add_class("apple-player-art")
            self.artwork.update()
            left.pack_start(self.artwork, False, False, 0)
        except Exception:
            self.artwork = None
            traceback.print_exc()

        try:
            self.label = LabelPlayerWidget()
            self.label.set_xalign(0.0)
            self.label.set_justify(Gtk.Justification.LEFT)
            self.label.set_ellipsize(Pango.EllipsizeMode.END)
            self.label.set_single_line_mode(False)
            self.label.set_max_width_chars(28)
            self.label.set_width_chars(18)
            self.label.get_style_context().add_class("apple-player-label")
            self.label.update()
            left.pack_start(self.label, True, True, 0)
        except Exception:
            self.label = None
            traceback.print_exc()

        self._left = left
        self.pack_start(left, False, False, 0)

    def _build_center(self):
        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        center.set_hexpand(True)
        center.set_valign(Gtk.Align.CENTER)
        center.get_style_context().add_class("apple-player-center")

        transport = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        transport.set_halign(Gtk.Align.CENTER)
        transport.set_valign(Gtk.Align.CENTER)

        self.shuffle_button = Gtk.ToggleButton()
        self.shuffle_button.set_image(Gtk.Image.new_from_icon_name(
            _icon("media-playlist-shuffle-symbolic"), Gtk.IconSize.BUTTON
        ))
        self.shuffle_button.set_tooltip_text("Shuffle")
        self.shuffle_button.get_style_context().add_class("apple-player-extra")
        self.shuffle_button.connect("toggled", self._on_shuffle_toggled)
        transport.pack_start(self.shuffle_button, False, False, 0)

        try:
            self.buttons = ButtonsPlayerWidget(["apple-player-button"])
            self.buttons.set_halign(Gtk.Align.CENTER)
            self.buttons.set_hexpand(False)
            self.buttons.set_size_request(3 * 40, -1)
            play_button = getattr(
                self.buttons, "_ButtonsPlayerWidget__play_button", None
            )
            if play_button is not None:
                play_button.get_style_context().add_class("apple-player-play")
                play_button.set_size_request(38, 38)
            prev_button = getattr(
                self.buttons, "_ButtonsPlayerWidget__prev_button", None
            )
            next_button = getattr(
                self.buttons, "_ButtonsPlayerWidget__next_button", None
            )
            for button in (prev_button, next_button):
                if button is not None:
                    button.set_size_request(32, 32)
            self.buttons.update()
            transport.pack_start(self.buttons, False, False, 0)
        except Exception:
            self.buttons = None
            traceback.print_exc()

        self.repeat_button = Gtk.Button()
        self.repeat_button.set_image(Gtk.Image.new_from_icon_name(
            _icon("media-playlist-repeat-symbolic"), Gtk.IconSize.BUTTON
        ))
        self.repeat_button.set_tooltip_text("Repeat")
        self.repeat_button.get_style_context().add_class("apple-player-extra")
        self.repeat_button.connect("clicked", self._on_repeat_clicked)
        transport.pack_start(self.repeat_button, False, False, 0)

        center.pack_start(transport, False, False, 0)

        try:
            self.progress = ProgressPlayerWidget()
            self.progress.get_style_context().add_class("apple-player-progress")
            self.progress.set_hexpand(True)
            self.progress.set_size_request(-1, 22)
            self.progress.update()
            center.pack_start(self.progress, False, True, 0)
        except Exception:
            self.progress = None
            traceback.print_exc()

        self._center = center
        self.pack_start(center, True, True, 0)

    def _build_right(self):
        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        right.set_valign(Gtk.Align.CENTER)
        right.set_size_request(220, -1)
        right.set_halign(Gtk.Align.END)
        right.get_style_context().add_class("apple-player-right")

        volume = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        volume.get_style_context().add_class("apple-player-volume")
        volume.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name(
            _icon("audio-volume-medium-symbolic"), Gtk.IconSize.BUTTON
        )
        icon.set_valign(Gtk.Align.CENTER)
        volume.pack_start(icon, False, False, 0)
        self.volume_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.01
        )
        self.volume_scale.set_draw_value(False)
        self.volume_scale.set_size_request(84, 20)
        self.volume_scale.set_tooltip_text("Volume")
        self.volume_scale.connect("value-changed", self._on_volume_changed)
        volume.pack_start(self.volume_scale, False, False, 0)
        right.pack_start(volume, False, False, 0)
        self._volume_box = volume

        self.lyrics_button = self._extra_button(
            "text-x-generic-symbolic", "Lyrics", self._on_lyrics_clicked
        )
        self.queue_button = self._extra_button(
            "view-list-symbolic", "Up Next", self._on_queue_clicked
        )
        self.fullscreen_button = self._extra_button(
            "view-fullscreen-symbolic", "Fullscreen", self._on_fullscreen_clicked
        )
        right.pack_start(self.lyrics_button, False, False, 0)
        right.pack_start(self.queue_button, False, False, 0)
        right.pack_start(self.fullscreen_button, False, False, 0)

        self._right = right
        self._extra_buttons = (
            self.lyrics_button, self.queue_button, self.fullscreen_button
        )
        self.pack_end(right, False, False, 0)

    def _extra_button(self, icon_name, tooltip, callback):
        button = Gtk.Button()
        button.set_image(Gtk.Image.new_from_icon_name(
            _icon(icon_name), Gtk.IconSize.BUTTON
        ))
        button.set_tooltip_text(tooltip)
        button.get_style_context().add_class("apple-player-extra")
        button.connect("clicked", callback)
        return button

    # ------------------------------------------------------------ state

    def _connect_player(self):
        player = _player()
        settings = _settings()
        if player is not None:
            for signal_name, callback in (
                ("volume-changed", self._on_player_volume_changed),
            ):
                try:
                    self._signal_ids.append(
                        (player, player.connect(signal_name, callback))
                    )
                except Exception:
                    traceback.print_exc()
        if settings is not None:
            for signal_name, callback in (
                ("changed::shuffle", self._on_settings_changed),
                ("changed::repeat", self._on_settings_changed),
            ):
                try:
                    self._settings_ids.append(
                        (settings, settings.connect(signal_name, callback))
                    )
                except Exception:
                    traceback.print_exc()

        self._sync_volume()
        self._sync_shuffle()
        self._sync_repeat()

    def _sync_volume(self):
        player = _player()
        if player is None or getattr(self, "volume_scale", None) is None:
            return
        try:
            value = max(0.0, min(1.0, float(player.volume)))
        except Exception:
            value = 0.0
        self._updating_volume = True
        try:
            self.volume_scale.set_value(value)
        finally:
            self._updating_volume = False

    def _sync_shuffle(self):
        settings = _settings()
        if settings is None or getattr(self, "shuffle_button", None) is None:
            return
        try:
            active = bool(settings.get_value("shuffle"))
        except Exception:
            active = False
        self._updating_shuffle = True
        try:
            self.shuffle_button.set_active(active)
        finally:
            self._updating_shuffle = False

    def _sync_repeat(self):
        settings = _settings()
        if settings is None or getattr(self, "repeat_button", None) is None:
            return
        mode = _repeat_mode(settings)
        self._updating_repeat = True
        try:
            image = self.repeat_button.get_image()
            if mode == Repeat.TRACK:
                image.set_from_icon_name(
                    _icon("media-playlist-repeat-song-symbolic",
                          "media-playlist-repeat-symbolic"),
                    Gtk.IconSize.BUTTON,
                )
            else:
                image.set_from_icon_name(
                    _icon("media-playlist-repeat-symbolic"),
                    Gtk.IconSize.BUTTON,
                )
            context = self.repeat_button.get_style_context()
            if mode == Repeat.NONE:
                context.remove_class("active")
            else:
                context.add_class("active")
            self.repeat_button.set_tooltip_text(
                "Repeat one" if mode == Repeat.TRACK else
                "Repeat all" if mode == Repeat.ALL else "Repeat off"
            )
        finally:
            self._updating_repeat = False

    # ------------------------------------------------------------ callbacks

    def _on_map(self, *_args):
        """Refresh cached native player state after the bar is mapped."""
        if self._destroyed or getattr(self, "progress", None) is None:
            return False
        GLib.idle_add(self._refresh_progress)
        return False

    def _refresh_progress(self):
        if self._destroyed or getattr(self, "progress", None) is None:
            return False
        try:
            self.progress.update()
        except Exception:
            traceback.print_exc()
        return False

    def _on_volume_changed(self, scale):
        if self._updating_volume:
            return
        player = _player()
        if player is None:
            return
        try:
            player.set_volume(max(0.0, min(1.0, scale.get_value())))
        except Exception:
            traceback.print_exc()

    def _on_player_volume_changed(self, *_args):
        self._sync_volume()

    def _on_shuffle_toggled(self, button):
        if self._updating_shuffle:
            return
        settings = _settings()
        if settings is None:
            return
        try:
            settings.set_value(
                "shuffle", GLib.Variant("b", button.get_active())
            )
        except Exception:
            traceback.print_exc()

    def _on_settings_changed(self, *_args):
        self._sync_shuffle()
        self._sync_repeat()

    def _on_repeat_clicked(self, _button):
        settings = _settings()
        if settings is None:
            return
        mode = _repeat_mode(settings)
        next_mode = {
            Repeat.NONE: Repeat.ALL,
            Repeat.ALL: Repeat.TRACK,
            Repeat.TRACK: Repeat.NONE,
        }.get(mode, Repeat.NONE)
        _set_repeat(settings, next_mode)
        self._sync_repeat()

    def _show_panel(self, tab_method=None):
        if self._toggle is not None:
            self._toggle.set_active(True)
        if self._revealer is not None:
            self._revealer.set_reveal_child(True)
        if self._panel is not None and tab_method:
            callback = getattr(self._panel, tab_method, None)
            if callback is not None:
                callback()

    def _on_lyrics_clicked(self, _button):
        self._show_panel("show_lyrics_tab")

    def _on_queue_clicked(self, _button):
        self._show_panel("show_queue_tab")

    def _on_fullscreen_clicked(self, _button):
        try:
            App().fullscreen()
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------ visibility

    def _sync_visibility(self):
        """Hide the bottom bar while the native mini player is showing."""
        if self._destroyed:
            return False
        try:
            toolbar_visible = (
                self._toolbar is None or self._toolbar.get_visible()
            )
            container_visible = (
                self._container is None or self._container.get_visible()
            )
            mini_visible = False
            try:
                window = App().window
                mini = getattr(window, "miniplayer", None)
                if mini is not None:
                    mini_visible = mini.get_visible()
                    if mini is not getattr(self, "_watched_mini", None):
                        self._watched_mini = mini
                        mini.connect(
                            "notify::visible",
                            lambda *_a: GLib.idle_add(self._sync_visibility),
                        )
            except Exception:
                pass
            self.set_visible(
                toolbar_visible and container_visible and not mini_visible
            )
        except Exception:
            pass
        return False

    def _on_size_allocate(self, _widget, allocation):
        try:
            compact = allocation.width < 820
            very_compact = allocation.width < 620
            if getattr(self, "_left", None) is not None:
                self._left.set_visible(not compact)
            if getattr(self, "_volume_box", None) is not None:
                self._volume_box.set_visible(not compact)
            for button in getattr(self, "_extra_buttons", ()):
                button.set_visible(not very_compact)
        except Exception:
            pass

    # ------------------------------------------------------------ headerbar

    def minimize_headerbar(self):
        """Hide native headerbar transport after the bottom bar exists.

        This is intentionally best-effort and only called by the installer
        after the bar has been parented successfully.  If anything is
        missing, the native controls stay exactly where they were.
        """
        toolbar = self._toolbar
        if toolbar is None:
            return False
        try:
            playback = getattr(toolbar, "playback", None)
            buttons = getattr(playback, "player_buttons", None)
            info = getattr(toolbar, "info", None)
            title = getattr(toolbar, "title", None)
            for widget in (buttons, info, title):
                if widget is not None:
                    widget.hide()

            def enforce():
                for widget in (buttons, info, title):
                    if widget is not None and widget.get_visible():
                        widget.hide()
                return False

            for widget in (buttons, info, title):
                if widget is not None:
                    widget.connect(
                        "notify::visible",
                        lambda _w, _p: GLib.idle_add(enforce),
                    )

            original = getattr(toolbar, "hide_info_and_buttons", None)
            if original is not None and not self._headerbar_wrapped:
                def hide_info_and_buttons(_self, folded):
                    try:
                        original(folded)
                    except Exception:
                        traceback.print_exc()
                    GLib.idle_add(enforce)

                toolbar.hide_info_and_buttons = types.MethodType(
                    hide_info_and_buttons, toolbar
                )
                self._headerbar_wrapped = True
            GLib.idle_add(enforce)
            return True
        except Exception:
            traceback.print_exc()
            return False

    # ------------------------------------------------------------ lifecycle

    def is_complete(self):
        """True only if the critical native transport widgets exist."""
        return (
            getattr(self, "buttons", None) is not None and
            getattr(self, "progress", None) is not None and
            getattr(self, "shuffle_button", None) is not None and
            getattr(self, "repeat_button", None) is not None
        )

    def _on_destroy(self, *_args):
        self._destroyed = True
        for obj, ident in self._signal_ids + self._settings_ids:
            try:
                obj.disconnect(ident)
            except Exception:
                pass
        self._signal_ids.clear()
        self._settings_ids.clear()


def install_player_bar(vgrid, toolbar, container, panel=None,
                       revealer=None, toggle=None):
    """Attach the bottom bar and minimize the headerbar only on success."""
    existing = getattr(vgrid, "_apple_player_bar", None)
    if existing is not None:
        return existing

    try:
        _patch_progress_label()
    except Exception:
        traceback.print_exc()

    bar = PlayerBar(
        toolbar=toolbar,
        container=container,
        panel=panel,
        revealer=revealer,
        toggle=toggle,
    )
    if not bar.is_complete():
        try:
            bar.destroy()
        except Exception:
            pass
        raise RuntimeError(
            "bottom player bar missing native transport widgets"
        )
    try:
        vgrid.attach(bar, 0, 2, 1, 1)
        bar.show_all()
        # The player bar is a child of the window grid, so it must hide when
        # Lollypop's mini player hides the toolbar and content area.
        if toolbar is not None:
            toolbar.connect(
                "notify::visible", lambda *_a: bar._sync_visibility()
            )
        if container is not None:
            container.connect(
                "notify::visible", lambda *_a: bar._sync_visibility()
            )
            try:
                leaflet = getattr(container, "widget", None)
                if leaflet is not None:
                    leaflet.connect(
                        "notify::folded",
                        lambda *_a: GLib.timeout_add(
                            60, bar._sync_visibility),
                    )
            except Exception as e:
                Logger.debug("Could not connect leaflet folded signal: %s", e)
        vgrid._apple_player_bar = bar
    except Exception:
        try:
            bar.destroy()
        except Exception:
            pass
        raise

    # Only now is it safe to remove the native headerbar transport.
    try:
        bar.minimize_headerbar()
    except Exception:
        traceback.print_exc()

    def report():
        print(
            f"{PREFIX} Bottom player bar installed: "
            f"visible={bar.get_visible()} "
            f"alloc={bar.get_allocated_width()}x{bar.get_allocated_height()}",
            flush=True,
        )
        return False

    GLib.idle_add(report)
    return bar
