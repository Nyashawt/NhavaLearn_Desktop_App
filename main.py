"""NhavaLearn Desktop — entry point.

Single process, single installer: PyWebView renders the UI through the native
WebView2 runtime that ships with Windows 11, so there is no browser to install
and no separate server to keep running.

Run in development:  python main.py
"""
import os
import sys

import webview

from app.api import Api
from app import db


def resource_path(relative: str) -> str:
    """Resolve bundled resources both in dev and when frozen by PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def main():
    db.init_db()
    api = Api()
    api._main_window = webview.create_window(
        title="NhavaLearn",
        url=resource_path(os.path.join("ui", "index.html")),
        js_api=api,
        width=1280,
        height=800,
        min_size=(1024, 700),
    )
    # debug=True opens devtools — handy while developing, off for deployment
    webview.start(debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
