import io
import json
import os
import re
import shutil
import sys
from datetime import datetime
from mutagen.flac import FLAC, Picture
from PIL import Image
import api_fetcher as api

ORGANIZED_MUSIC_DIR = os.path.expanduser(
    os.environ.get("MUSIC_LIBRARY_DIR", "~/Music")
)
BACKUP_ROOT = os.path.expanduser(
    "~/.local/share/lollypop-extension/library-backups"
)
GENERIC_ALBUMS = {"singles", "unsorted", "unknown album", "unknown"}

stats = {
    "tags": 0,
    "MusicBrainz": 0,
    "iTunes": 0,
    "Unknown": 0,
    "Unsorted": 0,
    "skipped": 0,
    "failed": 0,
}


def is_generic_album(value):
    """True for placeholders that must never become a real ALBUM tag."""
    return api.is_unknown(value) or (value or "").strip().lower() in GENERIC_ALBUMS


def is_channel_artist(value):
    """YouTube auto-generated channels are not usable library artists."""
    return bool(re.search(r"\b(?:official|topic|vevo)\b", value or "", re.I))


def library_artist(audio):
    """Prefer the track artist, falling back to a sane album artist.

    ALBUMARTIST is only a fallback: in the existing library it is frequently
    'Various Artists' or 'FREDERIC official', while ARTIST is usually correct.
    """
    artist = first_tag(audio, "artist")
    album_artist = first_tag(audio, "albumartist")
    if api.is_unknown(artist) or is_channel_artist(artist):
        if not api.is_unknown(album_artist) and not is_channel_artist(album_artist):
            artist = album_artist
    return artist or album_artist


def backup_file(src_path, backup_root, library_root):
    """Copy a source file and its sidecar into the timestamped backup tree."""
    if not backup_root:
        return None
    try:
        relative = os.path.relpath(src_path, library_root)
    except ValueError:
        relative = os.path.basename(src_path)
    if relative == ".." or relative.startswith(".." + os.sep):
        relative = os.path.basename(src_path)
    destination = os.path.join(backup_root, relative)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if not os.path.exists(destination):
        shutil.copy2(src_path, destination)
    sidecar = os.path.splitext(src_path)[0] + ".lrc"
    if os.path.exists(sidecar):
        sidecar_destination = os.path.splitext(destination)[0] + ".lrc"
        os.makedirs(os.path.dirname(sidecar_destination), exist_ok=True)
        if not os.path.exists(sidecar_destination):
            shutil.copy2(sidecar, sidecar_destination)
    return destination

def sanitize_filename(name):
    clean = re.sub(r'[\\/*?:"<>|]', "", name or "").strip().strip(".")
    return clean or "Unknown"

def is_within(path, parent):
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False

def parse_info_from_filename(filename):
    base = api.strip_track_number(os.path.splitext(filename)[0])
    parts = base.split(" - ")
    if len(parts) >= 2:
        return parts[0].strip(), " - ".join(parts[1:]).strip()
    return "Unknown Artist", base.strip()

# Artwork sidecar files the organizer/UI look for next to a track.
AUDIO_EXTENSIONS = (".flac",)
COVER_STEMS = ("cover", "folder", "front", "album")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def find_local_artwork(audio_path):
    """Sidecar artwork (cover.jpg / folder.* / front.* / album.*) next to a track."""
    if not audio_path:
        return None
    folder = os.path.dirname(audio_path)
    try:
        names = {n.lower(): n for n in os.listdir(folder)}
    except OSError:
        return None
    for stem in COVER_STEMS:
        for ext in IMAGE_EXTENSIONS:
            candidate = names.get((stem + ext).lower())
            if candidate:
                path = os.path.join(folder, candidate)
                if os.path.isfile(path):
                    return path
    return None


def read_song(audio_path):
    """Read a single FLAC into a plain song dict (no tagging, no artwork download)."""
    try:
        audio = FLAC(audio_path)
    except Exception:
        return None
    artist = library_artist(audio)
    album = first_tag(audio, "album")
    title = first_tag(audio, "title")
    track_no = first_tag(audio, "tracknumber")

    if api.is_unknown(artist) or api.is_unknown(title):
        fn_artist, fn_title = parse_info_from_filename(os.path.basename(audio_path))
        if api.is_unknown(artist):
            artist = fn_artist
        if api.is_unknown(title):
            title = fn_title

    artist = api.clean_text(artist)
    album = api.clean_text(album)
    title = api.clean_text(title) or api.clean_text(os.path.splitext(os.path.basename(audio_path))[0])
    if api.is_unknown(album):
        album = ""

    try:
        duration = float(getattr(audio.info, "length", 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0

    return {
        "path": audio_path,
        "artist": artist or "Unknown Artist",
        "album": album or "Unknown Album",
        "title": title or "Unknown Title",
        "track_number": (track_no or "").strip(),
        "duration_seconds": duration,
        "art_path": find_local_artwork(audio_path),
    }


def _track_number_key(value):
    """Numeric-aware sort key for free-form track tags ("3", "03", "3/12").

    Missing/non-numeric values sort after numbered tracks instead of being
    compared as strings ("10" < "2"/"999").
    """
    text = str(value or "").strip()
    match = re.match(r"\d+", text)
    if match:
        return (0, int(match.group()), text.casefold())
    return (1, 0, text.casefold())


def list_songs(music_dir=None):
    """Walk the music library and return every song as a plain dict.

    This backs the UI "Songs" section: one entry per FLAC under the library
    folder (~/Music by default), with locally-resolved artist/album/title,
    track number and duration.  Pure read-only scan — no tagging, no artwork
    downloads, no writes.
    """
    root = os.path.realpath(music_dir or ORGANIZED_MUSIC_DIR)
    songs = []
    if not os.path.isdir(root):
        return songs
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for filename in sorted(filenames):
            if not filename.lower().endswith(AUDIO_EXTENSIONS):
                continue
            song = read_song(os.path.join(dirpath, filename))
            if song:
                songs.append(song)
    songs.sort(key=lambda s: (
        (s.get("artist") or "").casefold(),
        (s.get("album") or "").casefold(),
        _track_number_key(s.get("track_number")),
        (s.get("title") or "").casefold(),
    ))
    return songs


def first_tag(audio, key):
    value = audio.get(key)
    return value[0].strip() if value and value[0].strip() else ""

def build_picture(img_bytes):
    with Image.open(io.BytesIO(img_bytes)) as img:
        width, height = img.size
        depth = {"RGB": 24, "RGBA": 32, "L": 8, "P": 8, "CMYK": 32}.get(img.mode, 24)
        mime = Image.MIME.get(img.format, "image/jpeg")
        pic = Picture()
        pic.type = 3  # front cover
        pic.mime = mime
        pic.desc = "Cover"
        pic.data = img_bytes
        pic.width, pic.height, pic.depth = width, height, depth
        return pic

def process_flac_file(src_path, move=False, dry_run=False, backup_root=None):
    filename = os.path.basename(src_path)
    print("\n========================================")
    print(f"🌿 {filename}")

    try:
        src = FLAC(src_path)
    except Exception as e:
        print(f"🔴 Can't read FLAC: {e}")
        stats["failed"] += 1
        return

    # ---- 1. READ WHAT WE HAVE ---------------------------------------------
    artist = library_artist(src)
    album = first_tag(src, "album")
    title = first_tag(src, "title")
    track_no = first_tag(src, "tracknumber")
    artist_tag_missing = api.is_unknown(first_tag(src, "artist"))

    if api.is_unknown(artist) or api.is_unknown(title):
        fn_artist, fn_title = parse_info_from_filename(filename)
        if api.is_unknown(artist):
            artist = fn_artist
        if api.is_unknown(title):
            title = fn_title
        print(f"📄 From filename -> artist: '{artist}' | title: '{title}'")

    artist = api.clean_text(artist)
    album = api.clean_text(album)
    title = api.clean_text(title) or api.clean_text(os.path.splitext(filename)[0])
    if api.is_unknown(album):
        album = ""

    # ---- 2. RESOLVE METADATA ----------------------------------------------
    source, mb_ids, retag_artist = "tags", None, artist_tag_missing

    if album and not is_generic_album(album):
        if not api.is_unknown(artist):
            mb_ids = api.fetch_musicbrainz_ids(artist, album)
    else:
        # Treat "Singles" and the other old placeholders as unresolved.  The
        # previous organizer silently accepted them and kept every download
        # in an artificial Singles bucket forever.
        source = None
        if not api.is_unknown(artist):
            hit = api.resolve_untagged(artist, title)
            if hit:
                if hit.get("real_artist"):
                    artist = hit["real_artist"]
                if hit.get("real_album") and not is_generic_album(hit["real_album"]):
                    album = hit["real_album"]
                if hit.get("real_title"):
                    title = hit["real_title"]
                if hit.get("track_number"):
                    track_no = str(hit["track_number"])
                mb_ids = (
                    {"mb_album_id": hit["mb_album_id"], "mb_artist_id": hit["mb_artist_id"]}
                    if hit.get("mb_album_id")
                    else None
                )
                source, retag_artist = hit.get("source"), True

    if api.is_unknown(artist):
        artist, album, source = "Unsorted", "Unsorted", "Unsorted"
    elif is_generic_album(album):
        album, source = "Unknown Album", "Unknown"

    is_real_album = album not in ("Unknown Album", "Unsorted") and not is_generic_album(album)
    print(f"   🧭 Resolved via {source}: artist='{artist}' | album='{album}' | title='{title}'")

    # ---- 3. TARGET PATHS ---------------------------------------------------
    artist_dir = os.path.join(ORGANIZED_MUSIC_DIR, sanitize_filename(artist))
    album_dir = os.path.join(artist_dir, sanitize_filename(album))

    prefix = ""
    m = re.match(r"\d+", track_no or "")
    if m and int(m.group()) > 0:
        prefix = f"{int(m.group()):02d} - "
    dest = os.path.join(album_dir, f"{prefix}{sanitize_filename(title)}.flac")

    is_inplace = (os.path.abspath(src_path) == os.path.abspath(dest))

    if os.path.exists(dest) and not is_inplace:
        print(f"   ⏭️  Already exists, skipping: {dest}")
        stats["skipped"] += 1
        return

    if dry_run:
        print(f"   📍 Artist node: {artist_dir}")
        print(f"   📍 Album node:  {album_dir}")
        print(f"   🧪 Dry run: would write -> {dest}")
        stats[source] += 1
        return

    os.makedirs(album_dir, exist_ok=True)
    print(f"   📍 Artist node: {artist_dir}")
    print(f"   📍 Album node:  {album_dir}")

    if is_inplace or move:
        backup = backup_file(src_path, backup_root, ORGANIZED_MUSIC_DIR)
        if backup:
            print(f"   🛟 Backup: {backup}")

    # ---- 4. ARTWORK --------------------------------------------------------
    api.fetch_artist_image(artist, artist_dir)

    art_bytes = None
    if is_real_album:
        cover = api.fetch_album_cover(artist, album, album_dir)
        if cover:
            with open(cover, "rb") as f:
                art_bytes = f.read()
    elif album not in ("Unsorted", "Unknown Album"):
        art_bytes = api.fetch_song_art(artist, title)

    # Lyrics are managed by lyrics_batch.py.  Do not overwrite validated
    # LYRICS/SYNCEDLYRICS tags from the organizer.

    # ---- 5. COPY/MODIFY & TAG ----------------------------------------------
    try:
        if not is_inplace:
            shutil.copy2(src_path, dest)

        out = FLAC(dest)
        out["albumartist"] = artist
        if retag_artist:
            out["artist"] = artist
        out["album"] = album
        out["title"] = title
        if track_no and m and int(m.group()) > 0:
            out["tracknumber"] = str(int(m.group()))
        if mb_ids:
            if mb_ids.get("mb_album_id"):
                out["musicbrainz_albumid"] = mb_ids["mb_album_id"]
            if mb_ids.get("mb_artist_id"):
                out["musicbrainz_artistid"] = mb_ids["mb_artist_id"]
        if art_bytes:
            out.clear_pictures()
            out.add_picture(build_picture(art_bytes))
        out.save()

        if not is_inplace:
            out = FLAC(dest)
            if out.info.md5_signature != src.info.md5_signature:
                raise RuntimeError("audio stream changed while copying")

    except Exception as e:
        print(f"   🔴 Failed: {e}")
        if not is_inplace and os.path.exists(dest):
            os.remove(dest)
        stats["failed"] += 1
        return

    if not is_inplace and move:
        os.remove(src_path)
        print(f"   🚚 Moved source: {src_path}")

    stats[source] += 1
    action = "Updated in-place" if is_inplace else ("Moved + tagged" if move else "Copied + tagged")
    print(f"   🍃 {action} -> {dest}")

def main():
    raw_args = sys.argv[1:]
    if "--list-songs" in raw_args:
        songs = list_songs()
        if "--json" in raw_args:
            print(json.dumps(songs, indent=2))
        else:
            print(f"Found {len(songs)} songs in {ORGANIZED_MUSIC_DIR}")
            for i, song in enumerate(songs, 1):
                duration = song.get("duration_seconds") or 0
                dur_text = f"{int(duration // 60)}:{int(duration % 60):02d}" if duration else "0:00"
                print(
                    f"{i:3d}. {song.get('artist') or 'Unknown Artist':25s} - "
                    f"{song.get('title') or 'Unknown Title'} [{song.get('album') or 'Unknown Album'}] "
                    f"({dur_text}) {song.get('path')}"
                )
        return

    paths = [arg for arg in raw_args if not arg.startswith("--")]
    if not paths:
        print("Usage: python main.py [--dry-run] [--move] [--list-songs] [--json] <path_to_music_folder_or_flac>")
        sys.exit(1)

    dry_run = "--dry-run" in raw_args
    move = "--move" in raw_args
    target = os.path.abspath(os.path.expanduser(paths[0]))
    organized = os.path.realpath(ORGANIZED_MUSIC_DIR)

    is_library_update = is_within(target, organized)
    if is_library_update:
        print(f"\n⚠️ Target is inside {organized}.")
        print("   Running in in-place update mode (modifying and moving library files)...")
        move = True
    elif move:
        print("\n⚠️ --move requested for an external source; originals will be removed.")

    backup_root = None
    if not dry_run and (move or is_library_update):
        backup_root = os.path.join(
            BACKUP_ROOT, datetime.now().strftime("%Y%m%d-%H%M%S")
        )
        os.makedirs(backup_root, exist_ok=True)
        print(f"   Backups: {backup_root}")

    if os.path.isdir(target):
        sources = []
        for root, dirs, files in os.walk(target):
            if not is_library_update:
                dirs[:] = [d for d in dirs if os.path.realpath(os.path.join(root, d)) != organized]
            for file in sorted(files):
                if file.lower().endswith(".flac"):
                    sources.append(os.path.join(root, file))
        # Snapshot the list before moving files: newly created album folders
        # must not be walked and processed a second time.
        for source in sources:
            process_flac_file(
                source,
                move=move,
                dry_run=dry_run,
                backup_root=backup_root,
            )
    else:
        process_flac_file(
            target,
            move=move,
            dry_run=dry_run,
            backup_root=backup_root,
        )

    print("\n================ SUMMARY ================")
    for key, count in stats.items():
        print(f"{key:12s} {count}")
    if dry_run:
        print("Dry run: no files were written or moved.")
    elif backup_root:
        print(f"Backups: {backup_root}")

if __name__ == "__main__":
    main()