"""Double-click to start Lyrics Downloader (Windows, or anywhere Python is installed).

.pyw files run with pythonw — no console window. Needs Python 3.8+ from python.org (or
`winget install Python.Python.3.13`). The Windows zip on the Releases page bundles `tinytag`
next to this file, so nothing else has to be installed.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import lyrics_downloader_gui
except ImportError as e:  # e.g. Python without Tk
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, f"Lyrics Downloader can't start:\n\n{e}\n\n"
                                        "Install Python from python.org (with Tcl/Tk).",
                                     "Lyrics Downloader", 0x10) if os.name == "nt" else print(e)
    sys.exit(1)

lyrics_downloader_gui.main()
