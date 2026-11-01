#!/usr/bin/env python3
"""LEGACY optional patch: embed a visualizer in Lollypop's fullscreen window.

The extension already installs its fullscreen lyrics layout at runtime from
``ui/fullscreen_lyrics.py``; no system-file patch is required for that.  This
script is retained only for manually adding the cava/GStreamer visualizer to
the native fullscreen screen.  It is **not applied automatically**.

Usage:
    sudo python3 ui/patcher_fullscreen.py            apply / re-apply
    sudo python3 ui/patcher_fullscreen.py --restore  restore the pristine file
    python3 ui/patcher_fullscreen.py --status        inspect state (no root)
"""

import ast
import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import traceback

START = "# LOLLYPOP FULLSCREEN PATCH START"
END = "# LOLLYPOP FULLSCREEN PATCH END"

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSION_DIR = os.path.join(PROJECT_DIR, "ui")
BACKUP_DIR = os.path.join(PROJECT_DIR, "backups")


def fail(message):
    print("ERROR:", message, file=sys.stderr)
    raise SystemExit(1)


def _lollypop_package_dir():
    spec = importlib.util.find_spec("lollypop")
    if spec is None:
        fail("Lollypop is not installed for this Python interpreter.")
    if spec.origin:
        return os.path.dirname(os.path.abspath(spec.origin))
    locations = list(spec.submodule_search_locations or ())
    if not locations:
        fail("Could not locate Lollypop's package directory.")
    return os.path.abspath(locations[0])


def _lollypop_version():
    try:
        return importlib.metadata.version("lollypop")
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        result = subprocess.run(
            ["pacman", "-Q", "lollypop"],
            capture_output=True, text=True, check=True,
        )
        fields = result.stdout.split()
        if len(fields) >= 2:
            return fields[1].rsplit("-", 1)[0]
    except (OSError, subprocess.CalledProcessError):
        pass
    return "unknown"


VERSION = _lollypop_version()
TARGET = os.path.join(_lollypop_package_dir(), "fullscreen.py")
BACKUP = os.path.join(BACKUP_DIR, f"fullscreen.py.{VERSION}.pristine")


PATCH_TEMPLATE = """
try:
    import sys as _lollypop_ext_sys
    _extension_dir = __EXTENSION_DIR__
    if _extension_dir not in _lollypop_ext_sys.path:
        _lollypop_ext_sys.path.append(_extension_dir)

    from widget_injector import SimpleVisualizer
    from lollypop.define import App

    # Only inject once per fullscreen window.
    if getattr(self, "_fs_visualizer", None) is None:
        self._fs_visualizer = SimpleVisualizer()
        self._fs_visualizer.set_size_request(-1, 80)
        self._fs_visualizer.set_valign(Gtk.Align.END)
        self._fs_visualizer.set_margin_bottom(40)
        self._fs_visualizer.set_margin_start(40)
        self._fs_visualizer.set_margin_end(40)
        self.__overlay_grid.attach(self._fs_visualizer, 0, 5, 4, 1)
        self._fs_visualizer.show_all()

    def _sync_fs_visualizer(*_args):
        try:
            visualizer = getattr(self, "_fs_visualizer", None)
            player = getattr(App(), "player", None)
            if visualizer is not None and player is not None:
                visualizer.set_playing(player.is_playing)
        except Exception:
            pass

    player = getattr(App(), "player", None)
    if player is not None:
        previous = getattr(self, "_fs_visualizer_handler", None)
        if previous is not None:
            try:
                player.disconnect(previous)
            except Exception:
                pass
        self._fs_visualizer_handler = player.connect(
            "status-changed", _sync_fs_visualizer
        )
        _sync_fs_visualizer()

        if not getattr(self, "_fs_visualizer_destroy_hooked", False):
            def _detach_fs_visualizer(*_args):
                player = getattr(App(), "player", None)
                handler = getattr(self, "_fs_visualizer_handler", None)
                if player is not None and handler is not None:
                    try:
                        player.disconnect(handler)
                    except Exception:
                        pass
                self._fs_visualizer_handler = None
                return False
            self.connect("destroy", _detach_fs_visualizer)
            self._fs_visualizer_destroy_hooked = True
except Exception as exc:
    print("Fullscreen visualizer injection failed:", exc)
"""


def is_pristine(path):
    try:
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        ast.parse(source, filename=path)
    except (OSError, SyntaxError):
        return False
    return START not in source and END not in source


def strip_patch(source):
    """Remove one complete patch block; reject incomplete markers."""
    starts = source.count(START)
    ends = source.count(END)
    if starts != ends or starts > 1:
        fail(
            "Malformed or duplicate fullscreen patch markers. Reinstall the "
            "lollypop package or restore the pristine backup first."
        )
    if starts == 0:
        return source
    pattern = (
        r"^[ \t]*" + re.escape(START)
        + r"[^\n]*\n.*?^[ \t]*" + re.escape(END)
        + r"[^\n]*(?:\n|$)"
    )
    result, count = re.subn(
        pattern, "", source, count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    if count != 1:
        fail("Could not safely remove the existing fullscreen patch.")
    return result


def ensure_backup():
    if os.path.isfile(BACKUP):
        if not is_pristine(BACKUP):
            fail(
                "Fullscreen backup exists but contains patch markers: "
                f"{BACKUP}\nDelete it and re-run; the patcher will rebuild "
                "it from the installed package file."
            )
        return BACKUP

    os.makedirs(BACKUP_DIR, exist_ok=True)
    candidates = (
        os.path.join(os.path.dirname(TARGET), "fullscreen.py.pristine"),
        TARGET + ".bak",
        TARGET + ".pristine",
    )
    for candidate in candidates:
        if os.path.isfile(candidate) and is_pristine(candidate):
            shutil.copy2(candidate, BACKUP)
            print("Created verified original backup:", BACKUP)
            return BACKUP

    with open(TARGET, encoding="utf-8") as handle:
        current = handle.read()
    if START in current or END in current:
        fail(
            "fullscreen.py is already patched but no pristine backup was "
            f"found.\nReinstall the lollypop package or restore "
            f"backups/fullscreen.py.{VERSION}.pristine by hand, then re-run."
        )

    shutil.copy2(TARGET, BACKUP)
    print("Created original backup:", BACKUP)
    return BACKUP


def write_atomic(source):
    try:
        ast.parse(source, filename=TARGET)
    except SyntaxError as exc:
        fail(f"Generated source failed syntax validation: {exc}")

    fd, temp_path = tempfile.mkstemp(
        prefix=".lollypop-fullscreen-", suffix=".py",
        dir=os.path.dirname(TARGET),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
        old_stat = os.stat(TARGET)
        shutil.copystat(TARGET, temp_path)
        os.chown(temp_path, old_stat.st_uid, old_stat.st_gid)
        with open(temp_path, encoding="utf-8") as handle:
            ast.parse(handle.read(), filename=TARGET)
        os.replace(temp_path, TARGET)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def build_patch(indent):
    code = textwrap.dedent(PATCH_TEMPLATE).strip("\n")
    code = code.replace("__EXTENSION_DIR__", repr(EXTENSION_DIR))
    lines = [START]
    for line in code.splitlines():
        lines.append(indent + line if line.strip() else "")
    lines.append(END)
    return "\n".join(lines)


def status():
    print("Lollypop version   :", VERSION)
    print("Package fullscreen :", TARGET)
    print("Extension dir      :", EXTENSION_DIR)
    try:
        with open(TARGET, encoding="utf-8") as handle:
            source = handle.read()
        parse_error = None
        try:
            ast.parse(source, filename=TARGET)
        except SyntaxError as exc:
            parse_error = str(exc)
    except OSError as exc:
        source = ""
        parse_error = str(exc)
    applied = source.count(START) == 1 and source.count(END) == 1
    print("Visualizer patch   :", "applied" if applied else "not applied")
    print("Syntax             :", parse_error or "OK")
    if os.path.isfile(BACKUP):
        print(
            "Pristine backup    :",
            "OK" if is_pristine(BACKUP) else "CORRUPT",
            BACKUP,
        )
    else:
        print("Pristine backup    : missing", BACKUP)
    if parse_error:
        raise SystemExit(1)


def restore():
    if not os.path.isfile(BACKUP):
        fail(
            f"No pristine backup found for Lollypop {VERSION}: {BACKUP}\n"
            "If the package was upgraded, reinstall lollypop instead."
        )
    if not is_pristine(BACKUP):
        fail("Refusing to restore: backup contains fullscreen markers.")
    with open(BACKUP, encoding="utf-8") as handle:
        source = handle.read()
    write_atomic(source)
    print("Restored original fullscreen.py from", BACKUP)


def apply():
    if not os.path.isfile(TARGET):
        fail("Lollypop fullscreen.py not found: " + TARGET)
    if not os.path.isfile(os.path.join(EXTENSION_DIR, "widget_injector.py")):
        fail("widget_injector.py not found in: " + EXTENSION_DIR)

    with open(TARGET, encoding="utf-8") as handle:
        original = handle.read()

    print("Preserving original backup:", ensure_backup())
    source = strip_patch(original)

    anchors = list(re.finditer(
        r"(?m)^([ \t]*)self\.add\(widget\)[ \t]*$", source
    ))
    if len(anchors) != 1:
        fail(
            "Expected exactly one `self.add(widget)` in fullscreen.py, found "
            f"{len(anchors)}. The installed Lollypop differs from the version "
            "this patch was written for."
        )
    anchor = anchors[0]
    source = (
        source[:anchor.end()]
        + "\n\n"
        + build_patch(anchor.group(1))
        + "\n"
        + source[anchor.end():]
    )
    write_atomic(source)
    print("Fullscreen visualizer patch applied and syntax-checked.")


def main():
    args = sys.argv[1:]
    if "--status" in args:
        status()
        return
    if os.geteuid() != 0:
        fail("Run with sudo.")
    if "--restore" in args:
        restore()
    else:
        apply()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
