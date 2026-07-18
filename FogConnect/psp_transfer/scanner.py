from __future__ import annotations

import concurrent.futures
import ipaddress
import socket
from typing import Callable, Iterable, Optional


ProgressCb = Callable[[str], None]


def local_ipv4_addresses() -> list[str]:
    addrs: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in addrs:
                addrs.append(ip)
    except OSError:
        pass
    # Дополнительно через UDP-сокет к внешнему адресу (без реальной отправки)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127.") and ip not in addrs:
            addrs.insert(0, ip)
    except OSError:
        pass
    return addrs


def subnet_hosts(ip: str) -> list[str]:
    try:
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
    except ValueError:
        return []
    return [str(h) for h in net.hosts()]


def probe_ftp(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_network(
    ports: Iterable[int] = (1337, 21),
    timeout: float = 0.35,
    on_progress: Optional[ProgressCb] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> list[tuple[str, int]]:
    """Ищет открытые FTP-порты в /24 подсетях локальных интерфейсов."""
    found: list[tuple[str, int]] = []
    hosts: list[str] = []
    for ip in local_ipv4_addresses():
        hosts.extend(subnet_hosts(ip))
    # уникальные
    seen = set()
    uniq_hosts = []
    for h in hosts:
        if h not in seen:
            seen.add(h)
            uniq_hosts.append(h)

    ports = list(ports)
    total = max(len(uniq_hosts), 1)
    done = 0

    def check(host: str) -> list[tuple[str, int]]:
        hits = []
        for port in ports:
            if probe_ftp(host, port, timeout=timeout):
                hits.append((host, port))
        return hits

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        futures = {pool.submit(check, h): h for h in uniq_hosts}
        for fut in concurrent.futures.as_completed(futures):
            if cancel_check and cancel_check():
                for f in futures:
                    f.cancel()
                break
            done += 1
            if on_progress and done % 16 == 0:
                on_progress(f"Сканирование… {done}/{total}")
            try:
                hits = fut.result()
            except Exception:
                hits = []
            for hit in hits:
                if hit not in found:
                    found.append(hit)
                    if on_progress:
                        on_progress(f"Найдено: {hit[0]}:{hit[1]}")
    found.sort()
    return found
