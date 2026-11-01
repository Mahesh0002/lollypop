#!/usr/bin/env python3
"""Lollypop Now Playing side panel: artwork, metadata, lyrics, queue.

The panel sits in a Gtk.Revealer. A toggle button in Lollypop's header bar
opens it; if the toggle cannot be placed, the panel opens by default.

Artwork:  embedded tag art -> cover.jpg etc. in the folder -> backend
          api_fetcher (network, worker thread)
Lyrics:   FLAC/ID3/MP4 tags -> local cache -> backend api_fetcher (network)
Palette:  color_extractor, computed from whichever artwork file was found.
Visualizer: real GStreamer spectrum when available, synthetic fallback.
"""

import hashlib
import os
import random
import re
import sys
import threading
import time
import traceback
import math
import shutil
import signal
import tempfile
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, Gdk, GLib, Gtk, Pango
import cairo

from lollypop.define import Repeat, StorageType, ViewType
from lollypop.view import View as LollypopView

PREFIX = "[Lollypop Extension]"
PADDING = 8
# Vertical rhythm between the stacked sections (artwork / metadata / media /
# lyrics / queue).  Doubling the old 4px gives the panel room to breathe
# without pushing the lyrics scroller off-screen.
PANEL_GAP = 8
MEDIA_HEIGHT = 116  # progress + buttons over the visualizer backdrop
PANEL_DEFAULT_WIDTH = 320
# Sentinel sidebar row id for the injected "Songs" library row.  Lollypop's
# Type enum has no free slot for a songs list, so we use an unused negative
# sentinel that the native sidebar container ignores on its own.
SONGS_SIDEBAR_ROW_ID = -999


def _log(*args):
    print(PREFIX, *args, flush=True)


def _debug(*args):
    if os.environ.get("LOLLYPOP_EXT_DEBUG") == "1":
        _log(*args)


NEUTRAL_PALETTE = ((34, 34, 44), (176, 156, 236), (126, 176, 226))

try:
    from lollypop.color_extractor import (
        PaletteStyler, PageStyler, extract_palette, NEUTRAL_PALETTE)
except Exception:
    PaletteStyler = None
    PageStyler = None
    extract_palette = None
    _log("Color module unavailable:")
    traceback.print_exc()

# Imported separately so an older/partial color_extractor that lacks
# AmbientStyler/PageStyler still leaves the proven PaletteStyler working.
try:
    from lollypop.color_extractor import AmbientStyler
except Exception:
    AmbientStyler = None

try:
    from lollypop.accent_provider import WallpaperAccentProvider, FALLBACK_ACCENT
except Exception:
    WallpaperAccentProvider = None
    FALLBACK_ACCENT = (10, 132, 255)
    _log("Wallpaper accent provider unavailable:")
    traceback.print_exc()

try:
    from lollypop.apple_shell import install_apple_shell
except Exception:
    install_apple_shell = None
    _log("Apple shell hooks unavailable:")
    traceback.print_exc()

try:
    from lollypop.player_bar import install_player_bar
except Exception:
    install_player_bar = None
    _log("Bottom player bar unavailable:")
    traceback.print_exc()

try:
    from lollypop.synced_lyrics import SyncedLyricsView, has_synced_lyrics
except Exception:
    SyncedLyricsView = None
    has_synced_lyrics = lambda _text: False
    _log("Synced lyrics module unavailable:")
    traceback.print_exc()


# ---------------------------------------------------------------- config

UI_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.environ.get(
    "LOLLYPOP_EXT_BACKEND_DIR",
    os.path.join(os.path.dirname(UI_DIR), "backend"),
)

CACHE_DIR = os.path.expanduser("~/.cache/lollypop-extension")
EMBEDDED_ART_DIR = os.path.join(CACHE_DIR, "artwork")
ONLINE_ART_DIR = os.path.join(CACHE_DIR, "online-artwork")
LYRICS_DIR = os.path.join(CACHE_DIR, "lyrics")

# cava config for the visualizer (ported from Quickshell's raw_output_config).
_CAVA_CONFIG = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "cava_config.txt",
))

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
COVER_STEMS = ("cover", "folder", "front", "album")


# ---------------------------------------------------------------- backend

_backend_lock = threading.Lock()
_backend = None
_backend_failed = False


def _api():
    """Import the backend api_fetcher lazily (worker thread). None if broken."""
    global _backend, _backend_failed
    with _backend_lock:
        if _backend is not None or _backend_failed:
            return _backend
        try:
            if BACKEND_DIR not in sys.path:
                sys.path.append(BACKEND_DIR)
            import api_fetcher

            _backend = api_fetcher
        except Exception:
            _backend_failed = True
            _log("Backend api_fetcher unavailable.")
        return _backend


# ---------------------------------------------------------------- helpers

TrackInfo = namedtuple(
    "TrackInfo",
    "has_track title artist primary_artist album audio_path duration_ms",
)
EMPTY_INFO = TrackInfo(False, "", "", "", "", None, 0)


def _attr(obj, names):
    if obj is None:
        return None
    for name in names:
        try:
            value = getattr(obj, name, None)
            if value is not None and value != "" and not callable(value):
                return value
        except Exception:
            pass
    return None


def _local_path(value):
    import urllib.parse
    if not value:
        return None
    try:
        value = os.fspath(value)
    except TypeError:
        value = str(value)
    if value.startswith("file://"):
        value = urllib.parse.unquote(urllib.parse.urlparse(value).path)
    elif "://" in value:
        return None
    if not os.path.isabs(value):
        return None
    value = os.path.realpath(value)
    return value if os.path.isfile(value) else None


def _track_info(track):
    if track is None or getattr(track, "id", None) is None:
        return EMPTY_INFO

    artists = _attr(track, ("artists",)) or []
    if isinstance(artists, str):
        artists = [artists]
    artists = [str(a).strip() for a in artists if str(a).strip()]

    album = _attr(track, ("album_name",))
    if not album:
        album = _attr(_attr(track, ("album",)), ("name", "title"))

    try:
        duration_ms = int(_attr(track, ("duration",)) or 0)
    except (TypeError, ValueError):
        duration_ms = 0

    return TrackInfo(
        True,
        str(_attr(track, ("name", "title")) or "").strip(),
        ", ".join(artists),
        artists[0] if artists else "",
        str(album or "").strip(),
        _local_path(_attr(track, ("path", "uri"))),
        duration_ms,
    )


def _image_ext(data, mime=None):
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    return {
        "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
        "image/webp": ".webp", "image/bmp": ".bmp",
    }.get((mime or "").lower(), ".img")


def _cache_image_bytes(directory, data, mime=None):
    if not data:
        return None
    try:
        os.makedirs(directory, exist_ok=True)
        data = bytes(data)
        name = hashlib.sha256(data).hexdigest()[:32] + _image_ext(data, mime)
        path = os.path.join(directory, name)
        if os.path.isfile(path) and os.path.getsize(path) == len(data):
            return path
        # A fixed "path.tmp" lets two workers writing the same artwork race:
        # both open/truncate the same temp file, then one os.replace() wins
        # and the other raises FileNotFoundError.  A unique temp file per
        # writer keeps both replacements atomic and successful.
        fd = None
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(
                prefix=os.path.basename(path) + ".", suffix=".tmp",
                dir=directory,
            )
            with os.fdopen(fd, "wb") as handle:
                fd = None
                handle.write(data)
            os.replace(tmp, path)
            tmp = None
            return path
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except OSError:
        return None


# ---------------------------------------------------------------- artwork / lyrics

_mutagen_warned = False


def _embedded_art(audio_path):
    global _mutagen_warned
    if not audio_path:
        return None
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return None

    try:
        audio = MutagenFile(audio_path)
        if audio is None:
            return None

        data = mime = None
        pictures = getattr(audio, "pictures", None)
        if pictures:
            front = [p for p in pictures if getattr(p, "type", None) == 3]
            picture = (front or pictures)[0]
            data, mime = picture.data, picture.mime

        tags = getattr(audio, "tags", None)
        if not data and tags is not None and callable(getattr(tags, "getall", None)):
            frames = tags.getall("APIC")
            if frames:
                front = [f for f in frames if getattr(f, "type", None) == 3]
                frame = (front or frames)[0]
                data, mime = frame.data, frame.mime

        if not data and tags is not None:
            try:
                covers = tags.get("covr")
                if covers:
                    data = bytes(covers[0])
            except Exception:
                pass

        return _cache_image_bytes(EMBEDDED_ART_DIR, data, mime)
    except Exception:
        return None


def _folder_cover(audio_path):
    if not audio_path:
        return None
    folder = os.path.dirname(audio_path)
    try:
        names = {n.lower(): n for n in os.listdir(folder)}
    except OSError:
        return None
    for stem in COVER_STEMS:
        for ext in IMAGE_EXTENSIONS:
            name = names.get(stem + ext)
            if name:
                path = os.path.join(folder, name)
                if os.path.isfile(path):
                    return path
    return None


_online_art_misses = set()


def _online_artwork(info):
    api = _api()
    if api is None or not info.primary_artist or api.is_unknown(info.primary_artist):
        return None

    key = (info.primary_artist.casefold(), info.album.casefold(), info.title.casefold())
    if key in _online_art_misses:
        return None

    album = info.album
    if album and not api.is_unknown(album) and album.casefold() != "singles":
        folder = os.path.join(
            ONLINE_ART_DIR,
            hashlib.sha1(f"{info.primary_artist}|{album}".casefold().encode("utf-8")).hexdigest()[:20],
        )
        os.makedirs(folder, exist_ok=True)
        path = api.fetch_album_cover(info.primary_artist, album, folder)
        if path and os.path.isfile(path):
            return path

    if info.title:
        data = api.fetch_song_art(info.primary_artist, info.title)
        path = _cache_image_bytes(ONLINE_ART_DIR, data)
        if path:
            return path

    _online_art_misses.add(key)
    return None


_LRC_TIME = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")
_LRC_META = re.compile(r"^\s*\[[A-Za-z]+:[^\]]*\]\s*$")


def _plain_lyrics(text):
    lines = []
    for line in (text or "").splitlines():
        if _LRC_META.match(line):
            continue
        lines.append(_LRC_TIME.sub("", line).strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _tag_lyrics(audio_path):
    if not audio_path:
        return None
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(audio_path)
        tags = getattr(audio, "tags", None) if audio is not None else None
        if tags is None:
            return None

        getall = getattr(tags, "getall", None)
        if callable(getall):
            for frame in getall("USLT"):
                text = str(getattr(frame, "text", "")).strip()
                if text:
                    return text

        for key in ("LRC", "LYRICS", "UNSYNCEDLYRICS", "\xa9lyr"):
            value = tags.get(key)
            if not value:
                continue
            if isinstance(value, (list, tuple)):
                value = "\n".join(str(v) for v in value)
            value = str(value).strip()
            if value:
                return value
    except Exception:
        pass
    return None


def _read_text_file(path):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def _sidecar_lyrics(audio_path):
    """Read a same-name .lrc file next to the track, if one exists."""
    if not audio_path:
        return None

    folder = os.path.dirname(audio_path)
    stem = os.path.splitext(os.path.basename(audio_path))[0]

    for extension in (".lrc", ".LRC", ".Lrc"):
        text = _read_text_file(os.path.join(folder, stem + extension))
        if text:
            return text

    try:
        for name in os.listdir(folder):
            name_stem, extension = os.path.splitext(name)
            if name_stem == stem and extension.lower() == ".lrc":
                return _read_text_file(os.path.join(folder, name))
    except OSError:
        pass
    return None


def _lyrics_identity(info):
    """Identity of the *recording*, not just its artist/title.

    Artist+title alone is not unique: albums routinely contain studio, live,
    acoustic or re-recorded versions with the same name.  The album and
    duration distinguish those, and they are also the values sent to LRCLIB,
    so the cache key matches the lookup inputs.
    """
    artist = info.artist or info.primary_artist or ""
    try:
        duration_ms = int(info.duration_ms or 0)
    except (TypeError, ValueError):
        duration_ms = 0
    return (
        artist.strip().casefold(),
        (info.title or "").strip().casefold(),
        (info.album or "").strip().casefold(),
        duration_ms,
    )


def _lyrics_cache_path(info):
    identity = "\x1f".join(str(part) for part in _lyrics_identity(info))
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()
    return os.path.join(LYRICS_DIR, digest + ".txt")


def _write_text_atomic(path, text):
    """Write a cache file without exposing a truncated read to other workers."""
    directory = os.path.dirname(path)
    fd = None
    tmp = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".lyrics-", suffix=".tmp", dir=directory
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(text)
        os.replace(tmp, path)
        tmp = None
        return True
    except OSError:
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _cached_lyrics(info):
    try:
        with open(_lyrics_cache_path(info), encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def _lyrics_duration_seconds(info):
    if not info.duration_ms:
        return None
    return info.duration_ms / 1000.0


def _local_lyrics_are_trustworthy(text, info):
    """Reject old LRC tags whose timestamps do not fit this recording.

    This is intentionally the same validation the batch tagger uses.  Without
    it, a stale sidecar can keep winning before the online source is asked.
    """
    if not has_synced_lyrics(text):
        return True
    api = _api()
    duration = _lyrics_duration_seconds(info)
    if api is None or not duration:
        return True
    try:
        valid, metrics, reason = api.validate_lrc(text, duration)
    except Exception:
        return True
    if not valid:
        _debug(
            "Ignoring implausible local lyrics:",
            reason,
            f"first={metrics.get('first')}",
            f"last={metrics.get('last')}",
            f"duration={duration}",
        )
    return valid


def _library_artist_hint(audio_path):
    """Best-effort first library folder, used for badly tagged artists."""
    if not audio_path:
        return None
    parts = os.path.realpath(audio_path).split(os.sep)
    generic = {
        "singles", "single", "non-album", "unsorted",
        "various artists", "downloads", "music",
    }
    try:
        index = parts.index("Music") + 1
    except ValueError:
        index = max(0, len(parts) - 3)
    if index < len(parts) - 1:
        candidate = parts[index].strip()
        if candidate and candidate.casefold() not in generic:
            return candidate
    return None


def _best_local_lyrics(info):
    """Sidecar .lrc -> embedded tag -> cache, preferring trusted synced sources."""
    fallback = None
    for source, loader in (
        ("sidecar", lambda: _sidecar_lyrics(info.audio_path)),
        ("tag", lambda: _tag_lyrics(info.audio_path)),
        ("cache", lambda: _cached_lyrics(info)),
    ):
        text = loader()
        if not text:
            continue
        if has_synced_lyrics(text):
            if _local_lyrics_are_trustworthy(text, info):
                return text
            _debug("Skipped untrusted", source, "lyrics for", info.title)
            continue
        if fallback is None:
            fallback = text
    return fallback


_online_lyrics_misses = set()
_online_lyrics_plain = set()


def _online_lyrics(info):
    api = _api()
    if api is None or not info.primary_artist or not info.title:
        return None

    key = _lyrics_identity(info)
    if key in _online_lyrics_misses or key in _online_lyrics_plain:
        return None

    text = api.fetch_synced_lyrics(
        info.artist or info.primary_artist,
        info.title,
        album_name=info.album or None,
        duration=_lyrics_duration_seconds(info),
        artist_hint=_library_artist_hint(info.audio_path),
    )
    if text and text.strip():
        text = text.strip()
        if not has_synced_lyrics(text):
            _online_lyrics_plain.add(key)
        _write_text_atomic(_lyrics_cache_path(info), text)
        return text

    _online_lyrics_misses.add(key)
    return None


# ---------------------------------------------------------------- widgets

DEFAULT_ACCENT = (0.65, 0.55, 0.98)


class CavaFeed:
    """Runs cava and streams normalised 0..1 band values to a callback.

    Port of Quickshell's MediaControls.qml cava Process: cava does the FFT,
    autosens normalisation and noise reduction, so the visualizer just draws
    what it receives — no dB math, no tilt, no gain, no gate.  It produces
    the smooth, balanced wave that made the Quickshell visualizer look good.

    A frame is a line of ``;``-separated ASCII integers (0..1000).  cava is
    spawned only while playback is live and the panel is mapped, and is
    SIGTERMed on pause/close/destroy so no orphan process is left behind.
    """

    def __init__(self, config_path, on_frame):
        self._config = config_path
        self._on_frame = on_frame
        self._pid = None
        self._fd = None
        self._watch = 0
        self._buffer = b""
        self._running = False
        self._last_frame = 0.0
        self._debug_first_watch = True

    @property
    def running(self):
        return self._running

    @property
    def available(self):
        """True when cava is installed and the config file exists."""
        return (
            bool(self._config) and os.path.isfile(self._config)
            and shutil.which("cava") is not None
        )

    def start(self):
        """Spawn cava; returns True when it is running.  Idempotent."""
        if self._running:
            return True
        # Recover from an inconsistent state left by a failed spawn rather
        # than starting a second cava while the first is still alive.
        if self._pid is not None or self._watch:
            self.stop()
        if not self.available:
            return False

        cava_bin = shutil.which("cava")
        if cava_bin is None:
            return False

        try:
            # spawn_async_with_pipes() always creates the requested pipes;
            # there is no STDOUT_PIPE spawn flag.  STDERR_TO_DEV_NULL keeps
            # cava's own diagnostics out of the host log without needing an
            # stderr pipe.  DO_NOT_REAP_CHILD lets GLib drive the child watch.
            flags = (
                GLib.SpawnFlags.DO_NOT_REAP_CHILD
                | GLib.SpawnFlags.STDERR_TO_DEV_NULL
            )
            success, pid, stdin_fd, stdout, stderr_fd = (
                GLib.spawn_async_with_pipes(
                    None,
                    [cava_bin, "-p", self._config],
                    None,
                    flags,
                    None,
                    None,
                )
            )
            # With STDERR_TO_DEV_NULL, unused pipe descriptors can come back
            # as 0 (not -1).  Never touch the process's stdio descriptors.
            for fd in (stdin_fd, stderr_fd):
                if fd is not None and fd > 2:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if not success or not pid:
                if stdout is not None and stdout > 2:
                    try:
                        os.close(stdout)
                    except OSError:
                        pass
                return False
            if stdout is None or stdout <= 2:
                # The process exists but we cannot read frames.  Kill it and
                # attach a child watch so GLib reaps it instead of leaving a
                # zombie behind.
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
                GLib.child_watch_add(pid, self._reap_child)
                return False
        except Exception as exc:
            _log("cava spawn failed:", exc)
            return False

        self._pid = pid
        self._fd = stdout
        self._buffer = b""
        os.set_blocking(stdout, False)
        GLib.child_watch_add(pid, self._on_child_exit)
        self._watch = GLib.io_add_watch(
            stdout, GLib.IOCondition.IN, self._on_stdout
        )
        self._running = True
        self._debug_first_watch = True
        _debug("cava started, pid", pid)
        return True

    @staticmethod
    def _reap_child(pid, _status):
        try:
            GLib.spawn_close_pid(pid)
        except Exception:
            pass

    def stop(self):
        """SIGTERM cava and drop the pipe.  Safe to call repeatedly."""
        if self._watch or self._pid is not None:
            _debug("cava stop(): pid=", self._pid, "watch=", self._watch)
        if self._watch:
            GLib.source_remove(self._watch)
            self._watch = 0
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        pid, self._pid = self._pid, None
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        self._buffer = b""
        self._running = False

    def _on_child_exit(self, pid, status):
        # cava exited on its own (audio server went away, killed, ...).
        # A child callback can arrive after stop()/start() has already spawned
        # a replacement.  Only clear state when this is still the current
        # process; otherwise the stale callback would detach the new cava's
        # pipe/watch and leave it orphaned.
        _debug("cava exited: pid=", pid, "status=", status)
        try:
            GLib.spawn_close_pid(pid)
        except Exception:
            pass
        if pid != self._pid:
            _debug("cava stale child exit ignored: pid=", pid,
                   "current=", self._pid)
            return False
        self._running = False
        self._pid = None
        if self._watch:
            GLib.source_remove(self._watch)
            self._watch = 0
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        self._buffer = b""
        return False

    def _on_stdout(self, fd, condition):
        if self._debug_first_watch:
            self._debug_first_watch = False
            _debug("cava watch fired: fd=", fd, "condition=", condition)
        if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR
                        | GLib.IOCondition.NVAL):
            _debug("cava watch dropped: condition=", condition)
            self.stop()
            return False
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return True
        except OSError as exc:
            _debug("cava read failed:", exc)
            self.stop()
            return False
        if not data:
            return True
        self._buffer += data
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            self._emit(line)
        return True

    def _emit(self, line):
        line = line.strip()
        if not line:
            return
        try:
            # cava terminates each frame with a trailing ";", so the split
            # yields an empty final segment.  Drop empty segments exactly as
            # Quickshell's QML does (".map(parseFloat).filter(!isNaN)"),
            # otherwise every frame raises ValueError and is discarded.
            values = [
                float(value) for value in line.split(b";")
                if value.strip()
            ]
        except ValueError:
            return
        if len(values) < 2:
            return
        self._last_frame = time.monotonic()
        self._on_frame(values)


class SimpleVisualizer(Gtk.DrawingArea):
    """Bottom-anchored, blurred, translucent area graph.

    Port of the QML reference Visualizer: points in 0..max_value, moving-
    average smoothing, one flat fill at ~15% alpha, soft blurred edge and a
    30fps paint cap.

    The primary data source is cava (``CavaFeed``), which normalises the
    audio itself and produces the balanced wave seen in Quickshell.  When
    cava is unavailable, the widget falls back to GStreamer's ``spectrum``
    element (with a dB correction), and finally to a synthetic animation so
    it is never blank.
    """

    POINT_COUNT = 64
    BLUR_DOWNSCALE = 2      # render at 1/2 size, upscale bilinear => soft fill
    FRAME_MS = 33
    SPECTRUM_FLOOR = -80.0
    LINE_ALPHA = 0.78

    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_can_focus(False)
        self.set_name("simple-visualizer")

        self.max_value = 1000.0
        self.smoothing = 2
        self.fill_alpha = 0.16

        self.live = False
        self.points = [0.0] * self.POINT_COUNT
        self.accent = DEFAULT_ACCENT
        self._target = [0.0] * self.POINT_COUNT
        self._phase = 0.0
        self._timer = 0
        self._real = False
        self._last_real = 0.0
        self._seen_spectrum = False
        self._gain = 1.0
        self._spectrum_attempted = False
        self._spectrum_pending = None
        self._spectrum_idle = 0
        self._spectrum_frames = 0
        self._gst = None
        self._bus_handlers = []
        self._spectrum_filters = []

        # cava is the primary source: it normalises the audio itself, which
        # is what made the Quickshell visualizer look balanced.  The
        # GStreamer spectrum below is only a fallback when cava is missing.
        self._cava = CavaFeed(_CAVA_CONFIG, self._on_cava_frame)
        self._cava_frames = 0
        self._seen_cava = False

        self.connect("draw", self.on_draw)
        self.connect("map", self._on_map)
        self.connect("unmap", self._stop)
        self.connect("destroy", self._on_destroy)

        # cava can start at any time (it is not tied to the playbin), so we
        # do not need the NULL-state window the spectrum attach relies on.
        if not self._cava.available:
            # Lollypop restores playback immediately after Window.setup(),
            # which is after this widget is constructed.  Attach the spectrum
            # filter now, while both playbins are still in NULL state;
            # changing playbin's audio-filter after it is PLAYING does not
            # rebuild the audio chain and would never produce messages.
            self._ensure_spectrum()

    # ---- public API --------------------------------------------------

    @property
    def playing(self):
        return self.live

    def set_playing(self, playing):
        was_live = self.live
        self.live = bool(playing)
        if self.live:
            if self._cava.available:
                # cava is the preferred source; only fall back to the
                # spectrum when cava cannot run.
                if not self._cava.start():
                    self._ensure_spectrum()
            else:
                self._ensure_spectrum()
        elif was_live:
            # Mirror Quickshell: stop cava when playback pauses so no idle
            # process keeps capturing the sink.
            self._cava.stop()
        if self.get_mapped():
            self._start()
        self.queue_draw()

    def set_color(self, red, green, blue):
        # Blend the artwork accent toward white.  The graph is drawn on a
        # dark card that is often tinted with the same accent colour, so the
        # raw accent can disappear into the background.
        mix = 0.5
        self.accent = tuple(
            value * (1.0 - mix) + mix
            for value in (
                max(0.0, min(1.0, float(red))),
                max(0.0, min(1.0, float(green))),
                max(0.0, min(1.0, float(blue))),
            )
        )
        self.queue_draw()

    # ---- cava (primary source) ---------------------------------------

    def _on_cava_frame(self, values):
        """A cava frame: ``;``-separated 0..1000 values, already normalised.

        cava's autosens + noise_reduction do the work the GStreamer path
        needs the tilt/gate/gain for, so here we only resample to the widget's
        point count and scale to ``max_value``.  cava writes on the GLib main
        loop (io_add_watch), so no idle coalescing is needed.
        """
        if not self.live or not self.get_mapped():
            if self._cava_frames < 3:
                _debug("cava frame dropped: live=", self.live,
                       "mapped=", self.get_mapped())
            return
        count = len(self._target)
        if len(values) != count:
            if not values:
                values = [0.0] * count
            else:
                last = len(values) - 1
                resampled = []
                for i in range(count):
                    position = i * last / max(1, count - 1)
                    low = int(position)
                    high = min(last, low + 1)
                    fraction = position - low
                    resampled.append(
                        values[low] * (1.0 - fraction) +
                        values[high] * fraction
                    )
                values = resampled
        self._target = [
            max(0.0, min(self.max_value, value)) for value in values
        ]
        self._last_real = time.monotonic()
        self._real = True
        self._cava_frames += 1
        if not self._seen_cava:
            self._seen_cava = True
            _debug(
                "First cava frame received; max=",
                round(max(values), 1) if values else 0.0,
                "values=", [round(value, 1) for value in values[:8]],
            )
        elif os.environ.get("LOLLYPOP_EXT_DEBUG") == "1" and \
                self._cava_frames % 180 == 0:
            _debug(
                "Cava:", self._cava_frames, "frames",
                "target_max=", round(max(self._target), 1),
                "point_max=", round(max(self.points), 1),
            )

    # ---- real spectrum -------------------------------------------------

    def _ensure_spectrum(self):
        """Attach a ``spectrum`` filter to Lollypop's playbin(s), once."""
        if self._spectrum_attempted:
            return
        try:
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            from lollypop.define import App
        except Exception as exc:
            _debug("GStreamer spectrum unavailable:", exc)
            self._spectrum_attempted = True
            return

        player = getattr(App(), "player", None)
        if player is None:
            # The application may still be constructing its player; retry
            # the next time playback starts.
            return

        self._spectrum_attempted = True
        self._gst = Gst
        attached = 0
        for name in ("_playbin1", "_playbin2"):
            playbin = getattr(player, name, None)
            if playbin is None:
                continue
            try:
                # Do not replace filters owned by Lollypop or another
                # extension.  In a stock Lollypop this property is None.
                if playbin.get_property("audio-filter") is not None:
                    _debug("Spectrum skipped: playbin already has audio-filter")
                    continue

                bus = playbin.get_bus()
                if bus is None:
                    continue
                spectrum = Gst.ElementFactory.make("spectrum", None)
                if spectrum is None:
                    continue
                spectrum.set_property("bands", self.POINT_COUNT)
                spectrum.set_property("threshold", self.SPECTRUM_FLOOR)
                spectrum.set_property("post-messages", True)
                spectrum.set_property("message-magnitude", True)
                spectrum.set_property("interval", self.FRAME_MS * Gst.MSECOND)

                # Add the watch only for a filter we actually install, and
                # roll both back if the property assignment fails.
                bus.add_signal_watch()
                handler = None
                try:
                    handler = bus.connect(
                        "message::element", self._on_spectrum_message
                    )
                    playbin.set_property("audio-filter", spectrum)
                except Exception:
                    if handler is not None:
                        try:
                            bus.disconnect(handler)
                        except Exception:
                            pass
                    bus.remove_signal_watch()
                    raise
                self._bus_handlers.append((bus, handler))
                self._spectrum_filters.append((playbin, spectrum))
                attached += 1
            except Exception as exc:
                _debug("Spectrum attach failed for", name, exc)

        if attached:
            _debug("Real GStreamer spectrum attached to", attached, "playbin(s)")
        else:
            _debug("Spectrum unavailable; using synthetic visualizer")

    def _on_spectrum_message(self, _bus, message):
        gst = self._gst
        if gst is None or message.type != gst.MessageType.ELEMENT:
            return
        try:
            structure = message.get_structure()
            if structure is None or structure.get_name() != "spectrum":
                return
            magnitude = structure.get_value("magnitude")
            values = [float(value) for value in magnitude]
        except Exception:
            return
        if not values:
            return
        # Do not keep processing spectrum frames while the panel is closed
        # or the visualizer is unmapped.
        if not self.live or not self.get_mapped():
            return

        floor = self.SPECTRUM_FLOOR
        normalised = []
        for value in values:
            level = (value - floor) / -floor
            level = max(0.0, min(1.0, level))
            # A root curve lifts the quiet high bands instead of burying
            # them (v**1.35 suppressed small values and made every frame
            # peak at the bass-heavy left edge).
            normalised.append(level ** 0.7)

        if not self._seen_spectrum:
            self._seen_spectrum = True
            _debug(
                "First real spectrum frame received; max=",
                round(max(normalised), 3),
                "values=", [round(value, 2) for value in normalised[:8]],
            )

        # Bus callbacks run on a GStreamer streaming thread.  Coalesce bursts
        # into one idle callback so a busy UI cannot accumulate a queue of
        # spectrum frames.
        self._spectrum_pending = normalised
        if not self._spectrum_idle:
            self._spectrum_idle = GLib.idle_add(self._flush_spectrum)

    def _flush_spectrum(self):
        self._spectrum_idle = 0
        values = self._spectrum_pending
        self._spectrum_pending = None
        if values is not None:
            self._apply_spectrum(values)
        return False

    def _apply_spectrum(self, values):
        count = len(self._target)
        if len(values) != count:
            if not values:
                values = [0.0] * count
            else:
                last = len(values) - 1
                resampled = []
                for i in range(count):
                    position = i * last / max(1, count - 1)
                    low = int(position)
                    high = min(last, low + 1)
                    fraction = position - low
                    resampled.append(
                        values[low] * (1.0 - fraction) +
                        values[high] * fraction
                    )
                values = resampled

        # The spectrum element reports dB values, so raw bands roll off
        # steeply from bass to treble and every frame peaks at the left
        # edge.  Two corrections, applied before gain:
        #   * a tilt that boosts the higher bands, and
        #   * a noise gate so near-silence (room noise, quiet intros) does
        #     not get amplified into a phantom hump.
        tilted = []
        for i, value in enumerate(values):
            tilt = 0.35 + 1.30 * i / max(1, count - 1)
            gated = value if value >= 0.04 else 0.0
            tilted.append(min(1.0, gated * tilt))
        values = tilted

        # Adaptive gain normalises against the 90th percentile rather than
        # the absolute peak, so one loud bass spike cannot flatten the rest
        # of the curve.  No boost at all below the noise gate.
        reference = 0.0
        if values:
            ordered = sorted(values)
            reference = ordered[int(0.90 * (len(ordered) - 1))]
        target_gain = min(2.0, 0.62 / reference) if reference > 0.08 else 1.0
        self._gain += (target_gain - self._gain) * 0.25
        self._target = [
            min(1.0, value * self._gain) * self.max_value
            for value in values
        ]
        self._last_real = time.monotonic()
        self._real = True
        self._spectrum_frames += 1
        if os.environ.get("LOLLYPOP_EXT_DEBUG") == "1" and \
                self._spectrum_frames % 90 == 0:
            _debug(
                "Spectrum:", self._spectrum_frames, "frames",
                "gain=", round(self._gain, 2),
                "target_max=", round(max(self._target), 1),
                "point_max=", round(max(self.points), 1),
            )
        return False

    # ---- animation timer: runs only while mapped AND (playing or easing) --

    def _on_map(self, *_args):
        # Mirror Quickshell: cava runs while the panel is visible and
        # playback is live.
        if self.live and self._cava.available:
            if not self._cava.start():
                self._ensure_spectrum()
        self._start()

    def _start(self):
        if not self._timer:
            self._timer = GLib.timeout_add(self.FRAME_MS, self._tick)

    def _stop(self, *_args):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        # Panel hidden: stop cava so it is not capturing the sink while the
        # visualizer cannot be seen.
        self._cava.stop()

    def _on_destroy(self, *_args):
        self._stop()
        self._cava.stop()
        if self._spectrum_idle:
            GLib.source_remove(self._spectrum_idle)
            self._spectrum_idle = 0
        self._spectrum_pending = None
        for bus, handler in self._bus_handlers:
            try:
                bus.disconnect(handler)
            except Exception:
                pass
            try:
                bus.remove_signal_watch()
            except Exception:
                pass
        self._bus_handlers.clear()
        # Detach only the exact spectrum elements we installed.  If Lollypop
        # or another extension has since replaced the filter, leave it alone.
        for playbin, spectrum in self._spectrum_filters:
            try:
                if playbin.get_property("audio-filter") is spectrum:
                    playbin.set_property("audio-filter", None)
            except Exception:
                pass
        self._spectrum_filters.clear()

    def _tick(self):
        count = len(self.points)
        self._phase += 0.075
        now = time.monotonic()
        real_data = (
            self.live and self._real and
            (now - self._last_real) < 1.0
        )

        for i in range(count):
            x = i / max(1, count - 1)
            if not self.live:
                target = 0.0
            elif real_data:
                target = self._target[i]
            else:
                envelope = 0.45 + 0.55 * math.sin(math.pi * x) ** 0.8
                wave = (
                    0.50
                    + 0.22 * math.sin(x * 13.0 + self._phase)
                    + 0.15 * math.sin(x * 29.0 - self._phase * 0.72)
                    + 0.10 * math.sin(x * 47.0 + self._phase * 1.3)
                )
                target = self.max_value * max(0.04, min(0.92, wave * envelope))
            factor = 0.28 if target > self.points[i] else 0.14
            if not self.live:
                # Pause/stop: snap down fast so no bass-weighted remnant
                # lingers as a left-side hump after the music stops.
                factor = 0.38
            self.points[i] += (target - self.points[i]) * factor

        self.queue_draw()

        if not self.live and max(self.points) < 0.5:
            # fully eased to zero: stop burning CPU until playback resumes
            self.points = [0.0] * count
            self._timer = 0
            return False
        return True

    # ---- drawing -------------------------------------------------------

    def _smoothed(self):
        pts = self.points
        n = len(pts)
        win = max(0, int(self.smoothing))
        out = []
        for i in range(n):
            total = 0.0
            for j in range(-win, win + 1):
                total += pts[max(0, min(n - 1, i + j))]
            out.append(total / (2 * win + 1))
        return out

    def on_draw(self, widget, cr):
        w = float(self.get_allocated_width())
        h = float(self.get_allocated_height())
        if w <= 1 or h <= 1:
            return False

        values = self._smoothed()
        if len(values) < 2 or max(values) < 0.5:
            return False

        red, green, blue = self.accent
        scale = self.BLUR_DOWNSCALE
        sw = max(1, int(math.ceil(w / scale)))
        sh = max(1, int(math.ceil(h / scale)))

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
        ctx = cairo.Context(surface)
        ctx.scale(sw / w, sh / h)

        step = w / (len(values) - 1)
        ctx.move_to(0, h)
        for i, value in enumerate(values):
            ctx.line_to(i * step, h - (value / self.max_value) * h)
        ctx.line_to(w, h)
        ctx.close_path()
        ctx.set_source_rgba(red, green, blue, self.fill_alpha)
        ctx.fill()

        cr.save()
        cr.scale(w / sw, h / sh)
        cr.set_source_surface(surface, 0, 0)
        pattern = cr.get_source()
        pattern.set_filter(cairo.FILTER_BILINEAR)
        pattern.set_extend(cairo.EXTEND_PAD)
        cr.rectangle(0, 0, sw, sh)
        cr.fill()
        cr.restore()

        # A crisp edge keeps the blurred fill from reading as a flat colour
        # block, especially in the short media strip.
        cr.save()
        cr.set_line_width(1.5)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(red, green, blue, self.LINE_ALPHA)
        cr.move_to(0, h - (values[0] / self.max_value) * h)
        for i, value in enumerate(values[1:], start=1):
            cr.line_to(i * step, h - (value / self.max_value) * h)
        cr.stroke()
        cr.restore()
        return False


def _accent_from_pixbuf(pixbuf):
    """Saturation-weighted average colour of the artwork, as 0..1 floats."""
    try:
        small = pixbuf.scale_simple(32, 32, GdkPixbuf.InterpType.BILINEAR)
        channels = small.get_n_channels()
        stride = small.get_rowstride()
        pixels = small.get_pixels()
        tr = tg = tb = total = 0.0
        ar = ag = ab = 0.0
        for y in range(32):
            row = y * stride
            for x in range(32):
                i = row + x * channels
                r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
                peak, low = max(r, g, b), min(r, g, b)
                sat = (peak - low) / peak if peak else 0.0
                ar += r
                ag += g
                ab += b
                # only colourful pixels count, so a white cover with a thin
                # coloured motif still yields that colour, not near-white
                weight = sat * sat if sat > 0.2 else 0.0
                tr += r * weight
                tg += g * weight
                tb += b * weight
                total += weight
        if total > 0:
            r, g, b = tr / total / 255.0, tg / total / 255.0, tb / total / 255.0
        else:  # greyscale artwork: plain average
            pixel_count = 32 * 32
            r = ar / pixel_count / 255.0
            g = ag / pixel_count / 255.0
            b = ab / pixel_count / 255.0
        peak = max(r, g, b)
        if 0 < peak < 0.6:
            lift = min(4.0, 0.6 / peak)
            r, g, b = min(1.0, r * lift), min(1.0, g * lift), min(1.0, b * lift)
        return (r, g, b)
    except Exception:
        return None


def _heading(text, xalign=0.0):
    """Section heading.  Lyrics centre over their centred lines, Up Next
    stays left to match its list rows."""
    label = Gtk.Label(label=text)
    label.set_name("right-panel-heading")
    label.set_xalign(xalign)
    return label


def _add(box, child, expand=False, fill=None):
    child.show()
    box.pack_start(child, expand, expand if fill is None else fill, 0)


def _pad(widget, amount=8):
    """Uniformly pads components in the right panel."""
    widget.set_margin_start(amount)
    widget.set_margin_end(amount)
    widget.set_margin_top(amount)
    widget.set_margin_bottom(amount)
    return widget


class AlbumArtwork(Gtk.DrawingArea):
    """A big, dynamically scaled square artwork with rounded corners via Cairo."""
    def __init__(self):
        super().__init__()
        self._pixbuf = None
        self._key = None
        self.accent = None
        self.set_name("right-panel-artwork-frame")
        self.set_hexpand(True)
        self.set_vexpand(False)
        self.connect("draw", self._on_draw)
        self.connect("size-allocate", self._on_size_allocate)

    def _on_size_allocate(self, widget, allocation):
        # Force a perfect square based on available panel width.  Defer the
        # size request: changing it from inside size-allocate re-enters GTK's
        # allocation cycle and can leave sibling views with a broken
        # allocation (the album FlowBox bug).
        if allocation.width > 1:
            GLib.idle_add(self._set_square, allocation.width)

    def _set_square(self, width):
        _, current_h = self.get_size_request()
        if current_h != width:
            self.set_size_request(-1, width)
        return False

    def set_artwork(self, path):
        if path:
            try:
                key = (path, os.path.getmtime(path))
                if key == self._key:
                    return True
                self._pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
                self._key = key
                self.accent = _accent_from_pixbuf(self._pixbuf)
                self.queue_draw()
                return True
            except Exception as exc:
                _log("Artwork decode failed:", exc)

        self._key = None
        self.accent = None
        theme = Gtk.IconTheme.get_default()
        if theme.has_icon("audio-x-generic"):
            try:
                self._pixbuf = theme.load_icon("audio-x-generic", 256, 0)
            except Exception:
                self._pixbuf = None
        else:
            self._pixbuf = None
            
        self.queue_draw()
        return False

    def _on_draw(self, widget, cr):
        if not self._pixbuf:
            return False
            
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        if w <= 1 or h <= 1:
            return False
            
        # Draw 16px border-radius clipping path
        radius = 16.0
        degrees = math.pi / 180.0

        cr.new_sub_path()
        cr.arc(w - radius, radius, radius, -90 * degrees, 0 * degrees)
        cr.arc(w - radius, h - radius, radius, 0 * degrees, 90 * degrees)
        cr.arc(radius, h - radius, radius, 90 * degrees, 180 * degrees)
        cr.arc(radius, radius, radius, 180 * degrees, 270 * degrees)
        cr.close_path()
        cr.clip()

        # Scale the pixbuf accurately without altering its native aspect ratio
        pb_w = self._pixbuf.get_width()
        pb_h = self._pixbuf.get_height()
        scale = max(w / max(1, pb_w), h / max(1, pb_h))
        
        cr.scale(scale, scale)
        dx = (w / scale - pb_w) / 2.0
        dy = (h / scale - pb_h) / 2.0
        
        Gdk.cairo_set_source_pixbuf(cr, self._pixbuf, dx, dy)
        cr.paint()
        return False


class TrackInformation(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_name("right-panel-track-info")
        self.set_valign(Gtk.Align.START)

        self.title = Gtk.Label(label="Nothing playing")
        self.title.set_name("right-panel-title")
        # Centred hero column: the title/artist/album line up on the panel
        # centre axis, matching the artwork, progress bar and transport.
        self.title.set_halign(Gtk.Align.CENTER)
        self.title.set_xalign(0.5)
        self.title.set_line_wrap(True)
        self.title.set_max_width_chars(32)

        self.artist = Gtk.Label(label="")
        self.artist.set_name("right-panel-artist")
        self.artist.set_halign(Gtk.Align.CENTER)
        self.artist.set_xalign(0.5)
        self.artist.set_ellipsize(Pango.EllipsizeMode.END)

        self.album = Gtk.Label(label="")
        self.album.set_name("right-panel-album")
        self.album.set_halign(Gtk.Align.CENTER)
        self.album.set_xalign(0.5)
        self.album.set_ellipsize(Pango.EllipsizeMode.END)

        for child in (self.title, self.artist, self.album):
            self.pack_start(child, False, False, 0)

        # Gtk.Container.pack_start() does not show children.  Without this
        # the labels were allocated but never drawn, which is why the whole
        # metadata block looked empty.
        self.show_all()

    def update(self, title, artist, album):
        self.title.set_text(title or "Nothing playing")
        self.artist.set_text(artist or "Unknown artist")
        self.album.set_text(album or "Unknown album")
        _debug("Metadata:", title, "|", artist, "|", album)


class LyricsSection(Gtk.Box):
    def __init__(self, on_seek=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=PADDING)
        self.set_name("right-panel-lyrics-container")

        heading = _heading("LYRICS", 0.5)
        self._heading = heading
        _add(self, heading)

        self._view = None
        if SyncedLyricsView is not None:
            self._view = SyncedLyricsView(on_seek=on_seek)
            self._view.set_vexpand(True)
            _add(self, self._view, expand=True)
            return

        # Fallback: the original single-label renderer.  This keeps the panel
        # usable if the synced-lyrics module cannot be imported.
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_min_content_height(110)
        self.scroller.set_vexpand(True)

        self.label = Gtk.Label(label="")
        self.label.set_name("right-panel-lyrics")
        self.label.set_halign(Gtk.Align.CENTER)
        self.label.set_xalign(0.5)
        self.label.set_yalign(0)
        self.label.set_line_wrap(True)
        self.label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label.show()
        self.scroller.add(self.label)
        _add(self, self.scroller, expand=True)

    def _to_top(self):
        self.scroller.get_vadjustment().set_value(0)

    def set_message(self, text):
        if self._view is not None:
            self._view.set_message(text)
            return
        self.label.set_text(text)
        self._to_top()

    def set_lyrics(self, text):
        if self._view is not None:
            self._view.set_lyrics(text)
            return
        plain = _plain_lyrics(text) if text else ""
        self.label.set_text(plain or "No lyrics found.")
        self._to_top()

    def set_position(self, position_ms, duration_ms=None):
        if self._view is not None:
            self._view.set_position(position_ms, duration_ms)

    def set_accent(self, rgb):
        if self._view is not None:
            self._view.set_accent(rgb)

    def scroll_to_current(self, animate=False):
        if self._view is not None:
            self._view.scroll_to_current(animate)


def _duration_text(ms):
    try:
        total = int(max(0, ms or 0)) // 1000
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    return "%d:%02d" % (total // 60, total % 60)


class _UpNextRow(Gtk.ListBoxRow):
    """Compact, clickable row used by the Up Next section."""

    def __init__(self, track, position, queued=False, on_remove=None):
        super().__init__()
        self.track = track
        self._on_remove = on_remove

        context = self.get_style_context()
        context.add_class("up-next-row")
        if queued:
            context.add_class("queued")
        self.set_activatable(True)
        self.set_selectable(False)

        info = _track_info(track)
        title = info.title or "Unknown track"
        artist = info.artist or info.album or "Unknown artist"

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        box.set_margin_start(4)
        box.set_margin_end(4)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        number = Gtk.Label(label=str(position))
        number.get_style_context().add_class("up-next-number")
        number.set_width_chars(2)
        number.set_max_width_chars(2)
        number.set_xalign(1.0)
        number.set_valign(Gtk.Align.CENTER)
        box.pack_start(number, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)

        title_label = Gtk.Label(label=title)
        title_label.get_style_context().add_class("up-next-title")
        title_label.set_xalign(0.0)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_label.set_single_line_mode(True)
        title_label.set_hexpand(True)
        text.pack_start(title_label, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        meta.set_hexpand(True)

        artist_label = Gtk.Label(label=artist)
        artist_label.get_style_context().add_class("up-next-artist")
        artist_label.set_xalign(0.0)
        artist_label.set_ellipsize(Pango.EllipsizeMode.END)
        artist_label.set_single_line_mode(True)
        artist_label.set_hexpand(True)
        meta.pack_start(artist_label, True, True, 0)

        if queued:
            badge = Gtk.Label(label="QUEUED")
            badge.get_style_context().add_class("up-next-badge")
            badge.set_valign(Gtk.Align.CENTER)
            meta.pack_start(badge, False, False, 0)

        text.pack_start(meta, False, False, 0)
        box.pack_start(text, True, True, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        right.set_valign(Gtk.Align.CENTER)

        duration = _duration_text(info.duration_ms)
        if duration:
            duration_label = Gtk.Label(label=duration)
            duration_label.get_style_context().add_class("up-next-duration")
            duration_label.set_valign(Gtk.Align.CENTER)
            right.pack_start(duration_label, False, False, 0)

        if queued and callable(on_remove):
            remove = Gtk.Button.new_from_icon_name(
                "list-remove-symbolic", Gtk.IconSize.MENU
            )
            remove.get_style_context().add_class("up-next-remove")
            remove.set_relief(Gtk.ReliefStyle.NONE)
            remove.set_focus_on_click(False)
            remove.set_tooltip_text("Remove from queue")
            remove.connect("clicked", self._on_remove_clicked)
            right.pack_start(remove, False, False, 0)

        if right.get_children():
            box.pack_start(right, False, False, 0)

        self.add(box)

    def _on_remove_clicked(self, _button):
        if self._on_remove is not None:
            self._on_remove(self.track)


class QueueSection(Gtk.Box):
    """Real Up Next list: the manual queue first, then the current
    album/playlist context.  Shuffle/party have no predictable future, so
    they show the immediate next track only.
    """

    MAX_ROWS = 60

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=PADDING)
        self.set_name("right-panel-queue-container")

        self._destroyed = False
        self._refresh_idle = 0
        self._player = None
        self._settings = None
        self._signal_ids = []

        self._heading = _heading("UP NEXT")
        _add(self, self._heading)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
        )
        self.scroller.set_vexpand(True)
        _add(self, self.scroller, expand=True)

        self.listbox = Gtk.ListBox()
        self.listbox.set_name("up-next-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.set_hexpand(True)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.show()
        self.scroller.add(self.listbox)

        self._connect()
        GLib.idle_add(self._refresh)
        self.connect("destroy", self._on_destroy)

    # ------------------------------------------------------------ wiring

    def _connect(self):
        try:
            from lollypop.define import App

            self._player = App().player
            for name in (
                "current-changed",
                "queue-changed",
                "next-changed",
                "playback-setted",
                "playback-added",
                "playback-updated",
                "playback-removed",
            ):
                try:
                    ident = self._player.connect(
                        name, self._schedule_refresh
                    )
                    self._signal_ids.append((self._player, ident))
                except Exception:
                    pass

            self._settings = App().settings
            for name in ("shuffle", "repeat"):
                try:
                    ident = self._settings.connect(
                        "changed::%s" % name, self._schedule_refresh
                    )
                    self._signal_ids.append((self._settings, ident))
                except Exception:
                    pass
        except Exception:
            _log("Up Next player connection failed")
            traceback.print_exc()

    def _on_destroy(self, *_args):
        self._destroyed = True
        if self._refresh_idle:
            try:
                GLib.source_remove(self._refresh_idle)
            except Exception:
                pass
            self._refresh_idle = 0
        for obj, ident in self._signal_ids:
            try:
                obj.disconnect(ident)
            except Exception:
                pass
        self._signal_ids.clear()

    def _schedule_refresh(self, *_args):
        if self._destroyed or self._refresh_idle:
            return
        self._refresh_idle = GLib.idle_add(self._refresh)

    # ------------------------------------------------------------ contents

    def _queue_tracks(self):
        from lollypop.objects_track import Track

        tracks = []
        seen = set()
        queue = list(getattr(self._player, "queue", []) or [])
        for track_id in queue:
            if track_id is None or track_id in seen:
                continue
            try:
                track = Track(track_id)
            except Exception:
                continue
            if getattr(track, "id", None) is None:
                continue
            seen.add(track.id)
            tracks.append(track)
        return tracks, seen

    def _context_tracks(self, current, queued_ids):
        from lollypop.define import App, Repeat

        if not self._player or current is None:
            return []

        # Stop After This Track.
        try:
            if getattr(self._player, "stop_after_track_id", None) == current.id:
                return []
        except Exception:
            pass

        next_track = getattr(self._player, "next_track", None)
        repeat = App().settings.get_enum("repeat")
        shuffle = bool(App().settings.get_value("shuffle")) or bool(
            getattr(self._player, "is_party", False)
        )

        # A repeated track is what actually plays after the queue.  In
        # shuffle/party mode the future order does not exist yet; the player
        # exposes only the immediate next track.
        if repeat == Repeat.TRACK:
            return [current]
        if shuffle:
            if next_track is not None and next_track.id is not None:
                return [next_track]
            return []

        albums = list(getattr(self._player, "albums", []) or [])
        current_album = getattr(current, "album", None)
        album_id = getattr(current_album, "id", None)
        index = None
        for i, album in enumerate(albums):
            if getattr(album, "id", None) == album_id:
                index = i
                break

        if index is None:
            if next_track is not None and next_track.id is not None:
                return [next_track]
            return []

        album = albums[index]
        try:
            position = list(album.track_ids).index(current.id)
        except Exception:
            if next_track is not None and next_track.id is not None:
                return [next_track]
            return []

        tracks = []

        def append_album_tracks(source):
            try:
                source_tracks = list(source.tracks)
            except Exception:
                return
            for track in source_tracks:
                if getattr(track, "id", None) is not None:
                    tracks.append(track)

        # The rest of the current album, then each following album.
        append_album_tracks_from = list(album.tracks)[position + 1:]
        for track in append_album_tracks_from:
            if getattr(track, "id", None) is not None:
                tracks.append(track)
        for following in albums[index + 1:]:
            append_album_tracks(following)

        # Repeat All wraps through the preceding albums and then starts the
        # current album again.  The row cap prevents this from becoming an
        # endless duplicated list.
        if repeat == Repeat.ALL:
            for preceding in albums[:index]:
                append_album_tracks(preceding)
            append_album_tracks(album)

        return tracks

    def _upcoming(self):
        current = None
        if self._player is not None:
            current = getattr(self._player, "current_track", None)
        if current is None or getattr(current, "id", None) is None:
            return []

        queued, seen = self._queue_tracks()
        rows = [(track, True) for track in queued]
        for track in self._context_tracks(current, seen):
            track_id = getattr(track, "id", None)
            if track_id is None or track_id in seen:
                continue
            seen.add(track_id)
            rows.append((track, False))
            if len(rows) >= self.MAX_ROWS:
                break
        return rows[:self.MAX_ROWS]

    def _clear_rows(self):
        for row in list(self.listbox.get_children()):
            try:
                self.listbox.remove(row)
            except Exception:
                row.destroy()

    def _show_empty(self, text):
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.get_style_context().add_class("up-next-empty-row")
        label = Gtk.Label(label=text)
        label.get_style_context().add_class("up-next-empty")
        label.set_xalign(0.0)
        label.set_line_wrap(True)
        label.set_margin_start(4)
        label.set_margin_end(4)
        label.set_margin_top(4)
        label.set_margin_bottom(4)
        row.add(label)
        self.listbox.add(row)
        row.show_all()

    def _refresh(self):
        self._refresh_idle = 0
        if self._destroyed:
            return False

        self._clear_rows()
        try:
            rows = self._upcoming()
        except Exception:
            _log("Up Next refresh failed:")
            traceback.print_exc()
            rows = []

        if self._player is None:
            self._show_empty("Player unavailable.")
        else:
            current = getattr(self._player, "current_track", None)
            if current is None or getattr(current, "id", None) is None:
                self._show_empty("Nothing playing right now.")
            elif not rows:
                self._show_empty("No upcoming tracks.")
            else:
                for position, (track, queued) in enumerate(rows, 1):
                    self.listbox.add(
                        _UpNextRow(
                            track,
                            position,
                            queued=queued,
                            on_remove=self._remove_from_queue,
                        )
                    )

        self.listbox.show_all()
        _debug(
            "Up Next refreshed:",
            len(rows),
            "rows, queue=",
            len(getattr(self._player, "queue", []) or []),
        )
        return False

    # ------------------------------------------------------------ actions

    def _on_row_activated(self, _listbox, row):
        track = getattr(row, "track", None)
        if track is None or self._player is None:
            return
        try:
            self._player.load(track)
        except Exception:
            _log("Up Next play failed:")
            traceback.print_exc()

    def _remove_from_queue(self, track):
        if self._player is None:
            return
        try:
            self._player.remove_from_queue(track.id)
        except Exception:
            _log("Up Next queue removal failed:")
            traceback.print_exc()


_COVER_CACHE = {}


def _square_cover_pixbuf(path, size):
    """Load artwork, center-crop it to a square, and scale it."""
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    side = min(width, height)
    if side <= 0:
        return None
    x_offset = (width - side) // 2
    y_offset = (height - side) // 2
    square = pixbuf.new_subpixbuf(x_offset, y_offset, side, side)
    return square.scale_simple(
        size, size, GdkPixbuf.InterpType.BILINEAR)


def _rounded_cover_pixbuf(path, size, radius):
    """Return artwork as a rounded, center-cropped pixbuf."""
    try:
        key = (os.path.realpath(path), os.path.getmtime(path), size, radius)
        cached = _COVER_CACHE.get(key)
        if cached is not None:
            return cached
        source = _square_cover_pixbuf(path, size)
        if source is None:
            return None
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        context = cairo.Context(surface)
        radius = max(0, min(radius, size // 2))
        context.new_sub_path()
        context.arc(size - radius, radius, radius,
                    -math.pi / 2, 0)
        context.arc(size - radius, size - radius, radius,
                    0, math.pi / 2)
        context.arc(radius, size - radius, radius,
                    math.pi / 2, math.pi)
        context.arc(radius, radius, radius,
                    math.pi, 3 * math.pi / 2)
        context.close_path()
        context.clip()
        Gdk.cairo_set_source_pixbuf(context, source, 0, 0)
        context.paint()
        rounded = Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
        _COVER_CACHE[key] = rounded
        return rounded
    except Exception:
        return None


def _songs_cover_pixbuf(songs, size=104, radius=16):
    """Create a four-up library mosaic, or use the first cover by itself."""
    paths = []
    seen = set()
    for song in songs:
        if not isinstance(song, dict):
            continue
        path = song.get("art_path")
        if not path or not os.path.isfile(path):
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        paths.append(path)
        if len(paths) == 4:
            break
    if not paths:
        return None
    if len(paths) < 4:
        return _rounded_cover_pixbuf(paths[0], size, radius)
    radius = max(0, min(radius, size // 2))
    try:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        context = cairo.Context(surface)
        context.new_sub_path()
        context.arc(size - radius, radius, radius,
                    -math.pi / 2, 0)
        context.arc(size - radius, size - radius, radius,
                    0, math.pi / 2)
        context.arc(radius, size - radius, radius,
                    math.pi / 2, math.pi)
        context.arc(radius, radius, radius,
                    math.pi, 3 * math.pi / 2)
        context.close_path()
        context.clip()
        context.set_source_rgba(0.035, 0.035, 0.045, 1.0)
        context.paint()
        gap = 2
        tile = (size - gap) // 2
        positions = (
            (0, 0),
            (tile + gap, 0),
            (0, tile + gap),
            (tile + gap, tile + gap),
        )
        for path, (x, y) in zip(paths, positions):
            cover = _square_cover_pixbuf(path, tile)
            if cover is None:
                continue
            context.save()
            context.rectangle(x, y, tile, tile)
            context.clip()
            Gdk.cairo_set_source_pixbuf(context, cover, x, y)
            context.paint()
            context.restore()
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
    except Exception:
        return _rounded_cover_pixbuf(paths[0], size, radius)


def _playback_albums_for_tracks(tracks):
    """Build Lollypop's album playback context from track objects.

    Lollypop drives next/previous, shuffle and repeat from ``player.albums``.
    Native playlist views use the same helper so their track rows queue the
    whole visible track list rather than loading one isolated song.
    """
    from lollypop.utils_album import tracks_to_albums

    return tracks_to_albums(tracks)


class _SongRow(Gtk.ListBoxRow):
    """One row of the library-wide Songs browser: number | art |
    title, artist · album | duration.  Activating plays the song."""

    def __init__(self, song, position, on_activate=None):
        super().__init__()
        self.song = song
        self.position = position
        self._on_activate = on_activate

        context = self.get_style_context()
        context.add_class("song-row")
        self.set_activatable(True)
        self.set_selectable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(4)
        box.set_margin_end(4)
        box.set_margin_top(5)
        box.set_margin_bottom(5)

        self.number_label = Gtk.Label(label=str(position))
        self.number_label.get_style_context().add_class("song-number")
        self.number_label.set_width_chars(2)
        self.number_label.set_max_width_chars(2)
        self.number_label.set_size_request(22, -1)
        self.number_label.set_xalign(1.0)
        self.number_label.set_valign(Gtk.Align.CENTER)
        box.pack_start(self.number_label, False, False, 0)

        art_frame = Gtk.Box()
        art_frame.get_style_context().add_class("song-art-frame")
        art_frame.set_size_request(44, 44)
        art_frame.set_valign(Gtk.Align.CENTER)

        art_path = song.get("art_path") if isinstance(song, dict) else None
        art = None
        if art_path and os.path.isfile(art_path):
            art = _rounded_cover_pixbuf(art_path, 44, 8)
        if art is not None:
            art_widget = Gtk.Image.new_from_pixbuf(art)
        else:
            art_widget = Gtk.Image.new_from_icon_name(
                "audio-x-generic-symbolic", Gtk.IconSize.MENU)
            art_widget.get_style_context().add_class("song-art-placeholder")
        art_widget.set_size_request(44, 44)
        art_frame.pack_start(art_widget, False, False, 0)
        box.pack_start(art_frame, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)

        title_label = Gtk.Label(label=str(song.get("title") or "Unknown Title"))
        title_label.get_style_context().add_class("song-title")
        title_label.set_xalign(0.0)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_label.set_single_line_mode(True)
        title_label.set_hexpand(True)
        text.pack_start(title_label, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        meta.set_hexpand(True)
        artist_label = Gtk.Label(label=str(song.get("artist") or "Unknown Artist"))
        artist_label.get_style_context().add_class("song-artist")
        artist_label.set_xalign(0.0)
        artist_label.set_ellipsize(Pango.EllipsizeMode.END)
        artist_label.set_single_line_mode(True)
        artist_label.set_hexpand(True)
        meta.pack_start(artist_label, True, True, 0)
        album = str(song.get("album") or "")
        if album.strip().lower() not in ("", "unknown album", "unknown"):
            album_label = Gtk.Label(label=album)
            album_label.get_style_context().add_class("song-album")
            album_label.set_xalign(0.0)
            album_label.set_ellipsize(Pango.EllipsizeMode.END)
            album_label.set_single_line_mode(True)
            album_label.set_hexpand(True)
            meta.pack_start(album_label, True, True, 0)
        text.pack_start(meta, False, False, 0)
        box.pack_start(text, True, True, 0)

        duration = _duration_text(int(float(song.get("duration_seconds") or 0) * 1000))
        if duration:
            duration_label = Gtk.Label(label=duration)
            duration_label.get_style_context().add_class("song-duration")
            duration_label.set_valign(Gtk.Align.CENTER)
            duration_label.set_xalign(1.0)
            duration_label.set_size_request(44, -1)
            box.pack_start(duration_label, False, False, 0)
        else:
            self._duration_spacer = Gtk.Box()
            self._duration_spacer.set_size_request(44, -1)
            box.pack_start(self._duration_spacer, False, False, 0)

        self.add(box)

    def set_playing(self, playing):
        """Mark the row as the currently playing song."""
        context = self.get_style_context()
        if playing:
            context.add_class("playing")
            self.number_label.set_text("▶")
        else:
            context.remove_class("playing")
            self.number_label.set_text(str(self.position))

    def activate(self):
        if callable(self._on_activate):
            self._on_activate(self.song)

    @property
    def name(self):
        """Text used by Lollypop's type-ahead filtering."""
        return "{} {}".format(
            self.song.get("title") or "",
            self.song.get("artist") or "",
        )


class SongsPage(LollypopView):
    """Full-page library-wide Songs browser.

    Shown in Lollypop's main content stack (like the Albums page) when the
    injected "Songs" sidebar row is activated.  Lists every track under the
    library folder and plays a song when its row is activated.
    """

    def __init__(self):
        LollypopView.__init__(self, StorageType.COLLECTION, ViewType.DEFAULT)
        self.set_name("songs-page")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_row_spacing(0)
        # Keep the sidebar selection on "Songs" when this view is mapped or
        # restored from navigation history.
        self.set_sidebar_id(SONGS_SIDEBAR_ROW_ID)

        self._songs = []
        self._token = 0
        self._destroy_pending = False
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="lollypop-songs"
        )
        # Playback controls mirror Lollypop's global settings and player.
        # Track ids are resolved lazily, only when the page starts playback.
        self._track_ids_by_path = {}
        self._rows_by_path = {}
        self._control_signals = []
        self._syncing_controls = False

        # Apple Music's Songs page is deliberately quiet: one large title,
        # a count and Play/Shuffle.  The old library mosaic is kept as a
        # hidden compatibility object so existing scan/update code stays
        # intact, but it is not part of the visual hierarchy.
        self.cover_frame = Gtk.Box()
        self.cover_frame.set_name("songs-page-cover")
        self.cover_frame.get_style_context().add_class("no-art")
        self.cover_frame.set_size_request(108, 108)
        self.cover_frame.set_valign(Gtk.Align.CENTER)
        self.cover_frame.set_no_show_all(True)
        self.cover_frame.hide()

        self.cover_image = Gtk.Image.new_from_icon_name(
            "folder-music-symbolic", Gtk.IconSize.DIALOG)
        self.cover_image.get_style_context().add_class(
            "songs-page-cover-icon")
        self.cover_image.set_size_request(104, 104)
        self.cover_frame.pack_start(self.cover_image, False, False, 0)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info.set_hexpand(True)
        info.set_valign(Gtk.Align.CENTER)

        eyebrow = Gtk.Label(label="LIBRARY")
        eyebrow.get_style_context().add_class("songs-page-eyebrow")
        eyebrow.set_xalign(0.0)
        eyebrow.set_no_show_all(True)
        eyebrow.hide()
        info.pack_start(eyebrow, False, False, 0)

        title = Gtk.Label(label="Songs")
        title.set_name("songs-page-title")
        title.get_style_context().add_class("songs-page-title")
        title.set_xalign(0.0)
        info.pack_start(title, False, False, 0)

        self.count_label = Gtk.Label(label="")
        self.count_label.set_name("songs-page-count")
        self.count_label.get_style_context().add_class("songs-page-count")
        self.count_label.set_xalign(0.0)
        info.pack_start(self.count_label, False, False, 0)

        self.actions = self._build_action_bar()
        info.pack_start(self.actions, False, False, 0)

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        hero.set_name("songs-page-hero")
        hero.set_margin_start(24)
        hero.set_margin_end(24)
        hero.set_margin_top(22)
        hero.set_margin_bottom(12)
        hero.pack_start(info, True, True, 0)
        self.hero = hero

        self.column_header = self._build_column_header()

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_name("songs-page-scroller")
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_hexpand(True)
        self.scroller.set_vexpand(True)

        self.listbox = Gtk.ListBox()
        self.listbox.set_name("songs-page-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.scroller.add(self.listbox)

        self.attach(hero, 0, 0, 1, 1)
        self.attach(self.column_header, 0, 1, 1, 1)
        self.attach(self.scroller, 0, 2, 1, 1)
        self.show_all()
        self.column_header.hide()
        self.connect("map", self._on_page_map)
        self.connect("unmap", self._on_page_unmap)
        self.connect("destroy", self._on_page_destroy)
        self._connect_control_signals()
        self._sync_playback_controls()
        self._set_status("Loading songs…")
        # History can reconstruct this page without calling populate(); load
        # immediately so a restored page never stays on the loading row.
        self.populate()

    def _build_column_header(self):
        """Tiny column labels that keep the track list easy to scan."""
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.set_name("songs-page-column-head")
        header.set_margin_start(28)
        header.set_margin_end(28)
        header.set_margin_top(2)

        def label(text, xalign=0.0, width=-1, expand=False):
            item = Gtk.Label(label=text)
            item.get_style_context().add_class("songs-page-column-label")
            item.set_xalign(xalign)
            item.set_size_request(width, -1)
            item.set_hexpand(expand)
            return item

        header.pack_start(label("#", 1.0, 22), False, False, 0)
        art_spacer = Gtk.Box()
        art_spacer.set_size_request(44, -1)
        header.pack_start(art_spacer, False, False, 0)
        header.pack_start(label("TITLE", 0.0, -1, True), True, True, 0)
        header.pack_start(label("ALBUM", 0.0, -1, True), True, True, 0)
        header.pack_start(label("TIME", 1.0, 44), False, False, 0)
        return header

    def _update_cover(self):
        """Refresh the library mosaic after a scan (hidden in Apple layout)."""
        if not self.cover_frame.get_visible():
            return
        pixbuf = _songs_cover_pixbuf(self._songs, 104, 16)
        context = self.cover_frame.get_style_context()
        if pixbuf is not None:
            self.cover_image.set_from_pixbuf(pixbuf)
            context.remove_class("no-art")
        else:
            self.cover_image.set_from_icon_name(
                "folder-music-symbolic", Gtk.IconSize.DIALOG)
            context.add_class("no-art")

    def _build_action_bar(self):
        """Spotify/Apple-Music-style playback controls for the page."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        bar.set_name("songs-page-actions")
        bar.set_margin_top(12)

        self.previous_button = self._transport_button(
            "media-skip-backward-symbolic", "Previous song")
        self.previous_button.connect("clicked", self._on_previous_clicked)

        self.play_button, self.play_image, self.play_label = \
            self._labelled_button(
                "media-playback-start-symbolic", "Play all",
                css_classes=("songs-page-play",))
        self.play_button.set_tooltip_text("Play all songs")
        self.play_button.connect("clicked", self._on_play_clicked)

        self.next_button = self._transport_button(
            "media-skip-forward-symbolic", "Next song")
        self.next_button.connect("clicked", self._on_next_clicked)

        divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        divider.get_style_context().add_class("songs-page-action-divider")
        divider.set_margin_start(4)
        divider.set_margin_end(4)
        self._action_divider = divider

        self.shuffle_button, self.shuffle_image, self.shuffle_label = \
            self._labelled_button(
                "media-playlist-shuffle-symbolic", "Shuffle",
                toggle=True, css_classes=("songs-page-shuffle",))
        self.shuffle_button.set_tooltip_text("Shuffle playback")
        self.shuffle_button.connect("toggled", self._on_shuffle_toggled)

        self.repeat_button, self.repeat_image, self.repeat_label = \
            self._labelled_button(
                "media-playlist-consecutive-symbolic", "Repeat off",
                css_classes=("songs-page-repeat",))
        self.repeat_button.set_tooltip_text(
            "Repeat mode: off. Click to cycle off / all / one.")
        self.repeat_button.connect("clicked", self._on_repeat_clicked)

        bar.pack_start(self.previous_button, False, False, 0)
        bar.pack_start(self.play_button, False, False, 0)
        bar.pack_start(self.next_button, False, False, 0)
        bar.pack_start(divider, False, False, 0)
        bar.pack_start(self.shuffle_button, False, False, 0)
        bar.pack_start(self.repeat_button, False, False, 0)

        # Apple Music's library header only shows Play and Shuffle.  Keep
        # the native transport/repeat objects alive (and synced) but hidden,
        # because the bottom bar now owns transport and repeat.
        for widget in (self.previous_button, self.next_button,
                       divider, self.repeat_button):
            widget.set_no_show_all(True)
            widget.hide()
        self.play_label.set_text("Play")
        self.shuffle_label.set_text("Shuffle")
        return bar

    @staticmethod
    def _transport_button(icon_name, tooltip):
        button = Gtk.Button.new_from_icon_name(
            icon_name, Gtk.IconSize.BUTTON)
        button.get_style_context().add_class("songs-page-transport")
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text(tooltip)
        return button

    def _labelled_button(self, icon_name, text, toggle=False,
                         css_classes=()):
        button = Gtk.ToggleButton() if toggle else Gtk.Button()
        button.get_style_context().add_class("songs-page-control")
        for css_class in css_classes:
            button.get_style_context().add_class(css_class)
        button.set_relief(Gtk.ReliefStyle.NONE)
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        image = Gtk.Image.new_from_icon_name(
            icon_name, Gtk.IconSize.BUTTON)
        label = Gtk.Label(label=text)
        label.get_style_context().add_class("songs-page-control-label")
        content.pack_start(image, False, False, 0)
        content.pack_start(label, False, False, 0)
        button.add(content)
        return button, image, label

    def _player(self):
        try:
            from lollypop.define import App

            return getattr(App(), "player", None)
        except Exception:
            return None

    def _settings(self):
        try:
            from lollypop.define import App

            return getattr(App(), "settings", None)
        except Exception:
            return None

    def _shuffle_enabled(self):
        settings = self._settings()
        if settings is None:
            return False
        try:
            value = settings.get_value("shuffle")
            if hasattr(value, "get_boolean"):
                return bool(value.get_boolean())
            return bool(value)
        except Exception:
            return False

    def _repeat_mode(self):
        settings = self._settings()
        if settings is None:
            return Repeat.NONE
        try:
            return settings.get_enum("repeat")
        except Exception:
            return Repeat.NONE

    @staticmethod
    def _repeat_setting_value(mode):
        return {
            Repeat.NONE: "none",
            Repeat.AUTO_SIMILAR: "auto_similar",
            Repeat.AUTO_RANDOM: "auto_random",
            Repeat.TRACK: "track",
            Repeat.ALL: "all",
        }.get(mode, "none")

    def _connect_control_signals(self):
        """Listen to global player/settings changes made outside this page."""
        try:
            player = self._player()
            if player is not None and hasattr(player, "connect"):
                for signal_name in ("current-changed", "status-changed"):
                    handler_id = player.connect(
                        signal_name, self._on_player_changed)
                    self._control_signals.append((player, handler_id))
            settings = self._settings()
            if settings is not None and hasattr(settings, "connect"):
                for signal_name in ("changed::shuffle", "changed::repeat"):
                    handler_id = settings.connect(
                        signal_name, self._on_playback_setting_changed)
                    self._control_signals.append((settings, handler_id))
        except Exception:
            _log("Songs controls unavailable:")
            traceback.print_exc()

    def _on_player_changed(self, *_args):
        self._sync_playback_controls()

    def _on_playback_setting_changed(self, *_args):
        self._sync_playback_controls()

    def _sync_playback_controls(self):
        if self.destroyed or self._syncing_controls:
            return
        self._syncing_controls = True
        try:
            shuffle = self._shuffle_enabled()
            if self.shuffle_button.get_active() != shuffle:
                self.shuffle_button.set_active(shuffle)
            self._sync_repeat_control()
            self._sync_play_control()
            self._sync_playing_rows()
        finally:
            self._syncing_controls = False

    def _sync_repeat_control(self):
        mode = self._repeat_mode()
        if mode == Repeat.TRACK:
            icon = "media-playlist-repeat-song-symbolic"
            label = "Repeat one"
            mode_text = "one"
        elif mode == Repeat.AUTO_SIMILAR:
            icon = "media-playlist-repeat-symbolic"
            label = "Repeat similar"
            mode_text = "auto similar"
        elif mode == Repeat.AUTO_RANDOM:
            icon = "media-playlist-repeat-symbolic"
            label = "Repeat random"
            mode_text = "auto random"
        elif mode == Repeat.ALL:
            icon = "media-playlist-repeat-symbolic"
            label = "Repeat all"
            mode_text = "all"
        else:
            icon = "media-playlist-consecutive-symbolic"
            label = "Repeat off"
            mode_text = "off"
        self.repeat_image.set_from_icon_name(icon, Gtk.IconSize.BUTTON)
        self.repeat_label.set_text(label)
        self.repeat_button.set_tooltip_text(
            "Repeat mode: {}. Click to cycle off / all / one.".format(
                mode_text))
        context = self.repeat_button.get_style_context()
        if mode == Repeat.NONE:
            context.remove_class("active")
        else:
            context.add_class("active")

    def _sync_play_control(self):
        player = self._player()
        playing = False
        try:
            playing = bool(player is not None and player.is_playing)
        except Exception:
            pass
        in_page = self._current_row() is not None
        if in_page and playing:
            icon = "media-playback-pause-symbolic"
            label = "Pause"
            tooltip = "Pause playback"
        elif in_page:
            icon = "media-playback-start-symbolic"
            label = "Resume"
            tooltip = "Resume playback"
        else:
            icon = "media-playback-start-symbolic"
            label = "Play all"
            tooltip = "Play all songs"
        self.play_image.set_from_icon_name(icon, Gtk.IconSize.BUTTON)
        self.play_label.set_text(label)
        self.play_button.set_tooltip_text(tooltip)

    def _current_row(self):
        player = self._player()
        try:
            track = getattr(player, "current_track", None)
            if track is None or getattr(track, "id", None) is None:
                return None
            uri = getattr(track, "uri", None)
            if not uri:
                return None
            path = GLib.filename_from_uri(uri)[0]
        except Exception:
            return None
        return self._rows_by_path.get(self._song_path(path))

    def _sync_playing_rows(self):
        current = self._current_row()
        for row in self.filtered:
            row.set_playing(row is current)

    @staticmethod
    def _song_path(song_or_path):
        if isinstance(song_or_path, dict):
            path = song_or_path.get("path")
        else:
            path = song_or_path
        if not path:
            return ""
        return os.path.realpath(os.path.abspath(path))

    def _track_id_for_song(self, song):
        path = self._song_path(song)
        if path in self._track_ids_by_path:
            return self._track_ids_by_path[path]
        track_id = None
        if path and os.path.isfile(path):
            try:
                from lollypop.define import App

                uri = GLib.filename_to_uri(path, None)
                track_id = App().tracks.get_id_by_uri(uri)
            except Exception:
                _log("Songs: track lookup failed for", path)
                traceback.print_exc()
        self._track_ids_by_path[path] = track_id
        return track_id

    def _track_pairs_for_songs(self, songs):
        from lollypop.objects_track import Track

        pairs = []
        for song in songs:
            track_id = self._track_id_for_song(song)
            if track_id is None:
                continue
            try:
                track = Track(track_id)
            except Exception:
                _log("Songs: track load failed for", song.get("path"))
                traceback.print_exc()
                continue
            pairs.append((song, track))
        return pairs

    def _start_songs_playback(self, songs, selected_song=None):
        """Queue every visible song so next/previous/shuffle/repeat work."""
        player = self._player()
        if player is None:
            return False
        pairs = self._track_pairs_for_songs(songs)
        if not pairs:
            return False
        tracks = [track for _song, track in pairs]
        selected = None
        if selected_song is not None:
            selected_path = self._song_path(selected_song)
            for song, track in pairs:
                if song is selected_song or \
                        self._song_path(song) == selected_path:
                    selected = track
                    break
        elif self._shuffle_enabled():
            selected = random.choice(tracks)
        if selected is None:
            selected = tracks[0]
        try:
            albums = _playback_albums_for_tracks(tracks)
            if hasattr(player, "play_track_for_albums"):
                player.play_track_for_albums(selected, albums)
            else:
                player.load(selected)
        except Exception:
            _log("Songs play failed:")
            traceback.print_exc()
            return False
        self._sync_playback_controls()
        return True

    def _visible_songs(self):
        return [row.song for row in self.filtered]

    def _on_play_clicked(self, _button):
        player = self._player()
        if player is None:
            return
        track = getattr(player, "current_track", None)
        if self._current_row() is not None and \
                getattr(track, "id", None) is not None:
            try:
                player.play_pause()
            except Exception:
                _log("Songs play/pause failed:")
                traceback.print_exc()
        else:
            self._start_songs_playback(self._visible_songs())
        self._sync_playback_controls()

    def _on_previous_clicked(self, _button):
        player = self._player()
        if player is None:
            return
        # Previous/Next belong to the Songs page context.  If something from
        # an album, playlist or radio is playing, do not steer that foreign
        # queue from here; (re)start the page queue like Play all does.
        if self._current_row() is None:
            self._start_songs_playback(self._visible_songs())
        else:
            try:
                player.prev()
            except Exception:
                _log("Songs previous failed:")
                traceback.print_exc()
        self._sync_playback_controls()

    def _on_next_clicked(self, _button):
        player = self._player()
        if player is None:
            return
        if self._current_row() is None:
            self._start_songs_playback(self._visible_songs())
        else:
            try:
                player.next()
            except Exception:
                _log("Songs next failed:")
                traceback.print_exc()
        self._sync_playback_controls()

    def _on_shuffle_toggled(self, button):
        if self._syncing_controls:
            return
        settings = self._settings()
        if settings is None:
            return
        try:
            settings.set_value(
                "shuffle", GLib.Variant("b", button.get_active()))
        except Exception:
            _log("Songs shuffle toggle failed:")
            traceback.print_exc()
        self._sync_playback_controls()

    def _on_repeat_clicked(self, _button):
        settings = self._settings()
        if settings is None:
            return
        mode = self._repeat_mode()
        if mode == Repeat.TRACK:
            next_mode = Repeat.NONE
        elif mode == Repeat.ALL:
            next_mode = Repeat.TRACK
        else:
            next_mode = Repeat.ALL
        try:
            settings.set_value(
                "repeat",
                GLib.Variant("s", self._repeat_setting_value(next_mode)))
        except Exception:
            _log("Songs repeat toggle failed:")
            traceback.print_exc()
        self._sync_playback_controls()

    def populate(self):
        """Load (or reload) every song in the library on a worker thread."""
        if self.destroyed:
            return
        self._token += 1
        token = self._token
        self._track_ids_by_path = {}
        self._set_status("Loading songs…")

        def run():
            try:
                if BACKEND_DIR not in sys.path:
                    sys.path.append(BACKEND_DIR)
                import main as library_main

                songs = library_main.list_songs()
            except Exception:
                _log("Songs page scan failed:")
                traceback.print_exc()
                GLib.idle_add(self._apply_error, token)
                return
            GLib.idle_add(self._apply_songs, token, songs)

        try:
            self._executor.submit(run)
        except RuntimeError:
            pass

    def _apply_songs(self, token, songs):
        if self.destroyed or token != self._token:
            return False
        self._songs = list(songs or [])
        self._clear_rows()
        if not self._songs:
            self._set_status_row("No songs found.")
            self.count_label.set_label("0 songs")
            self.column_header.hide()
            self._update_cover()
            self._sync_playback_controls()
            return False
        for position, song in enumerate(self._songs, 1):
            row = _SongRow(song, position, on_activate=self._play_song)
            self.listbox.add(row)
            self._rows_by_path[self._song_path(song)] = row
            row.show_all()
        count = len(self._songs)
        self.count_label.set_label(
            "1 song" if count == 1 else "{} songs".format(count)
        )
        self.column_header.show_all()
        self._update_cover()
        self._sync_playback_controls()
        return False

    def _apply_error(self, token):
        if self.destroyed or token != self._token:
            return False
        self._clear_rows()
        self._set_status_row("Could not read the music library.")
        self.count_label.set_label("")
        self.column_header.hide()
        self._update_cover()
        self._sync_playback_controls()
        return False

    def _set_status(self, text):
        self._clear_rows()
        self.count_label.set_label("")
        self.column_header.hide()
        self._set_status_row(text)

    def _set_status_row(self, text):
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.get_style_context().add_class("song-row")
        label = Gtk.Label(label=text)
        label.get_style_context().add_class("song-empty")
        label.set_xalign(0.0)
        label.set_line_wrap(True)
        label.set_margin_start(8)
        label.set_margin_end(8)
        label.set_margin_top(8)
        label.set_margin_bottom(8)
        row.add(label)
        self.listbox.add(row)
        row.show_all()

    def _clear_rows(self):
        self._rows_by_path = {}
        for row in list(self.listbox.get_children()):
            try:
                self.listbox.remove(row)
            except Exception:
                row.destroy()

    def _on_row_activated(self, _listbox, row):
        if isinstance(row, _SongRow):
            row.activate()

    def _play_song(self, song):
        """Play a song, with the whole Songs page as the playback queue."""
        self._start_songs_playback(self._visible_songs(), selected_song=song)

    def _on_page_map(self, *_args):
        """Keep the injected Songs row selected when this view is restored."""
        attempts = [20]

        def select_when_ready():
            if self._select_sidebar_row():
                return False
            attempts[0] -= 1
            return attempts[0] > 0

        GLib.timeout_add(120, select_when_ready)

    def _on_page_unmap(self, *_args):
        """Do not leave Songs highlighted after navigating to an album."""
        try:
            from lollypop.define import App

            listbox = App().window.container.sidebar.listbox
            selected = listbox.get_selected_row()
            if selected is not None and getattr(
                    selected, "id", None) == SONGS_SIDEBAR_ROW_ID:
                listbox.unselect_row(selected)
        except Exception:
            pass

    def _select_sidebar_row(self):
        try:
            from lollypop.define import App

            sidebar = App().window.container.sidebar
            listbox = sidebar.listbox
            for row in listbox.get_children():
                if getattr(row, "id", None) == SONGS_SIDEBAR_ROW_ID:
                    listbox.select_row(row)
                    return True
        except Exception:
            pass
        return False

    def _on_page_destroy(self, *_args):
        self._token += 1
        for source, handler_id in self._control_signals:
            try:
                source.disconnect(handler_id)
            except Exception:
                pass
        self._control_signals = []
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            pass

    def destroy_later(self):
        # StackContainer schedules this after a back-navigation.  Mark the
        # page so a quick re-open creates a fresh instance instead of reusing
        # one that is about to be destroyed.
        self._destroy_pending = True
        super().destroy_later()

    @property
    def filtered(self):
        return [row for row in self.listbox.get_children()
                if isinstance(row, _SongRow)]

    def activate_child(self):
        for row in self.filtered:
            if row.get_style_context().has_class("typeahead"):
                row.activate()
                return

    def _scroll_to_child(self, row):
        try:
            coordinates = row.translate_coordinates(self.scroller, 0, 0)
            if coordinates:
                self.scroller.get_vadjustment().set_value(coordinates[1])
        except Exception:
            pass


class MiniProgressBar(Gtk.EventBox):
    """Material 3 progress bar: a 4px pill with an accent fill, a muted
    track, and a small dot at the fill's leading edge.

    A compact, seekable replacement for Lollypop's native ``Gtk.Scale``
    progress widget, modelled on Quickshell's ``StyledProgressBar.qml``.
    It keeps the elapsed / total readouts (tabular numerals) but drops the
    heavy scale chrome so it fits the compact media card.

    * Click / drag on the bar to seek.
    * Scroll (or trackpad swipe) to nudge +/-5s, clamped like the native
      widget.

    The displayed value eases toward the real position every tick so the
    fill glides instead of jumping once a second.
    """

    BAR_HEIGHT = 4
    DOT_RADIUS = 2.5
    EASE = 0.22
    TICK_MS = 66           # ~15fps easing; cheap and smooth enough for a bar
    SCROLL_STEP_MS = 5000  # same nudge the native scale uses

    def __init__(self, on_seek=None, get_position=None, get_duration=None):
        super().__init__()
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self.set_name("right-panel-progress")
        self.set_can_focus(False)

        self._on_seek = on_seek
        self._get_position = get_position or (lambda: 0)
        self._get_duration = get_duration or (lambda: 0)

        self._position = 0.0    # eased display value, ms
        self._target = 0.0      # real player position, ms
        self._duration = 0.0    # ms
        self._seeking = False   # user is dragging / scrolling
        self._seek_value = 0.0  # value while seeking, ms
        self._timer = 0
        self._destroyed = False
        self._debug_last = 0.0

        self._accent = (0.65, 0.55, 0.98)
        self._track = (1.0, 1.0, 1.0, 0.18)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_halign(Gtk.Align.FILL)
        box.set_hexpand(True)

        self._elapsed = Gtk.Label(label="0:00")
        self._elapsed.set_name("right-panel-progress-time")
        self._elapsed.set_xalign(1.0)  # right-align against the bar

        self._bar = Gtk.DrawingArea()
        self._bar.set_hexpand(True)
        self._bar.set_size_request(-1, 14)  # click target taller than the pill
        self._bar.connect("draw", self._on_draw)

        self._total = Gtk.Label(label="0:00")
        self._total.set_name("right-panel-progress-time")
        self._total.set_xalign(0.0)  # left-align against the bar

        box.pack_start(self._elapsed, False, False, 0)
        box.pack_start(self._bar, True, True, 0)
        box.pack_start(self._total, False, False, 0)
        self.add(box)

        # Pointer + scroll handling for seek.
        self._bar.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.SCROLL_MASK |
            Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self._bar.connect("button-press-event", self._on_press)
        self._bar.connect("button-release-event", self._on_release)
        self._bar.connect("motion-notify-event", self._on_motion)
        self._bar.connect("scroll-event", self._on_scroll)

        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", self._on_destroy)

    # ---- public API --------------------------------------------------

    def set_colors(self, accent, track=None):
        """Set the fill/dot accent (0..1 RGB tuple) and optional track RGBA."""
        self._accent = tuple(
            max(0.0, min(1.0, float(value))) for value in accent[:3]
        )
        if track is not None:
            self._track = tuple(track)
        self._bar.queue_draw()

    def set_position(self, position_ms, duration_ms=None):
        """Push a fresh real position from the player (no seek in progress)."""
        if self._seeking:
            return
        try:
            self._target = max(0.0, float(position_ms or 0))
        except (TypeError, ValueError):
            self._target = 0.0
        if duration_ms is not None:
            try:
                self._duration = max(0.0, float(duration_ms or 0))
            except (TypeError, ValueError):
                self._duration = 0.0
        self._update_labels()

    # ---- timer -------------------------------------------------------

    def _ensure_timer(self):
        if not self._timer and not self._destroyed:
            self._timer = GLib.timeout_add(self.TICK_MS, self._tick)

    def _kill_timer(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _on_map(self, *_args):
        self._refresh_from_player()
        self._ensure_timer()

    def _on_unmap(self, *_args):
        self._kill_timer()

    def _on_destroy(self, *_args):
        self._destroyed = True
        self._kill_timer()

    def _refresh_from_player(self):
        try:
            self._target = max(0.0, float(self._get_position() or 0))
            self._duration = max(0.0, float(self._get_duration() or 0))
        except (TypeError, ValueError):
            pass
        now = time.monotonic()
        if now - self._debug_last >= 3.0:
            self._debug_last = now
            _debug(
                "Progress:", round(self._position), "of",
                round(self._duration), "ms accent=",
                tuple(round(value, 2) for value in self._accent),
            )
        self._update_labels()

    def _tick(self):
        # Always poll the player so the bar advances while playing and stays
        # correct after seeks made elsewhere (native buttons, lyrics click).
        if not self._seeking:
            self._refresh_from_player()
            diff = self._target - self._position
            if abs(diff) < 0.5:
                self._position = self._target
            else:
                self._position += diff * self.EASE
            self._bar.queue_draw()
            self._update_labels()
        return True  # keep the source alive; unmap/destroy removes it

    # ---- drawing -----------------------------------------------------

    def _on_draw(self, widget, cr):
        w = float(self._bar.get_allocated_width())
        h = float(self._bar.get_allocated_height())
        if w <= 1 or h <= 1:
            return False

        bar_h = float(self.BAR_HEIGHT)
        y = (h - bar_h) / 2.0
        radius = bar_h / 2.0

        value = self._seek_value if self._seeking else self._position
        frac = 0.0
        if self._duration > 0:
            frac = max(0.0, min(1.0, value / self._duration))
        fill_w = w * frac

        # Track (full-width pill).
        tr, tg, tb, ta = self._track
        cr.set_source_rgba(tr, tg, tb, ta)
        cr.new_sub_path()
        cr.arc(w - radius, y + radius, radius, -math.pi / 2, math.pi / 2)
        cr.arc(radius, y + radius, radius, math.pi / 2, 3 * math.pi / 2)
        cr.close_path()
        cr.fill()

        # Accent fill pill.
        if fill_w > 0.5:
            ar, ag, ab = self._accent
            cr.set_source_rgba(ar, ag, ab, 0.95)
            end = max(radius, fill_w)
            cr.new_sub_path()
            cr.arc(end - radius, y + radius, radius, -math.pi / 2, math.pi / 2)
            cr.arc(radius, y + radius, radius, math.pi / 2, 3 * math.pi / 2)
            cr.close_path()
            cr.fill()

            # Small dot at the fill's leading edge (the M3 stop point).
            cr.set_source_rgba(ar, ag, ab, 1.0)
            cr.arc(fill_w, y + radius, self.DOT_RADIUS, 0, math.pi * 2)
            cr.fill()
        return False

    # ---- labels ------------------------------------------------------

    @staticmethod
    def _fmt(ms):
        total = int(max(0, ms)) // 1000
        return "%d:%02d" % (total // 60, total % 60)

    def _update_labels(self):
        value = self._seek_value if self._seeking else self._position
        text = self._fmt(value)
        # Tabular numerals keep the readout from jittering as digits change.
        markup = "<span font_features='tnum'>%s</span>" % text
        # Compare against the *plain* text: after set_markup() the stored
        # "label" is the markup string, so get_label() would never equal the
        # bare time and the guard below would be dead.
        if self._elapsed.get_text() != text:
            self._elapsed.set_markup(markup)
        total_text = self._fmt(self._duration)
        if self._total.get_text() != total_text:
            self._total.set_markup(
                "<span font_features='tnum'>%s</span>" % total_text
            )
        # The drawn pill sits between the two time slots, so it is only
        # exactly centred when both slots are the same width
        # (bar_center = row_center + (elapsed_w - total_w) / 2).  Force them
        # equal so the bar never drifts left or right of panel centre.
        slots = max(len(text), len(total_text))
        if self._elapsed.get_width_chars() != slots:
            self._elapsed.set_width_chars(slots)
        if self._total.get_width_chars() != slots:
            self._total.set_width_chars(slots)

    # ---- seeking -----------------------------------------------------

    def _fraction_at(self, x):
        width = self._bar.get_allocated_width()
        if width <= 0:
            return 0.0
        return max(0.0, min(1.0, x / float(width)))

    def _begin_seek(self, x):
        self._seeking = True
        self._seek_value = self._fraction_at(x) * self._duration
        self._update_labels()
        self._bar.queue_draw()

    def _on_press(self, _widget, event):
        if event.button != 1:
            return False
        self._begin_seek(event.x)
        return True  # consume so the click does not fall through

    def _on_motion(self, _widget, event):
        if self._seeking and event.state & Gdk.ModifierType.BUTTON1_MASK:
            self._seek_value = self._fraction_at(event.x) * self._duration
            self._update_labels()
            self._bar.queue_draw()
        return False

    def _on_release(self, _widget, event):
        if event.button != 1 or not self._seeking:
            return False
        self._seek_value = self._fraction_at(event.x) * self._duration
        self._commit_seek()
        return True

    def _on_scroll(self, _widget, event):
        # Mirror the native widget: horizontal scroll maps to x, otherwise y.
        delta = event.delta_x if event.delta_x else -event.delta_y
        if event.direction == Gdk.ScrollDirection.UP:
            delta = 1.0
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta = -1.0
        if not delta:
            return False
        step = self.SCROLL_STEP_MS
        seek = (self._seek_value if self._seeking else self._position) \
            + delta * step
        seek = max(0.0, min(self._duration - 2000.0, seek)) \
            if self._duration > 2000.0 else max(0.0, seek)
        self._seeking = True
        self._seek_value = seek
        self._update_labels()
        self._bar.queue_draw()
        self._commit_seek()
        return True

    def _commit_seek(self):
        value = int(max(0.0, self._seek_value))
        self._position = float(value)
        self._target = float(value)
        self._seeking = False
        self._update_labels()
        self._bar.queue_draw()
        if self._on_seek is not None:
            try:
                self._on_seek(value)
            except Exception:
                traceback.print_exc()


class RightPanel(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=PANEL_GAP)
        self.set_name("right-panel")
        self.set_hexpand(False)
        self.set_vexpand(True)

        self._active = False
        self._destroyed = False
        self._last_key = None
        self._token = 0
        self._lyrics_timer = 0
        self._lyrics_debug_last = 0.0
        self._last_lyric_index = None
        self._player = None
        self._signal_ids = []
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="lollypop-ext"
        )
        self._styler = PaletteStyler() if PaletteStyler else None
        # Wallpaper-accent layer for the global chrome (selection/focus/
        # primary actions/scrubber/bottom player bar).
        self._ambient = AmbientStyler() if AmbientStyler else None
        # Separate wallpaper accent overrides for the Songs library page.
        self._page = PageStyler() if PageStyler else None
        self._wallpaper_accent = FALLBACK_ACCENT
        self._last_palette = None
        self._accent_provider = (
            WallpaperAccentProvider(self._on_wallpaper_accent)
            if WallpaperAccentProvider else None
        )

        # 1. Album artwork.  The frame sits 8px in from the panel edges and
        # keeps its own 8px inset around the image, so the artwork lines up
        # with every other card in the panel.
        self.artwork = AlbumArtwork()
        self.artwork.set_margin_start(PADDING)
        self.artwork.set_margin_end(PADDING)
        self.artwork.set_margin_top(PADDING)
        self.artwork.set_margin_bottom(PADDING)
        artwork_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        artwork_frame.set_margin_start(PADDING)
        artwork_frame.set_margin_end(PADDING)
        artwork_frame.set_margin_top(PADDING)
        artwork_frame.pack_start(self.artwork, True, True, 0)
        artwork_frame.show_all()
        _add(self, artwork_frame)

        # 2. Track metadata.  Its 8px padding comes from the scoped CSS rule;
        # the extra side margins align its text with the other cards' content.
        self.info = TrackInformation()
        self.info.set_margin_start(PADDING)
        self.info.set_margin_end(PADDING)
        _add(self, self.info)

        # 3. Media area: the visualizer is a full-card backdrop with the
        # native progress bar and buttons stacked on top of it.  Overlays
        # always draw above the main child, and with pass-through disabled
        # they keep receiving clicks, so the controls stay usable.
        self.media_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.media_frame.set_name("right-panel-media")
        self.media_frame.set_margin_start(PADDING)
        self.media_frame.set_margin_end(PADDING)
        self.media_frame.set_vexpand(False)

        self.media_area = Gtk.Overlay()
        self.media_area.set_hexpand(True)
        self.media_area.set_vexpand(False)
        self.media_area.set_margin_start(PADDING)
        self.media_area.set_margin_end(PADDING)
        self.media_area.set_margin_top(PADDING)
        self.media_area.set_margin_bottom(PADDING)
        self.media_area.set_size_request(-1, MEDIA_HEIGHT)
        self.media_overlay = self.media_area  # old attribute name

        self.visualizer = SimpleVisualizer()
        self.visualizer.set_hexpand(True)
        self.visualizer.set_vexpand(True)
        self.media_area.add(self.visualizer)

        self.controls_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )
        self.controls_box.set_hexpand(True)
        self.controls_box.set_halign(Gtk.Align.FILL)
        self.controls_box.set_valign(Gtk.Align.CENTER)

        try:
            from lollypop.widgets_player_buttons import ButtonsPlayerWidget

            # Material 3 progress bar (custom): 4px pill + accent fill +
            # end dot, with compact elapsed/total labels.  Replaces the
            # native Gtk.Scale widget, which was far too tall for this card
            # and brought a lot of chrome we do not need here.
            self.progress = MiniProgressBar(
                on_seek=self._seek_to,
                get_position=self._player_position_ms,
                get_duration=self._player_duration_ms,
            )
            self.buttons = ButtonsPlayerWidget(["banner-button"])

            # Strip Lollypop's native minimum-width limits
            for native in (self.progress, self.buttons):
                native.set_size_request(-1, -1)
                native.set_halign(Gtk.Align.FILL)
                native.set_hexpand(True)

            # The button box packs its three buttons with expand=True, so a
            # full-width allocation turns them into three huge pills.  Give
            # the row a compact fixed width and center it instead.
            self.buttons.set_halign(Gtk.Align.CENTER)
            self.buttons.set_hexpand(False)
            self.buttons.set_size_request(3 * 64, -1)

            self.controls_box.pack_start(self.progress, False, False, 0)
            self.controls_box.pack_start(self.buttons, False, False, 0)

            if os.environ.get("LOLLYPOP_EXT_DEBUG") == "1":
                for child in self.buttons.get_children():
                    _debug(
                        "Control:", "buttons",
                        child.__class__.__name__,
                        "sensitive=", child.get_sensitive(),
                    )
                    try:
                        child.connect(
                            "clicked",
                            lambda widget: _debug(
                                "Clicked:", widget.__class__.__name__
                            ),
                        )
                    except Exception:
                        pass

            GLib.idle_add(self._refresh_controls)
        except Exception as exc:
            _log("Native Lollypop controls unavailable:", exc)
            traceback.print_exc()

        # Overlays are always above the main child for both drawing and
        # input, so the controls sit on top of the visualizer backdrop and
        # still receive clicks.
        self.media_area.add_overlay(self.controls_box)
        self.media_area.set_overlay_pass_through(self.controls_box, False)
        self.media_area.show_all()
        self.media_frame.pack_start(self.media_area, True, True, 0)
        self.media_frame.show_all()
        _add(self, self.media_frame, expand=False)

        # 4 + 5. Lyrics / Up Next tabs.  The visualizer and transport stay
        # above in the now-playing card; the lower half is a native notebook
        # so the two long-form lists do not fight for vertical space.
        self.lyrics = LyricsSection(on_seek=self._on_lyric_seek)
        self.lyrics.set_vexpand(True)
        self.lyrics.set_margin_start(0)
        self.lyrics.set_margin_end(0)

        self.queue = QueueSection()
        self.queue.set_vexpand(True)
        self.queue.set_margin_start(0)
        self.queue.set_margin_end(0)
        self.queue.set_margin_bottom(0)

        self.tabs = Gtk.Notebook()
        self.tabs.set_name("right-panel-tabs")
        self.tabs.get_style_context().add_class("right-panel-tabs")
        self.tabs.set_show_tabs(True)
        self.tabs.set_show_border(False)
        self.tabs.set_scrollable(False)
        self.tabs.set_tab_pos(Gtk.PositionType.TOP)
        self.tabs.set_vexpand(True)
        self.tabs.set_margin_start(PADDING)
        self.tabs.set_margin_end(PADDING)
        self.tabs.set_margin_bottom(PADDING)
        self.tabs.append_page(self.lyrics, Gtk.Label(label="Lyrics"))
        self.tabs.append_page(self.queue, Gtk.Label(label="Up Next"))
        self.tabs.set_current_page(0)
        self.tabs.show_all()
        # The tab labels already name each section; hide the duplicated
        # in-page headings so the panel reads like Apple Music's tabbed
        # lyrics/queue area.
        for section in (self.lyrics, self.queue):
            heading = getattr(section, "_heading", None)
            if heading is not None:
                heading.set_no_show_all(True)
                heading.hide()
        _add(self, self.tabs, expand=True)

        # Provider order matters: ambient first, then panel, then the page
        # overrides (the Songs page must win over the panel's page rules).
        if self._ambient is not None:
            self._ambient.apply_accent(self._wallpaper_accent)
        self._apply_palette(None)
        self._connect_player()
        self.connect("destroy", self._on_destroy)
        if self._accent_provider is not None:
            try:
                self._accent_provider.start()
            except Exception:
                _log("Wallpaper accent provider failed to start:")
                traceback.print_exc()

    # ------------------------------------------------------------ player

    def _connect_player(self):
        try:
            from lollypop.define import App

            self._player = App().player
            self._signal_ids.append(
                self._player.connect("current-changed", self._on_current_changed)
            )
            self._signal_ids.append(
                self._player.connect("status-changed", self._on_status_changed)
            )
            self._signal_ids.append(
                self._player.connect("seeked", self._on_seeked)
            )
        except Exception:
            _log("Player connection failed")

    def _current_track(self):
        try:
            if self._player is None:
                self._connect_player()
            return getattr(self._player, "current_track", None)
        except Exception:
            return None

    def _is_playing(self):
        """is_playing is a property in some Lollypop builds, a method in others."""
        try:
            value = getattr(self._player, "is_playing", False)
            if callable(value):
                value = value()
            return bool(value)
        except Exception:
            return False

    def _refresh_controls(self):
        for name in ("progress", "buttons"):
            widget = getattr(self, name, None)
            update = getattr(widget, "update", None)
            if callable(update):
                try:
                    update()
                except Exception:
                    pass
        if os.environ.get("LOLLYPOP_EXT_DEBUG") == "1":
            try:
                states = [
                    (child.__class__.__name__, child.get_sensitive())
                    for child in self.buttons.get_children()
                ]
                _debug("Control sensitivity:", states)
            except Exception:
                pass
        return False

    def _on_current_changed(self, *_args):
        if self._destroyed:
            return
        if self._active:
            self._sync()
            self._refresh_controls()
            self.visualizer.set_playing(self._is_playing())
            if self._is_playing():
                self._start_lyrics_timer()
        else:
            # The ambient accent layer is global (it tracks the album across
            # the whole app), so keep it fresh even while the panel is
            # collapsed.  Only the palette is refreshed here; the expensive
            # lyrics/queue/artwork work stays gated behind _active.
            self._sync_palette()

    def _sync_palette(self):
        """Refresh just the global accent palette for the current track.

        Used when the panel is collapsed, where the full _sync() (lyrics,
        artwork, queue) is intentionally skipped but the AmbientStyler still
        needs to follow the album.  Runs the palette extraction on the worker
        thread and marshals the GTK update back to the main thread.
        """
        if self._destroyed:
            return
        track = self._current_track()
        info = _track_info(track)
        if not info.has_track:
            self._apply_palette(None)
            return
        self._token += 1
        token = self._token
        try:
            self._executor.submit(self._load_palette_only, token, info)
        except RuntimeError:
            pass

    def _load_palette_only(self, token, info):
        """Worker: resolve artwork and extract its palette, then apply it."""
        try:
            art = _embedded_art(info.audio_path) or _folder_cover(info.audio_path)
            palette = extract_palette(art) if (extract_palette and art) else None
            GLib.idle_add(self._apply_palette_if_current, token, palette)
        except Exception:
            _log("Palette-only load failed:")
            traceback.print_exc()

    def _apply_palette_if_current(self, token, palette):
        if self._stale(token):
            return False
        self._apply_palette(palette)
        return False

    def _on_status_changed(self, *_args):
        if self._active and not self._destroyed:
            _debug("Status changed: playing=", self._is_playing())
            self._refresh_controls()
            self.visualizer.set_playing(self._is_playing())
            if self._is_playing():
                self._start_lyrics_timer()
            else:
                self._stop_lyrics_timer()
                self._update_lyrics_position()

    def _on_seeked(self, _player, position_ms):
        if self._active and not self._destroyed:
            self._update_lyrics_position(position_ms)

    def _on_lyric_seek(self, position_ms):
        if self._player is None:
            return
        try:
            self._player.seek(max(0, int(position_ms)))
        except Exception:
            pass

    # ------------------------------------------------------------ progress bar

    def _seek_to(self, position_ms):
        """Seek from the Material 3 progress bar."""
        if self._player is None:
            return
        try:
            self._player.seek(max(0, int(position_ms)))
        except Exception:
            pass

    def _player_position_ms(self):
        try:
            return float(getattr(self._player, "position", 0) or 0)
        except Exception:
            return 0.0

    def _player_duration_ms(self):
        try:
            track = getattr(self._player, "current_track", None)
            return float(getattr(track, "duration", 0) or 0)
        except Exception:
            return 0.0

    # ------------------------------------------------------------ lyrics clock

    def _update_lyrics_position(self, position_ms=None):
        try:
            player = self._player
            if player is None:
                _debug("Lyrics position: no player")
                return
            if position_ms is None:
                position_ms = getattr(player, "position", 0)
            position_ms = max(0, int(position_ms or 0))

            track = getattr(player, "current_track", None)
            try:
                duration_ms = int(getattr(track, "duration", 0) or 0)
            except (TypeError, ValueError):
                duration_ms = 0

            now = time.monotonic()
            if now - self._lyrics_debug_last >= 2.0:
                self._lyrics_debug_last = now
                _debug(
                    "Lyrics position:",
                    position_ms,
                    "playing=",
                    self._is_playing(),
                    "timer=",
                    self._lyrics_timer,
                )
            self.lyrics.set_position(position_ms, duration_ms)
            view = getattr(self.lyrics, "_view", None)
            if view is not None and getattr(view, "_synced", False):
                active = getattr(view, "_active", -1)
                if active != self._last_lyric_index:
                    self._last_lyric_index = active
                    _debug("Lyrics active index:", active)
        except Exception:
            _log("Lyrics position update failed:")
            traceback.print_exc()

    def _start_lyrics_timer(self):
        if self._lyrics_timer or self._destroyed or not self._active:
            _debug(
                "Lyrics timer not started: timer=",
                self._lyrics_timer,
                "active=",
                self._active,
                "destroyed=",
                self._destroyed,
            )
            return
        self._update_lyrics_position()
        if self._is_playing():
            self._lyrics_timer = GLib.timeout_add(
                33, self._on_lyrics_tick
            )
            _debug("Lyrics timer started")
        else:
            _debug("Lyrics timer: not playing")

    def _stop_lyrics_timer(self):
        if self._lyrics_timer:
            try:
                GLib.source_remove(self._lyrics_timer)
            except Exception:
                pass
            self._lyrics_timer = 0

    def _on_lyrics_tick(self):
        if self._destroyed or not self._active:
            self._lyrics_timer = 0
            return False
        if not self._is_playing():
            self._update_lyrics_position()
            self._lyrics_timer = 0
            return False
        self._update_lyrics_position()
        return True

    # ------------------------------------------------------------ state

    def set_active(self, active):
        self._active = bool(active)
        _debug("Panel active:", self._active)
        if self._active:
            self._sync(force=True)
            self._refresh_controls()
            self.visualizer.set_playing(self._is_playing())
            self._start_lyrics_timer()
        else:
            self.visualizer.set_playing(False)
            self._stop_lyrics_timer()

    def show_lyrics_tab(self):
        """Select the Lyrics page (used by the bottom player bar)."""
        try:
            self.tabs.set_current_page(0)
            self._active = True
            self._start_lyrics_timer()
        except Exception:
            pass

    def show_queue_tab(self):
        """Select the Up Next page (used by the bottom player bar)."""
        try:
            self.tabs.set_current_page(1)
            self._active = True
        except Exception:
            pass

    def _apply_palette(self, palette):
        """Apply album art to the panel, wallpaper accent to library pages.

        The panel keeps the album-art palette so the now-playing card and
        lyrics feel connected to the record.  The global chrome and Songs
        page deliberately use the wallpaper accent instead, keeping the
        shell coherent while browsing unrelated albums.
        """
        self._last_palette = palette
        if self._styler is not None:
            self._styler.apply(palette)
        if self._page is not None:
            self._page.apply_accent(self._wallpaper_accent)
        accent = (palette or NEUTRAL_PALETTE)[1]
        _debug(
            "Palette applied:",
            None if palette is None else tuple(tuple(c) for c in palette),
            "-> album accent", accent,
            "wallpaper accent", self._wallpaper_accent,
        )
        if getattr(self, "lyrics", None) is not None:
            self.lyrics.set_accent(accent)
        if getattr(self, "progress", None) is not None:
            # Feed the accent (0..255 -> 0..1) to the Material 3 bar so its
            # fill and stop-point dot track the album colour like the rest.
            self.progress.set_colors(
                tuple(channel / 255.0 for channel in accent)
            )

    def _on_wallpaper_accent(self, accent):
        """Wallpaper changed: retint only the global chrome and Songs page."""
        if self._destroyed:
            return
        self._wallpaper_accent = tuple(accent)
        if self._ambient is not None:
            self._ambient.apply_accent(self._wallpaper_accent)
        if self._page is not None:
            self._page.apply_accent(self._wallpaper_accent)
        _debug("Wallpaper accent:", self._wallpaper_accent)

    def _sync(self, force=False):
        if self._destroyed:
            return

        info = _track_info(self._current_track())
        key = (info.has_track, info.title, info.artist, info.album, info.audio_path)
        if not force and key == self._last_key:
            return
        self._last_key = key

        self._token += 1
        token = self._token
        self.info.update(info.title, info.artist, info.album)

        if not info.has_track:
            self._stop_lyrics_timer()
            self.artwork.set_artwork(None)
            self._apply_palette(None)
            self.visualizer.set_color(*DEFAULT_ACCENT)
            self.lyrics.set_message("")
            return

        self.lyrics.set_message("Loading lyrics\u2026")
        try:
            self._executor.submit(self._load_assets, token, info)
        except RuntimeError:
            pass 

    def _stale(self, token):
        return self._destroyed or token != self._token

    # ------------------------------------------------------------ worker

    def _load_assets(self, token, info):
        try:
            source, art = "embedded", _embedded_art(info.audio_path)
            if not art:
                source, art = "folder", _folder_cover(info.audio_path)
            if art and not self._stale(token):
                self._push_artwork(token, art, source)

            # Sidecar .lrc -> embedded tag -> cache, preferring a synced
            # source.  A plain local version is shown immediately, then
            # LRCLIB is consulted for a timestamped upgrade.
            lyrics = _best_local_lyrics(info)
            _debug(
                "Local lyrics:",
                bool(lyrics),
                "synced=",
                bool(lyrics and has_synced_lyrics(lyrics)),
            )
            if lyrics and not self._stale(token):
                GLib.idle_add(self._apply_lyrics, token, lyrics)

            if (
                (not lyrics or not has_synced_lyrics(lyrics))
                and not self._stale(token)
            ):
                online = _online_lyrics(info)
                _debug(
                    "Online lyrics:",
                    bool(online),
                    "synced=",
                    bool(online and has_synced_lyrics(online)),
                )
                if online and (
                    not lyrics or has_synced_lyrics(online)
                ):
                    lyrics = online
                    if not self._stale(token):
                        GLib.idle_add(self._apply_lyrics, token, lyrics)

            if not art and not self._stale(token):
                art = _online_artwork(info)
                if not self._stale(token):
                    if art:
                        self._push_artwork(token, art, "online")
                    else:
                        GLib.idle_add(self._apply_artwork, token, None, None)

            if not lyrics and not self._stale(token):
                GLib.idle_add(self._apply_lyrics, token, None)
        except Exception:
            _log("Asset load failed for", info.title, "|", info.artist)
            traceback.print_exc()

    def _push_artwork(self, token, path, source):
        palette = extract_palette(path) if extract_palette else None
        GLib.idle_add(self._apply_artwork, token, path, palette)

    # ------------------------------------------------------------ main thread

    def _apply_artwork(self, token, path, palette):
        if self._stale(token):
            return False
        shown = self.artwork.set_artwork(path)
        self._apply_palette(palette if shown else None)
        accent = self.artwork.accent if shown else None
        self.visualizer.set_color(*(accent or DEFAULT_ACCENT))
        return False

    def _apply_lyrics(self, token, text):
        if not self._stale(token):
            _debug(
                "Lyrics applied:",
                len(text or ""),
                "chars, synced=",
                bool(text and has_synced_lyrics(text)),
            )
            self.lyrics.set_lyrics(text)
            self._update_lyrics_position()
            self._start_lyrics_timer()
        return False

    def _on_destroy(self, *_args):
        self._destroyed = True
        self._stop_lyrics_timer()
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self._player is not None:
            for ident in self._signal_ids:
                try:
                    self._player.disconnect(ident)
                except Exception:
                    pass
        self._signal_ids.clear()
        if self._accent_provider is not None:
            try:
                self._accent_provider.stop()
            except Exception:
                pass
        if self._styler is not None:
            self._styler.remove()
        if self._page is not None:
            self._page.remove()
        if self._ambient is not None:
            self._ambient.remove()


# ---------------------------------------------------------------- install

def _toggle_icon():
    theme = Gtk.IconTheme.get_default()
    for name in ("sidebar-show-right-symbolic", "view-dual-symbolic",
                 "audio-x-generic-symbolic"):
        if theme is not None and theme.has_icon(name):
            return name
    return "audio-x-generic-symbolic"


def _dump_widget_tree(widget, depth=0, max_depth=6):
    """Print widget visibility/allocations when LOLLYPOP_EXT_DEBUG=1."""
    if widget is None or depth > max_depth:
        return
    try:
        alloc = widget.get_allocation()
        _debug(
            "  " * depth + "%s name=%r visible=%s mapped=%s alloc=%dx%d" % (
                widget.__class__.__name__,
                widget.get_name(),
                widget.get_visible(),
                widget.get_mapped(),
                alloc.width,
                alloc.height,
            )
        )
        children = widget.get_children()
    except Exception:
        return
    for child in children:
        _dump_widget_tree(child, depth + 1, max_depth)


def _place_toggle(toolbar, toggle):
    try:
        end = getattr(toolbar, "end", None)
        inner = end.get_child() if end is not None else None
        if isinstance(inner, Gtk.Box):
            inner.pack_end(toggle, False, False, 0)
            return True
        if toolbar is not None and hasattr(toolbar, "pack_end"):
            toolbar.pack_end(toggle)
            return True
    except Exception:
        pass
    return False


def _show_songs_page():
    """Show the full-page Songs browser in Lollypop's main content stack."""
    try:
        from lollypop.define import App

        container = App().window.container
        page = getattr(container, "_lollypop_ext_songs_page", None)
        created = False
        if page is None or page.destroyed or getattr(
                page, "_destroy_pending", False):
            page = SongsPage()
            container._lollypop_ext_songs_page = page
            created = True
        page.show()
        container.sub_widget.set_visible_child(container.grid_view)
        if page not in container.stack.get_children():
            container.stack.add(page)
        container.stack.set_visible_child(page)
        container.set_focused_view(page)
        # Keep the injected sidebar row visibly selected while the Songs page
        # is the active view.  This is selection only; it does not re-enter
        # Lollypop's row-activated view switching.
        try:
            sidebar = getattr(container, "sidebar", None)
            listbox = getattr(sidebar, "listbox", None)
            if listbox is not None:
                for row in listbox.get_children():
                    if getattr(row, "id", None) == SONGS_SIDEBAR_ROW_ID:
                        listbox.select_row(row)
                        break
        except Exception:
            pass
        if not created:
            page.populate()
        container.emit("can-go-back-changed", container.can_go_back)
        return True
    except Exception:
        _log("Songs page failed:")
        traceback.print_exc()
        return False


def _inject_songs_sidebar_row():
    """Add a "Songs" row to the native Lollypop sidebar, directly below the
    "Albums" section, and show the full-page Songs browser when activated.

    Lollypop's sidebar is its own SelectionList of SelectionListRow items
    driven from `ShownLists`; the extension therefore inserts one extra row at
    runtime.  The row id is the extension sentinel, so Lollypop's own sidebar
    activation handler ignores it (falls through to the no-op branch) and our
    own row-activated handler drives the behaviour.

    The sidebar is populated asynchronously (SelectionList is a
    LazyLoadingView), so at install time there may be no "Albums" row to
    anchor against yet.  Insertion is therefore deferred until the native
    rows exist, and repeated after every sidebar repopulation.
    """
    try:
        from lollypop.define import App, Type, SelectionListMask
        from lollypop.selectionlist import SelectionListRow

        sidebar = App().window.container.sidebar
        listbox = sidebar.listbox

        def find_all_index(rows):
            for i, row in enumerate(rows):
                try:
                    if int(getattr(row, "id", -2)) == Type.ALL:
                        return i
                except Exception:
                    continue
            return None

        def insert_row():
            """Insert the row below Albums; False if the sidebar is not ready."""
            try:
                rows = list(listbox.get_children())
                all_index = find_all_index(rows)
                if all_index is None:
                    return False

                existing = [
                    row for row in rows
                    if getattr(row, "id", None) == SONGS_SIDEBAR_ROW_ID
                ]
                if existing and rows.index(existing[0]) == all_index + 1:
                    return True

                # Remove a stale/duplicated row before re-inserting it at the
                # correct position (indexes shift when rows are removed).
                for row in existing:
                    try:
                        listbox.remove(row)
                    except Exception:
                        row.destroy()

                rows = list(listbox.get_children())
                all_index = find_all_index(rows)
                if all_index is None:
                    return False

                row = SelectionListRow(
                    SONGS_SIDEBAR_ROW_ID, "Songs", "Songs",
                    SelectionListMask.SIDEBAR | SelectionListMask.LABEL, 0,
                )
                row.populate()
                # The sentinel id has no icon mapping in Lollypop's
                # get_icon_name(); give the injected row the same visual
                # weight as the native Albums/Artists rows.
                try:
                    grid = row.get_child()
                    artwork = None
                    if grid is not None:
                        for child in grid.get_children():
                            if isinstance(child, Gtk.Image):
                                artwork = child
                                break
                    if artwork is not None:
                        artwork.set_from_icon_name(
                            "folder-music-symbolic", Gtk.IconSize.INVALID)
                        artwork.set_pixel_size(20)
                        artwork.show()
                except Exception:
                    pass
                listbox.insert(row, all_index + 1)
                row.show()
                row.set_activatable(True)
                return True
            except Exception:
                _log("Songs sidebar row insertion failed:")
                traceback.print_exc()
                return False

        def on_songs_activated(_listbox, activated_row):
            if getattr(activated_row, "id", None) != SONGS_SIDEBAR_ROW_ID:
                return
            _show_songs_page()

        def insert_idle():
            insert_row()
            return False

        if not getattr(listbox, "_lollypop_ext_songs_wired", False):
            listbox.connect("row-activated", on_songs_activated)
            # Re-add the row whenever Lollypop rebuilds the sidebar (switching
            # between Albums/Artists/Genres/Playlists clears the listbox).
            sidebar.connect("populated", lambda _sidebar: GLib.idle_add(
                insert_idle))
            try:
                listbox._lollypop_ext_songs_wired = True
            except Exception:
                pass

        # Immediate attempt for the common case where rows already exist,
        # then a short retry loop for the normal async startup path.
        if insert_row():
            return True
        attempts = [40]  # ~10s at 250ms

        def retry():
            if insert_row():
                return False
            attempts[0] -= 1
            return attempts[0] > 0

        GLib.idle_add(insert_idle)
        GLib.timeout_add(250, retry)
        return True
    except Exception:
        _log("Songs sidebar row injection failed:")
        traceback.print_exc()
        return False


def install_right_panel(vgrid, container, toolbar=None):
    panel = RightPanel()
    # Give the revealer a real width before the first window allocation.
    # A zero-width initial request can leave the album grid unallocated until
    # something (like opening the header menu) forces a relayout.
    panel.set_size_request(PANEL_DEFAULT_WIDTH, -1)

    revealer = Gtk.Revealer()
    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
    revealer.set_transition_duration(250)
    revealer.set_vexpand(True)
    revealer.add(panel)
    panel.show()
    panel.set_margin_top(0)
    panel.set_margin_bottom(0)

    # Changing a child's size request from inside a "size-allocate" handler
    # re-enters GTK's allocation cycle.  That is what left the album view
    # (especially its GtkFlowBox) with a 1px allocation until something else
    # forced a new layout.  Defer the width/height update until allocation
    # has finished.
    resize_idle = [0]
    pending_size = [0, 0]

    def apply_window_size():
        resize_idle[0] = 0
        width, height = pending_size
        target_width = max(280, int(width * 0.25))
        if panel.get_size_request()[0] != target_width:
            panel.set_size_request(target_width, -1)

        # The Lyrics / Up Next notebook owns the remaining height.  No fixed
        # queue height is needed now that both lists are separate tabs.
        return False

    def on_window_size_allocate(widget, allocation):
        pending_size[0] = allocation.width
        pending_size[1] = allocation.height
        if not resize_idle[0]:
            resize_idle[0] = GLib.idle_add(apply_window_size)

    def attach_resize_listener():
        window = vgrid.get_toplevel()
        if window and hasattr(window, "connect"):
            window.connect("size-allocate", on_window_size_allocate)
            allocation = window.get_allocation()
            if allocation.width > 1:
                on_window_size_allocate(window, allocation)
        return False

    GLib.idle_add(attach_resize_listener)

    toggle = Gtk.ToggleButton()
    toggle.add(Gtk.Image.new_from_icon_name(_toggle_icon(), Gtk.IconSize.BUTTON))
    toggle.get_child().show()
    toggle.set_tooltip_text("Now playing panel")
    toggle.get_style_context().add_class("image-button")

    def on_toggled(button):
        revealer.set_reveal_child(button.get_active())

    def on_reveal_changed(rev, _pspec):
        active = rev.get_reveal_child()
        if toggle.get_active() != active:
            toggle.set_active(active)
        panel.set_active(active)

    toggle.connect("toggled", on_toggled)
    revealer.connect("notify::reveal-child", on_reveal_changed)

    container.connect(
        "notify::visible",
        lambda c, _p: revealer.set_visible(c.get_visible()),
    )

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    box.set_hexpand(True)
    box.set_vexpand(True)

    placed = _place_toggle(toolbar, toggle)
    if placed:
        toggle.show()

    try:
        vgrid.remove(container)
        box.pack_start(container, True, True, 0)
        box.pack_end(revealer, False, False, 0)
        # Put the wrapper back into the row the container occupied (row 0
        # is Lollypop's header bar, row 1 is the content area).  Using
        # vgrid.add() appended a third row, which could be allocated at
        # zero height until another widget forced a resize.
        vgrid.attach(box, 0, 1, 1, 1)
        box.show_all()
        container.show()
        revealer.show()
        panel.show_all()

        if os.environ.get("LOLLYPOP_EXT_DEBUG") == "1":
            def dump_later():
                _debug("---- window grid tree ----")
                _dump_widget_tree(vgrid, 0, 5)
                _debug("---- panel wrapper tree ----")
                _dump_widget_tree(box, 0, 7)
                try:
                    view = container.view
                except Exception:
                    view = None
                _debug("---- current view ----", view)
                _dump_widget_tree(view, 0, 9)
                return False
            GLib.timeout_add(2500, dump_later)
    except Exception:
        # The panel registered two screen-level CSS providers on construction.
        # If we bail before parenting it, the "destroy" signal never fires, so
        # tear it down explicitly to avoid leaking the providers on screen.
        try:
            panel._on_destroy()
        except Exception:
            pass
        try:
            if container.get_parent() is box:
                box.remove(container)
            if container.get_parent() is None:
                vgrid.add(container)
        except Exception:
            pass
        raise

    if not placed:
        revealer.set_reveal_child(True)

    # Bottom playback bar.  It is attached only after the panel wrapper is in
    # the grid; the native headerbar transport is hidden only if this
    # succeeds, so playback always has a visible control surface.
    if install_player_bar is not None:
        try:
            install_player_bar(
                vgrid, toolbar, container,
                panel=panel, revealer=revealer, toggle=toggle,
            )
        except Exception:
            _log("Bottom player bar installation failed; keeping headerbar:")
            traceback.print_exc()

    # Apple shell decorations: sidebar/top-bar classes and the album hero.
    if install_apple_shell is not None:
        try:
            install_apple_shell(container, toolbar)
        except Exception:
            _log("Apple shell installation failed:")
            traceback.print_exc()

    # Native-sidebar "Songs" row; opens the full-page Songs browser.
    _inject_songs_sidebar_row()

    if os.environ.get("LOLLYPOP_EXT_DEBUG") == "1":
        def open_for_debug():
            revealer.set_reveal_child(True)
            return False
        GLib.timeout_add(1200, open_for_debug)

    # Install the fullscreen lyrics hook after the main window has finished
    # setting up.  It patches FullScreen lazily, so no additional system file
    # patch is needed and a failure leaves native fullscreen untouched.
    def install_fullscreen_hook():
        try:
            from lollypop.fullscreen_lyrics import install_fullscreen
            install_fullscreen()
        except Exception:
            _log("Fullscreen lyrics hook failed:")
            traceback.print_exc()
        return False

    GLib.idle_add(install_fullscreen_hook)
    return panel