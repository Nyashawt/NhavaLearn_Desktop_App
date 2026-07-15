"""Tiny in-process media server.

pywebview serves the UI over a local HTTP server, and an http:// page cannot
load file:// resources — so lesson media is served from here instead.
Bottle is already a pywebview dependency, and its static_file handles HTTP
Range requests, which <video> needs for seeking.

Routes:
  /media/<path>   — lesson media AND the GeoGebra runtime tree (nested paths)
  /ggb/<filename> — a generated same-origin viewer page that boots the
                    GeoGebra runtime and loads the given .ggb file. Same-origin
                    matters: the runtime fetches the .ggb via XHR, which would
                    be CORS-blocked if the viewer lived on the UI's server.

The port is ephemeral (chosen by the OS at startup); stored lesson HTML never
bakes the port in — the UI rebuilds media URLs from the current base on load.
"""
import os
import threading
import urllib.parse
from wsgiref.simple_server import WSGIRequestHandler, make_server

import bottle

from . import db

_port = None

GGB_RUNTIME_DIR = "geogebra"  # under the media root


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):  # keep the console clean
        pass


def media_root() -> str:
    return os.path.join(db.data_dir(), "media")


def find_ggb_runtime():
    """Locate deployggb.js inside the installed runtime tree.

    Returns (deploy_url_path, codebase_url_path) relative to /media/, or None.
    The official Math Apps Bundle nests things one level deep (a 'GeoGebra'
    folder), but we search rather than assume, in case the layout changes.
    """
    base = os.path.join(media_root(), GGB_RUNTIME_DIR)
    if not os.path.isdir(base):
        return None
    for dirpath, _dirs, files in os.walk(base):
        if "deployggb.js" in files:
            rel_dir = os.path.relpath(dirpath, media_root()).replace(os.sep, "/")
            deploy = f"/media/{rel_dir}/deployggb.js"
            codebase_fs = os.path.join(dirpath, "HTML5", "5.0", "web3d")
            codebase = f"/media/{rel_dir}/HTML5/5.0/web3d/" if os.path.isdir(codebase_fs) else None
            return (deploy, codebase)
    return None


_GGB_VIEWER = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>GeoGebra</title>
<style>html,body{{margin:0;height:100%;background:#fff;overflow:hidden}}</style>
<script src="{deploy}"></script></head>
<body><div id="ggb"></div>
<script>
var applet = new GGBApplet({{
  filename: "/media/{ggb}",
  showToolBar: true, showAlgebraInput: false, showMenuBar: false,
  enableShiftDragZoom: true, showFullscreenButton: false,
  width: window.innerWidth, height: window.innerHeight
}}, true);
{codebase_line}
window.addEventListener("load", function() {{ applet.inject("ggb"); }});
window.addEventListener("resize", function() {{
  var a = window.ggbApplet;
  if (a && a.setSize) a.setSize(window.innerWidth, window.innerHeight);
}});
</script></body></html>"""

_GGB_MISSING = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
html,body{margin:0;height:100%;display:flex;align-items:center;justify-content:center;
font-family:'Segoe UI',system-ui,sans-serif;color:#64748B;background:#fff;text-align:center}
</style></head><body><div>
<div style="font-size:40px">&#9881;</div>
<h2 style="color:#0F172A">GeoGebra runtime not installed</h2>
<p>Ask your administrator to install it:<br>Admin &rarr; Simulations &rarr; Install GeoGebra runtime.</p>
</div></body></html>"""


def start() -> int:
    """Start the server once; return its port."""
    global _port
    if _port:
        return _port

    root = media_root()
    app = bottle.Bottle()

    @app.get("/media/<filepath:path>")
    def _serve(filepath):
        return bottle.static_file(filepath, root=root)

    @app.get("/ggb/<filename>")
    def _ggb(filename):
        runtime = find_ggb_runtime()
        if not runtime:
            return _GGB_MISSING
        deploy, codebase = runtime
        codebase_line = (
            'applet.setHTML5Codebase("' + codebase + '");' if codebase else ""
        )
        return _GGB_VIEWER.format(
            deploy=deploy,
            ggb=urllib.parse.quote(filename),
            codebase_line=codebase_line,
        )

    server = make_server("127.0.0.1", 0, app, handler_class=_QuietHandler)
    _port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return _port


def base_url() -> str:
    return f"http://127.0.0.1:{start()}/media/"


def ggb_base_url() -> str:
    return f"http://127.0.0.1:{start()}/ggb/"
