#!/usr/bin/env python3
"""Lyrics Downloader — the desktop app (macOS, Windows, Linux).

A small window around lyrics_downloader.run(): pick your music folder, press Start, and lyrics
from LRCLIB are saved as .lrc/.txt files next to your songs. Needs Python 3.8+ with Tk and the
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
        self.total = 0
        settings = load_settings()

        root.title(APP_NAME)
        root.minsize(560, 460)
        root.geometry("700x560")
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
        self.synced_only = tk.BooleanVar(value=settings.get("synced_only", False))
        self.overwrite = tk.BooleanVar(value=False)
        self.dry_run = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Only synced lyrics (.lrc) — skip songs with plain lyrics only",
                        variable=self.synced_only).pack(anchor="w")
        ttk.Checkbutton(opts, text="Replace lyrics files that already exist",
                        variable=self.overwrite).pack(anchor="w")
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
        link = ttk.Label(foot, text="Lyrics from LRCLIB (lrclib.net) — for your own music library only.",
                         foreground="#888888", cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda _: webbrowser.open("https://lrclib.net"))
        about = ttk.Label(foot, text=f"v{core.__version__} · About", foreground="#888888", cursor="hand2")
        about.pack(side="right")
        about.bind("<Button-1>", lambda _: webbrowser.open(REPO))

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.pump)

    # --- actions ---------------------------------------------------------------------------

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
        save_settings({"folder": folder, "synced_only": self.synced_only.get()})
        self.counts = dict.fromkeys(core.KINDS, 0)
        self.stop.clear()
        self.set_running(True)
        self.clear_log()
        self.status.set("Looking for songs…")
        self.progress.configure(value=0, maximum=1)
        opts = core.Options(dry_run=self.dry_run.get(), synced_only=self.synced_only.get(),
                            overwrite=self.overwrite.get())

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
        elif event == "error":
            self.counts["error"] += 1
            self.append(f"error        {os.path.basename(d['path'])}: {d['message']}", "error")
            self.status.set("LRCLIB isn't answering — waiting and retrying…")
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
        c = self.counts
        parts = [f"{c['synced']} synced", f"{c['plain']} plain", f"{c['not found']} not found"]
        if c["skipped"]:
            parts.append(f"{c['skipped']} already had lyrics")
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
