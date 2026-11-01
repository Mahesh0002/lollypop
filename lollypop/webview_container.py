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

from lollypop.objects_album import Album
from lollypop.define import App, StorageType, ArtSize

# Use real Album object for cover path lookup (has all needed attrs)
from lollypop.web_ipc import WebIPC
from lollypop.logger import Logger

# Cap for the initial hydrate; Phase 2 adds pagination
LIBRARY_LIMIT = 300
# Default page size for paginated loads
LIBRARY_PAGE_SIZE = 100

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
        self.__ipc.on("PLAY", self.__on_play)
        self.__ipc.on("PAUSE", self.__on_pause)
        self.__ipc.on("SEEK", self.__on_seek)
        self.__ipc.on("NAVIGATE", self.__on_navigate)
        # Connect to player signals for PLAYBACK_STATE updates
        self.__connect_player_signals()
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

    def __connect_player_signals(self):
        """Connect to player signals for PLAYBACK_STATE updates"""
        try:
            # Listen for track changes
            App().player.connect("current-changed", self.__on_current_changed)
            # Listen for play/pause
            App().player.connect("notify::is-playing", self.__on_playing_changed)
            # Listen for position updates (emitted periodically)
            App().player.connect("notify::position", self.__on_position_changed)
        except Exception as e:
            Logger.warning("WebViewContainer: player signals unavailable: %s", e)

    def __emit_playback_state(self):
        """Emit current playback state to SPA"""
        try:
            track = App().player.current_track
            if track and track.id:
                self.__ipc.send({
                    "type": "PLAYBACK_STATE",
                    "payload": {
                        "playing": App().player.is_playing,
                        "trackId": track.id,
                        "position": App().player.position / 1000.0,  # ms -> seconds
                        "duration": track.duration,
                    }
                })
        except Exception as e:
            Logger.debug("WebViewContainer::__emit_playback_state(): %s", e)

    def __on_current_changed(self, *args):
        self.__emit_playback_state()

    def __on_playing_changed(self, *args):
        self.__emit_playback_state()

    def __on_position_changed(self, *args):
        # Throttle position updates to avoid flooding
        self.__emit_playback_state()

    def __on_get_library(self, payload, message):
        """
            SPA asked for the library. Phase 0 stub: flat album list.
            Supports pagination via payload: {offset, limit}
            @param payload as dict|None
            @param message as dict
        """
        offset = 0
        limit = LIBRARY_PAGE_SIZE
        if payload and isinstance(payload, dict):
            offset = int(payload.get("offset", 0) or 0)
            limit = int(payload.get("limit", LIBRARY_PAGE_SIZE) or LIBRARY_PAGE_SIZE)
            limit = min(limit, LIBRARY_LIMIT)

        try:
            album_ids = App().albums.get_ids([], [],
                                              StorageType.COLLECTION |
                                              StorageType.SAVED)
        except Exception as e:
            Logger.error("WebViewContainer::__on_get_library(): %s", e)
            album_ids = []

        albums = []
        for album_id in album_ids[offset:offset + limit]:
            try:
                lp_album_id = App().albums.get_lp_album_id(album_id)
                # Get cover art path (cached, medium size)
                cover_path = None
                try:
                    cover_path = App().album_art.get_cache_path(
                        Album(album_id),
                        ArtSize.MEDIUM,
                        ArtSize.MEDIUM)
                except Exception:
                    pass

                albums.append({
                    "id": album_id,
                    "name": App().albums.get_name(album_id),
                    "artists": App().albums.get_artists(album_id),
                    "year": App().albums.get_year(album_id),
                    "lp_album_id": lp_album_id,
                    "cover_path": cover_path,
                })
            except Exception:
                continue

        Logger.info("WebViewContainer::__on_get_library(): %d/%d albums (offset=%d)",
                    len(albums), len(album_ids), offset)
        self.__ipc.send({
            "type": "LIBRARY_LOADED",
            "payload": {
                "albums": albums,
                "total": len(album_ids),
                "offset": offset,
                "limit": limit,
                "truncated": len(album_ids) > offset + len(albums)
            }
        })

    # ----- Playback control (Phase 2) -----
    def __on_play(self, payload, message):
        """
            SPA requests playback of a track/album
            @param payload as dict: {trackId: int, queue?: int[]}
        """
        if not payload or "trackId" not in payload:
            return
        track_id = payload["trackId"]
        queue = payload.get("queue", [])
        try:
            # Get the track object and play it
            track = App().tracks.get_track(track_id)
            if track and track.id:
                # If queue provided, set up the playlist
                if queue:
                    albums = [App().albums.get_album_id(t) for t in queue]
                    # TODO: properly set up album queue
                    pass
                App().player.load(track)
        except Exception as e:
            Logger.error("WebViewContainer::__on_play(): %s", e)

    def __on_pause(self, payload, message):
        """SPA requests pause"""
        try:
            App().player.pause()
        except Exception as e:
            Logger.error("WebViewContainer::__on_pause(): %s", e)

    def __on_seek(self, payload, message):
        """
            SPA requests seek
            @param payload as dict: {position: float} (seconds)
        """
        if not payload or "position" not in payload:
            return
        position = int(payload["position"] * 1000)  # convert to ms
        try:
            App().player.seek(position)
        except Exception as e:
            Logger.error("WebViewContainer::__on_seek(): %s", e)

    def __on_navigate(self, payload, message):
        """
            SPA requests navigation
            @param payload as dict: {view: str, id?: int}
            Views: 'album', 'artist', 'playlist', 'tracks', 'search'
        """
        if not payload or "view" not in payload:
            return
        view = payload["view"]
        target_id = payload.get("id")
        try:
            if view == "album" and target_id:
                self.__load_album_detail(target_id)
            elif view == "tracks" and target_id:
                self.__load_album_tracks(target_id)
            elif view == "search" and payload.get("query"):
                self.__search(payload["query"])
            # Other views can be added as needed
        except Exception as e:
            Logger.error("WebViewContainer::__on_navigate(): %s", e)

    def __load_album_detail(self, album_id):
        """Load album details and tracks for AlbumDetailPage"""
        try:
            album = Album(album_id)
            if album.id is None:
                return
            # Get tracks for this album
            track_ids = App().albums.get_disc_track_ids(album_id, [], [], True)
            tracks = []
            for tid in track_ids:
                try:
                    track = App().tracks.get_track(tid)
                    if track and track.id:
                        tracks.append({
                            "id": track.id,
                            "name": track.name,
                            "artists": track.artists,
                            "album": album.name,
                            "duration": track.duration,
                            "track_number": track.tracknumber,
                            "disc_number": track.discnumber,
                        })
                except Exception:
                    continue

            lp_album_id = App().albums.get_lp_album_id(album_id)
            cover_path = None
            try:
                cover_path = App().album_art.get_cache_path(
                    Album(album_id), ArtSize.BIG, ArtSize.BIG)
            except Exception:
                pass

            self.__ipc.send({
                "type": "ALBUM_LOADED",
                "payload": {
                    "album": {
                        "id": album_id,
                        "name": album.name,
                        "artists": album.artists,
                        "year": album.year,
                        "lp_album_id": lp_album_id,
                        "cover_path": cover_path,
                    },
                    "tracks": tracks
                }
            })
        except Exception as e:
            Logger.error("WebViewContainer::__load_album_detail(): %s", e)

    def __load_album_tracks(self, album_id):
        """Load just tracks for an album (for TrackList view)"""
        try:
            track_ids = App().albums.get_disc_track_ids(album_id, [], [], True)
            tracks = []
            for tid in track_ids:
                try:
                    track = App().tracks.get_track(tid)
                    if track:
                        tracks.append({
                            "id": track.id,
                            "name": track.name,
                            "artists": track.artists,
                            "duration": track.duration,
                            "track_number": track.tracknumber,
                            "disc_number": track.discnumber,
                        })
                except Exception:
                    continue
            self.__ipc.send({
                "type": "TRACKS_LOADED",
                "payload": {"tracks": tracks, "albumId": album_id}
            })
        except Exception as e:
            Logger.error("WebViewContainer::__load_album_tracks(): %s", e)

    def __search(self, query):
        """Search the library (stub for Phase 2)"""
        self.__ipc.send({
            "type": "SEARCH_RESULTS",
            "payload": {"query": query, "albums": [], "artists": [], "tracks": []}
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