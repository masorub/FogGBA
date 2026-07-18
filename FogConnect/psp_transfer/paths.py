"""Resolve app / resource paths for source runs and PyInstaller builds."""

from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    """Writable directory next to the exe (or project root when running from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Bundled assets directory (PyInstaller _MEIPASS or project root)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent
