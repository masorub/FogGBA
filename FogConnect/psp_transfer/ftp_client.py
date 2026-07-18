from __future__ import annotations

import ftplib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


ProgressCb = Callable[[str, int, int], None]


@dataclass
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0


class PspFtpError(Exception):
    pass


class PspFtpClient:
    """FTP-клиент с учётом особенностей PSP (CMFileManager / PSP-FTPD)."""

    def __init__(
        self,
        host: str,
        port: int = 1337,
        user: str = "anonymous",
        password: str = "",
        passive: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.host = host.strip()
        self.port = int(port)
        self.user = user or "anonymous"
        self.password = password or ""
        self.passive = passive
        self.timeout = timeout
        self._ftp: Optional[ftplib.FTP] = None
        self.cwd = "/ms0:"

    @property
    def connected(self) -> bool:
        return self._ftp is not None

    def connect(self) -> str:
        self.close()
        ftp = ftplib.FTP()
        ftp.connect(self.host, self.port, timeout=self.timeout)
        ftp.login(self.user, self.password)
        ftp.set_pasv(self.passive)
        # PSP часто отдаёт пустой корень — сразу заходим на Memory Stick
        welcome = ftp.getwelcome() or ""
        self._ftp = ftp
        for candidate in ("/ms0:", "ms0:", "/"):
            try:
                ftp.cwd(candidate)
                self.cwd = self._pwd()
                break
            except ftplib.all_errors:
                continue
        return welcome

    def close(self) -> None:
        if self._ftp is not None:
            try:
                self._ftp.quit()
            except ftplib.all_errors:
                try:
                    self._ftp.close()
                except ftplib.all_errors:
                    pass
            self._ftp = None

    def _require(self) -> ftplib.FTP:
        if self._ftp is None:
            raise PspFtpError("Нет подключения к PSP")
        return self._ftp

    def _pwd(self) -> str:
        ftp = self._require()
        try:
            return ftp.pwd()
        except ftplib.all_errors:
            return self.cwd

    def chdir(self, path: str) -> str:
        ftp = self._require()
        path = path.strip()
        if not path:
            return self.cwd
        # Нормализация путей вида ms0:/ISO
        if path.startswith("ms0:") and not path.startswith("/"):
            path = "/" + path
        if path.startswith("ef0:") and not path.startswith("/"):
            path = "/" + path
        if path.startswith("disc0:") and not path.startswith("/"):
            path = "/" + path
        try:
            ftp.cwd(path)
        except ftplib.all_errors as exc:
            raise PspFtpError(f"Не удалось открыть {path}: {exc}") from exc
        self.cwd = self._pwd()
        return self.cwd

    def parent_dir(self) -> str:
        cur = self.cwd.rstrip("/")
        if cur in ("/ms0:", "ms0:", "/ef0:", "ef0:", "/disc0:", "disc0:", "/", ""):
            return self.cwd
        # /ms0:/ISO/foo -> /ms0:/ISO
        if ":" in cur:
            drive, _, rest = cur.partition(":")
            if rest.startswith("/"):
                rest = rest[1:]
            parts = [p for p in rest.split("/") if p]
            if not parts:
                return cur if cur.startswith("/") else f"/{cur}"
            parts.pop()
            nxt = f"{drive}:/" + "/".join(parts) if parts else f"{drive}:"
            if not nxt.startswith("/"):
                nxt = "/" + nxt
            return self.chdir(nxt)
        parent = str(Path(cur).parent).replace("\\", "/")
        return self.chdir(parent if parent else "/")

    def list_dir(self, path: Optional[str] = None) -> list[RemoteEntry]:
        ftp = self._require()
        if path:
            self.chdir(path)
        entries: list[RemoteEntry] = []
        # MLSD предпочтительнее, но на PSP часто нет — fallback на LIST/NLST
        try:
            for name, facts in ftp.mlsd():
                if name in (".", ".."):
                    continue
                is_dir = facts.get("type") == "dir"
                size = int(facts.get("size") or 0)
                entries.append(
                    RemoteEntry(
                        name=name,
                        path=self._join(self.cwd, name),
                        is_dir=is_dir,
                        size=size,
                    )
                )
        except (*ftplib.all_errors, ValueError, TypeError):
            entries = self._list_fallback()

        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def _list_fallback(self) -> list[RemoteEntry]:
        ftp = self._require()
        raw: list[str] = []
        try:
            ftp.retrlines("LIST", raw.append)
        except ftplib.all_errors:
            names: list[str] = []
            ftp.retrlines("NLST", names.append)
            return [
                RemoteEntry(name=n, path=self._join(self.cwd, n), is_dir=False, size=0)
                for n in names
                if n not in (".", "..") and n.strip()
            ]

        out: list[RemoteEntry] = []
        for line in raw:
            parsed = self._parse_list_line(line)
            if parsed:
                out.append(parsed)
        return out

    def _parse_list_line(self, line: str) -> Optional[RemoteEntry]:
        line = line.strip()
        if not line:
            return None
        # Unix-like: drwxr-xr-x ... name
        parts = line.split(maxsplit=8)
        if len(parts) >= 9 and parts[0][0] in ("d", "-", "l"):
            name = parts[8]
            if name in (".", ".."):
                return None
            is_dir = parts[0].startswith("d") or name.endswith(":")
            try:
                size = int(parts[4])
            except ValueError:
                size = 0
            # CMFileManager иногда отдаёт "Jan 0 0000 ms0:" как имя
            if " " in name and (":" in name or name.endswith(":")):
                # уже разобрано
                pass
            return RemoteEntry(
                name=name.rstrip("/"),
                path=self._join(self.cwd, name.rstrip("/")),
                is_dir=is_dir,
                size=size,
            )
        # Простая строка-имя
        name = line.rstrip("/")
        if name in (".", ".."):
            return None
        return RemoteEntry(name=name, path=self._join(self.cwd, name), is_dir=False, size=0)

    @staticmethod
    def _join(base: str, name: str) -> str:
        base = base.rstrip("/")
        name = name.lstrip("/")
        if not base:
            return "/" + name
        return f"{base}/{name}"

    def download(
        self,
        remote_path: str,
        local_path: str | Path,
        progress: Optional[ProgressCb] = None,
    ) -> None:
        ftp = self._require()
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        size = self._size(remote_path)
        done = 0

        def _cb(chunk: bytes) -> None:
            nonlocal done
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(remote_path, done, size)

        with local_path.open("wb") as f:
            ftp.retrbinary(f"RETR {remote_path}", _cb, blocksize=32 * 1024)

    def upload(
        self,
        local_path: str | Path,
        remote_path: Optional[str] = None,
        progress: Optional[ProgressCb] = None,
    ) -> str:
        ftp = self._require()
        local_path = Path(local_path)
        if not local_path.is_file():
            raise PspFtpError(f"Файл не найден: {local_path}")
        if remote_path is None:
            remote_path = self._join(self.cwd, local_path.name)
        size = local_path.stat().st_size
        done = 0

        def _cb(chunk: bytes) -> None:
            nonlocal done
            done += len(chunk)
            if progress:
                progress(str(local_path), done, size)

        with local_path.open("rb") as f:
            ftp.storbinary(f"STOR {remote_path}", f, blocksize=32 * 1024, callback=_cb)
        return remote_path

    def download_tree(
        self,
        remote_path: str,
        local_dir: str | Path,
        progress: Optional[ProgressCb] = None,
    ) -> None:
        local_dir = Path(local_dir)
        name = remote_path.rstrip("/").split("/")[-1]
        target = local_dir / name
        target.mkdir(parents=True, exist_ok=True)
        prev = self.cwd
        try:
            self.chdir(remote_path)
            for entry in self.list_dir():
                if entry.is_dir:
                    self.download_tree(entry.path, target, progress)
                else:
                    self.download(entry.path, target / entry.name, progress)
        finally:
            try:
                self.chdir(prev)
            except PspFtpError:
                pass

    def upload_tree(
        self,
        local_path: str | Path,
        remote_dir: Optional[str] = None,
        progress: Optional[ProgressCb] = None,
    ) -> None:
        local_path = Path(local_path)
        remote_dir = remote_dir or self.cwd
        if local_path.is_file():
            prev = self.cwd
            self.chdir(remote_dir)
            try:
                self.upload(local_path, progress=progress)
            finally:
                self.chdir(prev)
            return

        dest = self._join(remote_dir, local_path.name)
        try:
            self.chdir(dest)
        except PspFtpError:
            self.makedirs(dest)
            self.chdir(dest)
        for child in sorted(local_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if child.is_dir():
                self.upload_tree(child, dest, progress)
            else:
                self.upload(child, self._join(dest, child.name), progress)

    def makedirs(self, path: str) -> None:
        ftp = self._require()
        path = path.strip()
        if ":" in path and not path.startswith("/"):
            path = "/" + path
        # Создаём по частям: /ms0:/ISO/Games
        if ":" in path:
            bare = path.lstrip("/")
            drive, _, rest = bare.partition(":")
            parts = [p for p in rest.strip("/").split("/") if p]
            cur = f"/{drive}:"
            for part in parts:
                cur = f"{cur}/{part}"
                try:
                    ftp.cwd(cur)
                except ftplib.all_errors:
                    try:
                        ftp.mkd(cur)
                        ftp.cwd(cur)
                    except ftplib.all_errors as exc:
                        raise PspFtpError(f"Не удалось создать {cur}: {exc}") from exc
            self.cwd = self._pwd()
            return
        try:
            ftp.mkd(path)
        except ftplib.all_errors as exc:
            raise PspFtpError(f"Не удалось создать {path}: {exc}") from exc

    def delete(self, path: str, is_dir: bool = False) -> None:
        ftp = self._require()
        try:
            if is_dir:
                self._rmtree(path)
            else:
                self._ftp_delete(path)
        except PspFtpError:
            raise
        except ftplib.all_errors as exc:
            if self._is_ftp_success(exc):
                return
            raise PspFtpError(f"Удаление не удалось: {exc}") from exc

    @staticmethod
    def _is_ftp_success(exc: BaseException) -> bool:
        """CMFileManager часто отвечает 226 на DELE — это успех, не ошибка."""
        msg = str(exc).strip()
        if len(msg) >= 3 and msg[:3].isdigit():
            code = int(msg[:3])
            return 200 <= code < 300
        return False

    def _ftp_delete(self, path: str) -> None:
        ftp = self._require()
        try:
            ftp.delete(path)
        except ftplib.all_errors as exc:
            if self._is_ftp_success(exc):
                return
            raise

    def _ftp_rmd(self, path: str) -> None:
        ftp = self._require()
        try:
            ftp.rmd(path)
        except ftplib.all_errors as exc:
            if self._is_ftp_success(exc):
                return
            raise

    def _rmtree(self, path: str) -> None:
        ftp = self._require()
        prev = self.cwd
        self.chdir(path)
        for entry in self.list_dir():
            if entry.is_dir:
                self._rmtree(entry.path)
            else:
                self._ftp_delete(entry.path)
        self.chdir(prev)
        self._ftp_rmd(path)

    def _size(self, remote_path: str) -> int:
        ftp = self._require()
        try:
            return int(ftp.size(remote_path) or 0)
        except (*ftplib.all_errors, TypeError):
            return 0


def format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024.0
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} TB"
