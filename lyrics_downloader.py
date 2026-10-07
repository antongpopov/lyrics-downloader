#!/usr/bin/env python3
"""Download lyrics for a local music library and save them next to the songs.

For every audio file that has no lyrics yet, reads its tags with ffprobe, looks the song up on
LRCLIB (https://lrclib.net) and writes the result beside it with the same name:

    Artist/Album/03 Song.flac  ->  Artist/Album/03 Song.lrc   (synced, time-stamped lyrics)
                               ->  Artist/Album/03 Song.txt   (plain lyrics, when no synced exist)

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
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

__version__ = "1.1.0"

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".aac", ".alac",
                    ".aiff", ".aif", ".wma", ".ape", ".wv"}
API = "https://lrclib.net/api"
# LRCLIB asks clients to identify themselves with a User-Agent.
USER_AGENT = f"lyrics-downloader/{__version__} (+https://github.com/antongpopov/lyrics-downloader)"
CACHE_NAME = ".lyrics-downloader-cache.json"
EMBEDDED_LYRICS_TAGS = ("lyrics", "unsyncedlyrics", "uslt", "©lyr")
KINDS = ("synced", "plain", "instrumental", "not found", "skipped", "error")

try:
    from tinytag import TinyTag
except ImportError:  # the command line works with ffprobe alone
    TinyTag = None


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

def get_json(url, stop=None):
    """GET -> parsed JSON, None on 404. 429/5xx and network errors are retried with growing waits."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for wait in (10, 30, 90, None):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code not in (429, 500, 502, 503, 504) or wait is None:
                raise
        except (urllib.error.URLError, TimeoutError):
            if wait is None:
                raise
        if stop is not None:
            if stop.wait(wait):
                raise InterruptedError("stopped")
        else:
            time.sleep(wait)


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
                 tolerance=3.0, cache=None, use_cache=True, retry_days=30.0, extensions=None):
        self.dry_run, self.synced_only, self.overwrite = dry_run, synced_only, overwrite
        self.limit, self.delay, self.tolerance = limit, delay, tolerance
        self.cache, self.use_cache, self.retry_days = cache, use_cache, retry_days
        self.extensions = extensions or AUDIO_EXTENSIONS


def run(paths, opts, emit, stop=None):
    """Downloads lyrics for every song under `paths`. Reports through `emit(event, **data)`:

        start(total)                          songs found, before any lookup
        song(index, total, kind, path, artist, title)   kind: one of KINDS
        error(index, total, path, message)
        done(counts, stopped)

    `stop` is a threading.Event; set it to end the run after the current song."""
    stop = stop or threading.Event()
    songs = list(find_songs(paths, opts.extensions))
    total = len(songs)
    emit("start", total=total)
    first_dir = next((p for p in paths if os.path.isdir(p)), os.path.dirname(os.path.abspath(paths[0])))
    cache = Cache(opts.cache or os.path.join(first_dir, CACHE_NAME), opts.retry_days,
                  enabled=opts.use_cache and not opts.dry_run)
    counts = dict.fromkeys(KINDS, 0)
    looked_up = errors_in_a_row = 0

    def song(index, kind, path, artist="", title=""):
        counts[kind] += 1
        emit("song", index=index, total=total, kind=kind, path=path, artist=artist, title=title)

    try:
        for index, path in enumerate(songs, 1):
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
    finally:
        cache.save()
        emit("done", counts=counts, stopped=stop.is_set())
    return counts


# --- command line ------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="lyrics_downloader.py",
        description="Download lyrics from LRCLIB and save them as .lrc/.txt files next to your songs.")
    ap.add_argument("paths", nargs="+", metavar="PATH", help="music folder(s) or file(s)")
    ap.add_argument("-n", "--dry-run", action="store_true", help="look songs up but write nothing")
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
                   use_cache=not args.no_cache, retry_days=args.retry_days, extensions=extensions)

    def emit(event, **d):
        if event == "song" and not args.quiet and d["kind"] != "skipped":
            label = f"{d['artist']} — {d['title']}" if d["artist"] else d["path"]
            print(f"{d['kind']:12} {label}", flush=True)
        elif event == "error":
            print(f"error        {d['path']}: {d['message']}", file=sys.stderr, flush=True)
        elif event == "done":
            summary = ", ".join(f"{v} {k}" for k, v in d["counts"].items() if v)
            note = " (stopped — run again to continue)" if d["stopped"] else ""
            print(f"done{' (dry run)' if args.dry_run else ''}: {summary or 'nothing to do'}{note}",
                  flush=True)

    stop = threading.Event()
    try:
        run(args.paths, opts, emit, stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
