"""FogTransfer client — send ROM to FogGBA Wi-Fi Receive (port 2121)."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Callable, Optional

ProgressCb = Callable[[str, int, int], None]

FOG_PORT = 2121
FOG_BANNER = "FOGGBA 1"
FOG_MAX_SIZE = 32 * 1024 * 1024
CHUNK = 64 * 1024


class FogTransferError(Exception):
    pass


def _recv_line(sock: socket.socket, timeout: float = 30.0) -> str:
    sock.settimeout(timeout)
    buf = bytearray()
    while True:
        ch = sock.recv(1)
        if not ch:
            raise FogTransferError("Connection closed by PSP")
        if ch == b"\n":
            break
        if ch != b"\r":
            buf.extend(ch)
        if len(buf) > 512:
            raise FogTransferError("Line too long from PSP")
    return buf.decode("utf-8", errors="replace")


def probe_fog(host: str, port: int = FOG_PORT, timeout: float = 1.5) -> bool:
    """True if host answers with FogTransfer banner."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            line = _recv_line(sock, timeout=timeout)
            return line.strip().startswith("FOGGBA")
    except OSError:
        return False


def send_rom(
    host: str,
    path: Path,
    port: int = FOG_PORT,
    timeout: float = 60.0,
    progress: Optional[ProgressCb] = None,
) -> None:
    path = Path(path)
    if not path.is_file():
        raise FogTransferError(f"File not found: {path}")

    name = path.name
    lower = name.lower()
    if not (lower.endswith(".gba") or lower.endswith(".zip")):
        raise FogTransferError("Only .gba / .zip allowed")

    size = path.stat().st_size
    if size <= 0 or size > FOG_MAX_SIZE:
        raise FogTransferError(f"Bad size (max {FOG_MAX_SIZE} bytes)")

    if "/" in name or "\\" in name or ".." in name:
        raise FogTransferError("Invalid filename")

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        banner = _recv_line(sock)
        if not banner.strip().startswith("FOGGBA"):
            raise FogTransferError(f"Not FogGBA (got: {banner!r})")

        sock.sendall(f"PUT {name}\n".encode("utf-8"))
        sock.sendall(f"SIZE {size}\n".encode("utf-8"))

        reply = _recv_line(sock)
        if not reply.startswith("READY"):
            raise FogTransferError(reply or "PSP rejected transfer")

        sent = 0
        with path.open("rb") as f:
            while sent < size:
                chunk = f.read(min(CHUNK, size - sent))
                if not chunk:
                    raise FogTransferError("Unexpected EOF reading file")
                sock.sendall(chunk)
                sent += len(chunk)
                if progress:
                    progress(name, sent, size)

        final = _recv_line(sock)
        if not final.startswith("OK"):
            raise FogTransferError(final or "Transfer failed on PSP")


def scan_fog_hosts(
    subnet_prefix: str,
    port: int = FOG_PORT,
    timeout: float = 0.4,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> list[tuple[str, int]]:
    """Scan x.x.x.1-254 for FogTransfer. subnet_prefix like '192.168.1'."""
    found: list[tuple[str, int]] = []
    for i in range(1, 255):
        if cancel_check and cancel_check():
            break
        host = f"{subnet_prefix}.{i}"
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                line = _recv_line(sock, timeout=timeout)
                if line.strip().startswith("FOGGBA"):
                    found.append((host, port))
        except OSError:
            pass
    return found
