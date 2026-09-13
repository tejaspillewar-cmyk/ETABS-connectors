"""User-writable locations, saved settings, and ETABS discovery.

Deliberately free of tkinter and of any third-party import at module level:
the crash handler in etabs_gui.pyw imports this before Tk is up, and it has
to keep working on a machine where the dependencies were never installed.
psutil is imported lazily inside the one function that needs it.
"""

import json
import os
import re
import sys
from pathlib import Path

APP_NAME = "ETABSLiveConnector"

# ETABS.exe lives in "<Program Files>\Computers and Structures\ETABS <ver>\".
CSI_VENDOR_DIR = "Computers and Structures"
ETABS_EXE = "ETABS.exe"


# ── Locations ────────────────────────────────────────────────────────────────

def app_dir() -> Path:
    """Per-user data directory. Never needs admin rights."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    return (Path(base) if base else Path.home()) / APP_NAME


def logs_dir() -> Path:
    return app_dir() / "logs"


def settings_path() -> Path:
    return app_dir() / "settings.json"


def ensure_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


# ── Settings ─────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    """Read settings.json. A missing or corrupt file yields defaults, never
    an exception -- this runs on the startup path."""
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> bool:
    """Write settings.json atomically, so an interrupted write cannot leave
    a truncated file behind that would then fail to parse on next start."""
    if not ensure_dir(app_dir()):
        return False
    target = settings_path()
    tmp = target.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, target)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def update_setting(key: str, value) -> bool:
    data = load_settings()
    if data.get(key) == value:
        return True
    data[key] = value
    data.setdefault("version", 1)
    return save_settings(data)


# ── ETABS discovery ──────────────────────────────────────────────────────────

def _version_key(name: str):
    """Sort key from a folder name like 'ETABS 23' or 'ETABS 21.2.0'."""
    nums = re.findall(r"\d+", name)
    return tuple(int(n) for n in nums) if nums else (0,)


def _program_dirs():
    seen = []
    for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(var)
        if value and value not in seen:
            seen.append(value)
    return [Path(p) for p in seen]


def detect_etabs() -> list:
    """Every installed ETABS.exe we can find, newest version first.

    Checks for the executable itself rather than just the versioned folder:
    uninstalling ETABS can leave the folder behind holding only CSiLicensing,
    and offering that hollow directory would be worse than finding nothing.
    """
    found = []
    for program_dir in _program_dirs():
        vendor = program_dir / CSI_VENDOR_DIR
        if not vendor.is_dir():
            continue
        try:
            candidates = list(vendor.glob("ETABS*"))
        except OSError:
            continue
        for folder in candidates:
            exe = folder / ETABS_EXE
            if exe.is_file() and exe not in found:
                found.append(exe)
    found.sort(key=lambda p: _version_key(p.parent.name), reverse=True)
    return found


def running_etabs() -> list:
    """Running ETABS processes as dicts: pid, name, exe, access_denied.

    A process whose name we can read but whose exe path we cannot is the
    signature of an elevation mismatch -- ETABS is running as administrator
    and we are not. Callers use access_denied to say so in plain words
    instead of letting COM fail with an opaque error later.
    """
    try:
        import psutil
    except ImportError:
        return []

    results = []
    for proc in psutil.process_iter(["pid", "name"]):
        name = proc.info.get("name") or ""
        if "ETABS" not in name.upper():
            continue
        entry = {"pid": proc.info["pid"], "name": name,
                 "exe": None, "access_denied": False}
        try:
            entry["exe"] = proc.exe()
        except psutil.AccessDenied:
            entry["access_denied"] = True
        except (psutil.NoSuchProcess, OSError):
            continue
        results.append(entry)
    return results


def resolve_etabs_path() -> str:
    """Best known path to ETABS.exe, or "" if we genuinely cannot find one.

    Order matters: a running instance is authoritative (it is the binary
    actually in use, so it cannot be a stale or wrong-version guess), a saved
    path is the user's own previous answer, and the filesystem scan is only a
    guess. The caller offers a file picker when this returns "".
    """
    for proc in running_etabs():
        if proc["exe"] and os.path.isfile(proc["exe"]):
            return proc["exe"]

    saved = load_settings().get("etabs_path")
    if saved and os.path.isfile(saved):
        return saved

    detected = detect_etabs()
    return str(detected[0]) if detected else ""


# ── Diagnostics ──────────────────────────────────────────────────────────────

def is_elevated() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
