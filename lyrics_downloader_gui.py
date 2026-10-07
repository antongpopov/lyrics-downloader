#!/usr/bin/env python3
"""Lyrics Downloader — the desktop app (macOS, Windows, Linux).

A small window around lyrics_downloader.run(): pick your music folder, press Start, and lyrics
from LRCLIB are saved as .lrc/.txt files next to your songs — and, if ticked, each album's front
cover as cover.jpg in its folder. Needs Python 3.8+ with Tk and the
`tinytag` package; the packaged apps on the Releases page include both.
"""
import json
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import lyrics_downloader as core

APP_NAME = "Lyrics Downloader"
REPO = "https://github.com/antongpopov/lyrics-downloader"


def settings_path():
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "Lyrics Downloader", "settings.json")


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass  # remembering the folder is a convenience, never worth an error


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.counts = dict.fromkeys(core.KINDS, 0)
        self.cover_counts = dict.fromkeys(core.COVER_KINDS, 0)
        self.total = 0
        settings = load_settings()

        root.title(APP_NAME)
        root.minsize(600, 540)
        root.geometry("720x640")
        pad = {"padx": 12, "pady": 6}

        # Folder
        frame = ttk.Frame(root)
        frame.pack(fill="x", **pad)
        ttk.Label(frame, text="Music folder").pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(4, 0))
        self.folder = tk.StringVar(value=settings.get("folder", ""))
        ttk.Entry(row, textvariable=self.folder).pack(side="left", fill="x", expand=True)
        self.choose_button = ttk.Button(row, text="Choose…", command=self.choose_folder)
        self.choose_button.pack(side="left", padx=(8, 0))

        # Options
        opts = ttk.Frame(root)
        opts.pack(fill="x", **pad)
        self.lyrics = tk.BooleanVar(value=settings.get("lyrics", True))
        self.synced_only = tk.BooleanVar(value=settings.get("synced_only", False))
        self.covers = tk.BooleanVar(value=settings.get("covers", False))
        self.itunes = tk.BooleanVar(value=settings.get("itunes", False))
        self.overwrite = tk.BooleanVar(value=False)
        self.dry_run = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Lyrics — a .lrc or .txt file next to each song",
                        variable=self.lyrics, command=self.update_options).pack(anchor="w")
        self.synced_box = ttk.Checkbutton(opts, text="Only synced lyrics (.lrc) — skip songs with plain lyrics only",
                                          variable=self.synced_only)
        self.synced_box.pack(anchor="w", padx=(24, 0))
        ttk.Checkbutton(opts, text="Album covers — cover.jpg in each album folder (Cover Art Archive)",
                        variable=self.covers, command=self.update_options).pack(anchor="w", pady=(4, 0))
        self.itunes_box = ttk.Checkbutton(opts, text="Also look in Apple's iTunes catalog when it's missing there",
                                          variable=self.itunes)
        self.itunes_box.pack(anchor="w", padx=(24, 0))
        ttk.Checkbutton(opts, text="Replace lyrics and covers that already exist",
                        variable=self.overwrite).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(opts, text="Test run — look songs up but don't save anything",
                        variable=self.dry_run).pack(anchor="w")

        # Start / Stop + progress
        bar = ttk.Frame(root)
        bar.pack(fill="x", **pad)
        self.start_button = ttk.Button(bar, text="Start", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(bar, text="Stop", command=self.request_stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        self.status = tk.StringVar(value="Choose your music folder, then press Start.")
        ttk.Label(bar, textvariable=self.status).pack(side="left", padx=(12, 0))
        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x", padx=12)

        # Log
        self.log = ScrolledText(root, height=14, wrap="none", state="disabled",
                                font=("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=12, pady=(8, 4))
        self.log.tag_configure("synced", foreground="#2e7d32")
        self.log.tag_configure("plain", foreground="#1565c0")
        self.log.tag_configure("muted", foreground="#888888")
        self.log.tag_configure("error", foreground="#c62828")

        # Footer
        foot = ttk.Frame(root)
        foot.pack(fill="x", padx=12, pady=(0, 10))
        link = ttk.Label(foot, text="Lyrics: LRCLIB · Covers: Cover Art Archive — for your own music library only.",
                         foreground="#888888", cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda _: webbrowser.open("https://lrclib.net"))
        about = ttk.Label(foot, text=f"v{core.__version__} · About", foreground="#888888", cursor="hand2")
        about.pack(side="right")
        about.bind("<Button-1>", lambda _: webbrowser.open(REPO))

        self.update_options()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.pump)

    # --- actions ---------------------------------------------------------------------------

    def update_options(self):
        self.synced_box.configure(state="normal" if self.lyrics.get() else "disabled")
        self.itunes_box.configure(state="normal" if self.covers.get() else "disabled")

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Choose your music folder",
                                         initialdir=self.folder.get() or os.path.expanduser("~"))
        if folder:
            self.folder.set(folder)

    def start(self):
        folder = self.folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning(APP_NAME, "Choose a music folder first.")
            return
        if not self.lyrics.get() and not self.covers.get():
            messagebox.showwarning(APP_NAME, "Tick Lyrics, Album covers, or both.")
            return
        save_settings({"folder": folder, "lyrics": self.lyrics.get(), "synced_only": self.synced_only.get(),
                       "covers": self.covers.get(), "itunes": self.itunes.get()})
        self.counts = dict.fromkeys(core.KINDS, 0)
        self.cover_counts = dict.fromkeys(core.COVER_KINDS, 0)
        self.stop.clear()
        self.set_running(True)
        self.clear_log()
        self.status.set("Looking for songs…")
        self.progress.configure(value=0, maximum=1)
        opts = core.Options(dry_run=self.dry_run.get(), synced_only=self.synced_only.get(),
                            overwrite=self.overwrite.get(), lyrics=self.lyrics.get(),
                            covers=self.covers.get(), itunes=self.itunes.get())

        def emit(event, **data):
            self.events.put((event, data))

        def work():
            try:
                core.run([folder], opts, emit, self.stop)
            except Exception as e:  # never leave the window stuck in "running"
                emit("crash", message=str(e))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def request_stop(self):
        self.stop.set()
        self.stop_button.configure(state="disabled")
        self.status.set("Stopping after the current song…")

    def on_close(self):
        if self.worker and self.worker.is_alive():
            self.stop.set()
            self.worker.join(timeout=3)
        self.root.destroy()

    # --- events from the worker -------------------------------------------------------------

    def pump(self):
        try:
            while True:
                event, data = self.events.get_nowait()
                self.handle(event, data)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def handle(self, event, d):
        if event == "start":
            self.total = d["total"]
            self.progress.configure(maximum=max(1, self.total), value=0)
            if not self.total:
                self.append("No audio files found in this folder.", "muted")
        elif event == "song":
            self.counts[d["kind"]] += 1
            self.progress.configure(value=d["index"])
            if d["kind"] != "skipped":
                label = f"{d['artist']} — {d['title']}" if d["artist"] else os.path.basename(d["path"])
                tag = d["kind"] if d["kind"] in ("synced", "plain") else "muted"
                self.append(f"{d['kind']:12} {label}", tag)
            self.status.set(self.summary(d["index"]))
        elif event == "cover":
            self.cover_counts[d["kind"]] += 1
            self.progress.configure(value=d["index"])
            what = f"{d['artist']} — {d['album']}" if d["album"] else os.path.basename(d["folder"])
            extra = f" ({d['source'] or d['reason']})" if d["source"] or d["reason"] else ""
            self.append(f"{'cover ' + d['kind']:12} {what}{extra}", "synced" if d["kind"] == "found" else "muted")
            self.status.set(self.summary(d["index"]))
        elif event == "status":
            # What it's busy with between results — e.g. a server that needs a retry.
            self.status.set(d["message"])
        elif event == "error":
            self.counts["error"] += 1
            self.append(f"error        {os.path.basename(d['path'])}: {d['message']}", "error")
            self.status.set("A server isn't answering — waiting and retrying…")
        elif event == "done":
            self.set_running(False)
            word = "Stopped" if d["stopped"] else ("Test run done" if self.dry_run.get() else "Done")
            self.status.set(f"{word}. {self.summary()}")
            if not d["stopped"]:
                self.progress.configure(value=self.progress["maximum"])
        elif event == "crash":
            self.set_running(False)
            self.status.set("Something went wrong.")
            messagebox.showerror(APP_NAME, d["message"])

    # --- helpers -----------------------------------------------------------------------------

    def summary(self, index=None):
        c, k = self.counts, self.cover_counts
        parts = []
        if self.lyrics.get():
            parts += [f"{c['synced']} synced", f"{c['plain']} plain", f"{c['not found']} not found"]
            if c["skipped"]:
                parts.append(f"{c['skipped']} already had lyrics")
        if self.covers.get():
            parts.append(f"covers: {k['found']} found, {k['not found']} not found"
                         + (f", {k['skipped']} already had one" if k["skipped"] else ""))
        prefix = f"{index:,} / {self.total:,} — " if index is not None else ""
        return prefix + ", ".join(parts)

    def set_running(self, running):
        self.start_button.configure(state="disabled" if running else "normal")
        self.choose_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def append(self, line, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")


def main():
    # With arguments it is the text version: the packaged Mac app's program doubles as the
    # command-line tool (`Lyrics Downloader.app/Contents/MacOS/Lyrics Downloader ~/Music`).
    # macOS may pass a -psn_… argument when launched from Finder; that's not a request for text.
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    if args:
        sys.exit(core.main(args))
    if core.TinyTag is None and not core.shutil.which("ffprobe"):
        tk.Tk().withdraw()
        messagebox.showerror(APP_NAME, "Lyrics Downloader needs the tinytag package to read your songs.\n\n"
                                       "Install it with:  python -m pip install tinytag")
        return
    core.use_system_certificates()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("aqua" if sys.platform == "darwin" else
                              "vista" if os.name == "nt" else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
