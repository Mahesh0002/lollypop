"""LEGACY MPRIS -> gtk.css helper.

The live extension applies its accent layer in-process (AmbientStyler), so
this script is unused.  It is kept runnable for reference/manual fallback.
"""

import os
import sys
import tempfile
import urllib.parse
from gi.repository import Gio, GLib

# Import the color extractor module
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from color_extractor import extract_palette, generate_global_accent_css

GTK_CSS_PATH = os.path.expanduser("~/.config/gtk-3.0/gtk.css")

def update_gtk_css(new_css):
    """
    Safely injects the ambient theme block into ~/.config/gtk-3.0/gtk.css
    without overwriting any pre-existing user GTK CSS rules.
    """
    os.makedirs(os.path.dirname(GTK_CSS_PATH), exist_ok=True)

    start_marker = "/* --- LOLLYPOP EXTENSION AMBIENT START --- */"
    end_marker = "/* --- LOLLYPOP EXTENSION AMBIENT END --- */"

    existing_css = ""
    if os.path.exists(GTK_CSS_PATH):
        with open(GTK_CSS_PATH, "r", encoding="utf-8", errors="replace") as f:
            existing_css = f.read()

    # Strip old extension theme block if it exists
    if start_marker in existing_css and end_marker in existing_css:
        before = existing_css.split(start_marker)[0]
        after = existing_css.split(end_marker)[1]
        existing_css = before.strip() + "\n" + after.strip()

    # Append fresh ambient CSS block
    updated_css = f"{existing_css.strip()}\n\n{start_marker}\n{new_css}\n{end_marker}\n"

    # Write atomically so GTK never reads a half-written gtk.css.
    fd, tmp = tempfile.mkstemp(
        prefix=".lollypop-ambient-", suffix=".css",
        dir=os.path.dirname(GTK_CSS_PATH),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(updated_css.strip() + "\n")
        os.replace(tmp, GTK_CSS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    print(f"✨ Injected ambient theme into: {GTK_CSS_PATH}")


def on_properties_changed(connection, sender_name, object_path, interface_name, signal_name, parameters, user_data):
    """
    Triggers automatically whenever Lollypop changes songs or playback states.
    """
    interface, changed_properties, _ = parameters.unpack()

    if "Metadata" in changed_properties:
        metadata = changed_properties["Metadata"]

        def value(key):
            item = metadata.get(key, "")
            if hasattr(item, "unpack"):
                item = item.unpack()
            return item or ""

        print("\n🎵 Track Change Detected via MPRIS DBus!")

        art_url = value("mpris:artUrl")
        # Standard MPRIS key; older Lollypop builds used mpris:url.
        track_url = value("xesam:url") or value("mpris:url")

        image_path = None

        # 1. Parse artwork path from MPRIS artUrl
        if art_url.startswith("file://"):
            image_path = urllib.parse.unquote(art_url[7:])

        # 2. Fallback: Check for sidecar cover.jpg in track directory
        if not image_path or not os.path.exists(image_path):
            if track_url.startswith("file://"):
                audio_path = urllib.parse.unquote(track_url[7:])
                folder = os.path.dirname(audio_path)
                candidate = os.path.join(folder, "cover.jpg")
                if os.path.exists(candidate):
                    image_path = candidate

        if image_path and os.path.exists(image_path):
            print(f"🖼️ Active Artwork: {image_path}")
            palette = extract_palette(image_path)
            if palette:
                background, accent1, accent2 = palette
                css = generate_global_accent_css(
                    background, accent1, accent2
                )
                update_gtk_css(css)
        else:
            print("⚠️ No artwork located for current track.")


def main():
    print("🎧 Initializing Lollypop DBus Real-Time Listener...")
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    # Subscribe to Lollypop's MPRIS DBus signals
    bus.signal_subscribe(
        "org.mpris.MediaPlayer2.Lollypop",  # Bus Name
        "org.freedesktop.DBus.Properties",  # Interface
        "PropertiesChanged",                 # Signal
        "/org/mpris/MediaPlayer2",          # Object Path
        None,
        Gio.DBusSignalFlags.NONE,
        on_properties_changed,
        None
    )

    print("🟢 Listening for live track changes... (Press Ctrl+C to exit)")
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n👋 DBus listener stopped.")


if __name__ == "__main__":
    main()
