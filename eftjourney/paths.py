"""Locate the folders EFT writes to (logs, screenshots) and our own data folder.

Every finder can be overridden: CLI flags win, then environment variables
(EFT_LOGS_DIR, EFT_SCREENSHOTS_DIR, EFTJOURNEY_DATA), then auto-detection.
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def default_data_dir() -> Path:
    """<project>/data in a source checkout, else a per-user folder.

    Installed with pip, the package's parent is site-packages: no place for raids.
    """
    if (PROJECT_ROOT / "pyproject.toml").is_file():
        return PROJECT_ROOT / "data"
    local = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    return Path(local) / "eftjourney" if local else Path.home() / ".eftjourney"


DEFAULT_DATA_DIR = default_data_dir()

# Folder name under the user's Documents (case-insensitive on Windows)
SCREENSHOTS_SUBDIR = Path("Escape from Tarkov") / "Screenshots"

_FOLDERID_DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"
_BSG_UNINSTALL_KEYS = (
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\EscapeFromTarkov",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\EscapeFromTarkov",
)


def _reg_value(hive_name: str, subkey: str, name: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    hive = getattr(winreg, hive_name)
    try:
        with winreg.OpenKey(hive, subkey) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return str(value) if value else None


def _known_folder(guid: str) -> Path | None:
    """SHGetKnownFolderPath - follows OneDrive/known-folder redirection."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    u = uuid.UUID(guid)
    g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
    out = ctypes.c_wchar_p()
    try:
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out))
        if hr != 0 or not out.value:
            return None
        return Path(out.value)
    except (AttributeError, OSError):
        return None
    finally:
        if out:
            ctypes.windll.ole32.CoTaskMemFree(out)


def documents_dir() -> Path:
    found = _known_folder(_FOLDERID_DOCUMENTS)
    if found:
        return found
    personal = _reg_value("HKEY_CURRENT_USER", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders", "Personal")
    if personal:
        return Path(os.path.expandvars(personal))
    return Path.home() / "Documents"


def steam_libraries() -> list[Path]:
    steam = _reg_value("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath")
    if not steam:
        return []
    root = Path(steam)
    libs = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        libs.append(Path(match.group(1).replace("\\\\", "\\")))
    unique: dict[str, Path] = {}
    for lib in libs:
        unique.setdefault(os.path.normcase(str(lib)), lib)
    return list(unique.values())


def log_dir_candidates() -> list[Path]:
    candidates: list[Path] = []
    for subkey in _BSG_UNINSTALL_KEYS:
        loc = _reg_value("HKEY_LOCAL_MACHINE", subkey, "InstallLocation")
        if loc:
            candidates += [Path(loc) / "Logs", Path(loc) / "build" / "Logs"]
    for lib in steam_libraries():
        game = lib / "steamapps" / "common" / "Escape from Tarkov"
        candidates += [game / "build" / "Logs", game / "Logs"]
    return candidates


def _latest_session_mtime(logs_dir: Path) -> float:
    latest = 0.0
    try:
        for child in logs_dir.iterdir():
            if child.is_dir() and child.name.startswith("log_"):
                latest = max(latest, child.stat().st_mtime)
    except OSError:
        return 0.0
    return latest


def find_logs_dir(override: str | os.PathLike | None = None) -> Path | None:
    """The Logs folder that holds log_* session folders; the most recently used one wins."""
    explicit = override or os.environ.get("EFT_LOGS_DIR")
    if explicit:
        return Path(explicit)
    scored = [(mtime, path) for path in log_dir_candidates() if (mtime := _latest_session_mtime(path)) > 0]
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def find_screenshots_dir(override: str | os.PathLike | None = None) -> Path:
    explicit = override or os.environ.get("EFT_SCREENSHOTS_DIR")
    if explicit:
        return Path(explicit)
    return documents_dir() / SCREENSHOTS_SUBDIR


def data_dir(override: str | os.PathLike | None = None) -> Path:
    explicit = override or os.environ.get("EFTJOURNEY_DATA")
    return Path(explicit) if explicit else DEFAULT_DATA_DIR
