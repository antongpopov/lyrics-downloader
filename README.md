# lyrics-downloader

Download lyrics for your own music library and save them **next to the songs**, as sidecar
files that music players and servers pick up on their own.

```
Music/ABBA/ABBA Gold/02 Knowing Me, Knowing You.m4a
Music/ABBA/ABBA Gold/02 Knowing Me, Knowing You.lrc   ← synced, time-stamped lyrics
```

- One Python file, no packages to install — just Python 3.8+ and `ffprobe` (part of FFmpeg).
- Lyrics come from [LRCLIB](https://lrclib.net), a free, community-built lyrics database with an
  open API (no account or API key).
- Prefers **synced** lyrics (`.lrc`, which scroll along with the song); falls back to plain lyrics
  (`.txt`) when that's all there is.
- Safe to stop and run again: songs that already have lyrics are skipped, and songs with no match
  are remembered so they aren't looked up on every run.

## Requirements

- **Python 3.8 or newer** — `python3 --version`
- **ffprobe**, which comes with FFmpeg — `ffprobe -version`
  - macOS: `brew install ffmpeg`
  - Debian / Ubuntu / Raspberry Pi OS: `sudo apt install ffmpeg`
  - Windows: `winget install ffmpeg`, or a build from [ffmpeg.org](https://ffmpeg.org/download.html)

## Install

```sh
git clone https://github.com/antongpopov/lyrics-downloader.git
cd lyrics-downloader
```

Or grab just the script:

```sh
curl -O https://raw.githubusercontent.com/antongpopov/lyrics-downloader/main/lyrics_downloader.py
```

## Use

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

### Options

| Option | What it does |
|---|---|
| `-n`, `--dry-run` | Look songs up, but write nothing |
| `--synced-only` | Only write synced lyrics (`.lrc`); skip songs that only have plain lyrics |
| `--overwrite` | Replace existing `.lrc`/`.txt` files, and look up songs that have lyrics in their tags |
| `--limit N` | Stop after looking up N songs |
| `--delay SEC` | Pause between songs, default `0.5` — please keep it, LRCLIB is a free service |
| `--tolerance SEC` | How far a search result's length may differ from your file, default `3` |
| `--cache FILE` | Where to remember songs without lyrics (default: `.lyrics-downloader-cache.json` in the first folder) |
| `--no-cache` | Don't remember them; look everything up every time |
| `--retry-days DAYS` | Look up songs without lyrics again after this many days, default `30` |
| `--ext .EXT` | Only these audio extensions (repeatable), e.g. `--ext .flac --ext .mp3` |
| `-q`, `--quiet` | Only print errors and the summary |

### Keeping it up to date

New songs get lyrics the next time it runs. For example, once a week with cron:

```cron
0 4 * * 0  python3 /path/to/lyrics_downloader.py -q /srv/music >> /var/log/lyrics-downloader.log 2>&1
```

## How it works

1. **Finds songs.** Walks the folders you give it and picks up common audio formats
   (mp3, m4a, flac, ogg, opus, wav, aac, alac, aiff, wma, ape, wv). Hidden folders are skipped.
2. **Skips songs that already have lyrics:** a `.lrc` or `.txt` with the same name next to the song,
   or lyrics embedded in its tags. (`--overwrite` turns both checks off.)
3. **Reads the song's tags** — artist, title, album and length — with `ffprobe`. If a file has no
   tags, it falls back to the folder layout `Artist/Album/NN Title.ext`.
4. **Asks LRCLIB**, first for an exact match on artist, title, album and length, then with a search
   whose results must be within `--tolerance` seconds of your file's length. That length check is
   what keeps a live version or a radio edit from getting the wrong lyrics.
5. **Writes** `Song.lrc` (synced) or `Song.txt` (plain) beside the song, in UTF-8.
6. **Remembers misses** (no lyrics, instrumental, no tags) in the cache file. Network errors are
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

## Please be nice to LRCLIB

LRCLIB is run for free by volunteers. This script identifies itself with its own User-Agent and
waits between songs by default. Please don't lower `--delay` to hammer it, and consider
[contributing lyrics or supporting the project](https://lrclib.net).

## A note on lyrics and copyright

Song lyrics are copyrighted by their authors and publishers. LRCLIB's *software* is open source, but
that doesn't licence the lyrics in it. This tool is meant for adding lyrics to **your own music
library for your own use**. Don't use it to republish lyrics or to ship them inside an app or
product.

## Licence

[MIT](LICENSE)
