# Copyright (c) 2014-2021 Cedric Bellegarde <cedric.bellegarde@adishatz.org>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""
Custom HeaderBar with transparency and blur for Apple Music UI.
Replaces the standard Handy.HeaderBar.
"""

from gi.repository import Gtk, Gdk, GLib, Handy

from lollypop.define import App, Size
from lollypop.toolbar_playback import ToolbarPlayback
from lollypop.objects_track import Track
from lollypop.toolbar_info import ToolbarInfo
from lollypop.toolbar_title import ToolbarTitle
from lollypop.toolbar_end import ToolbarEnd
from lollypop.helper_size_allocation import SizeAllocationHelper
from lollypop.helper_signals import SignalsHelper, signals


class CustomHeaderBar(Handy.HeaderBar, SizeAllocationHelper, SignalsHelper):
    """
        Lollypop custom toolbar with transparency and blur support
    """

    @signals
    def __init__(self, window):
        """
            Init custom toolbar
            @param window as Window
        """
        Handy.HeaderBar.__init__(self)
        SizeAllocationHelper.__init__(self)
        self.__width = Size.MINI
        self.__timeout_id = None

        # Make headerbar transparent - CSS will handle blur
        self.get_style_context().add_class("custom-headerbar")

        self.__toolbar_playback = ToolbarPlayback(window)
        self.__toolbar_playback.show()
        self.__toolbar_info = ToolbarInfo()
        self.__toolbar_info.show()
        self.__toolbar_title = ToolbarTitle()
        self.__toolbar_end = ToolbarEnd(window)
        self.__toolbar_end.show()

        # Sidebar toggle button
        self.__sidebar_toggle = Gtk.Button()
        self.__sidebar_toggle.set_relief(Gtk.ReliefStyle.NONE)
        self.__sidebar_toggle.get_style_context().add_class("sidebar-toggle")
        icon = Gtk.Image.new_from_icon_name("sidebar-show-symbolic", Gtk.IconSize.MENU)
        self.__sidebar_toggle.set_image(icon)
        self.__sidebar_toggle.set_tooltip_text("Toggle Sidebar")
        self.__sidebar_toggle.connect("clicked", self.__on_sidebar_toggle_clicked)

        self.pack_start(self.__toolbar_playback)
        self.pack_start(self.__toolbar_info)
        self.pack_start(self.__sidebar_toggle)
        self.set_custom_title(self.__toolbar_title)
        self.pack_end(self.__toolbar_end)

        # Add search entry to the headerbar
        self.__search_entry = Gtk.SearchEntry()
        self.__search_entry.set_placeholder_text("Search")
        self.__search_entry.set_halign(Gtk.Align.CENTER)
        self.__search_entry.set_hexpand(True)
        self.__search_entry.set_max_width_chars(40)
        self.__search_entry.get_style_context().add_class("headerbar-search")
        self.__search_entry.connect("activate", self.__on_search_activate)
        self.__search_entry.connect("search-changed", self.__on_search_changed)

        # Center the search entry in a box
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        search_box.set_halign(Gtk.Align.CENTER)
        search_box.set_valign(Gtk.Align.CENTER)
        search_box.pack_start(self.__search_entry, False, False, 0)
        self.set_custom_title(search_box)

        return [
            (App().player, "current-changed", "_on_current_changed"),
            (App().window.container.widget, "notify::folded",
             "_on_container_folded")
        ]

    def do_get_preferred_width(self):
        """
            Allow window resize
            @return (int, int)
        """
        width = max(Size.PHONE, self.__width)
        return (Size.PHONE, width)

    def hide_info_and_buttons(self, folded):
        """
            Hide information and buttons
            @param folded as bool
        """
        def show_children():
            self.__timeout_id = None
            self.__toolbar_info.show_children()

        if self.__timeout_id is not None:
            GLib.source_remove(self.__timeout_id)
            self.__timeout_id = None

        if folded:
            self.__toolbar_playback.player_buttons.hide()
            self.__toolbar_title.hide()
            self.__toolbar_info.hide_children()
        else:
            if App().player.current_track.id is not None:
                self.__toolbar_title.show()
            # If user double click headerbar to maximize window
            # We do not want info bar to receive click signal
            self.__timeout_id = GLib.timeout_add(200, show_children)
            self.__toolbar_playback.player_buttons.show()

    @property
    def end(self):
        """
            Return end toolbar
            @return ToolbarEnd
        """
        return self.__toolbar_end

    @property
    def info(self):
        """
            Return info toolbar
            @return ToolbarInfo
        """
        return self.__toolbar_info

    @property
    def playback(self):
        """
            Return playback toolbar
            @return ToolbarPlayback
        """
        return self.__toolbar_playback

    @property
    def title_widget(self):
        """
            Return title widget
            @return ToolbarTitle
        """
        return self.__toolbar_title

    @property
    def search_entry(self):
        """
            Return search entry
            @return Gtk.SearchEntry
        """
        return self.__search_entry

    # Signal handlers
    def _on_current_changed(self, *args):
        """Update title when track changes"""
        track = App().player.current_track
        if track and track.id:
            self.__toolbar_title.show()
        else:
            self.__toolbar_title.hide()

    def _on_container_folded(self, widget, *args):
        """Handle sidebar folded state"""
        self.hide_info_and_buttons(widget.get_folded())
        # Update sidebar toggle button icon
        if hasattr(self, '__sidebar_toggle'):
            icon = self.__sidebar_toggle.get_image()
            if icon:
                icon.set_from_icon_name(
                    "sidebar-hide-symbolic" if widget.get_folded() else "sidebar-show-symbolic",
                    Gtk.IconSize.MENU
                )

    def __on_search_activate(self, entry):
        """Handle search activation"""
        query = entry.get_text().strip()
        if query:
            # Send search to SPA via webview
            if App().window.container.webview:
                App().window.container.webview.ipc.send({
                    "type": "SEARCH",
                    "payload": {"query": query}
                })

    def __on_search_changed(self, entry):
        """Handle search text changes (debounced)"""
        query = entry.get_text().strip()
        # Debounce: could add a timeout here for live search
        pass

    def __on_sidebar_toggle_clicked(self, button):
        """Handle sidebar toggle button click"""
        action = App().lookup_action("sidebar")
        if action:
            state = action.get_state()
            folded = not state.get_boolean()
            action.change_state(GLib.Variant.new_boolean(folded))
            # Update button icon
            icon = button.get_image()
            if icon:
                icon.set_from_icon_name(
                    "sidebar-hide-symbolic" if folded else "sidebar-show-symbolic",
                    Gtk.IconSize.MENU
                )