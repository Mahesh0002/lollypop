import difflib
import html
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import quote

import musicbrainzngs
import requests

# Set MUSIC_API_CONTACT to a real email or project URL before regular use.
APP_NAME = os.environ.get("MUSIC_API_NAME", "LollypopMusicOrganizer")
APP_VERSION = os.environ.get("MUSIC_API_VERSION", "0.3")
API_CONTACT = os.environ.get("MUSIC_API_CONTACT", "https://github.com/")
ITUNES_COUNTRY = os.environ.get("ITUNES_COUNTRY", "IN").upper()

musicbrainzngs.set_useragent(APP_NAME, APP_VERSION, API_CONTACT)
musicbrainzngs.set_rate_limit(1.0, 1)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": f"{APP_NAME}/{APP_VERSION} ({API_CONTACT})",
    "Accept": "*/*",
})

ITUNES_MIN_INTERVAL = 3.2
_last_itunes_call = 0.0

# ---------------------------------------------------------------- text & unknown helpers

UNKNOWN_NAMES = {"unknown", "unknown artist", "unknown album", "unknown title", "<unknown>", ""}

NOISE_WORDS = (
    r"official|audio|video|music video|lyrics?|lyric video|visuali[sz]er|"
    r"hd|hq|4k|remaster(?:ed)?(?: \d{4})?|full album|explicit"
)

def is_unknown(value):
    """Check if a tag or string represents an unknown metadata value."""
    v = (value or "").strip().lower()
    return v in UNKNOWN_NAMES or v.startswith("unknown")

def strip_track_number(text):
    """'01 - ', '01. ', '01_', '07 Song' (leading zero only, so '7 Years' and '1979' survive)."""
    return re.sub(r"^\s*(?:\d{1,3}\s*[-_.)]+\s*|0\d\s+)", "", text)

def clean_text(text):
    """Strip YouTube junk: (Official Audio), [4K], (feat. X), '- Topic', leading track numbers."""
    if not text:
        return ""
    text = re.sub(r"\[[^\]]*\]", "", text)  # anything in [square brackets]
    text = re.sub(rf"(?i)\(\s*(?:{NOISE_WORDS})[^)]*\)", "", text)  # (Official Audio) etc.
    text = re.sub(r"(?i)\s*[\(\[]\s*(?:ft|feat)\b.*$", "", text)  # (feat. X) -> gone
    text = re.sub(r"(?i)\s+(?:ft|feat)\.\s.*$", "", text)  # "Song ft. X"
    text = re.sub(r"(?i)\s*-\s*topic$", "", text)  # YouTube auto-generated artist channels
    text = strip_track_number(text)
    return re.sub(r"\s+", " ", text).strip()

def _norm(s):
    """Unicode-aware comparison key. Keeps non-Latin scripts (Telugu, Hindi, Japanese...)."""
    return re.sub(r"[\W_]+", "", (s or "").casefold())

def _same_name(a, b):
    na, nb = _norm(clean_text(a)), _norm(clean_text(b))
    if not na or not nb:
        return False
    return na == nb

def _title_match(a, b):
    na, nb = _norm(clean_text(a)), _norm(clean_text(b))
    if not na or not nb:
        return False
    return na == nb

def _lucene_quote(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')

# ---------------------------------------------------------------- http helpers

def _get_json(url, params):
    try:
        response = SESSION.get(url, params=params, timeout=(5, 15))
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except (requests.RequestException, ValueError) as exc:
        host = url.split("/")[2] if "://" in url else url
        print(f"   🔴 HTTP/JSON error from {host}: {exc}")
        return {}

def _itunes_request(endpoint, params):
    """Rate-limited request to Apple's public Search/Lookup API."""
    global _last_itunes_call
    wait = ITUNES_MIN_INTERVAL - (time.monotonic() - _last_itunes_call)
    if wait > 0:
        time.sleep(wait)
    _last_itunes_call = time.monotonic()
    request_params = dict(params)
    request_params.setdefault("country", ITUNES_COUNTRY)
    return _get_json(f"https://itunes.apple.com/{endpoint}", request_params)

def _itunes_get(params):
    return _itunes_request("search", params)

def itunes_lookup(item_id, country=None, entity="song"):
    """Return Apple Music/iTunes results for one numeric track or album ID.

    A track ID returns the track.  A collection (album) ID with
    ``entity="song"`` returns the collection row followed by every track.
    This is the keyless public Lookup API used by Apple Music share links.
    """
    item_id = str(item_id or "").strip()
    if not item_id.isdigit():
        return []
    params = {"id": item_id, "entity": entity, "limit": 200}
    if country:
        params["country"] = country.upper()
    data = _itunes_request("lookup", params)
    results = data.get("results", [])
    return results if isinstance(results, list) else []

def _artwork_url(url, size=1200):
    """Build a larger Apple artwork URL when the returned URL supports size substitution."""
    if not url:
        return ""
    return re.sub(
        r"/\d+x\d+(?:bb)?(?:-\d+)?\.(?:jpg|jpeg|png|webp)$",
        f"/{size}x{size}bb.jpg",
        url,
        flags=re.IGNORECASE,
    )

def _download_bytes(url):
    try:
        r = SESSION.get(url, timeout=(5, 20))
        content_type = r.headers.get("Content-Type", "").lower()
        if r.status_code == 200 and content_type.startswith("image/") and len(r.content) > 1000:
            return r.content
    except requests.RequestException as e:
        print(f"   🔴 Image download error: {e}")
    return None

def _download_itunes_art(art100_url):
    """Download larger artwork where available, falling back to smaller variants."""
    if not art100_url:
        return None
    candidates = []
    for size in (1200, 1000, 600, 300):
        candidate = _artwork_url(art100_url, size)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    if art100_url not in candidates:
        candidates.append(art100_url)
    for url in candidates:
        data = _download_bytes(url)
        if data:
            return data
    return None

def fetch_itunes_artwork(art100_url):
    """Download exact Apple artwork for a known Lookup result."""
    return _download_itunes_art(art100_url)

def _write_atomic(path, data):
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

# ---------------------------------------------------------------- artist image (Deezer)

def fetch_artist_image(artist_name, artist_dir):
    """Save artist.jpg into the artist folder. Exact name match only -> no wrong-artist photos."""
    c_artist = clean_text(artist_name)
    if is_unknown(c_artist) or c_artist == "Unsorted":
        return None

    path = os.path.join(artist_dir, "artist.jpg")
    if os.path.exists(path):
        return path

    print(f"   👤 Fetching artist image for '{c_artist}'...")
    data = _get_json("https://api.deezer.com/search/artist", {"q": c_artist, "limit": 5})
    for item in data.get("data", []):
        if _norm(item.get("name")) != _norm(c_artist):
            continue
        url = item.get("picture_xl") or item.get("picture_big") or item.get("picture_medium")
        if not url or "/artist//" in url:
            continue
        img = _download_bytes(url)
        if img:
            _write_atomic(path, img)
            print(f"   ✅ Saved artist image -> {path}")
            return path
    print("   ⚠️  No artist image found (exact-name match required).")
    return None

# ---------------------------------------------------------------- iTunes lookups

def _explicit_flag(result):
    value = (result.get("trackExplicitness") or result.get("collectionExplicitness") or "").lower()
    if value == "explicit":
        return True
    if value in {"notexplicit", "clean"}:
        return False
    return None

def _track_result(r, artist, title):
    album = (r.get("collectionName") or "").strip()
    # Apple appends " - Single" to standalone releases.  Keep the actual
    # release title (for example "Blur" or "Around the World") instead of
    # collapsing every single into a generic "Singles" bucket.
    if album.lower().endswith(" - single"):
        album = album[:-len(" - Single")].rstrip()
    return {
        "real_artist": r.get("artistName") or artist,
        "real_album": album,
        "real_title": r.get("trackName") or title,
        "track_number": r.get("trackNumber"),
        "art100": r.get("artworkUrl100", ""),
        "mb_album_id": None,
        "mb_artist_id": None,
        "source": "iTunes",
    }

@lru_cache(maxsize=512)
def itunes_search_song(artist, title):
    """Find a strict artist + title match on iTunes."""
    artist, title = clean_text(artist), clean_text(title)
    if is_unknown(artist) or is_unknown(title):
        return None
    data = _itunes_get({
        "term": f"{artist} {title}",
        "entity": "song",
        "media": "music",
        "limit": 25,
    })
    for result in data.get("results", []):
        if not _same_name(artist, result.get("artistName")):
            continue
        if not _title_match(title, result.get("trackName")):
            continue
        return _track_result(result, artist, title)
    return None

@lru_cache(maxsize=512)
def itunes_search_album(artist, album):
    """Return artwork URL for a strict artist + album match."""
    artist, album = clean_text(artist), clean_text(album)
    if is_unknown(artist) or is_unknown(album):
        return None
    data = _itunes_get({
        "term": f"{artist} {album}",
        "entity": "album",
        "media": "music",
        "limit": 25,
    })
    for result in data.get("results", []):
        if not _same_name(artist, result.get("artistName")):
            continue
        if not _title_match(album, result.get("collectionName")):
            continue
        return {"artwork_url": result.get("artworkUrl100", "")}
    return None

@lru_cache(maxsize=512)
def itunes_search_album_art(artist, album):
    result = itunes_search_album(artist, album)
    return result.get("artwork_url", "") if result else ""

def fetch_album_cover(artist_name, album_name, album_dir):
    """Save cover.jpg into the album folder."""
    c_artist, c_album = clean_text(artist_name), clean_text(album_name)
    if is_unknown(c_artist) or is_unknown(c_album):
        return None

    path = os.path.join(album_dir, "cover.jpg")
    if os.path.exists(path):
        return path

    print(f"   🖼️  Fetching album cover for '{c_artist} - {c_album}'...")
    data = _download_itunes_art(itunes_search_album_art(c_artist, c_album))
    if data:
        _write_atomic(path, data)
        print(f"   ✅ Saved album cover -> {path}")
        return path
    print("   ⚠️  No verified album cover found.")
    return None

def fetch_song_art(artist_name, title):
    """Per-track art (bytes) for singles."""
    hit = itunes_search_song(clean_text(artist_name), clean_text(title))
    return _download_itunes_art(hit["art100"]) if hit else None

# ---------------------------------------------------------------- MusicBrainz

@lru_cache(maxsize=512)
def resolve_track_metadata(artist_name, track_title):
    """MusicBrainz recording search. Accepts score >= 90, prefers plain studio albums."""
    c_artist, c_title = clean_text(artist_name), clean_text(track_title)
    if not c_title:
        return None

    query = f'recording:"{_lucene_quote(c_title)}"'
    if not is_unknown(c_artist):
        query += f' AND artist:"{_lucene_quote(c_artist)}"'

    print(f"   🔍 MusicBrainz: '{c_artist}' - '{c_title}'")
    try:
        result = musicbrainzngs.search_recordings(query=query, limit=10)
    except Exception as e:
        print(f"   🔴 MusicBrainz error: {e}")
        return None

    best = None
    for rec in result.get("recording-list", []):
        if int(rec.get("ext:score", 0)) < 90:
            continue
        for rel in rec.get("release-list", []):
            rg = rel.get("release-group", {})
            if rg.get("secondary-type-list"):
                continue
            rtype = rg.get("type") or rg.get("primary-type")
            rank = {"Album": 0, "EP": 1, "Single": 2}.get(rtype, 3)
            key = (rank, rel.get("date") or "9999")
            if best is None or key < best[0]:
                best = (key, rec, rel, rtype)

    if not best:
        print("   ⚠️  MusicBrainz: no confident match.")
        return None

    _, rec, rel, rtype = best
    credit = next((c for c in rec.get("artist-credit", []) if isinstance(c, dict) and "artist" in c), {})
    artist = credit.get("artist", {})

    track_number = None
    for medium in rel.get("medium-list", []):
        for track in medium.get("track-list", []):
            recording = track.get("recording", {})
            if recording.get("id") == rec.get("id"):
                track_number = track.get("position") or track.get("number")
                break
        if track_number:
            break

    # Use the release title for singles and EPs too.  "Singles" is not a
    # release and must never be written as ALBUM.
    return {
        "real_artist": artist.get("name") or c_artist,
        "real_album": rel.get("title") or "",
        "real_title": rec.get("title", c_title),
        "track_number": track_number,
        "mb_album_id": rel["id"],
        "mb_artist_id": artist.get("id"),
        "source": "MusicBrainz",
    }

def resolve_untagged(artist_name, track_title):
    """MusicBrainz first, iTunes as backup."""
    hit = resolve_track_metadata(artist_name, track_title)
    if hit:
        return hit
    print("   🔍 iTunes song lookup...")
    return itunes_search_song(clean_text(artist_name), clean_text(track_title))

@lru_cache(maxsize=512)
def fetch_musicbrainz_ids(artist_name, album_name):
    """MBIDs for an already-tagged album. Score >= 90 or nothing."""
    c_artist, c_album = clean_text(artist_name), clean_text(album_name)
    if is_unknown(c_artist) or is_unknown(c_album):
        return None
    query = f'release:"{_lucene_quote(c_album)}" AND artist:"{_lucene_quote(c_artist)}"'
    try:
        result = musicbrainzngs.search_releases(query=query, limit=3)
    except Exception as e:
        print(f"   🔴 MusicBrainz ID lookup error: {e}")
        return None
    for rel in result.get("release-list", []):
        if int(rel.get("ext:score", 0)) >= 90:
            credit = next((c for c in rel.get("artist-credit", []) if isinstance(c, dict) and "artist" in c), {})
            return {"mb_album_id": rel["id"], "mb_artist_id": credit.get("artist", {}).get("id")}
    return None

# ---------------------------------------------------------------- lyrics
#
# LRCLIB is the primary source: its search endpoint gives us several
# recordings for the same title, which lets us reject a candidate whose
# duration, album, or timestamps do not describe *this* file.  The old code
# used /api/get with only artist/title; that is why a ten-minute live version
# could be written over a three-minute studio track.
#
# Fallback order:
#   1. LRCLIB synced LRC (validated against duration and line spacing)
#   2. LRCLIB plain text
#   3. lyrics.ovh plain text (independent source; no timestamps)
#
# This module is intentionally dependency-free beyond requests.  The batch
# tagger and the GTK panel both use the same result object, so they make the
# same match/no-match decision.

LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
LYRICS_OVH_URL = "https://api.lyrics.ovh/v1"

_LRC_TIME = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_LRC_META = re.compile(r"^\s*\[([A-Za-z][A-Za-z0-9_-]*):([^\]]*)\]\s*$")
_LRC_WORD_TIME = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")
_METADATA_LYRIC_LINE = re.compile(
    r"^\s*(?:artist|album|title|track|lyrics?|by)\s*[:：]", re.IGNORECASE
)

_LRCLIB_LOCK = threading.Lock()
_LRCLIB_LAST_CALL = 0.0
_LRCLIB_MIN_INTERVAL = 0.85

UNKNOWN_ARTISTS = {
    "various artists", "various", "unknown", "unknown artist", "unknown artists",
}


@dataclass
class LyricsResult:
    """A fetched lyric plus the evidence used to decide it belongs to a file."""

    text: str
    source: str
    synced: bool
    score: float
    artist_name: str = ""
    track_name: str = ""
    album_name: str = ""
    duration: float | None = None
    lrclib_id: int | None = None
    plain_text: str = ""
    metrics: dict = field(default_factory=dict)
    note: str = ""


def _norm_lyric(value):
    """Unicode-aware lyric/title comparison key (keeps non-Latin scripts)."""
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = (
        value.replace("’", "'").replace("‘", "'")
        .replace("“", '"').replace("”", '"')
    )
    return re.sub(r"[\W_]+", "", value)


def _clean_artist_name(value):
    value = str(value or "").strip()
    value = re.sub(r"(?i)\s+official\b", "", value)
    value = re.sub(r"(?i)\s*-\s*topic$", "", value)
    value = value.strip().strip("\"'“”‘’")
    value = re.sub(r"\s+", " ", value).strip()
    return "" if is_unknown(value) else value


def _artist_keys(value):
    value = re.sub(r"(?i)\b(?:feat|ft|featuring)\.?\b", ",", value or "")
    parts = re.split(r"[,&/;+]+|\band\b|\bx\b", value)
    return {_norm_lyric(part) for part in parts if _norm_lyric(part)}


def _artist_match_score(query, candidate):
    if not query or not candidate:
        return 0.0
    q_full, c_full = _norm_lyric(query), _norm_lyric(candidate)
    q_keys, c_keys = _artist_keys(query), _artist_keys(candidate)

    if q_full == c_full or q_keys == c_keys:
        return 1.0
    if q_keys and c_keys and q_keys <= c_keys:
        return 0.96
    if q_keys and c_keys and c_keys <= q_keys:
        return 0.90
    if q_keys and c_keys and q_keys & c_keys:
        return 0.78
    if q_full.removeprefix("the") == c_full.removeprefix("the"):
        return 0.95
    if q_full and c_full and (q_full in c_full or c_full in q_full):
        return 0.60
    return 0.0


def _strip_leading_artist_prefix(title, artists):
    """Remove 'Daft Punk - ' style prefixes without guessing at ordinary dashes."""
    text = str(title or "").strip()
    artist_keys = set()
    artist_fulls = []
    for artist in artists or ():
        artist = _clean_artist_name(artist)
        if not artist:
            continue
        artist_keys.update(_artist_keys(artist))
        artist_fulls.append(_norm_lyric(artist))

    for separator in (" - ", " – ", " — ", " | ", " / "):
        if separator not in text:
            continue
        left, right = text.split(separator, 1)
        left_norm = _norm_lyric(left)
        left_keys = _artist_keys(left)
        if not left_norm or not right.strip():
            continue
        if (
            (left_keys and left_keys & artist_keys)
            or any(full and (full in left_norm or left_norm in full) for full in artist_fulls)
        ):
            return right.strip()
    return text


def _strip_version_noise(text):
    """Drop download-site/YouTube/version decoration, keeping 'Live' and 'Remix'."""
    text = str(text or "")
    text = re.sub(r"\[[^\]]*\]", " ", text)

    # Parenthetical noise.  Do not remove a parenthetical merely because it
    # contains the word "live" - a live version has different timestamps.
    noise = (
        r"official(?:\s+(?:audio|video|music video|lyric video))?|"
        r"audio|video|music video|musicvideo|lyric video|lyrics?|"
        r"visuali[sz]er|hd|hq|4k|remaster(?:ed)?(?:\s+\d{4})?|"
        r"drumless edition|radio edit|single version|album version|"
        r"short version|clean|explicit"
    )
    text = re.sub(rf"(?i)\(\s*(?:{noise})[^)]*\)", " ", text)
    text = re.sub(rf"(?i)\(\s*{noise}\s*$", " ", text)
    text = re.sub(
        r"(?i)\b(?:official\s+(?:audio|video|music video|lyric video)|"
        r"music\s*video|musicvideo|lyric\s*video|visuali[sz]er|"
        r"drumless edition|radio edit|single version|album version)\b",
        " ",
        text,
    )

    # Feature credits are metadata, not part of the title for matching.
    text = re.sub(r"(?i)\s*[\(\[]\s*(?:ft|feat)\.?[^\)\]]*[\)\]]", " ", text)
    text = re.sub(r"(?i)\s+(?:ft|feat)\.?\s+.*$", " ", text)

    # Trailing fragments left by malformed YouTube titles.
    text = re.sub(r"\s*[\(\[]\s*$", " ", text)
    text = re.sub(r"\s*[\)\]]\s*$", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—|/()[]")
    return text


def _title_variants(title, artists):
    raw = strip_track_number(str(title or "")).strip()
    values = []

    def add(value):
        value = re.sub(r"\s+", " ", str(value or "")).strip(" -–—|/()[]")
        if value and all(_norm_lyric(value) != _norm_lyric(old) for old in values):
            values.append(value)

    # Frederic-style titles put the song title in Japanese quotes after the
    # artist: フレデリック「オドループ」... / frederic“ONLYWONDER”
    quoted_after_artist = re.search(
        r"(?:フレデリック|frederic)\s*[「『\"“]([^」』\"”]+)",
        raw,
        re.IGNORECASE,
    )
    if quoted_after_artist:
        add(quoted_after_artist.group(1))

    for match in re.finditer(r"[「『\"“]([^」』\"”]+)[」』\"”]", raw):
        add(match.group(1))

    without_artist = _strip_leading_artist_prefix(raw, artists)
    add(_strip_version_noise(without_artist))
    add(_strip_version_noise(raw))

    # Split noisy "Artist | Live ..." / download-site titles only after the
    # cleaner variants have been tried.
    for separator in (" | ", " / "):
        if separator in raw:
            add(_strip_version_noise(raw.split(separator, 1)[1]))
            add(_strip_version_noise(raw.split(separator, 1)[0]))

    add(strip_track_number(clean_text(raw)))
    add(raw)
    return values[:6]


def _artist_variants(artist_name, artist_aliases=None, artist_hint=None):
    values = []

    def add(value):
        value = _clean_artist_name(value)
        if not value or value.casefold() in UNKNOWN_ARTISTS:
            return
        if all(_norm_lyric(value) != _norm_lyric(old) for old in values):
            values.append(value)

    add(artist_name)
    # A combined credit often works better on LRCLIB, but the primary artist
    # is a useful second attempt for sites which reject "A, B" paths.
    if artist_name and re.search(r"[,&/]", artist_name):
        add(re.split(r"[,&/]", artist_name, 1)[0])
    for alias in artist_aliases or ():
        add(alias)
    add(artist_hint)
    return values[:5]


def _duration_score(file_duration, candidate_duration):
    if not file_duration or not candidate_duration:
        return 0.60
    try:
        difference = abs(float(candidate_duration) - float(file_duration))
    except (TypeError, ValueError):
        return 0.60
    if difference <= 2:
        return 1.0
    if difference <= 5:
        return 0.92
    if difference <= 10:
        return 0.80
    if difference <= 15:
        return 0.65
    if difference <= 25:
        return 0.35
    return 0.0


def _timestamp_seconds(match):
    fraction = match.group(3) or ""
    if len(fraction) == 1:
        fraction_value = int(fraction) / 10
    elif len(fraction) == 2:
        fraction_value = int(fraction) / 100
    elif len(fraction) >= 3:
        fraction_value = int(fraction[:3]) / 1000
    else:
        fraction_value = 0.0
    return int(match.group(1)) * 60 + int(match.group(2)) + fraction_value


def lrc_metrics(text, duration=None):
    """Return timestamps/coverage metrics for an LRC document.

    ``last`` is when the final lyric appears, not the file length.  Many
    legitimate songs have a long instrumental outro, so coverage is exposed
    for scoring rather than treated as a hard requirement.
    """
    offset = 0
    times = []
    content_lines = 0
    for raw_line in str(text or "").splitlines():
        meta = _LRC_META.match(raw_line)
        if meta:
            if meta.group(1).lower() == "offset":
                try:
                    offset = int(meta.group(2).strip())
                except (TypeError, ValueError):
                    pass
            continue
        matches = list(_LRC_TIME.finditer(raw_line))
        if not matches:
            continue
        content = raw_line[matches[-1].end():]
        if _norm_lyric(content):
            content_lines += 1
        for match in matches:
            times.append(_timestamp_seconds(match))

    times = sorted({max(0.0, value + offset / 1000.0) for value in times})
    if not times:
        return {
            "n": 0, "content_lines": content_lines, "first": None, "last": None,
            "coverage": 0.0, "first_ratio": 0.0, "last_ratio": 0.0,
            "avg_gap": 0.0, "max_gap": 0.0,
        }

    first, last = times[0], times[-1]
    gaps = [right - left for left, right in zip(times, times[1:]) if right > left]
    duration = float(duration) if duration else 0.0
    return {
        "n": len(times),
        "content_lines": content_lines,
        "first": first,
        "last": last,
        "coverage": (last / duration) if duration else 0.0,
        "first_ratio": (first / duration) if duration else 0.0,
        "last_ratio": (last / duration) if duration else 0.0,
        "avg_gap": ((last - first) / (len(times) - 1)) if len(times) > 1 else 0.0,
        "max_gap": max(gaps) if gaps else 0.0,
    }


def validate_lrc(text, duration=None, min_lines=4):
    """Reject obvious LRC corruption.  Returns (ok, metrics, reason)."""
    metrics = lrc_metrics(text, duration)
    if metrics["n"] < min_lines:
        return False, metrics, "too few timestamps"
    if metrics["last"] <= metrics["first"]:
        return False, metrics, "timestamps do not advance"

    if duration:
        duration = float(duration)
        if metrics["last"] > duration + 30:
            return False, metrics, "last timestamp is past the end"
        if metrics["first"] > duration * 0.55 and metrics["n"] >= 8:
            return False, metrics, "lyrics start too late"
        # A spoken intro or instrumental track can legitimately have only a
        # few timestamped lines.  Reject a tiny excerpt, not a song with a
        # long instrumental outro (Contact, Giorgio by Moroder, etc.).
        if metrics["last"] < max(20.0, duration * 0.10):
            return False, metrics, "lyrics end too early"
        if metrics["avg_gap"] < 0.65 and metrics["n"] >= 8:
            return False, metrics, "timestamps are compressed"
    return True, metrics, ""


def _lrc_quality(metrics, valid):
    if not valid or not metrics.get("n"):
        return 0.0
    count = 1.0 if metrics["n"] >= 12 else 0.8 if metrics["n"] >= 6 else 0.5
    coverage = metrics.get("coverage", 0)
    coverage_score = 1.0 if coverage >= 0.85 else 0.8 if coverage >= 0.65 else 0.5 if coverage >= 0.5 else 0.2
    first_score = 1.0 if metrics.get("first_ratio", 1) <= 0.30 else 0.7 if metrics.get("first_ratio", 1) <= 0.45 else 0.3
    gap_score = 1.0 if metrics.get("avg_gap", 0) >= 1.2 else 0.7 if metrics.get("avg_gap", 0) >= 0.8 else 0.3
    return (count + coverage_score + first_score + gap_score) / 4.0


def _plain_from_lrc(text):
    lines = []
    for raw_line in str(text or "").splitlines():
        if _LRC_META.match(raw_line):
            continue
        line = _LRC_TIME.sub("", raw_line)
        line = _LRC_WORD_TIME.sub("", line).strip()
        if not line or all(char in "♪♫ \t" for char in line):
            continue
        lines.append(line)
    return clean_plain_lyrics("\n".join(lines))


def _looks_like_metadata_lyrics(text):
    """True for fake \"lyrics\" such as Artist:/Album:/Title: only."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return True
    return all(
        _METADATA_LYRIC_LINE.match(line) or _LRC_META.match(line)
        for line in lines
    )


def clean_plain_lyrics(text):
    """Normalise plain lyrics and remove non-sung section/credit labels."""
    text = html.unescape(str(text or "")).replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"[\u2000-\u200b\u202f\u205f\u3000]", " ", raw_line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        # "[Justin Bieber]", "[Chorus]" etc. are labels, not lyric lines.
        if re.fullmatch(r"\[[^\]]+\]", line):
            continue
        if all(char in "♪♫" for char in line):
            continue
        lines.append(line)
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = result.strip()
    return "" if _looks_like_metadata_lyrics(result) else result


def _lrc_lines(text):
    lines = []
    for raw_line in str(text or "").splitlines():
        if _LRC_META.match(raw_line):
            continue
        matches = list(_LRC_TIME.finditer(raw_line))
        if not matches:
            continue
        content = raw_line[matches[-1].end():]
        content = _LRC_WORD_TIME.sub("", content).strip()
        if content and not all(char in "♪♫ \t" for char in content):
            lines.append(content)
    return lines


def _line_plain_ratio(line, plain_norm):
    line_norm = _norm_lyric(line)
    if not line_norm or not plain_norm:
        return 0.0
    if line_norm in plain_norm:
        return 1.0
    match = difflib.SequenceMatcher(
        None, line_norm, plain_norm, autojunk=False
    ).find_longest_match(0, len(line_norm), 0, len(plain_norm))
    return match.size / len(line_norm)


def _content_metrics(lrc_text, plain_text):
    """How much of the LRC appears in an independent plain-lyrics text."""
    plain_norm = _norm_lyric(plain_text)
    if not plain_norm:
        return {"precision": 0.0, "first_match": 0.0, "lines": 0}
    lines = [line for line in _lrc_lines(lrc_text) if len(_norm_lyric(line)) >= 4]
    if not lines:
        return {"precision": 0.0, "first_match": 0.0, "lines": 0}
    matched = 0
    for line in lines:
        if _line_plain_ratio(line, plain_norm) >= 0.65:
            matched += 1
    first_match = 1.0 if _line_plain_ratio(lines[0], plain_norm) >= 0.65 else 0.0
    return {
        "precision": matched / len(lines),
        "first_match": first_match,
        "lines": len(lines),
    }


def _album_match_score(file_album, candidate_album):
    if not file_album or is_unknown(file_album):
        return 0.0
    if file_album.casefold() in {"singles", "single", "unsorted", "non-album"}:
        return 0.0
    file_key, candidate_key = _norm_lyric(file_album), _norm_lyric(candidate_album)
    if not file_key or not candidate_key:
        return 0.0
    if file_key == candidate_key:
        return 1.0
    if file_key in candidate_key or candidate_key in file_key:
        return 0.5
    return 0.0


def _candidate_is_wrong_version(query_title, candidate_title, candidate_album=""):
    query_text = f"{query_title or ''}".casefold()
    candidate_text = f"{candidate_title or ''} {candidate_album or ''}".casefold()
    for word in (
        "live", "remix", "cover", "karaoke", "instrumental", "sped up",
        "slowed", "nightcore", "8d", "acoustic", "demo",
    ):
        if word in candidate_text and word not in query_text:
            return True
    return False


def _candidate_title_score(titles, candidate_title, candidate_artist, artists):
    best = 0.0
    for title in titles:
        q = _norm_lyric(
            _strip_version_noise(_strip_leading_artist_prefix(title, artists))
        )
        c = _norm_lyric(
            _strip_version_noise(
                _strip_leading_artist_prefix(
                    candidate_title, [candidate_artist, *artists]
                )
            )
        )
        if not q or not c:
            continue
        if q == c:
            score = 1.0
        elif q in c or c in q:
            score = 0.85
        else:
            score = 0.0
        best = max(best, score)
    return best


def _candidate_plain(candidate):
    plain = clean_plain_lyrics(candidate.get("plainLyrics"))
    if len(plain) >= 60 and not _looks_like_metadata_lyrics(plain):
        return plain
    return ""


def _score_candidate(candidate, artists, titles, file_album, duration, query_title):
    artist_name = candidate.get("artistName") or ""
    track_name = candidate.get("trackName") or ""
    album_name = candidate.get("albumName") or ""

    artist_score = max(
        (_artist_match_score(artist, artist_name) for artist in artists),
        default=0.0,
    )
    title_score = _candidate_title_score(
        titles, track_name, artist_name, artists
    )
    if artist_score < 0.55 or title_score < 0.55:
        return None
    if _candidate_is_wrong_version(query_title, track_name, album_name):
        return None

    candidate_duration = candidate.get("duration")
    if duration and candidate_duration:
        try:
            difference = abs(float(candidate_duration) - float(duration))
        except (TypeError, ValueError):
            difference = None
    else:
        difference = None
    if duration and difference is not None and difference > 30:
        return None

    lrc_text = str(candidate.get("syncedLyrics") or "")
    if lrc_text and _looks_like_metadata_lyrics(_plain_from_lrc(lrc_text)):
        lrc_text = ""
    valid, metrics, reason = validate_lrc(lrc_text, duration)
    plain = _candidate_plain(candidate)
    if not plain and valid:
        plain = _plain_from_lrc(lrc_text)

    if candidate.get("instrumental") and not plain:
        return None

    quality = _lrc_quality(metrics, valid)
    content = _content_metrics(lrc_text, plain) if plain and valid else {
        "precision": 0.0, "first_match": 0.0, "lines": 0,
    }
    album_score = _album_match_score(file_album, album_name)

    score = (
        artist_score * 34
        + title_score * 32
        + _duration_score(duration, candidate_duration) * 18
        + quality * 8
        + album_score * 2
    )
    if valid:
        score += 2
        score += content["precision"] * 4
        score += content["first_match"] * 2
    elif plain:
        score += 6

    return {
        "candidate": candidate,
        "artist_score": artist_score,
        "title_score": title_score,
        "duration_difference": difference,
        "lrc": lrc_text,
        "plain": plain,
        "valid": valid,
        "metrics": metrics,
        "reason": reason,
        "quality": quality,
        "content": content,
        "score": score,
    }


def _lrclib_throttle():
    global _LRCLIB_LAST_CALL
    with _LRCLIB_LOCK:
        wait = _LRCLIB_MIN_INTERVAL - (time.monotonic() - _LRCLIB_LAST_CALL)
        if wait > 0:
            time.sleep(wait)
        _LRCLIB_LAST_CALL = time.monotonic()


def _search_lrclib(artist, title, verbose=False):
    if not artist or not title:
        return []

    def request(params, label):
        for attempt in range(2):
            _lrclib_throttle()
            try:
                response = SESSION.get(
                    LRCLIB_SEARCH_URL, params=params, timeout=(5, 20)
                )
            except requests.RequestException as exc:
                if verbose:
                    print(f"   🎤 LRCLIB {label} error: {exc}")
                return []
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    return []
                return payload if isinstance(payload, list) else []
            if response.status_code in (429, 500, 502, 503, 504):
                if verbose:
                    print(
                        f"   🎤 LRCLIB {label} busy "
                        f"({response.status_code}); retrying..."
                    )
                time.sleep(1.5 + attempt * 1.5)
                continue
            return []
        return []

    # Exact artist/title is more precise, while free text sometimes exposes a
    # different master (Get Lucky's original is buried under remixes in q;
    # Intentions' complete LRC is buried in the exact result page).  Merge
    # both and let duration/timestamp scoring choose.
    results = []
    seen = set()
    for params, label in (
        ({"artist_name": artist, "track_name": title}, "artist/title search"),
        ({"q": f"{artist} {title}".strip()}, "keyword search"),
    ):
        for candidate in request(params, label):
            candidate_id = candidate.get("id")
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            results.append(candidate)
    return results


def _candidate_has_lyrics(candidate, duration):
    """True when this LRCLIB row can actually provide usable lyrics."""
    lrc_text = str(candidate.get("syncedLyrics") or "")
    if lrc_text:
        plain_from_lrc = _plain_from_lrc(lrc_text)
        if plain_from_lrc and not _looks_like_metadata_lyrics(plain_from_lrc):
            valid, _metrics, _reason = validate_lrc(lrc_text, duration)
            if valid:
                return True
    return bool(_candidate_plain(candidate))


def _has_promising_candidate(candidates, artists, titles, duration):
    """Stop variant searching only after finding a matching *lyrics* row.

    A metadata match alone is not enough: LRCLIB returns rows with both
    syncedLyrics and plainLyrics empty (instrumentals, bad submissions).  If
    we stop on those, alternate title variants that do have lyrics are never
    queried.
    """
    for candidate in candidates:
        artist_score = max(
            (_artist_match_score(artist, candidate.get("artistName") or "") for artist in artists),
            default=0.0,
        )
        title_score = _candidate_title_score(
            titles,
            candidate.get("trackName") or "",
            candidate.get("artistName") or "",
            artists,
        )
        if artist_score < 0.55 or title_score < 0.55:
            continue
        if duration and candidate.get("duration"):
            try:
                if abs(float(candidate["duration"]) - float(duration)) > 30:
                    continue
            except (TypeError, ValueError):
                pass
        if _candidate_has_lyrics(candidate, duration):
            return True
    return False


def _collect_candidates(artists, titles, duration, verbose=False):
    pairs = []
    for artist in artists:
        for title in titles:
            if (artist, title) not in pairs:
                pairs.append((artist, title))

    candidates = []
    seen = set()
    for artist, title in pairs[:8]:
        found = _search_lrclib(artist, title, verbose=verbose)
        for candidate in found:
            candidate_id = candidate.get("id")
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            candidates.append(candidate)
        if found and _has_promising_candidate(candidates, artists, titles, duration):
            break
    return candidates


def _fetch_lyrics_ovh(artists, titles, verbose=False):
    """Independent plain-lyrics fallback.  This endpoint has no timestamps."""
    pairs = []
    for artist in artists[:3]:
        for title in titles[:3]:
            if (artist, title) not in pairs:
                pairs.append((artist, title))

    for artist, title in pairs[:6]:
        url = f"{LYRICS_OVH_URL}/{quote(artist, safe='')}/{quote(title, safe='')}"
        try:
            response = SESSION.get(url, timeout=(5, 9))
        except requests.RequestException as exc:
            if verbose:
                print(f"   🎤 lyrics.ovh error: {exc}")
            continue
        if response.status_code != 200:
            continue
        try:
            payload = response.json()
        except ValueError:
            continue
        plain = clean_plain_lyrics(payload.get("lyrics"))
        if len(plain) >= 60 and not _looks_like_metadata_lyrics(plain):
            if verbose:
                print(f"   🎤 lyrics.ovh plain lyrics: {artist} - {title}")
            return {
                "text": plain,
                "artist": artist,
                "title": title,
            }
    return None


def _score_with_authority(scored, authority):
    if not authority or not authority.get("text"):
        return scored
    for item in scored:
        content = _content_metrics(item["lrc"], authority["text"])
        item["authority_content"] = content
        # First-line agreement is valuable: it catches a bad intro or a
        # completely different recording even when the rest of the words
        # happen to overlap.
        item["score"] += content["precision"] * 5.0 + content["first_match"] * 5.0
        if (
            item["valid"]
            and content["precision"] < 0.45
            and content["first_match"] == 0.0
        ):
            item["valid"] = False
            item["reason"] = "timestamps do not match the plain-lyrics source"
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored


def fetch_lyrics(
    artist_name,
    track_title,
    album_name=None,
    duration=None,
    artist_hint=None,
    artist_aliases=None,
    allow_plain=True,
    verify_plain=True,
    verbose=False,
):
    """Fetch the best lyrics for a track and explain the match.

    ``duration`` is in seconds (FLAC ``info.length`` / Lollipop's duration
    divided by 1000).  Set ``verify_plain=False`` for an interactive caller
    that wants to avoid the extra lyrics.ovh request when LRCLIB already has
    a clear synced result.
    """
    artists = _artist_variants(artist_name, artist_aliases, artist_hint)
    titles = _title_variants(track_title, artists)
    if not artists or not titles:
        return None

    candidates = _collect_candidates(artists, titles, duration, verbose=verbose)
    scored = []
    for candidate in candidates:
        item = _score_candidate(
            candidate, artists, titles, album_name, duration, track_title
        )
        if item is not None:
            scored.append(item)
    scored.sort(key=lambda item: item["score"], reverse=True)

    authority = None
    best_synced = next((item for item in scored if item["valid"]), None)
    needs_authority = (
        verify_plain
        or best_synced is None
        or len(scored) > 1
        and scored[0]["score"] - scored[1]["score"] < 5.0
    )
    if needs_authority:
        authority = _fetch_lyrics_ovh(artists, titles, verbose=verbose)
        scored = _score_with_authority(scored, authority)

    synced = [item for item in scored if item["valid"] and item["lrc"]]
    synced.sort(key=lambda item: item["score"], reverse=True)
    if synced:
        best = synced[0]
        candidate = best["candidate"]
        lrc_text = best["lrc"]
        plain_text = best["plain"] or _plain_from_lrc(lrc_text)
        return LyricsResult(
            text=lrc_text,
            plain_text=plain_text,
            source="lrclib",
            synced=True,
            score=best["score"],
            artist_name=candidate.get("artistName") or "",
            track_name=candidate.get("trackName") or "",
            album_name=candidate.get("albumName") or "",
            duration=candidate.get("duration"),
            lrclib_id=candidate.get("id"),
            metrics=best["metrics"],
            note=best["reason"],
        )

    if not allow_plain:
        return None

    plain_candidates = [
        item for item in scored
        if item["plain"] and item["score"] >= 55
    ]
    plain_candidates.sort(key=lambda item: item["score"], reverse=True)
    if plain_candidates:
        best = plain_candidates[0]
        candidate = best["candidate"]
        return LyricsResult(
            text=best["plain"],
            plain_text=best["plain"],
            source="lrclib",
            synced=False,
            score=best["score"],
            artist_name=candidate.get("artistName") or "",
            track_name=candidate.get("trackName") or "",
            album_name=candidate.get("albumName") or "",
            duration=candidate.get("duration"),
            lrclib_id=candidate.get("id"),
            metrics=best["metrics"],
            note=best["reason"] or "plain lyrics only",
        )

    if authority and authority.get("text"):
        return LyricsResult(
            text=authority["text"],
            plain_text=authority["text"],
            source="lyrics.ovh",
            synced=False,
            score=60.0,
            artist_name=authority.get("artist", ""),
            track_name=authority.get("title", ""),
            note="plain lyrics fallback",
        )

    return None


def fetch_synced_lyrics(
    artist_name,
    track_title,
    album_name=None,
    duration=None,
    artist_hint=None,
    artist_aliases=None,
    verify_plain=False,
):
    """Backward-compatible text return used by the panel and old organizer.

    Despite the historical name, a plain-lyrics fallback is returned when no
    trustworthy synced version exists.  Call :func:`fetch_lyrics` when the
    source/quality metadata matters.
    """
    result = fetch_lyrics(
        artist_name,
        track_title,
        album_name=album_name,
        duration=duration,
        artist_hint=artist_hint,
        artist_aliases=artist_aliases,
        allow_plain=True,
        verify_plain=verify_plain,
        verbose=False,
    )
    if result:
        kind = "synced" if result.synced else "plain"
        print(
            f"   🎤 Lyrics ({kind}, {result.source}, score {result.score:.1f}): "
            f"{result.artist_name} - {result.track_name}"
        )
        return result.text
    print("   ⚠️ No trustworthy lyrics found.")
    return None