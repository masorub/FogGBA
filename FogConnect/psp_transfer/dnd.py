from __future__ import annotations

import ctypes
import platform
import queue
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Iterable, Optional

# Из WndProc сюда — без вызовов Tk (иначе вылет)
_DROP_QUEUE: queue.Queue = queue.Queue()
_HOOK: dict = {"n": 0, "poll": False, "hwnd": 0}

LRESULT = ctypes.c_ssize_t
UINT = ctypes.c_uint
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t


def decode_drop_path(raw) -> str:
    if isinstance(raw, Path):
        return str(raw)
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bytes):
        for enc in ("utf-8", "mbcs", "cp1251", "cp866"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="surrogateescape")
    return str(raw)


def normalize_drop_paths(files: Iterable) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for raw in files or []:
        text = decode_drop_path(raw).strip().strip('"')
        if not text:
            continue
        path = Path(text)
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def get_clipboard_file_paths() -> list[Path]:
    if sys.platform != "win32":
        return []
    CF_HDROP = 15
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    if not user32.IsClipboardFormatAvailable(CF_HDROP):
        return []
    if not user32.OpenClipboard(None):
        return []
    try:
        hdrop = user32.GetClipboardData(CF_HDROP)
        if not hdrop:
            return []
        count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        buf = ctypes.create_unicode_buffer(32768)
        paths: list[str] = []
        for i in range(int(count)):
            n = shell32.DragQueryFileW(hdrop, i, buf, 32768)
            if n:
                paths.append(buf.value)
        return normalize_drop_paths(paths)
    finally:
        user32.CloseClipboard()


def _enqueue(files) -> None:
    try:
        paths = normalize_drop_paths(files)
        if paths:
            _DROP_QUEUE.put(paths)
    except Exception:
        pass


def _hook_tk_hwnd(hwnd: int) -> bool:
    """
    Подмена WndProc главного окна Tk (как windnd), но:
    - в колбэке только очередь путей
    - никаких widget.after / messagebox / Tk API
    """
    if not hwnd or _HOOK.get("hwnd") == hwnd:
        return bool(_HOOK.get("hwnd"))

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32

    if platform.architecture()[0] == "32bit":
        GetWindowLong = user32.GetWindowLongW
        SetWindowLong = user32.SetWindowLongW
        argtype = wintypes.DWORD
    else:
        # Именно *A-варианты — так работает windnd с Tk на x64
        GetWindowLong = user32.GetWindowLongPtrA
        SetWindowLong = user32.SetWindowLongPtrA
        argtype = ctypes.c_uint64

    prototype = ctypes.WINFUNCTYPE(argtype, argtype, argtype, argtype, argtype)
    WM_DROPFILES = 0x233
    GWL_WNDPROC = -4
    GWL_EXSTYLE = -20
    WS_EX_ACCEPTFILES = 0x10

    idx = _HOOK["n"]
    if idx >= 8:
        return False
    _HOOK["n"] = idx + 1
    old_key = f"old_{idx}"
    new_key = f"new_{idx}"

    shell32.DragQueryFileW.argtypes = [argtype, argtype, wintypes.LPWSTR, argtype]
    shell32.DragQueryFileW.restype = argtype
    shell32.DragFinish.argtypes = [argtype]
    shell32.DragAcceptFiles.argtypes = [argtype, wintypes.BOOL]

    def py_drop_func(hw, msg, wp, lp):
        # КРИТИЧНО: никаких вызовов Tk отсюда
        try:
            if msg == WM_DROPFILES:
                try:
                    count = int(shell32.DragQueryFileW(argtype(wp), 0xFFFFFFFF, None, 0))
                    buf = ctypes.create_unicode_buffer(32768)
                    files = []
                    for i in range(count):
                        shell32.DragQueryFileW(argtype(wp), i, buf, 32768)
                        if buf.value:
                            files.append(buf.value)
                    _enqueue(files)
                except Exception:
                    pass
                try:
                    shell32.DragFinish(argtype(wp))
                except Exception:
                    pass
                return argtype(0)
        except Exception:
            pass
        try:
            return user32.CallWindowProcW(
                *map(argtype, (_HOOK.get(old_key) or 0, hw, msg, wp, lp))
            )
        except Exception:
            return argtype(0)

    _HOOK[old_key] = None
    _HOOK[new_key] = prototype(py_drop_func)

    try:
        shell32.DragAcceptFiles(hwnd, True)
        try:
            if platform.architecture()[0] == "64bit":
                get_ex = user32.GetWindowLongPtrW
                set_ex = user32.SetWindowLongPtrW
            else:
                get_ex = user32.GetWindowLongW
                set_ex = user32.SetWindowLongW
            ex = get_ex(hwnd, GWL_EXSTYLE) or 0
            set_ex(hwnd, GWL_EXSTYLE, int(ex) | WS_EX_ACCEPTFILES)
        except Exception:
            pass

        old = GetWindowLong(hwnd, GWL_WNDPROC)
        if not old:
            return False
        _HOOK[old_key] = old
        SetWindowLong(hwnd, GWL_WNDPROC, _HOOK[new_key])
        _HOOK["hwnd"] = hwnd
        return True
    except Exception:
        return False


def enable_file_drop(widget, on_paths: Callable[[list[Path]], None]) -> bool:
    """DnD прямо на главное окно программы (без отдельного окошка)."""
    if sys.platform != "win32":
        return False

    last = {"t": 0.0, "sig": ""}

    def _deliver(paths: list[Path]) -> None:
        if not paths:
            return
        sig = "|".join(str(p).lower() for p in paths)
        now = time.monotonic()
        if sig == last["sig"] and (now - last["t"]) < 0.35:
            return
        last["sig"] = sig
        last["t"] = now
        try:
            on_paths(paths)
        except Exception:
            pass

    def _poll() -> None:
        try:
            while True:
                paths = _DROP_QUEUE.get_nowait()
                _deliver(paths)
        except queue.Empty:
            pass
        try:
            widget.after(80, _poll)
        except Exception:
            pass

    hooked = False
    try:
        widget.update_idletasks()
        hwnd = int(widget.winfo_id())
        hooked = _hook_tk_hwnd(hwnd)
    except Exception:
        hooked = False

    if not _HOOK["poll"]:
        _HOOK["poll"] = True
        try:
            widget.after(80, _poll)
        except Exception:
            pass

    return hooked


def bind_clipboard_paste(widget, on_paths: Callable[[list[Path]], None]) -> None:
    def _paste(_event=None):
        try:
            paths = get_clipboard_file_paths()
            if paths:
                on_paths(paths)
        except Exception:
            pass
        return "break"

    try:
        widget.bind_all("<Control-v>", _paste)
        widget.bind_all("<Control-V>", _paste)
    except Exception:
        pass


def destroy_drop_window() -> None:
    """Совместимость: отдельного окна больше нет."""
    return


def ensure_dnd_hint() -> Optional[str]:
    if sys.platform != "win32":
        return "Drag-and-drop поддерживается только на Windows."
    return None


ensure_windnd_hint = ensure_dnd_hint
