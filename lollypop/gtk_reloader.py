#!/usr/bin/env python3
"""LEGACY GTK CSS reload helper (unused).

The extension now applies accent colours in-process, so no file watcher or
gsettings toggle is needed.  This script is retained for reference only.
"""
import time
import subprocess
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

TARGET_FILE = os.path.expanduser("~/.config/gtk-3.0/gtk.css")
TARGET_DIR = os.path.dirname(TARGET_FILE)

def _gsettings_get(key):
    result = subprocess.run(
        ["gsettings", "get", "org.gnome.desktop.interface", key],
        capture_output=True, text=True, check=True
    )
    return result.stdout.strip().strip("'")


def force_gtk_redraw():
    """Forces GTK applications to reload CSS by briefly toggling the system theme."""
    print("🔄 GTK CSS change detected! Triggering live redraw...")
    try:
        # 1. Read both values first so the fallback trigger can restore the
        # user's actual colour scheme instead of leaving it on prefer-dark.
        current_theme = _gsettings_get("gtk-theme")
        try:
            current_scheme = _gsettings_get("color-scheme")
        except Exception:
            current_scheme = None

        # 2. Toggle to a dummy theme, then immediately back
        dummy_theme = "Adwaita" if current_theme != "Adwaita" else "HighContrast"

        try:
            subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "gtk-theme", dummy_theme])
            time.sleep(0.05)  # Brief pause to ensure GTK catches the state change
        finally:
            subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "gtk-theme", current_theme])

        # Fallback secondary trigger: toggle colour scheme, then restore the
        # value we read above (which may have been "default" or prefer-light).
        if current_scheme:
            try:
                subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "prefer-light"])
                time.sleep(0.05)
            finally:
                subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", current_scheme])

        print(f"✨ Redraw complete (Toggled {current_theme})")
    except Exception as e:
        print(f"🔴 Error triggering redraw: {e}")

class CSSHandler(FileSystemEventHandler):
    def on_modified(self, event):
        # Trigger only when the specific CSS file is saved
        if event.src_path == TARGET_FILE:
            force_gtk_redraw()

if __name__ == "__main__":
    print(f"👀 Watching {TARGET_FILE} for live CSS changes...")

    os.makedirs(TARGET_DIR, exist_ok=True)
    event_handler = CSSHandler()
    observer = Observer()
    observer.schedule(event_handler, path=TARGET_DIR, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\n👋 Reloader stopped.")
    observer.join()
