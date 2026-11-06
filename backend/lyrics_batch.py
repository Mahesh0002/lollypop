#!/usr/bin/env python3
"""Fetch and embed trustworthy lyrics into the FLAC library.

The default is a read-only dry run.  Use --apply to write tags.  Every file
which is changed is copied to a timestamped backup directory first.

Tag layout written:
  LYRICS / UNSYNCEDLYRICS  plain text (safe for Lollypop's native reader)
  SYNCEDLYRICS / LRC       timestamped LRC (used by the extension panel)

A sidecar .lrc is kept in sync by default because the panel gives sidecars
priority over embedded tags.  Pass --no-sidecar to leave sidecars alone.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

from mutagen.flac import FLAC

import api_fetcher as api


DEFAULT_MUSIC_DIR = os.path.expanduser("~/Music")
DEFAULT_BACKUP_ROOT = os.path.expanduser(
    "~/.local/share/lollypop-extension/lyrics-backups"
)
GENERIC_FOLDERS = {"singles", "single", "unsorted", "non-album", "various artists"}

SYNCED_TAGS = ("SYNCEDLYRICS", "LRC")
PLAIN_TAGS = ("LYRICS", "UNSYNCEDLYRICS")


def tag_values(audio, key):
    for tag_key in audio.keys():
        if tag_key.casefold() == key.casefold():
            value = audio[tag_key]
            if isinstance(value, str):
                return [value]
            return [str(item) for item in value]
    return []


def tag_text(audio, key):
    return "\n".join(tag_values(audio, key)).strip()


def first_tag(audio, tags):
    for key in tags:
        value = tag_text(audio, key)
        if value:
            return key, value
    return "", ""


def remove_tag(audio, key):
    for tag_key in list(audio.keys()):
        if tag_key.casefold() == key.casefold():
            del audio[tag_key]


def set_tag(audio, key, value):
    remove_tag(audio, key)
    audio[key] = [value]


def sidecar_path(flac_path):
    return os.path.splitext(flac_path)[0] + ".lrc"


def read_sidecar(flac_path):
    path = sidecar_path(flac_path)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def timestamp_count(text):
    return api.lrc_metrics(text).get("n", 0)


def existing_state(audio, path, duration):
    """Describe both embedded lyrics and a sidecar, without trusting a bad LRC."""
    embedded_synced_key, embedded_synced = first_tag(audio, SYNCED_TAGS)
    embedded_plain_key, embedded_plain = first_tag(audio, PLAIN_TAGS)
    plain_from_lrc = False

    # Older versions of this batch tool stored LRC in LYRICS.  A plain value
    # derived from an invalid LRC must not be treated as an independent,
    # trustworthy plain tag when deciding whether to replace it.
    if embedded_plain and timestamp_count(embedded_plain) >= 2:
        if not embedded_synced:
            embedded_synced_key = embedded_plain_key
            embedded_synced = embedded_plain
        embedded_plain = api._plain_from_lrc(embedded_plain)
        plain_from_lrc = True

    sidecar = read_sidecar(path)
    sidecar_valid = False
    sidecar_metrics = {}
    sidecar_reason = ""
    if sidecar:
        sidecar_valid, sidecar_metrics, sidecar_reason = api.validate_lrc(
            sidecar, duration
        )

    embedded_valid = False
    embedded_metrics = {}
    embedded_reason = ""
    if embedded_synced:
        embedded_valid, embedded_metrics, embedded_reason = api.validate_lrc(
            embedded_synced, duration
        )

    valid_synced_for_layout = (
        embedded_synced if embedded_valid else sidecar if sidecar_valid else ""
    )
    if valid_synced_for_layout:
        lyrics_tag = tag_text(audio, "LYRICS")
        unsynced_tag = tag_text(audio, "UNSYNCEDLYRICS")
        tag_layout_ok = (
            bool(tag_text(audio, "SYNCEDLYRICS"))
            and bool(tag_text(audio, "LRC"))
            and bool(lyrics_tag)
            and bool(unsynced_tag)
            and timestamp_count(lyrics_tag) < 2
            and timestamp_count(unsynced_tag) < 2
            and not api._looks_like_metadata_lyrics(lyrics_tag)
            and not api._looks_like_metadata_lyrics(unsynced_tag)
        )
    else:
        tag_layout_ok = True

    return {
        "embedded_synced": embedded_synced,
        "embedded_synced_key": embedded_synced_key,
        "embedded_synced_valid": embedded_valid,
        "embedded_synced_metrics": embedded_metrics,
        "embedded_synced_reason": embedded_reason,
        "embedded_plain": embedded_plain,
        "embedded_plain_key": embedded_plain_key,
        "embedded_plain_trusted": bool(embedded_plain) and not plain_from_lrc,
        "tag_layout_ok": tag_layout_ok,
        "sidecar": sidecar,
        "sidecar_valid": sidecar_valid,
        "sidecar_metrics": sidecar_metrics,
        "sidecar_reason": sidecar_reason,
    }


def _choose_action_base(existing, result, prefer_online=False):
    """Return (action, reason).  Actions: keep, synced, plain, none."""
    have_synced_tag = bool(existing["embedded_synced"])
    have_valid_synced_tag = bool(existing["embedded_synced_valid"])
    have_sidecar = bool(existing["sidecar"])
    have_valid_sidecar = bool(existing["sidecar_valid"])

    if result is None:
        if have_synced_tag and not have_valid_synced_tag:
            return "keep", "no confident replacement; existing synced tag is invalid"
        if have_sidecar and not have_valid_sidecar:
            return "keep", "no confident replacement; existing sidecar is invalid"
        return "none", "no trustworthy lyrics found"

    if result.synced:
        if existing["embedded_synced"]:
            if (
                have_valid_synced_tag
                and existing["embedded_synced"].strip() == result.text.strip()
            ):
                return "keep", "existing synced lyrics already match"
            old_lines = existing["embedded_synced_metrics"].get("n", 0)
            new_lines = result.metrics.get("n", 0)
            old_last = existing["embedded_synced_metrics"].get("last", 0) or 0
            new_last = result.metrics.get("last", 0) or 0
            if have_valid_synced_tag and not prefer_online:
                # Do not replace a valid tag with a shorter/incomplete one.
                if new_lines < old_lines and new_last <= old_last + 2:
                    return "keep", "existing synced tag is valid and more complete"
            if not have_valid_synced_tag:
                return "synced", "replace invalid/missing synced tag"
            return "synced", "refresh with validated online synced lyrics"
        return "synced", "write synced lyrics"

    # Plain fallback.
    if have_valid_synced_tag or have_valid_sidecar:
        return "keep", "keep valid synced lyrics; online source is plain only"
    if have_synced_tag and not have_valid_synced_tag:
        return "plain", "replace invalid synced tag with validated plain lyrics"
    if have_sidecar and not have_valid_sidecar:
        return "plain", "replace invalid sidecar with validated plain lyrics"
    if existing["embedded_plain_trusted"] and not prefer_online:
        return "keep", "existing plain lyrics are present"
    return "plain", "write validated plain lyrics"


def choose_action(existing, result, prefer_online=False):
    """Choose an action and normalise older non-standard tag layouts."""
    action, reason = _choose_action_base(existing, result, prefer_online)
    has_valid_lrc = bool(
        existing["embedded_synced_valid"] or existing["sidecar_valid"]
    )
    if (
        action in {"keep", "none"}
        and has_valid_lrc
        and not existing["tag_layout_ok"]
    ):
        return "normalize", "normalise existing synced lyrics into standard tags"
    return action, reason


def format_metrics(metrics):
    if not metrics or not metrics.get("n"):
        return "no timestamps"
    return (
        f"n={metrics.get('n')} "
        f"first={metrics.get('first', 0):.2f}s "
        f"last={metrics.get('last', 0):.2f}s "
        f"coverage={metrics.get('coverage', 0):.2f} "
        f"avg_gap={metrics.get('avg_gap', 0):.2f}s"
    )


def backup_path_for(flac_path, music_dir, backup_dir):
    relative = os.path.relpath(flac_path, music_dir)
    return os.path.join(backup_dir, relative)


def copy_to_backup(path, flac_path, music_dir, backup_dir):
    if not os.path.isfile(path):
        return ""
    destination = backup_path_for(flac_path, music_dir, backup_dir)
    if not path.casefold().endswith(".flac"):
        destination = os.path.splitext(destination)[0] + ".lrc"
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copy2(path, destination)
    return destination


def write_sidecar(flac_path, text):
    path = sidecar_path(flac_path)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text.strip() + "\n")
    return path


def apply_synced(audio, flac_path, result, write_lrc=True):
    plain = result.plain_text or api._plain_from_lrc(result.text)
    set_tag(audio, "LYRICS", plain)
    set_tag(audio, "UNSYNCEDLYRICS", plain)
    set_tag(audio, "SYNCEDLYRICS", result.text.strip())
    set_tag(audio, "LRC", result.text.strip())
    audio.save()
    if write_lrc:
        write_sidecar(flac_path, result.text)


def apply_existing_synced(audio, flac_path, lrc_text, write_lrc=True):
    plain = api._plain_from_lrc(lrc_text)
    set_tag(audio, "LYRICS", plain)
    set_tag(audio, "UNSYNCEDLYRICS", plain)
    set_tag(audio, "SYNCEDLYRICS", lrc_text.strip())
    set_tag(audio, "LRC", lrc_text.strip())
    audio.save()
    if write_lrc:
        write_sidecar(flac_path, lrc_text)


def apply_plain(audio, flac_path, result, write_lrc=True):
    plain = result.text.strip()
    set_tag(audio, "LYRICS", plain)
    set_tag(audio, "UNSYNCEDLYRICS", plain)
    remove_tag(audio, "SYNCEDLYRICS")
    remove_tag(audio, "LRC")
    audio.save()
    # If a sidecar was the stale synced copy, remove it.  Do not overwrite a
    # valid sidecar belonging to another source.
    sidecar = sidecar_path(flac_path)
    if write_lrc and os.path.isfile(sidecar):
        text = read_sidecar(flac_path)
        valid, _metrics, _reason = api.validate_lrc(
            text, audio.info.length if audio.info else None
        )
        if not valid:
            os.remove(sidecar)


def scan_files(music_dir):
    files = []
    for folder, _directories, names in os.walk(music_dir):
        for name in names:
            if name.lower().endswith(".flac"):
                files.append(os.path.join(folder, name))
    files.sort()
    return files


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes (default is a read-only dry run)",
    )
    parser.add_argument(
        "--music-dir",
        default=DEFAULT_MUSIC_DIR,
        help=f"FLAC library root (default: {DEFAULT_MUSIC_DIR})",
    )
    parser.add_argument(
        "--backup-dir",
        default="",
        help="backup root (default: ~/.local/share/lollypop-extension/lyrics-backups/<timestamp>)",
    )
    parser.add_argument(
        "--only",
        default="",
        help="only process paths containing this case-insensitive text",
    )
    parser.add_argument("--limit", type=int, default=0, help="process at most N files")
    parser.add_argument(
        "--prefer-online",
        action="store_true",
        help="replace valid embedded lyrics when the online match is validated",
    )
    parser.add_argument(
        "--no-sidecar",
        action="store_true",
        help="do not create/update/remove .lrc sidecars",
    )
    parser.add_argument("--verbose", action="store_true", help="show network retries")
    parser.add_argument("--json", default="", help="write a JSON report to this path")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    music_dir = os.path.abspath(os.path.expanduser(args.music_dir))
    if not os.path.isdir(music_dir):
        print(f"Music directory not found: {music_dir}")
        return 2

    files = scan_files(music_dir)
    if args.only:
        needle = args.only.casefold()
        files = [path for path in files if needle in path.casefold()]
    if args.limit and args.limit > 0:
        files = files[: args.limit]

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = os.path.abspath(
        os.path.expanduser(args.backup_dir)
        or os.path.join(DEFAULT_BACKUP_ROOT, timestamp)
    )

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"Lyrics batch — {mode}")
    print(f"Library: {music_dir}")
    print(f"Files:   {len(files)}")
    if args.apply:
        print(f"Backups: {backup_root}")
    print()

    stats = {
        "synced": 0,
        "plain": 0,
        "normalize": 0,
        "keep": 0,
        "none": 0,
        "failed": 0,
        "unchanged": 0,
    }
    report = []

    for index, path in enumerate(files, 1):
        relative = os.path.relpath(path, music_dir)
        print(f"[{index}/{len(files)}] {relative}")
        entry = {"file": relative}

        try:
            audio = FLAC(path)
            artist = tag_text(audio, "ARTIST")
            albumartist = tag_text(audio, "ALBUMARTIST")
            title = tag_text(audio, "TITLE")
            album = tag_text(audio, "ALBUM")
            duration = float(audio.info.length) if audio.info else None
            folder_hint = ""
            parts = relative.split(os.sep)
            if len(parts) > 1 and parts[0].casefold() not in GENERIC_FOLDERS:
                folder_hint = parts[0]

            if not artist or not title:
                print("  SKIP: missing ARTIST or TITLE")
                entry["action"] = "skip"
                entry["reason"] = "missing ARTIST or TITLE"
                stats["failed"] += 1
                report.append(entry)
                continue

            existing = existing_state(audio, path, duration)
            print(
                "  existing: "
                f"tag={'yes' if existing['embedded_synced'] else 'no'} "
                f"tag_valid={existing['embedded_synced_valid']} "
                f"plain={'yes' if existing['embedded_plain'] else 'no'} "
                f"sidecar={'yes' if existing['sidecar'] else 'no'} "
                f"sidecar_valid={existing['sidecar_valid']}"
            )
            if existing["embedded_synced"]:
                print(
                    "           tag metrics: "
                    + format_metrics(existing["embedded_synced_metrics"])
                )
                if existing["embedded_synced_reason"]:
                    print(f"           tag issue: {existing['embedded_synced_reason']}")
            if existing["sidecar"]:
                print(
                    "           sidecar metrics: "
                    + format_metrics(existing["sidecar_metrics"])
                )
                if existing["sidecar_reason"]:
                    print(f"           sidecar issue: {existing['sidecar_reason']}")

            result = api.fetch_lyrics(
                artist,
                title,
                album_name=album or None,
                duration=duration,
                artist_hint=folder_hint or None,
                artist_aliases=[albumartist] if albumartist else None,
                allow_plain=True,
                verify_plain=True,
                verbose=args.verbose,
            )

            if result:
                kind = "synced" if result.synced else "plain"
                print(
                    f"  online: {kind} from {result.source} "
                    f"score={result.score:.1f} id={result.lrclib_id or '-'}"
                )
                print(
                    f"          {result.artist_name} - {result.track_name} "
                    f"[{result.album_name or 'no album'}] "
                    f"candidate_duration={result.duration or '?'}s"
                )
                print("          " + format_metrics(result.metrics))
            else:
                print("  online: no trustworthy match")

            action, reason = choose_action(
                existing, result, prefer_online=args.prefer_online
            )
            entry.update(
                {
                    "action": action,
                    "reason": reason,
                    "existing": {
                        "synced": bool(existing["embedded_synced"]),
                        "synced_valid": existing["embedded_synced_valid"],
                        "plain": bool(existing["embedded_plain"]),
                        "sidecar": bool(existing["sidecar"]),
                        "sidecar_valid": existing["sidecar_valid"],
                    },
                }
            )
            if result:
                entry["fetched"] = {
                    "source": result.source,
                    "synced": result.synced,
                    "score": round(result.score, 2),
                    "id": result.lrclib_id,
                    "artist": result.artist_name,
                    "track": result.track_name,
                    "album": result.album_name,
                    "duration": result.duration,
                    "match_note": result.note,
                }

            if action == "keep":
                print(f"  ACTION: KEEP — {reason}")
                stats["keep"] += 1
                report.append(entry)
                continue
            if action == "none":
                print(f"  ACTION: NONE — {reason}")
                stats["none"] += 1
                report.append(entry)
                continue

            backup = ""
            sidecar_backup = ""
            if args.apply:
                backup = copy_to_backup(path, path, music_dir, backup_root)
                if not args.no_sidecar and os.path.isfile(sidecar_path(path)):
                    sidecar_backup = copy_to_backup(
                        sidecar_path(path), path, music_dir, backup_root
                    )

                if action == "synced":
                    apply_synced(
                        audio, path, result, write_lrc=not args.no_sidecar
                    )
                elif action == "plain":
                    apply_plain(
                        audio, path, result, write_lrc=not args.no_sidecar
                    )
                elif action == "normalize":
                    lrc_text = (
                        existing["embedded_synced"]
                        if existing["embedded_synced_valid"]
                        else existing["sidecar"]
                    )
                    apply_existing_synced(
                        audio, path, lrc_text, write_lrc=not args.no_sidecar
                    )
                else:
                    raise RuntimeError(f"unknown action: {action}")
                entry["backup"] = backup
                if sidecar_backup:
                    entry["sidecar_backup"] = sidecar_backup
                print(f"  ACTION: WROTE {action.upper()} — {reason}")
            else:
                print(f"  ACTION: WOULD WRITE {action.upper()} — {reason}")

            stats[action] += 1
            report.append(entry)

        except Exception as exc:
            print(f"  ERROR: {exc}")
            entry["action"] = "error"
            entry["error"] = str(exc)
            stats["failed"] += 1
            report.append(entry)

    print("\n========== LYRICS REPORT ==========")
    labels = {
        "synced": "Synced lyrics",
        "plain": "Plain lyrics",
        "normalize": "Normalized tags",
        "keep": "Kept existing",
        "none": "No match",
        "failed": "Failed",
    }
    for key, label in labels.items():
        print(f"{label:20}: {stats[key]}")
    print(f"{'Total files':20}: {len(files)}")
    if args.apply:
        print(f"Backups              : {backup_root}")
    else:
        print("No files were changed (dry run).")

    if args.json:
        output_path = os.path.abspath(os.path.expanduser(args.json))
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "generated": timestamp,
                    "mode": mode,
                    "music_dir": music_dir,
                    "backup_dir": backup_root if args.apply else None,
                    "stats": stats,
                    "files": report,
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
        print(f"JSON report written: {output_path}")

    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
