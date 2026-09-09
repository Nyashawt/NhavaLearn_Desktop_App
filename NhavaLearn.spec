# PyInstaller build spec for NhavaLearn Desktop.
#
# Build (from repo root, inside the project's venv):
#   pyinstaller NhavaLearn.spec --noconfirm
#
# Output: dist\NhavaLearn\NhavaLearn.exe  (a folder build — llama-cpp-python's
# native binaries and the WebView2 runtime interop are safer as onedir than
# onefile, which would re-extract them to a temp dir on every launch).
#
# See build.bat for a one-command wrapper that also cleans previous builds.

import sys
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

block_cipher = None

binaries = []
binaries += collect_dynamic_libs("llama_cpp")

datas = []
datas += [("ui", "ui")]
datas += collect_data_files("llama_cpp")
# pywebview reads its own JS injection templates (webview/js/*.js) straight
# off disk relative to its __file__ at runtime (see webview/util.py's
# get_js_dir()) rather than importing them as Python code. PyInstaller only
# bundles pure-.py modules into the PYZ archive by default, so without this
# the "js" folder never lands next to the frozen module and pywebview fails
# to inject the JS API bridge — window.pywebview never fires "pywebviewready"
# and the UI hangs forever on the boot screen, with no visible error.
datas += collect_data_files("webview")

hiddenimports = [
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "llama_cpp",
    "anthropic",
    "pdfplumber",
    "docx",
    "bottle",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NhavaLearn",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="ui/app.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NhavaLearn",
)
