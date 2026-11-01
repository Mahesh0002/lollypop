#!/usr/bin/env python3
"""Safe, idempotent patcher for Lollypop's custom Now Playing panel.

  sudo python ui/patcher.py            apply / re-apply the patch
  sudo python ui/patcher.py --restore  put the original window.py back
  python ui/patcher.py --status        show patch/backup state (no root)

Re-run after every Lollypop package update (the update overwrites window.py).
The pristine backup lives in `backups/` and is tied to the installed Lollypop
version, so a package upgrade can never restore the wrong file by accident.
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
import traceback

# Marker names are unchanged from the previous patcher on purpose, so
# re-running this removes the OLD patch blocks cleanly before applying.
IS = "# LOLLYPOP EXTENSION PATCH START"
IE = "# LOLLYPOP EXTENSION PATCH END"
WS = "# LOLLYPOP EXTENSION 3-PANE INJECTION START"
WE = "# LOLLYPOP EXTENSION 3-PANE INJECTION END"


def fail(message):
    print("ERROR:", message, file=sys.stderr)
    raise SystemExit(1)


def _lollypop_package_dir():
    spec = importlib.util.find_spec("lollypop")
    if spec is None:
        fail("Lollypop is not installed for this Python interpreter.")
    if spec.origin:
        return os.path.dirname(os.path.abspath(spec.origin))
    # Arch's lollypop is a namespace package (no __init__.py), so origin is
    # None; the package directory is in submodule_search_locations.
    locations = list(spec.submodule_search_locations or ())
    if not locations:
        fail("Could not locate Lollypop's package directory.")
    return os.path.abspath(locations[0])


def _lollypop_version():
    try:
        return importlib.metadata.version("lollypop")
    except importlib.metadata.PackageNotFoundError:
        pass
    # Arch packages do not install Python metadata (.dist-info), so fall back
    # to pacman and strip the package release suffix: 1.4.48-1 -> 1.4.48.
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


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSION_DIR = os.path.join(PROJECT_DIR, "ui")
BACKUP_DIR = os.path.join(PROJECT_DIR, "backups")

VERSION = _lollypop_version()
TARGET = os.path.join(_lollypop_package_dir(), "window.py")
BACKUP = os.path.join(BACKUP_DIR, f"window.py.{VERSION}.pristine")


def is_pristine(path):
    """True if `path` is readable Python without any extension markers."""
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
        ast.parse(source, filename=path)
    except (OSError, SyntaxError):
        return False
    return IS not in source and WS not in source


def ensure_backup():
    """Return a verified pristine backup, creating it only from safe sources."""
    if os.path.isfile(BACKUP):
        if not is_pristine(BACKUP):
            fail(
                "Backup exists but is not pristine (it contains extension "
                f"markers): {BACKUP}\n"
                "Delete it and re-run; the patcher will rebuild it from the "
                "installed Lollypop package files."
            )
        return BACKUP

    os.makedirs(BACKUP_DIR, exist_ok=True)
    candidates = (
        os.path.join(os.path.dirname(TARGET), f"window.py.{VERSION}.pristine"),
        TARGET + ".bak",
        TARGET + ".extension-original.bak",
    )
    for candidate in candidates:
        if os.path.isfile(candidate) and is_pristine(candidate):
            shutil.copy2(candidate, BACKUP)
            print("Created verified original backup:", BACKUP)
            return BACKUP

    with open(TARGET, encoding="utf-8") as f:
        current = f.read()
    if IS in current or WS in current:
        fail(
            "window.py is already patched but no pristine backup could be "
            "found.\nReinstall the lollypop package (or restore "
            f"backups/window.py.{VERSION}-pristine by hand), then re-run."
        )

    shutil.copy2(TARGET, BACKUP)
    print("Created original backup:", BACKUP)
    return BACKUP


def status():
    marker_counts = {IS: 0, WS: 0, IE: 0, WE: 0}
    parse_error = None
    try:
        with open(TARGET, encoding="utf-8") as f:
            source = f.read()
        for marker in marker_counts:
            marker_counts[marker] = source.count(marker)
        try:
            ast.parse(source, filename=TARGET)
        except SyntaxError as exc:
            parse_error = str(exc)
    except OSError as exc:
        source = ""
        parse_error = str(exc)

    boot_patch = marker_counts[IS] == 1 and marker_counts[IE] == 1
    panel_patch = marker_counts[WS] == 1 and marker_counts[WE] == 1
    print("Lollypop version :", VERSION)
    print("Package window.py:", TARGET)
    print("Extension dir    :", EXTENSION_DIR)
    print("Bootstrap patch  :", "applied" if boot_patch else "not applied")
    print("Panel patch      :", "applied" if panel_patch else "not applied")
    print("Syntax           :", parse_error or "OK")
    if os.path.isfile(BACKUP):
        print("Pristine backup  :", "OK" if is_pristine(BACKUP) else "CORRUPT", BACKUP)
    else:
        print("Pristine backup  : missing", BACKUP)
    if parse_error:
        raise SystemExit(1)


def remove_block(source, start, end):
    """Remove one complete patch block; reject incomplete markers."""
    starts = source.count(start)
    ends = source.count(end)

    if starts != ends or starts > 1:
        fail(f"Malformed or duplicate markers: {start} / {end}")

    if starts == 0:
        return source

    pattern = (
        r"^[ \t]*" + re.escape(start) +
        r"[^\n]*\n.*?^[ \t]*" + re.escape(end) +
        r"[^\n]*(?:\n|$)"
    )
    result, count = re.subn(
        pattern, "", source, count=1,
        flags=re.MULTILINE | re.DOTALL
    )
    if count != 1:
        fail(f"Could not safely remove block: {start}")
    return result


def remove_blocks(source):
    source = remove_block(source, WS, WE)
    source = remove_block(source, IS, IE)
    return source


def build_import_block():
    # sys.path.append (not insert(0)): the extension dirs must never shadow
    # Lollypop's own modules.
    return f'''{IS}
import sys as _lollypop_extension_sys
import traceback as _lollypop_extension_traceback

_extension_dir = {EXTENSION_DIR!r}
if _extension_dir not in _lollypop_extension_sys.path:
    _lollypop_extension_sys.path.append(_extension_dir)

try:
    from widget_injector import install_right_panel as _LollypopExtensionInstall
except Exception:
    _LollypopExtensionInstall = None
    print("[Lollypop Extension] Import failed:")
    _lollypop_extension_traceback.print_exc()
{IE}
'''


def build_injection(indent):
    # Inserted right after `self.__vgrid.add(self.__container)`, i.e. before
    # the grid is attached to the window. All the GTK work lives in
    # widget_injector.install_right_panel(), which rolls itself back on error.
    lines = [
        WS,
        "try:",
        "    if _LollypopExtensionInstall is not None:",
        "        _LollypopExtensionInstall(",
        "            self.__vgrid,",
        "            self.__container,",
        '            getattr(self, "_Window__toolbar", None),',
        "        )",
        "except Exception:",
        '    print("[Lollypop Extension] Panel injection failed:")',
        "    _lollypop_extension_traceback.print_exc()",
        WE,
    ]
    return "\n".join(indent + line for line in lines)


def write_atomic(source):
    """Write `source` to TARGET via a verified temp file."""
    try:
        ast.parse(source, filename=TARGET)
    except SyntaxError as exc:
        fail(f"Generated source failed syntax validation: {exc}")

    fd, temp_path = tempfile.mkstemp(
        prefix=".lollypop-extension-",
        suffix=".py",
        dir=os.path.dirname(TARGET),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(source)
            f.flush()
            os.fsync(f.fileno())

        old_stat = os.stat(TARGET)
        shutil.copystat(TARGET, temp_path)
        os.chown(temp_path, old_stat.st_uid, old_stat.st_gid)

        with open(temp_path, encoding="utf-8") as f:
            ast.parse(f.read(), filename=TARGET)

        os.replace(temp_path, TARGET)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def restore():
    if not os.path.isfile(BACKUP):
        fail(
            "No pristine backup found for Lollypop "
            f"{VERSION}: {BACKUP}\n"
            "If the package was upgraded, reinstall the lollypop package "
            "instead (it ships an unpatched window.py)."
        )
    if not is_pristine(BACKUP):
        fail("Refusing to restore: backup contains extension markers: " + BACKUP)
    if not os.path.isfile(TARGET):
        fail("Lollypop window.py not found: " + TARGET)
    shutil.copy2(BACKUP, TARGET)
    print("Restored original window.py from", BACKUP)


def apply():
    if not os.path.isfile(TARGET):
        fail("Lollypop window.py not found: " + TARGET)

    if not os.path.isfile(os.path.join(EXTENSION_DIR, "widget_injector.py")):
        fail("widget_injector.py not found in: " + EXTENSION_DIR)

    with open(TARGET, encoding="utf-8") as f:
        original = f.read()

    # Preserve the first backup; never replace it on later runs. A backup is
    # only accepted if it has no extension markers (see ensure_backup).
    print("Preserving original backup:", ensure_backup())

    source = remove_blocks(original)

    import_match = re.search(
        r"^[ \t]*from gi\.repository import Gtk[^\n]*\n",
        source,
        re.MULTILINE,
    )
    if not import_match:
        fail("Could not find the Gtk import.")

    source = (
        source[:import_match.end()]
        + build_import_block()
        + source[import_match.end():]
    )

    # Patch only inside Window.setup().
    setup_match = re.search(
        r"(?m)^([ \t]*)def setup\(self\):[ \t]*\n",
        source,
    )
    if not setup_match:
        fail("Could not find Window.setup().")

    setup_start = setup_match.end()
    next_method = re.search(r"(?m)^    def \w+\(", source[setup_start:])
    setup_end = (
        setup_start + next_method.start() if next_method else len(source)
    )
    setup_source = source[setup_start:setup_end]

    anchors = list(re.finditer(
        r"(?m)^([ \t]*)self\.__vgrid\.add\(self\.__container\)[ \t]*$",
        setup_source,
    ))
    if len(anchors) != 1:
        fail(
            "Expected exactly one `self.__vgrid.add(self.__container)` in "
            f"setup(), found {len(anchors)}. Lollypop's window.py differs "
            "from the version this patch was written for."
        )
    anchor = anchors[0]

    absolute_end = setup_start + anchor.end()
    source = (
        source[:absolute_end]
        + "\n"
        + build_injection(anchor.group(1))
        + source[absolute_end:]
    )

    write_atomic(source)

    print("Patch applied and syntax-checked.")
    print("Run `lollypop` from a terminal to see [Lollypop Extension] logs.")


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