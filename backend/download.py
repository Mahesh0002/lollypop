#!/usr/bin/env python3
"""Download a track or album and run the FLAC metadata organizer.

Apple Music share links are used as *metadata* sources only.  Apple's audio is
DRM-protected; the public iTunes Lookup API supplies exact artist/album/title,
track number, release date, genre, duration and artwork.  The matching audio is
downloaded from YouTube (or another yt-dlp-supported URL), tagged with the
Apple metadata, and then handed to ``backend/main.py`` for library organization.

Examples:
    python backend/download.py "Daft Punk - Around the World"
    python backend/download.py "https://music.apple.com/us/album/...?...i=..."
    python backend/download.py --album "https://music.apple.com/us/album/..."
    python backend/download.py --dry-run "https://music.apple.com/..."
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from urllib.parse import parse_qs, unquote, urlparse

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import api_fetcher as api
import main as organizer
from mutagen.flac import FLAC

CACHE_ROOT = os.path.expanduser("~/.cache/lollypop-extension/downloads")
APPLE_HOSTS = ("music.apple.com", "itunes.apple.com")
YTDLP = os.environ.get("YTDLP", "yt-dlp")


class DownloadError(RuntimeError):
    pass


# ---------------------------------------------------------------- Apple links

def _is_apple_host(host):
    host = (host or "").lower().rstrip(".")
    return any(host == root or host.endswith("." + root) for root in APPLE_HOSTS)


def _numeric_id(value):
    match = re.fullmatch(r"(?:id)?(\d+)", str(value or "").strip(), re.I)
    return match.group(1) if match else None


def parse_apple_music_url(url):
    """Parse an Apple Music song/album share link.

    Returns ``{"kind": "track"|"album", "id": "...", "country": "US"|None}``.
    ``?i=<trackId>`` wins over the album path; ``/song/<id>`` is also a track.
    """
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if not parsed.scheme and _is_apple_host(parsed.path.split("/")[0]):
        parsed = urlparse("https://" + raw)
    if parsed.scheme not in ("http", "https") or not _is_apple_host(parsed.hostname):
        return None

    parts = [unquote(part) for part in parsed.path.split("/") if part]
    country = None
    if parts and re.fullmatch(r"[A-Za-z]{2}", parts[0]):
        country = parts[0].upper()

    query = parse_qs(parsed.query)
    track_id = _numeric_id((query.get("i") or [""])[0])
    if track_id:
        return {"kind": "track", "id": track_id, "country": country, "url": raw}

    if any(part.lower() == "song" for part in parts):
        ids = [item for item in (_numeric_id(part) for part in parts) if item]
        if ids:
            return {"kind": "track", "id": ids[-1], "country": country, "url": raw}

    ids = [item for item in (_numeric_id(part) for part in parts) if item]
    if ids:
        return {"kind": "album", "id": ids[-1], "country": country, "url": raw}
    return None


def looks_like_apple_music_url(value):
    raw = (value or "").strip()
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    return _is_apple_host(parsed.hostname)


def _strip_single_suffix(name):
    value = (name or "").strip()
    if value.lower().endswith(" - single"):
        value = value[:-len(" - Single")].rstrip()
    return value


def metadata_from_itunes(result):
    artist = api.clean_text((result.get("artistName") or "").strip())
    title = api.clean_text((result.get("trackName") or "").strip())
    album = api.clean_text(_strip_single_suffix(result.get("collectionName")))
    if not album:
        album = title
    return {
        "track_id": result.get("trackId"),
        "collection_id": result.get("collectionId"),
        "artist": artist or "Unknown Artist",
        "album_artist": api.clean_text(
            (result.get("collectionArtistName") or artist or "").strip()
        ) or artist or "Unknown Artist",
        "album": album or "Unknown Album",
        "title": title or "Unknown Title",
        "track_number": result.get("trackNumber"),
        "track_count": result.get("trackCount"),
        "disc_number": result.get("discNumber"),
        "disc_count": result.get("discCount"),
        "release_date": (result.get("releaseDate") or "")[:10],
        "genre": (result.get("primaryGenreName") or "").strip(),
        "isrc": (result.get("isrc") or "").strip(),
        "duration_ms": result.get("trackTimeMillis"),
        "artwork_url": result.get("artworkUrl100") or "",
        "preview_url": result.get("previewUrl") or "",
    }


def _lookup_apple(item_id, country):
    results = api.itunes_lookup(item_id, country, entity="song")
    if not results and country and country.upper() != api.ITUNES_COUNTRY:
        print(f"   ⚠️  No result in the {country} store; retrying {api.ITUNES_COUNTRY}...")
        results = api.itunes_lookup(item_id, api.ITUNES_COUNTRY, entity="song")
    return results


def _is_song(result):
    return bool(result.get("trackName")) and (
        result.get("wrapperType") == "track" or result.get("kind") == "song"
    ) and result.get("kind") != "music-video"


def _track_result(results):
    for result in results:
        if _is_song(result):
            return result
    return None


def _track_sort_key(result):
    def number(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return (
        number(result.get("discNumber") or 1),
        number(result.get("trackNumber")),
        (result.get("trackName") or "").casefold(),
    )


def resolve_apple(link, force_album=False):
    """Return a list of iTunes metadata dicts for a parsed Apple link."""
    results = _lookup_apple(link["id"], link["country"])
    track = _track_result(results)

    if link["kind"] == "track" and not force_album:
        if not track:
            raise DownloadError(
                f"Apple Music track {link['id']} was not found in the "
                f"{link['country'] or api.ITUNES_COUNTRY} iTunes store."
            )
        meta = metadata_from_itunes(track)
        print(
            f"   🍎 Apple Music track: {meta['artist']} - {meta['title']} "
            f"[{meta['album']}]"
        )
        return [meta]

    # A track link with --album means "download the album this track is on".
    if link["kind"] == "track" and force_album:
        if not track:
            raise DownloadError(f"Apple Music track {link['id']} was not found.")
        collection_id = track.get("collectionId")
        if not collection_id:
            print("   ⚠️  Track has no album collection; downloading just the track.")
            return [metadata_from_itunes(track)]
        results = _lookup_apple(str(collection_id), link["country"])
        if not results:
            print("   ⚠️  Album lookup failed; downloading just the track.")
            return [metadata_from_itunes(track)]

    tracks = [result for result in results if _is_song(result)]
    if not tracks:
        raise DownloadError(
            f"Apple Music album {link['id']} returned no tracks. "
            "The link may be region-locked, a playlist, or unavailable."
        )
    tracks.sort(key=_track_sort_key)
    metas = [metadata_from_itunes(result) for result in tracks]
    print(f"   🍎 Apple Music album: {len(metas)} track(s) via iTunes")
    return metas


# ---------------------------------------------------------------- YouTube match

def _norm_text(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", value)


def _has_word(text, word):
    return bool(re.search(rf"\b{re.escape(word)}\b", str(text or ""), re.I))


NOISE_TERMS = (
    "live", "cover", "remix", "reaction", "sped up", "slowed", "nightcore",
    "karaoke", "instrumental", "8d", "bass boosted", "mashup", "medley",
)


def score_candidate(entry, meta):
    title = entry.get("title") or ""
    channel = entry.get("channel") or entry.get("uploader") or ""
    norm_title = _norm_text(meta.get("title"))
    norm_video = _norm_text(title)
    norm_channel = _norm_text(channel)
    norm_artist = _norm_text(meta.get("artist"))
    score = 0.0

    if norm_title and norm_title in norm_video:
        score += 7
    elif norm_title and norm_video and (
        norm_title in norm_video or norm_video in norm_title
    ):
        score += 3

    if norm_artist and (norm_artist in norm_video or norm_artist in norm_channel):
        score += 4
    for part in re.split(r"[,&/]|\bfeat\.?\b|\bft\.?\b", meta.get("artist") or "", flags=re.I):
        norm_part = _norm_text(part)
        if norm_part and (norm_part in norm_video or norm_part in norm_channel):
            score += 1

    duration = entry.get("duration")
    expected = (meta.get("duration_ms") or 0) / 1000.0
    if duration and expected:
        try:
            difference = abs(float(duration) - expected)
        except (TypeError, ValueError):
            difference = None
        if difference is not None:
            if difference <= 3:
                score += 4
            elif difference <= 10:
                score += 3
            elif difference <= 20:
                score += 1
            elif difference <= 45:
                score -= 2
            else:
                score -= 5

    for term in NOISE_TERMS:
        if _has_word(title, term) and not _has_word(meta.get("title"), term):
            score -= 4

    if "topic" in norm_channel:
        score += 2
    if "vevo" in norm_channel:
        score += 1
    if entry.get("is_live") or entry.get("live_status") == "is_live":
        score -= 10
    if duration and expected and float(duration) > 900 and expected < 600:
        score -= 3
    return score


def _last_line(text):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else "unknown error"


def search_youtube(query, limit=5):
    command = [
        YTDLP, "--flat-playlist", "--dump-single-json", "--no-warnings",
        f"ytsearch{limit}:{query}",
    ]
    try:
        process = subprocess.run(
            command, capture_output=True, text=True, timeout=120
        )
    except FileNotFoundError as exc:
        raise DownloadError("yt-dlp is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise DownloadError("YouTube search timed out") from exc
    if process.returncode != 0:
        raise DownloadError(f"YouTube search failed: {_last_line(process.stderr)}")

    output = process.stdout.strip()
    start = output.find("{")
    if start > 0:
        output = output[start:]
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise DownloadError("YouTube search returned invalid JSON") from exc

    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not entries and isinstance(payload, dict) and payload.get("id"):
        entries = [payload]
    return [
        entry for entry in (entries or [])
        if isinstance(entry, dict) and entry.get("id")
    ]


def pick_candidate(entries, meta, verbose=False):
    if not entries:
        raise DownloadError("YouTube search returned no candidates")
    scored = sorted(
        ((score_candidate(entry, meta), entry) for entry in entries),
        key=lambda item: (item[0], item[1].get("duration") or 0),
        reverse=True,
    )
    if verbose:
        print("   🔎 YouTube candidates:")
        for score, entry in scored[:5]:
            duration = entry.get("duration")
            print(
                f"      {score:5.1f}  {entry.get('title', '')[:70]} "
                f"[{duration or '?'}s]"
            )
    best_score, best = scored[0]
    if best_score <= 0:
        raise DownloadError(
            "No convincing YouTube match. Try a direct URL or check the title."
        )

    expected = (meta.get("duration_ms") or 0) / 1000.0
    duration = best.get("duration")
    if expected and duration:
        difference = abs(float(duration) - expected)
        if difference > 60:
            print(
                f"   ⚠️  YouTube duration differs from Apple by "
                f"{int(difference)}s; check the result."
            )
    return best


def candidate_url(entry):
    return (
        entry.get("webpage_url")
        or entry.get("url")
        or f"https://www.youtube.com/watch?v={entry['id']}"
    )


# ---------------------------------------------------------------- download / tag

def download_audio(url, staging, playlist=False):
    os.makedirs(staging, exist_ok=True)
    output_template = os.path.join(
        staging,
        "%(playlist_index)02d - %(id)s.%(ext)s" if playlist else "%(id)s.%(ext)s",
    )
    command = [
        YTDLP, "-x", "--audio-format", "flac", "--audio-quality", "0",
        "--embed-metadata", "--add-metadata",
    ]
    command.append("--yes-playlist" if playlist else "--no-playlist")
    if playlist:
        command.append("--ignore-errors")
    command += ["-o", output_template, url]

    print(f"   ⬇️  yt-dlp: {url}")
    try:
        process = subprocess.run(command)
    except FileNotFoundError as exc:
        raise DownloadError("yt-dlp is not installed or not on PATH") from exc
    if process.returncode != 0:
        raise DownloadError(f"yt-dlp exited with status {process.returncode}")

    files = sorted(glob.glob(os.path.join(staging, "*.flac")))
    if not files:
        raise DownloadError("yt-dlp finished but produced no FLAC file")
    return files


def tag_with_itunes(path, meta):
    artwork = None
    if meta.get("artwork_url"):
        artwork = api.fetch_itunes_artwork(meta["artwork_url"])

    try:
        audio = FLAC(path)
        # yt-dlp embeds YouTube-only fields; remove the noisy ones before
        # writing the authoritative Apple metadata.
        for key in (
            "comment", "description", "synopsis", "purl", "webpage_url",
            "uploader", "channel", "channel_url", "uploader_url", "track",
            "release_date",
        ):
            if key in audio:
                del audio[key]
        if meta.get("artist"):
            audio["artist"] = meta["artist"]
        if meta.get("album_artist"):
            audio["albumartist"] = meta["album_artist"]
        if meta.get("album"):
            audio["album"] = meta["album"]
        if meta.get("title"):
            audio["title"] = meta["title"]
        if meta.get("track_number"):
            audio["tracknumber"] = str(meta["track_number"])
        if meta.get("track_count"):
            audio["tracktotal"] = str(meta["track_count"])
        if meta.get("disc_number"):
            audio["discnumber"] = str(meta["disc_number"])
        if meta.get("disc_count"):
            audio["disctotal"] = str(meta["disc_count"])
        if meta.get("release_date"):
            audio["date"] = meta["release_date"]
        if meta.get("genre"):
            audio["genre"] = meta["genre"]
        if meta.get("isrc"):
            audio["isrc"] = meta["isrc"]
        if artwork:
            audio.clear_pictures()
            audio.add_picture(organizer.build_picture(artwork))
        audio.save()
    except Exception as exc:
        raise DownloadError(f"Could not tag {path}: {exc}") from exc


def expected_destination(meta):
    artist = organizer.sanitize_filename(meta.get("artist") or "Unsorted")
    album = organizer.sanitize_filename(meta.get("album") or "Unknown Album")
    title = organizer.sanitize_filename(meta.get("title") or "Unknown Title")
    prefix = ""
    try:
        track_number = int(meta.get("track_number") or 0)
    except (TypeError, ValueError):
        track_number = 0
    if track_number > 0:
        prefix = f"{track_number:02d} - "
    return os.path.join(
        organizer.ORGANIZED_MUSIC_DIR, artist, album, f"{prefix}{title}.flac"
    )


def format_duration(milliseconds):
    if not milliseconds:
        return "?:??"
    seconds = int(round(float(milliseconds) / 1000.0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def organize_files(files, args):
    results = []
    for path in files:
        if args.no_organize:
            print(f"   📁 Downloaded (not organized): {path}")
            results.append("downloaded")
            continue
        before_failed = organizer.stats["failed"]
        organizer.process_flac_file(path, move=False)
        if organizer.stats["failed"] > before_failed:
            raise DownloadError(f"Organizer failed for {path}")
        results.append("organized")
    return results


def process_itunes_track(meta, args, staging, index=1):
    """Download, tag and organize one iTunes track.  Returns a status string."""
    destination = expected_destination(meta)
    if os.path.exists(destination) and not args.no_organize:
        print(f"   ⏭️  Already in library: {destination}")
        return "skipped"

    query = f"{meta['artist']} {meta['title']} audio"
    entries = search_youtube(query, limit=args.limit)
    candidate = pick_candidate(entries, meta, verbose=args.verbose)
    url = candidate_url(candidate)
    duration = candidate.get("duration")
    channel = candidate.get("channel") or candidate.get("uploader") or ""
    print(
        f"   🎯 Match: {candidate.get('title', '')} "
        f"[{duration or '?'}s, {channel}]"
    )

    if args.dry_run:
        print(f"   🧪 Dry run: would download {url}")
        print(f"   🧪 Dry run: would organize -> {destination}")
        return "dry-run"

    track_staging = os.path.join(
        staging, f"{index:02d}-{meta.get('track_id') or index}"
    )
    files = download_audio(url, track_staging, playlist=False)
    if len(files) != 1:
        raise DownloadError(
            f"Expected one FLAC for {meta['artist']} - {meta['title']}, "
            f"got {len(files)}"
        )
    path = files[0]
    tag_with_itunes(path, meta)
    print(f"   🏷️  Tagged: {meta['artist']} - {meta['title']} [{meta['album']}]")
    organize_files([path], args)
    return "organized" if not args.no_organize else "downloaded"


def process_direct_url(url, args, staging):
    if args.dry_run:
        print(f"   🧪 Dry run: would download {url}")
        return []
    if not args.album and "list=" in url:
        print("   ⚠️  URL contains a playlist; use `music album <url>` to download all.")
    files = download_audio(url, staging, playlist=args.album)
    return organize_files(files, args)


def process_search_query(query, args, staging, title=None):
    """Search YouTube, pick the best candidate, then download/organize it."""
    meta = {"artist": "", "title": title or query, "duration_ms": 0}
    entries = search_youtube(query, limit=args.limit)
    candidate = pick_candidate(entries, meta, verbose=args.verbose)
    url = candidate_url(candidate)
    duration = candidate.get("duration")
    channel = candidate.get("channel") or candidate.get("uploader") or ""
    print(
        f"   🎯 Match: {candidate.get('title', '')} "
        f"[{duration or '?'}s, {channel}]"
    )
    if args.dry_run:
        print(f"   🧪 Dry run: would download {url}")
        return ["dry-run"]
    files = download_audio(url, staging, playlist=False)
    return organize_files(files, args)


def is_url(value):
    return bool(re.match(r"^(?:https?://|www\.|youtu\.be/)", value or "", re.I))


# ---------------------------------------------------------------- CLI

def build_parser():
    parser = argparse.ArgumentParser(
        prog="music",
        description=(
            "Download audio, tag it with Apple/iTunes metadata when available, "
            "and organize it into ~/Music with backend/main.py."
        ),
    )
    parser.add_argument(
        "source",
        nargs="+",
        help="Apple Music URL, yt-dlp-supported URL, or YouTube search text",
    )
    parser.add_argument(
        "--album", action="store_true",
        help="download an album/playlist (also expands an Apple track link)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="resolve metadata and choose a YouTube match without downloading",
    )
    parser.add_argument(
        "--no-organize", action="store_true",
        help="download and tag only; keep the staging folder",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="keep the staging folder after organizing",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="show scored YouTube candidates",
    )
    parser.add_argument(
        "--limit", type=int, default=5,
        help="number of YouTube search results to score (default: 5)",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    text = " ".join(args.source).strip()
    if not text:
        parser.error("a URL or search text is required")
    if args.limit < 1:
        parser.error("--limit must be at least 1")

    apple = parse_apple_music_url(text)
    staging = None
    failures = 0
    try:
        if apple:
            metas = resolve_apple(apple, force_album=args.album)
            if not args.dry_run:
                os.makedirs(CACHE_ROOT, exist_ok=True)
                staging = tempfile.mkdtemp(prefix="download-", dir=CACHE_ROOT)
            for index, meta in enumerate(metas, 1):
                print(
                    f"\n[{index}/{len(metas)}] {meta['artist']} - {meta['title']}"
                    f"  [{meta['album']}] ({format_duration(meta.get('duration_ms'))})"
                )
                try:
                    process_itunes_track(meta, args, staging, index=index)
                except DownloadError as exc:
                    failures += 1
                    print(f"   🔴 {exc}")
        elif looks_like_apple_music_url(text):
            raise DownloadError(
                "Unsupported Apple Music URL. Paste a song or album link; "
                "artist and playlist links are not supported."
            )
        elif is_url(text):
            if args.dry_run:
                print(f"🧪 Dry run: would download {text}")
                return 0
            os.makedirs(CACHE_ROOT, exist_ok=True)
            staging = tempfile.mkdtemp(prefix="download-", dir=CACHE_ROOT)
            process_direct_url(text, args, staging)
        else:
            search_query = text
            if args.album:
                print(
                    "   ℹ️  Album text search is best-effort; for exact albums "
                    "paste an Apple Music album URL."
                )
                search_query = f"{text} full album playlist"
            if args.dry_run:
                # A plain text query has no Apple duration; score on title only.
                entries = search_youtube(search_query, limit=args.limit)
                meta = {"artist": "", "title": text, "duration_ms": 0}
                candidate = pick_candidate(entries, meta, verbose=args.verbose)
                print(f"🧪 Dry run: would download {candidate_url(candidate)}")
                return 0
            os.makedirs(CACHE_ROOT, exist_ok=True)
            staging = tempfile.mkdtemp(prefix="download-", dir=CACHE_ROOT)
            if args.album:
                process_direct_url(f"ytsearch1:{search_query}", args, staging)
            else:
                process_search_query(text, args, staging, title=text)

        if staging:
            keep = args.keep or args.no_organize or failures > 0
            if keep:
                print(f"\n📁 Staging kept: {staging}")
            else:
                shutil.rmtree(staging, ignore_errors=True)
        return 1 if failures else 0
    except DownloadError as exc:
        print(f"🔴 {exc}", file=sys.stderr)
        if staging and not args.keep:
            print(f"📁 Partial download kept for inspection: {staging}")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        if staging:
            print(f"📁 Partial download kept: {staging}")
        return 130


if __name__ == "__main__":
    sys.exit(main())
