"""Resolves bundled resource paths, both in dev and when frozen by PyInstaller."""
import os
import sys


def resource_path(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, relative)
