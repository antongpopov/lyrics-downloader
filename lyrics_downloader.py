#!/usr/bin/env python3
"""Download lyrics (and optionally album covers) for a local music library, saved next to the songs.

For every audio file that has no lyrics yet, reads its tags with ffprobe, looks the song up on
LRCLIB (https://lrclib.net) and writes the result beside it with the same name:

    Artist/Album/03 Song.flac  ->  Artist/Album/03 Song.lrc   (synced, time-stamped lyrics)
                               ->  Artist/Album/03 Song.txt   (plain lyrics, when no synced exist)

With --covers it also saves each album folder's front cover as cover.jpg, from the Cover Art
Archive (MusicBrainz), optionally falling back to Apple's iTunes catalog (--itunes).

Safe to stop and re-run: songs that already have a .lrc/.txt (or lyrics embedded in their tags)
are skipped, and songs LRCLIB had nothing for are remembered in a small cache file and only
asked again after --retry-days.

Requires Python 3.8+ and either the `tinytag` package (`pip install tinytag`) or ffprobe (part
of FFmpeg) to read the songs' tags. The desktop app (lyrics_downloader_gui.py) uses this module.
https://github.com/antongpopov/lyrics-downloader — MIT licence.
"""
import argparse
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

__version__ = "1.2.1"

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".aac", ".alac",
                    ".aiff", ".aif", ".wma", ".ape", ".wv"}
API = "https://lrclib.net/api"
# LRCLIB asks clients to identify themselves with a User-Agent.
USER_AGENT = f"lyrics-downloader/{__version__} (+https://github.com/antongpopov/lyrics-downloader)"
CACHE_NAME = ".lyrics-downloader-cache.json"
EMBEDDED_LYRICS_TAGS = ("lyrics", "unsyncedlyrics", "uslt", "©lyr")
KINDS = ("synced", "plain", "instrumental", "not found", "skipped", "error")
COVER_KINDS = ("found", "not found", "skipped", "error")
COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg", "folder.png",
               "front.jpg", "front.jpeg", "front.png", "album.jpg", "album.png", "albumart.jpg")
MUSICBRAINZ = "https://musicbrainz.org/ws/2"
COVER_ART_ARCHIVE = "https://coverartarchive.org"
ITUNES_SEARCH = "https://itunes.apple.com/search"

try:
    from tinytag import TinyTag
except ImportError:  # the command line works with ffprobe alone
    TinyTag = None


def use_system_certificates():
    """Verify HTTPS the way the operating system does (via `truststore`, Python 3.10+), so
    certificates Windows or macOS trust — e.g. a company proxy's — are trusted here too.
    Without it Python uses its own list and rejects such networks."""
    try:
        import truststore
        truststore.inject_into_ssl()
        return True
    except Exception:  # not installed, or an older Python: fall back to Python's own list
        return False


class CertificateProblem(Exception):
    """HTTPS certificate rejected — retrying won't help, so the run stops."""


# --- reading the song -------------------------------------------------------------------------

def probe(path):
    """(tags, duration_seconds), tag names lower-cased. tinytag if installed, else ffprobe."""
    if TinyTag is not None:
        try:
            t = TinyTag.get(path)
        except Exception:  # unreadable or unsupported file: treat as untagged
            return {}, 0.0
        tags = {k: v for k, v in (("title", t.title), ("artist", t.artist),
                                  ("album_artist", t.albumartist), ("album", t.album)) if v}
        for k, v in (t.other or {}).items():
            tags[k.lower()] = v[0] if isinstance(v, list) and v else v
        return tags, float(t.duration or 0)
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=30).stdout
        fmt = json.loads(out or "{}").get("format", {})
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        fmt = {}
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    return tags, duration


def has_embedded_lyrics(tags):
    return any(k == t or k.startswith(t + "-") for k in tags for t in EMBEDDED_LYRICS_TAGS)


def song_info(path, tags):
    """(artist, title, album) from the tags, falling back to an Artist/Album/NN Title.ext layout."""
    parts = os.path.normpath(path).split(os.sep)
    stem = os.path.splitext(parts[-1])[0]
    title = str(tags.get("title") or "") or re.sub(r"^\d{1,3}[\s._-]+", "", stem)
    artist = str(tags.get("artist") or tags.get("album_artist") or tags.get("albumartist")
                 or (parts[-3] if len(parts) >= 3 else ""))
    album = str(tags.get("album") or (parts[-2] if len(parts) >= 2 else ""))
    return artist.strip(), title.strip(), album.strip()


# --- LRCLIB -----------------------------------------------------------------------------------

# Set by run(): reports what the downloader is waiting for, so a slow server doesn't look like a
# frozen app. None outside a run.
_status = [None]


def status(message):
    if _status[0]:
        _status[0](message)


def http_get(url, stop=None, as_json=True, waits=(10, 30, 90), timeout=30):
    """GET -> parsed JSON (or raw bytes), None on 404. 429/5xx and network errors are retried
    after each of `waits` seconds; `stop` (a threading.Event) cuts a wait short."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    host = urllib.parse.urlparse(url).netloc
    for wait in (*waits, None):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response) if as_json else response.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code not in (429, 500, 502, 503, 504) or wait is None:
                raise
            problem = f"answered {e.code}"
        except (urllib.error.URLError, TimeoutError) as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                raise CertificateProblem(
                    f"The secure connection to {host} was rejected ({e.reason.verify_message}). "
                    "This usually means a company network, VPN or antivirus is inspecting web "
                    "traffic. Try another network, or ask whoever runs this one.") from e
            if wait is None:
                raise
            problem = "isn't answering" if isinstance(e, TimeoutError) or "timed out" in str(e) else f"failed ({e})"
        status(f"{host} {problem} — trying again in {wait} s…")
        if stop is not None:
            if stop.wait(wait):
                raise InterruptedError("stopped")
        else:
            time.sleep(wait)


def get_json(url, stop=None):
    return http_get(url, stop, as_json=True)


def lookup(artist, title, album, duration, tolerance, stop=None):
    """The best LRCLIB record for the song, or None.

    First an exact match on artist, title, album and duration; failing that, a search whose results
    must be within `tolerance` seconds of the file's duration, preferring synced lyrics."""
    query = {"artist_name": artist, "track_name": title}
    if album:
        query["album_name"] = album
    if duration:
        query["duration"] = str(round(duration))
    hit = get_json(f"{API}/get?{urllib.parse.urlencode(query)}", stop)
    if hit and (hit.get("syncedLyrics") or hit.get("plainLyrics") or hit.get("instrumental")):
        return hit

    results = get_json(f"{API}/search?" + urllib.parse.urlencode(
        {"artist_name": artist, "track_name": title}), stop) or []

    def close_enough(r):
        return not duration or abs((r.get("duration") or 0) - duration) <= tolerance

    candidates = [r for r in results if close_enough(r) and (r.get("syncedLyrics") or r.get("plainLyrics"))]
    if not candidates:
        return None
    return min(candidates, key=lambda r: (0 if r.get("syncedLyrics") else 1,
                                          abs((r.get("duration") or 0) - duration)))


# --- album covers ------------------------------------------------------------------------------

def has_cover_file(folder):
    try:
        names = {n.lower() for n in os.listdir(folder)}
    except OSError:
        return False
    return any(n in names for n in COVER_NAMES)


def has_embedded_picture(path):
    if TinyTag is not None:
        try:
            return TinyTag.get(path, image=True).images.any is not None
        except Exception:
            return False
    try:
        out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
                             capture_output=True, text=True, timeout=30).stdout
        streams = json.loads(out or "{}").get("streams", [])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return False
    return any((s.get("disposition") or {}).get("attached_pic") for s in streams)


def _norm(text):
    """Comparable form of an album or artist name: no edition notes, case or punctuation."""
    text = re.sub(r"[\(\[][^\)\]]*[\)\]]", "", text or "")
    return re.sub(r"[\W_]+", "", text.casefold())


_last_musicbrainz = [0.0]


def musicbrainz_get(url, stop):
    """MusicBrainz allows one request per second per client."""
    wait = 1.1 - (time.time() - _last_musicbrainz[0])
    if wait > 0 and stop.wait(wait):
        raise InterruptedError("stopped")
    try:
        return get_json(url, stop)
    finally:
        _last_musicbrainz[0] = time.time()


def _plain_title(text):
    """'The Wall (Deluxe Experience Edition) [Remastered]' -> 'The Wall', for searching."""
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", text or "").strip() or (text or "")


def _same(a, b):
    # Exact, after _norm. Looser matching ("one contains the other") put "More ABBA Gold" on
    # ABBA Gold: a missing cover is better than a wrong one.
    a, b = _norm(a), _norm(b)
    return bool(a) and a == b


def find_cover(artist, album, use_itunes, stop):
    """(image bytes, source) for the album's front cover, or (None, None)."""
    def quoted(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    title = _plain_title(album)
    status(f"Looking for the cover of {artist} — {album}…")
    query = f"releasegroup:{quoted(title)} AND artist:{quoted(artist)}"
    found = musicbrainz_get(f"{MUSICBRAINZ}/release-group/?" + urllib.parse.urlencode(
        {"query": query, "fmt": "json", "limit": 5}), stop) or {}
    for group in found.get("release-groups", []):
        if group.get("score", 0) < 90 or not _same(group.get("title"), album):
            continue
        status(f"Downloading the cover of {artist} — {album}…")
        image = http_get(f"{COVER_ART_ARCHIVE}/release-group/{group['id']}/front-1200", stop,
                         as_json=False, waits=(5, 15), timeout=20)
        if image:
            return image, "Cover Art Archive"

    if use_itunes:
        status(f"Trying iTunes for {artist} — {album}…")
        results = get_json(f"{ITUNES_SEARCH}?" + urllib.parse.urlencode(
            {"term": f"{artist} {title}", "entity": "album", "limit": 10}), stop) or {}
        for r in results.get("results", []):
            if _same(r.get("collectionName"), album) and _same(r.get("artistName"), artist) \
                    and r.get("artworkUrl100"):
                url = r["artworkUrl100"].replace("100x100bb", "1200x1200bb")
                image = http_get(url, stop, as_json=False, waits=(5, 15), timeout=20)
                if image:
                    return image, "iTunes"
    return None, None


def write_bytes_atomic(path, data):
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


# --- files ------------------------------------------------------------------------------------

def find_songs(paths, extensions):
    for root in paths:
        if os.path.isfile(root):
            yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if os.path.splitext(name)[1].lower() in extensions and not name.startswith("._"):
                    yield os.path.join(dirpath, name)


def write_atomic(path, text):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


class Cache:
    """Songs looked up without a result, so a re-run doesn't ask LRCLIB again for a while."""

    def __init__(self, path, retry_days, enabled):
        self.path, self.enabled = path, enabled
        self.retry = retry_days * 86400
        self.data = {}
        if enabled and path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (OSError, json.JSONDecodeError):
                self.data = {}

    def fresh_miss(self, key):
        entry = self.data.get(key)
        return bool(self.enabled and entry and time.time() - entry.get("ts", 0) < self.retry)

    def remember(self, key, status):
        if self.enabled:
            self.data[key] = {"status": status, "ts": time.time()}

    def save(self):
        if not (self.enabled and self.path):
            return
        tmp = self.path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)


# --- the run ---------------------------------------------------------------------------------

class Options:
    def __init__(self, dry_run=False, synced_only=False, overwrite=False, limit=0, delay=0.5,
                 tolerance=3.0, cache=None, use_cache=True, retry_days=30.0, extensions=None,
                 lyrics=True, covers=False, itunes=False):
        self.dry_run, self.synced_only, self.overwrite = dry_run, synced_only, overwrite
        self.limit, self.delay, self.tolerance = limit, delay, tolerance
        self.cache, self.use_cache, self.retry_days = cache, use_cache, retry_days
        self.extensions = extensions or AUDIO_EXTENSIONS
        self.lyrics, self.covers, self.itunes = lyrics, covers, itunes


def run(paths, opts, emit, stop=None):
    """Downloads lyrics for every song (and covers for every album folder) under `paths`.
    Reports through `emit(event, **data)`:

        start(total)              steps: songs (if lyrics) + album folders (if covers)
        song(index, total, kind, path, artist, title)       kind: one of KINDS
        cover(index, total, kind, folder, artist, album, source)   kind: one of COVER_KINDS
        error(index, total, path, message)
        status(message)           what it's busy with or waiting for, e.g. a server retry
        done(counts, covers, stopped)

    `stop` is a threading.Event; set it to end the run after the current song."""
    stop = stop or threading.Event()
    _status[0] = lambda message: emit("status", message=message)
    songs = list(find_songs(paths, opts.extensions))
    folders = list(dict.fromkeys(os.path.dirname(p) for p in songs)) if opts.covers else []
    lyric_songs = songs if opts.lyrics else []
    total = len(lyric_songs) + len(folders)
    emit("start", total=total)
    first_dir = next((p for p in paths if os.path.isdir(p)), os.path.dirname(os.path.abspath(paths[0])))
    cache = Cache(opts.cache or os.path.join(first_dir, CACHE_NAME), opts.retry_days,
                  enabled=opts.use_cache and not opts.dry_run)
    counts = dict.fromkeys(KINDS, 0)
    cover_counts = dict.fromkeys(COVER_KINDS, 0)
    looked_up = errors_in_a_row = 0

    def song(index, kind, path, artist="", title=""):
        counts[kind] += 1
        emit("song", index=index, total=total, kind=kind, path=path, artist=artist, title=title)

    try:
        for index, path in enumerate(lyric_songs, 1):
            if stop.is_set():
                break
            base = os.path.splitext(path)[0]
            key = os.path.abspath(path)
            if not opts.overwrite and (os.path.exists(base + ".lrc") or os.path.exists(base + ".txt")):
                song(index, "skipped", path)
                continue
            if not opts.overwrite and cache.fresh_miss(key):
                song(index, "skipped", path)
                continue
            if opts.limit and looked_up >= opts.limit:
                break

            tags, duration = probe(path)
            if not opts.overwrite and has_embedded_lyrics(tags):
                song(index, "skipped", path)
                continue
            artist, title, album = song_info(path, tags)
            if not artist or not title:
                cache.remember(key, "no tags")
                song(index, "not found", path)
                continue

            looked_up += 1
            try:
                hit = lookup(artist, title, album, duration, opts.tolerance, stop)
            except InterruptedError:
                break
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
                # Not remembered: the song is tried again on the next run.
                counts["error"] += 1
                errors_in_a_row += 1
                emit("error", index=index, total=total, path=path, message=str(e))
                # Many failures in a row means LRCLIB is down: wait instead of racing through.
                if stop.wait(600 if errors_in_a_row >= 5 else 5):
                    break
                continue
            errors_in_a_row = 0

            if hit and hit.get("syncedLyrics"):
                kind, target, text = "synced", base + ".lrc", hit["syncedLyrics"]
            elif hit and hit.get("plainLyrics") and not opts.synced_only:
                kind, target, text = "plain", base + ".txt", hit["plainLyrics"]
            elif hit and hit.get("instrumental"):
                kind, target, text = "instrumental", None, None
            else:
                kind, target, text = "not found", None, None
            if target and not opts.dry_run:
                write_atomic(target, text)
            if not target:
                cache.remember(key, kind)
            song(index, kind, path, artist, title)
            if looked_up % 100 == 0:
                cache.save()
            if stop.wait(opts.delay):
                break

        # Covers: one per album folder, after the lyrics.
        for n, folder in enumerate(folders, 1):
            index = len(lyric_songs) + n
            if stop.is_set():
                break
            key = "cover:" + os.path.abspath(folder)
            first = next(p for p in songs if os.path.dirname(p) == folder)

            def cover(kind, artist="", album="", source="", reason=""):
                cover_counts[kind] += 1
                emit("cover", index=index, total=total, kind=kind, folder=folder,
                     artist=artist, album=album, source=source, reason=reason)

            if not opts.overwrite:
                reason = ("already has a cover file" if has_cover_file(folder) else
                          "not found last time" if cache.fresh_miss(key) else
                          "artwork is embedded in the songs" if has_embedded_picture(first) else "")
                if reason:
                    cover("skipped", reason=reason)
                    continue
            tags, _ = probe(first)
            artist, _, album = song_info(first, tags)
            artist = str(tags.get("album_artist") or tags.get("albumartist") or artist)
            if not artist or not album:
                cache.remember(key, "no tags")
                cover("not found")
                continue
            try:
                image, source = find_cover(artist, album, opts.itunes, stop)
            except InterruptedError:
                break
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
                cover_counts["error"] += 1
                emit("error", index=index, total=total, path=folder, message=str(e))
                if stop.wait(5):
                    break
                continue
            if image:
                name = "cover.png" if image[:8] == b"\x89PNG\r\n\x1a\n" else "cover.jpg"
                if not opts.dry_run:
                    write_bytes_atomic(os.path.join(folder, name), image)
                cover("found", artist, album, source)
            else:
                cache.remember(key, "no cover")
                cover("not found", artist, album)
            if n % 50 == 0:
                cache.save()
    finally:
        cache.save()
        _status[0] = None
        emit("done", counts=counts, covers=cover_counts, stopped=stop.is_set())
    return counts


# --- command line ------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="lyrics_downloader.py",
        description="Download lyrics from LRCLIB and save them as .lrc/.txt files next to your songs.")
    ap.add_argument("paths", nargs="+", metavar="PATH", help="music folder(s) or file(s)")
    ap.add_argument("-n", "--dry-run", action="store_true", help="look songs up but write nothing")
    ap.add_argument("--covers", action="store_true",
                    help="also save each album folder's front cover as cover.jpg (Cover Art Archive)")
    ap.add_argument("--itunes", action="store_true",
                    help="with --covers: fall back to Apple's iTunes catalog for covers")
    ap.add_argument("--no-lyrics", action="store_true", help="skip lyrics (e.g. covers only)")
    ap.add_argument("--synced-only", action="store_true",
                    help="only write synced lyrics (.lrc); skip songs that only have plain lyrics")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace existing .lrc/.txt files and ignore embedded lyrics")
    ap.add_argument("--limit", type=int, default=0, metavar="N", help="stop after looking up N songs")
    ap.add_argument("--delay", type=float, default=0.5, metavar="SEC",
                    help="pause between songs, to go easy on LRCLIB (default: 0.5)")
    ap.add_argument("--tolerance", type=float, default=3, metavar="SEC",
                    help="allowed duration difference for search matches (default: 3)")
    ap.add_argument("--cache", metavar="FILE",
                    help=f"where to remember misses (default: {CACHE_NAME} in the first folder)")
    ap.add_argument("--no-cache", action="store_true", help="don't remember misses")
    ap.add_argument("--retry-days", type=float, default=30, metavar="DAYS",
                    help="ask again about songs with no lyrics after this many days (default: 30)")
    ap.add_argument("--ext", action="append", metavar=".EXT",
                    help="audio extension to include (repeatable); default: common audio formats")
    ap.add_argument("-q", "--quiet", action="store_true", help="only print errors and the summary")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)

    if TinyTag is None and not shutil.which("ffprobe"):
        sys.exit("Can't read tags: install tinytag (pip install tinytag) or FFmpeg (for ffprobe).")
    for p in args.paths:
        if not os.path.exists(p):
            sys.exit(f"no such file or folder: {p}")
    extensions = {e.lower() if e.startswith(".") else "." + e.lower() for e in args.ext} \
        if args.ext else None
    opts = Options(dry_run=args.dry_run, synced_only=args.synced_only, overwrite=args.overwrite,
                   limit=args.limit, delay=args.delay, tolerance=args.tolerance, cache=args.cache,
                   use_cache=not args.no_cache, retry_days=args.retry_days, extensions=extensions,
                   lyrics=not args.no_lyrics, covers=args.covers, itunes=args.itunes)
    if args.no_lyrics and not args.covers:
        sys.exit("Nothing to do: --no-lyrics without --covers.")

    def emit(event, **d):
        if event == "song" and not args.quiet and d["kind"] != "skipped":
            label = f"{d['artist']} — {d['title']}" if d["artist"] else d["path"]
            print(f"{d['kind']:12} {label}", flush=True)
        elif event == "cover" and not args.quiet:
            what = f"{d['artist']} — {d['album']}" if d["album"] else d["folder"]
            extra = f" ({d['source'] or d['reason']})" if d["source"] or d["reason"] else ""
            print(f"{'cover ' + d['kind']:12} {what}{extra}", flush=True)
        elif event == "status" and not args.quiet and "trying again" in d["message"]:
            print(f"             {d['message']}", file=sys.stderr, flush=True)
        elif event == "error":
            print(f"error        {d['path']}: {d['message']}", file=sys.stderr, flush=True)
        elif event == "done":
            parts = []
            if any(d["counts"].values()):
                parts.append(", ".join(f"{v} {k}" for k, v in d["counts"].items() if v))
            if any(d["covers"].values()):
                parts.append("covers: " + ", ".join(f"{v} {k}" for k, v in d["covers"].items() if v))
            summary = "; ".join(parts)
            note = " (stopped — run again to continue)" if d["stopped"] else ""
            print(f"done{' (dry run)' if args.dry_run else ''}: {summary or 'nothing to do'}{note}",
                  flush=True)

    use_system_certificates()
    stop = threading.Event()
    try:
        run(args.paths, opts, emit, stop)
    except KeyboardInterrupt:
        stop.set()
    except CertificateProblem as e:
        sys.exit(str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
