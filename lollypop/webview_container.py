# Copyright (c) 2014-2021 Cedric Bellegarde <adishatz.org>
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
    Hosts the local SPA (Apple Music UI) inside a WebKit2GTK 4.1 WebView.

    GTK3 + WebKit2 4.1 path: no GTK4 migration required. The SPA replaces the
    native browse views only; GStreamer, the scanner, MPRIS and the DB are
    untouched.
"""

import os

import gi
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, Gdk, Gio, GLib, WebKit2

from lollypop.define import App, StorageType
from lollypop.web_ipc import WebIPC
from lollypop.logger import Logger

# Cap for the initial hydrate; Phase 2 adds pagination
LIBRARY_LIMIT = 300

# Override to point at a dev server or an alternate build.
ENV_WEB_DIR = "LOLLYPOP_WEB_DIR"


def spa_index_path():
    """
        Resolve index.html of the built SPA.
        Order: $LOLLYPOP_WEB_DIR, installed datadir, source tree.
        @return str or None
    """
    override = os.environ.get(ENV_WEB_DIR)
    if override:
        candidate = os.path.join(override, "index.html")
        if os.path.exists(candidate):
            return candidate
        Logger.warning("WebViewContainer::spa_index_path(): %s missing",
                       candidate)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    # <prefix>/share/lollypop/web -- walk up from the installed module
    node = here
    for _ in range(6):
        candidates.append(os.path.join(node, "share", "lollypop", "web",
                                       "index.html"))
        parent = os.path.dirname(node)
        if parent == node:
            break
        node = parent
    # running from the source tree: <repo>/lollypop/webview_container.py
    candidates.append(os.path.join(os.path.dirname(here), "data", "web",
                                   "index.html"))
    candidates.append(os.path.join(os.path.sep, "usr", "share", "lollypop",
                                   "web", "index.html"))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


class WebViewContainer(Gtk.Box):
    """
        Gtk.Box holding a WebKit2.WebView + its WebIPC bridge.
        Drop into a Gtk.Stack page named "web".
    """

    def __init__(self, ipc=None):
        """
            Init container
            @param ipc as WebIPC or None to create one
        """
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.__ipc = ipc if ipc is not None else WebIPC()

        settings = WebKit2.Settings.new()
        settings.set_enable_javascript(True)
        settings.set_enable_smooth_scrolling(True)
        # file:// SPA may reach sibling assets
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_allow_universal_access_from_file_urls(True)
        settings.set_enable_developer_extras(True)
        settings.set_enable_write_console_messages_to_stdout(True)

        self.__webview = WebKit2.WebView.new_with_user_content_manager(
            self.__ipc.content_manager)
        self.__webview.set_settings(settings)
        # Lollypop's GTK CSS makes the window background transparent
        # (rounded corners under the compositor). Without an opaque
        # WebView background the desktop shows through the page and every
        # backdrop-filter blurs the wallpaper instead of the UI.
        bg = Gdk.RGBA()
        bg.parse("#181818")
        try:
            self.__webview.set_background_color(bg)
        except Exception as e:
            Logger.warning("WebViewContainer: no set_background_color: %s", e)
        self.__webview.set_hexpand(True)
        self.__webview.set_vexpand(True)
        self.__webview.connect("load-failed", self.__on_load_failed)
        self.__webview.connect("load-changed", self.__on_load_changed)
        # Do not let the SPA handle target=_blank / window.open
        self.__webview.connect("create", self.__on_create)
        self.__ipc.bind(self.__webview)
        self.add(self.__webview)
        self.show_all()
        self.__ipc.on("GET_LIBRARY", self.__on_get_library)
        self.__load()

    @property
    def webview(self):
        """
            @return WebKit2.WebView
        """
        return self.__webview

    @property
    def ipc(self):
        """
            @return WebIPC
        """
        return self.__ipc

    def send(self, message):
        """
            Convenience passthrough, Python -> SPA
            @param message as dict
        """
        self.__ipc.send(message)

    def reload(self):
        """
            Reload the SPA (dev helper)
        """
        self.__webview.reload()

############
# PRIVATE  #
############
    def __load(self):
        """
            Load the SPA
        """
        index = spa_index_path()
        if index is None:
            Logger.error("WebViewContainer::__load(): no SPA found, "
                         "set %s", ENV_WEB_DIR)
            self.__show_error("UI bundle not found.\nSet $%s" % ENV_WEB_DIR)
            return
        uri = Gio.File.new_for_path(index).get_uri()
        Logger.info("WebViewContainer::__load(): %s", uri)
        self.__webview.load_uri(uri)

    def __show_error(self, text):
        """
            Render a readable failure instead of a blank webview
            @param text as str
        """
        label = Gtk.Label()
        label.set_text(text)
        label.set_justify(Gtk.Justification.CENTER)
        label.get_style_context().add_class("dim-label")
        self.__webview.destroy()
        self.remove(self.__webview)
        self.__webview = None
        self.add(label)
        label.show()

    def __on_get_library(self, payload, message):
        """
            SPA asked for the library. Phase 0 stub: flat album list.
            @param payload as dict|None
            @param message as dict
        """
        try:
            album_ids = App().albums.get_ids([], [],
                                              StorageType.COLLECTION |
                                              StorageType.SAVED)
        except Exception as e:
            Logger.error("WebViewContainer::__on_get_library(): %s", e)
            album_ids = []
        albums = []
        for album_id in album_ids[:LIBRARY_LIMIT]:
            try:
                albums.append({
                    "id": album_id,
                    "name": App().albums.get_name(album_id),
                    "artists": App().albums.get_artists(album_id),
                })
            except Exception:
                continue
        Logger.info("WebViewContainer::__on_get_library(): %d/%d albums",
                    len(albums), len(album_ids))
        self.__ipc.send({
            "type": "LIBRARY_LOADED",
            "payload": {
                "albums": albums,
                "total": len(album_ids),
                "truncated": len(album_ids) > len(albums)
            }
        })

    def __on_load_failed(self, webview, event, failing_uri, error, *ignore):
        """
            @return bool to stop further handling
        """
        Logger.error("WebViewContainer::__on_load_failed(): %s -> %s",
                     failing_uri, error)
        return False

    def __on_load_changed(self, webview, event, *ignore):
        """
            @param event as WebKit2.LoadEvent
        """
        if event == WebKit2.LoadEvent.FINISHED:
            self.__ipc.flush()
            # Tell the SPA we are alive so it can request the library
            self.__ipc.send({"type": "HOST_READY", "payload": {
                             "version": "1.4.48",
                             "protocol": 1}})

    def __on_create(self, webview, navigation_action, *ignore):
        """
            Never spawn a second WebView
            @return bool
        """
        return True