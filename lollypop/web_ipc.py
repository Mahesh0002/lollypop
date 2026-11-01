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
    Python <-> SPA message bridge.

    Python -> SPA : WebView.run_javascript("window.pybridge.__receive(...)")
    SPA -> Python : window.webkit.messageHandlers.pybridge.postMessage(json)
                    (registered handler "pybridge", delivered on
                     UserContentManager::script-message-received)
"""

import json

import gi
gi.require_version("WebKit2", "4.1")
gi.require_version("JavaScriptCore", "4.1")
from gi.repository import Gio, GLib, GObject, WebKit2, JavaScriptCore

from lollypop.utils import emit_signal
from lollypop.logger import Logger

HANDLER_NAME = "pybridge"

# Injected at document start, before any SPA script runs. Defines the single
# global the SPA is allowed to touch.
USER_SCRIPT = """
(function () {
    if (window.pybridge) { return; }
    var handlers = Object.create(null);

    window.pybridge = {
        send: function (message) {
            try {
                window.webkit.messageHandlers.pybridge.postMessage(
                    JSON.stringify(message));
            } catch (e) {
                console.error("pybridge.send failed", e);
            }
        },
        on: function (type, callback) {
            (handlers[type] || (handlers[type] = [])).push(callback);
            return function () {
                var list = handlers[type] || [];
                var i = list.indexOf(callback);
                if (i !== -1) { list.splice(i, 1); }
            };
        },
        __receive: function (message) {
            var parsed = message;
            if (typeof message === "string") {
                try { parsed = JSON.parse(message); }
                catch (e) { console.error("pybridge bad payload", e); return; }
            }
            if (!parsed || !parsed.type) { return; }
            (handlers[parsed.type] || []).forEach(function (fn) {
                try { fn(parsed.payload, parsed); }
                catch (e) { console.error("pybridge handler error", e); }
            });
            (handlers["*"] || []).forEach(function (fn) {
                try { fn(parsed); } catch (e) { console.error(e); }
            });
        }
    };
})();
"""


class WebIPC(GObject.GObject):
    """
        Owns the UserContentManager, injects the pybridge shim and routes
        messages in both directions. One instance per WebView.
    """

    __gsignals__ = {
        # (type as str, payload as dict/None) -- raw, untyped access
        "py-to-web": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        # (message as str, raw json from the page)
        "web-to-py": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        """
            Init IPC
        """
        GObject.GObject.__init__(self)
        self.__ucm = WebKit2.UserContentManager()
        self.__ucm.register_script_message_handler(HANDLER_NAME)
        self.__ucm.add_script(
            WebKit2.UserScript.new(USER_SCRIPT,
                                   WebKit2.UserContentInjectedFrames.
                                   ALL_FRAMES,
                                   WebKit2.UserScriptInjectionTime.
                                   START))
        self.__ucm.connect("script-message-received",
                           self.__on_script_message_received)
        self.__webview = None
        self.__handlers = {}
        self.__queue = []
        self.__ready = False

    @property
    def content_manager(self):
        """
            Pass to WebKit2.WebView constructor
            @return WebKit2.UserContentManager
        """
        return self.__ucm

    @property
    def ready(self):
        """
            True once a WebView is bound and loaded
            @return bool
        """
        return self.__ready

    def bind(self, webview):
        """
            Attach the WebView this IPC talks to
            @param webview as WebKit2.WebView
        """
        self.__webview = webview
        webview.connect("notify::estimated-load-progress",
                        self.__on_load_progress)

    def send(self, message):
        """
            Python -> SPA. Message is a dict, see ipc type union in web/src.
            @param message as dict
        """
        try:
            script = "window.pybridge && window.pybridge.__receive(%s);" % (
                json.dumps(message))
        except (TypeError, ValueError) as e:
            Logger.error("WebIPC::send(): %s", e)
            return
        Logger.info("WebIPC::py -> web: %s", message.get("type", "?"))
        emit_signal(self, "py-to-web",
                    message.get("type", ""), json.dumps(message))
        if not self.__ready or self.__webview is None:
            self.__queue.append(script)
            return
        self.__run(script)

    def on(self, message_type, callback):
        """
            Subscribe to a SPA -> Python message
            @param message_type as str, e.g. "GET_LIBRARY"
            @param callback as fn(payload: dict|None, message: dict)
            @return fn() unsubscribe
        """
        (self.__handlers.setdefault(message_type, [])).append(callback)

        def unsubscribe():
            try:
                self.__handlers[message_type].remove(callback)
            except ValueError:
                pass
        return unsubscribe

    def flush(self):
        """
            Deliver messages queued before the page finished loading
        """
        self.__ready = True
        pending, self.__queue = self.__queue, []
        for script in pending:
            self.__run(script)

############
# PRIVATE  #
############
    def __run(self, script):
        """
            Evaluate a script in the page
            @param script as str
        """
        if self.__webview is None:
            return
        try:
            self.__webview.run_javascript(script,
                                           None,
                                           self.__on_javascript_finished,
                                           None)
        except Exception as e:
            Logger.error("WebIPC::__run(): %s", e)

    def __on_javascript_finished(self, webview, result, *ignore):
        """
            Swallow GAsyncResult, surface JS exceptions in logs
        """
        try:
            webview.run_javascript_finish(result)
        except GLib.Error as e:
            Logger.error("WebIPC::__on_javascript_finished(): %s", e)

    def __on_load_progress(self, webview, *ignore):
        """
            Page reaching 100% means the shim exists; drain the queue
        """
        if webview.get_estimated_load_progress() >= 1.0:
            self.flush()

    def __on_script_message_received(self, ucm, result):
        """
            @param ucm as WebKit2.UserContentManager
            @param result as WebKit2.JavascriptResult
        """
        raw = None
        try:
            value = result.get_js_value()
            if value is None:
                raw = None
            elif value.is_string():
                # page sends a JSON *string*; unwrap the JS string itself
                raw = value.to_string()
            else:
                # jsc_value_to_json() indent is unsigned; 0 = compact
                raw = value.to_json(0)
        except GLib.Error as e:
            Logger.error("WebIPC::__on_script_message_received(): %s", e)
            return
        if not raw:
            return
        try:
            message = json.loads(raw)
        except ValueError as e:
            Logger.error("WebIPC::bad json from page: %s (%s)", e, raw)
            return
        if not isinstance(message, dict) or "type" not in message:
            Logger.error("WebIPC::malformed message: %s", raw)
            return
        Logger.info("WebIPC::web -> py: %s", raw)
        emit_signal(self, "web-to-py", raw)
        payload = message.get("payload")
        for callback in self.__handlers.get("*", []):
            self.__safe(callback, payload, message)
        for callback in self.__handlers.get(message["type"], []):
            self.__safe(callback, payload, message)

    @staticmethod
    def __safe(callback, *args):
        try:
            callback(*args)
        except Exception as e:
            Logger.error("WebIPC::handler raised: %s", e)