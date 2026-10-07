# lyrics-downloader

Download lyrics — and, if you like, album covers — for your own music library and save them
**next to the songs**, as files that music players and servers pick up on their own.

```
Music/ABBA/ABBA Gold/02 Knowing Me, Knowing You.m4a
Music/ABBA/ABBA Gold/02 Knowing Me, Knowing You.lrc   ← synced, time-stamped lyrics
Music/ABBA/ABBA Gold/cover.jpg                        ← album cover (optional)
```

- **Desktop app** for macOS and Windows: pick your music folder, press Start.
- Or the **command line**: one Python file; needs Python 3.8+ plus either the small `tinytag`
  package or `ffprobe` (part of FFmpeg) to read your songs' tags.
- Lyrics come from [LRCLIB](https://lrclib.net), a free, community-built lyrics database with an
  open API (no account or API key).
- Prefers **synced** lyrics (`.lrc`, which scroll along with the song); falls back to plain lyrics
  (`.txt`) when that's all there is.
- **Album covers** (optional): one `cover.jpg` per album folder, from the
  [Cover Art Archive](https://coverartarchive.org) (via [MusicBrainz](https://musicbrainz.org)),
  with Apple's iTunes catalog as an optional fallback.
- Safe to stop and run again: songs that already have lyrics are skipped, and songs with no match
  are remembered so they aren't looked up on every run.

## Desktop app

Download it from the [Releases page](https://github.com/antongpopov/lyrics-downloader/releases/latest):

| System | Download |
|---|---|
| Mac with Apple silicon (M1 and later), macOS 11+ | `LyricsDownloader-…-mac-arm64.zip` |
| Mac with an Intel processor, macOS 10.15+ | `LyricsDownloader-…-mac-x86_64.zip` |
| Windows 10 / 11 | `LyricsDownloader-…-windows.exe` |

- **Mac:** unzip, move **Lyrics Downloader** to Applications and open it. The app is signed and
  notarized by Apple, so it opens normally.
- **Windows:** run the `.exe`, nothing to install. Windows may say *"Windows protected your PC"*,
  because the program isn't signed with a paid Microsoft certificate: click **More info → Run anyway**.

Choose your music folder, tick what you want (lyrics, album covers or both), and press **Start**. The window lists every song as it
goes and shows a running count; **Stop** ends the run, and the next Start carries on where it
left off. The app remembers your folder.

## Command line

### Requirements

- **Python 3.8 or newer** — `python3 --version`
- To read your songs' tags, **one** of:
  - **tinytag** (recommended, pure Python) — `python3 -m pip install tinytag`
  - **ffprobe**, which comes with FFmpeg — `ffprobe -version`
    - macOS: `brew install ffmpeg`
    - Debian / Ubuntu / Raspberry Pi OS: `sudo apt install ffmpeg`
    - Windows: `winget install ffmpeg`, or a build from [ffmpeg.org](https://ffmpeg.org/download.html)

### Install

```sh
git clone https://github.com/antongpopov/lyrics-downloader.git
cd lyrics-downloader
```

Or grab just the script:

```sh
curl -O https://raw.githubusercontent.com/antongpopov/lyrics-downloader/main/lyrics_downloader.py
```

### Use

Try it first without writing anything:

```sh
python3 lyrics_downloader.py --dry-run --limit 20 ~/Music
```

Then run it for real:

```sh
python3 lyrics_downloader.py ~/Music
```

You can pass several folders, or single files. Each song prints one line — `synced`, `plain`,
`instrumental` or `not found` — and a summary is printed at the end. A big library takes a while
(roughly a second or two per song), so on a server you may want to run it in the background:

```sh
nohup python3 lyrics_downloader.py /srv/music >> lyrics.log 2>&1 &
```

#### Options

| Option | What it does |
|---|---|
| `-n`, `--dry-run` | Look songs up, but write nothing |
| `--covers` | Also save each album folder's front cover as `cover.jpg` |
| `--itunes` | With `--covers`: look in Apple's iTunes catalog when the Cover Art Archive has no cover |
| `--no-lyrics` | Skip lyrics — e.g. `--covers --no-lyrics` for covers only |
| `--synced-only` | Only write synced lyrics (`.lrc`); skip songs that only have plain lyrics |
| `--overwrite` | Replace existing lyrics and covers, and look up songs that have lyrics or a picture in their tags |
| `--limit N` | Stop after looking up N songs |
| `--delay SEC` | Pause between songs, default `0.5` — please keep it, LRCLIB is a free service |
| `--tolerance SEC` | How far a search result's length may differ from your file, default `3` |
| `--cache FILE` | Where to remember songs without lyrics (default: `.lyrics-downloader-cache.json` in the first folder) |
| `--no-cache` | Don't remember them; look everything up every time |
| `--retry-days DAYS` | Look up songs without lyrics again after this many days, default `30` |
| `--ext .EXT` | Only these audio extensions (repeatable), e.g. `--ext .flac --ext .mp3` |
| `-q`, `--quiet` | Only print errors and the summary |

#### Keeping it up to date

New songs get lyrics the next time it runs. For example, once a week with cron:

```cron
0 4 * * 0  python3 /path/to/lyrics_downloader.py -q /srv/music >> /var/log/lyrics-downloader.log 2>&1
```

## How it works

1. **Finds songs.** Walks the folders you give it and picks up common audio formats
   (mp3, m4a, flac, ogg, opus, wav, aac, alac, aiff, wma, ape, wv). Hidden folders are skipped.
2. **Skips songs that already have lyrics:** a `.lrc` or `.txt` with the same name next to the song,
   or lyrics embedded in its tags. (`--overwrite` turns both checks off.)
3. **Reads the song's tags** — artist, title, album and length — with `tinytag` (or `ffprobe`). If a file has no
   tags, it falls back to the folder layout `Artist/Album/NN Title.ext`.
4. **Asks LRCLIB**, first for an exact match on artist, title, album and length, then with a search
   whose results must be within `--tolerance` seconds of your file's length. That length check is
   what keeps a live version or a radio edit from getting the wrong lyrics.
5. **Writes** `Song.lrc` (synced) or `Song.txt` (plain) beside the song, in UTF-8.
6. **Album covers** (with `--covers`, after the lyrics): for every folder with songs, unless it
   already has a cover file (`cover.jpg`, `folder.jpg`, `front.jpg`, …) or its songs carry an
   embedded picture, it finds the album on MusicBrainz — at most one request per second, as
   MusicBrainz asks — and saves the Cover Art Archive's front cover (1200 px) as `cover.jpg`.
   Album names must match exactly, apart from case, punctuation and edition notes such as
   "(Remastered)": a missing cover is better than a wrong one. With `--itunes`, albums the
   archive doesn't have are looked up in Apple's iTunes catalog.
7. **Remembers misses** (no lyrics, instrumental, no tags) in the cache file. Network errors are
   *not* remembered, so those songs are simply tried again next time. If LRCLIB is unreachable, the
   script retries with growing pauses, and after five failures in a row it waits ten minutes
   instead of rushing through your library.

Good tags give the best results. If a song isn't found, check that its artist and title tags match
how the song is usually written.

## Which players use the files?

Sidecar `.lrc` files with the same name as the song are a long-standing convention. Among others,
[Jellyfin](https://jellyfin.org) and [Navidrome](https://www.navidrome.org) read them on the server
side, and many players (for example Poweramp on Android) show them directly. Support varies and
some players want an add-on, so check your player's documentation.

## Please be nice to LRCLIB and MusicBrainz

LRCLIB, MusicBrainz and the Cover Art Archive are run for free by volunteers and non-profits. This
tool identifies itself with its own User-Agent, waits between songs by default and keeps to
MusicBrainz's one request per second. Please don't lower `--delay` to hammer them, and consider
supporting [LRCLIB](https://lrclib.net) or [MetaBrainz](https://metabrainz.org/donate), or adding
lyrics and covers you have.

## A note on lyrics, covers and copyright

Song lyrics are copyrighted by their authors and publishers, and album covers by their artists and
labels. LRCLIB's *software* is open source, but that doesn't licence the lyrics in it. This tool
is meant for adding lyrics and covers to **your own music library for your own use**. Don't use it
to republish them or to ship them inside an app or
product.

## Building the apps

The desktop app is `lyrics_downloader_gui.py` (Tk, part of Python) on top of `lyrics_downloader.py`.
Run it from source with `python3 lyrics_downloader_gui.py` (needs `tinytag` and a Python with Tk).

- **Windows:** the [Windows build](.github/workflows/windows.yml) workflow builds the `.exe` with
  PyInstaller on every `v*` tag and attaches it to that release.
- **macOS:** `scripts/build_mac.sh arm64` and `scripts/build_mac.sh x86_64` build the app with
  PyInstaller (via [uv](https://docs.astral.sh/uv/)), sign it with a *Developer ID Application*
  certificate, notarize it with an App Store Connect API key (`ASC_KEY_PATH`, `ASC_KEY_ID`,
  `ASC_ISSUER_ID`) and staple it; `--upload` attaches the zip to the release. This runs on the
  maintainer's Mac on purpose, so no signing keys live in this public repository. Without a
  certificate it builds an unsigned app (`SIGN=0` forces that).

## Licence

[MIT](LICENSE)
