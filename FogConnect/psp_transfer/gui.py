"""FogConnect — dark horror UI matching the brand mockup."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from . import config
from .fog_transfer import FogTransferError, FOG_PORT, probe_fog, send_rom
from .paths import resource_dir
from .scanner import local_ipv4_addresses, subnet_hosts

ASSETS = resource_dir() / "assets"

HELP_TEXT = """\
FogConnect — FogGBA Wi-Fi ROM transfer
======================================

РУССКИЙ
-------
1. PSP и ПК в одной Wi-Fi сети (роутер).
2. На PSP: FogGBA → меню → Wi-Fi Receive (IP, порт 2121).
3. Кликните IP:порт в карточке DEVICE (или Find).
4. Check / Save → Drag & drop .gba / .zip → Send to PSP.
5. Файл появится в roms/ на PSP.

ENGLISH
-------
1. PSP and PC on the same Wi-Fi network (router).
2. On PSP: FogGBA → menu → Wi-Fi Receive (IP, port 2121).
3. Click IP:port inside the DEVICE card (or press Find).
4. Check / Save → Drag & drop .gba / .zip → Send to PSP.
5. File appears in roms/ on the PSP.
"""

BG = "#0a0a0a"
CARD = "#161616"
PANEL = "#121212"
TEXT = "#f2f2f2"
MUTED = "#6a6a6a"
DIM = "#9b9b9b"
GREEN = "#3dff6e"
LINE = "#2e2e2e"


def _round_rect(draw: ImageDraw.ImageDraw, xy, r: int, fill=None, outline=None, width: int = 1):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=fill, outline=outline, width=width)


def _dashed_round_rect(img: Image.Image, xy, r: int, color="#555555", width: int = 2, dash: int = 8, gap: int = 6):
    """Draw dashed rounded rectangle by masking."""
    x1, y1, x2, y2 = xy
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rounded_rectangle([x1, y1, x2, y2], radius=r, outline=color, width=width)
    # approximate dash by punching gaps along edges is hard; solid soft outline is fine
    # add a second thinner inset for depth
    d.rounded_rectangle([x1 + 1, y1 + 1, x2 - 1, y2 - 1], radius=max(1, r - 1), outline=color + "55" if False else color, width=1)
    return Image.alpha_composite(img.convert("RGBA"), overlay)


class FogConnectApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FogConnect")
        self.geometry("960x640")
        self.minsize(920, 600)
        self.configure(bg=BG)
        self.resizable(False, False)

        self.cfg = config.load()
        self._ui_queue: queue.Queue = queue.Queue()
        self._cancel_scan = False
        self._busy = False
        self._connected = False
        self._pending: list[Path] = []
        self._photos: list[ImageTk.PhotoImage] = []

        self.host_var = tk.StringVar(value=str(self.cfg.get("host", "192.168.1.11")))
        self.port_var = tk.StringVar(value=str(self.cfg.get("fog_port", FOG_PORT)))
        self.addr_var = tk.StringVar(value=f"{self.host_var.get()}:{self.port_var.get()}")
        self.status_var = tk.StringVar(value="WAITING FOR PSP")
        self.file_var = tk.StringVar(value="")

        self._build()
        self.after(100, self._poll_queue)
        self.after(300, self._try_enable_drop)
        self.after(500, self.check_fog_silent)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _photo(self, path: Path, size: tuple[int, int] | None = None) -> ImageTk.PhotoImage | None:
        if not path.exists():
            return None
        try:
            im = Image.open(path).convert("RGBA")
            if size:
                im = im.resize(size, Image.Resampling.LANCZOS)
            ph = ImageTk.PhotoImage(im)
            self._photos.append(ph)
            return ph
        except OSError:
            return None

    def _photo_from(self, im: Image.Image) -> ImageTk.PhotoImage:
        ph = ImageTk.PhotoImage(im)
        self._photos.append(ph)
        return ph

    def _make_device_card(self, w: int = 480, h: int = 72) -> Image.Image:
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        _round_rect(d, (0, 0, w - 1, h - 1), 16, fill=(22, 22, 22, 255), outline=(40, 40, 40, 255), width=1)
        # subtle top highlight
        d.rounded_rectangle([1, 1, w - 2, h // 2], radius=14, fill=(28, 28, 28, 40))
        return im

    def _make_send_btn(self, w: int = 480, h: int = 52, hover: bool = False) -> Image.Image:
        return self._make_chunky_pill(w, h, pressed=False, hover=hover)

    def _make_chunky_pill(
        self,
        w: int,
        h: int,
        *,
        label: str | None = None,
        pressed: bool = False,
        hover: bool = False,
        font_size: int = 12,
    ) -> Image.Image:
        """Dark 3D stadium button: thick side wall + raised face + rim highlight."""
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        depth = max(4, h // 7)
        r = (h - depth) // 2

        # Side wall / thickness (visible under the face)
        wall = (28, 28, 28, 255)
        face = (42, 42, 42, 255) if hover else (34, 34, 34, 255)
        rim = (78, 78, 78, 255)
        if pressed:
            face = (28, 28, 28, 255)
            wall = (18, 18, 18, 255)

        # Bottom body (wall) — full pill
        d.rounded_rectangle([0, depth // 2, w - 1, h - 1], radius=r + 1, fill=wall)
        # Raised face
        face_box = [0, 0 if not pressed else depth // 2, w - 1, h - 1 - depth]
        if pressed:
            face_box = [0, depth // 2, w - 1, h - 1 - depth // 2]
        d.rounded_rectangle(face_box, radius=r, fill=face)
        # Soft top sheen
        fx1, fy1, fx2, fy2 = face_box
        sheen_h = max(2, (fy2 - fy1) // 2)
        sheen = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sheen)
        sd.rounded_rectangle([fx1 + 1, fy1 + 1, fx2 - 1, fy1 + sheen_h], radius=max(1, r - 2), fill=(255, 255, 255, 18))
        im = Image.alpha_composite(im, sheen)
        d = ImageDraw.Draw(im)
        # Thin rim highlight along top of face
        d.arc([fx1, fy1, fx1 + 2 * r, fy1 + 2 * r], 180, 270, fill=rim, width=1)
        d.line([(fx1 + r, fy1), (fx2 - r, fy1)], fill=rim, width=1)
        d.arc([fx2 - 2 * r, fy1, fx2, fy1 + 2 * r], 270, 360, fill=rim, width=1)

        if label:
            from PIL import ImageFont

            try:
                f = ImageFont.truetype("C:/Windows/Fonts/bahnschrift.ttf", font_size)
            except OSError:
                f = ImageFont.load_default()
            cy = (fy1 + fy2) // 2
            d.text((w // 2, cy), label, fill=(236, 236, 236, 255), font=f, anchor="mm")
        return im

    def _pill_button(self, parent: tk.Frame, text: str, cmd, *, side=tk.LEFT, width: int = 108) -> tk.Label:
        h = 38
        normal = self._make_chunky_pill(width, h, label=text)
        pressed = self._make_chunky_pill(width, h, label=text, pressed=True)
        ph_n = self._photo_from(normal)
        ph_p = self._photo_from(pressed)
        pad = (0, 10) if side == tk.LEFT else (10, 0)
        lbl = tk.Label(parent, image=ph_n, bg=BG, bd=0, cursor="hand2")
        lbl.pack(side=side, padx=pad)
        lbl._pill_n = ph_n  # noqa: SLF001 — keep refs
        lbl._pill_p = ph_p

        def down(_e=None):
            lbl.configure(image=ph_p)

        def up(_e=None):
            lbl.configure(image=ph_n)
            cmd()

        lbl.bind("<ButtonPress-1>", down)
        lbl.bind("<ButtonRelease-1>", up)
        lbl.bind("<Leave>", lambda _e: lbl.configure(image=ph_n))
        return lbl

    def _make_drop_zone(self, w: int = 480, h: int = 200) -> Image.Image:
        im = Image.new("RGBA", (w, h), (18, 18, 18, 255))
        d = ImageDraw.Draw(im)
        # dashed-like rounded border using many short arcs approx via dotted outline
        color = (70, 70, 70, 255)
        _round_rect(d, (6, 6, w - 7, h - 7), 18, fill=(18, 18, 18, 255), outline=color, width=2)
        # simulate dashes by overlaying bg color gaps on edges
        gap = ImageDraw.Draw(im)
        for x in range(24, w - 24, 18):
            gap.rectangle([x, 5, x + 8, 9], fill=(18, 18, 18, 255))
            gap.rectangle([x, h - 9, x + 8, h - 5], fill=(18, 18, 18, 255))
        for y in range(24, h - 24, 18):
            gap.rectangle([5, y, 9, y + 8], fill=(18, 18, 18, 255))
            gap.rectangle([w - 9, y, w - 5, y + 8], fill=(18, 18, 18, 255))
        return im

    def _build(self) -> None:
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill=tk.BOTH, expand=True)

        # LEFT art
        left = tk.Frame(shell, bg=BG, width=400)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        face = self._photo(ASSETS / "face_panel.png", (400, 620))
        if face:
            tk.Label(left, image=face, bg=BG, bd=0).place(x=0, y=10)
        # bottom mist
        mist = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
        md = ImageDraw.Draw(mist)
        for i in range(120):
            a = int(i / 120 * 220)
            md.line([(0, i), (400, i)], fill=(10, 10, 10, a))
        mph = self._photo_from(mist)
        tk.Label(left, image=mph, bg=BG, bd=0).place(x=0, y=520)

        # RIGHT
        right = tk.Frame(shell, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 36), pady=32)

        # Title + tagline as rendered images for smooth fonts
        title = self._photo(ASSETS / "title_fogconnect.png")
        if title:
            tk.Label(right, image=title, bg=BG, bd=0).pack(anchor=tk.W)
        else:
            row = tk.Frame(right, bg=BG)
            row.pack(anchor=tk.W)
            tk.Label(row, text="Fog", fg=TEXT, bg=BG, font=("Bahnschrift", 30, "bold")).pack(side=tk.LEFT)
            tk.Label(row, text="Connect", fg=TEXT, bg=BG, font=("Bahnschrift", 30)).pack(side=tk.LEFT)

        tag = self._photo(ASSETS / "tagline.png")
        if tag:
            tk.Label(right, image=tag, bg=BG, bd=0).pack(anchor=tk.W, pady=(6, 28))
        else:
            tk.Label(right, text="SEND FILE. ONE TYPE. ONE WAY.", fg=MUTED, bg=BG, font=("Bahnschrift", 8)).pack(
                anchor=tk.W, pady=(4, 28)
            )

        # DEVICE — status card with inline editable IP:port
        tk.Label(right, text="DEVICE", fg=MUTED, bg=BG, font=("Bahnschrift", 8)).pack(anchor=tk.W)
        card_im = self._make_device_card(500, 76)
        self._device_card_ph = self._photo_from(card_im)

        device_wrap = tk.Canvas(right, width=500, height=76, bg=BG, highlightthickness=0, bd=0)
        device_wrap.pack(anchor=tk.W, pady=(8, 8))
        device_wrap.create_image(0, 0, image=self._device_card_ph, anchor="nw")

        psp = self._photo(ASSETS / "icon_psp.png", (44, 44))
        if psp:
            device_wrap.create_image(26, 38, image=psp, anchor="w")

        self._device_status_id = device_wrap.create_text(
            88, 28, text="WAITING FOR PSP", fill=TEXT, font=("Bahnschrift", 13, "bold"), anchor="w"
        )
        self._dot_id = device_wrap.create_oval(460, 32, 474, 46, fill="#333333", outline="")
        self._device_canvas = device_wrap

        # Editable address sits inside the card under the status line
        self.addr_entry = tk.Entry(
            device_wrap,
            textvariable=self.addr_var,
            bg="#161616",
            fg=MUTED,
            insertbackground=DIM,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            font=("Bahnschrift", 9),
            cursor="xterm",
        )
        device_wrap.create_window(88, 52, window=self.addr_entry, anchor="w", width=350, height=18)
        self.addr_entry.bind("<Return>", lambda _e: self.save_settings())
        self.addr_entry.bind("<FocusOut>", lambda _e: self._apply_addr_field())

        actions = tk.Frame(right, bg=BG)
        actions.pack(anchor=tk.W, fill=tk.X, pady=(2, 16))
        self._pill_button(actions, "CHECK", self.check_fog, width=112)
        self._pill_button(actions, "FIND", self.find_fog, width=104)
        self._pill_button(actions, "SAVE", self.save_settings, side=tk.RIGHT, width=104)

        # FILE
        tk.Label(right, text="FILE", fg=MUTED, bg=BG, font=("Bahnschrift", 8)).pack(anchor=tk.W)
        drop_im = self._make_drop_zone(500, 180)
        # composite file icon + texts onto drop
        file_icon = None
        try:
            file_icon = Image.open(ASSETS / "icon_file.png").convert("RGBA").resize((56, 56), Image.Resampling.LANCZOS)
        except OSError:
            pass
        if file_icon:
            drop_im.alpha_composite(file_icon, (222, 36))
        from PIL import ImageFont

        font_path = "C:/Windows/Fonts/bahnschrift.ttf"
        try:
            f_title = ImageFont.truetype(font_path, 15)
            f_sub = ImageFont.truetype(font_path, 11)
            f_hint = ImageFont.truetype(font_path, 9)
        except OSError:
            f_title = f_sub = f_hint = ImageFont.load_default()
        dd = ImageDraw.Draw(drop_im)
        dd.text((250, 104), "DRAG & DROP YOUR ROM HERE", fill=(180, 180, 180, 255), font=f_title, anchor="mt")
        dd.text((250, 128), "ONLY .GBA / .ZIP FILES ARE SUPPORTED", fill=(100, 100, 100, 255), font=f_hint, anchor="mt")
        self._drop_file_pos = (250, 150)
        self._drop_base = drop_im.copy()
        self._drop_ph = self._photo_from(drop_im)

        self.drop_canvas = tk.Canvas(right, width=500, height=180, bg=BG, highlightthickness=0, bd=0)
        self.drop_canvas.pack(anchor=tk.W, pady=(8, 14))
        self.drop_canvas.create_image(0, 0, image=self._drop_ph, anchor="nw")
        self._drop_name_id = self.drop_canvas.create_text(
            250, 152, text="", fill="#777777", font=("Bahnschrift", 8), anchor="center"
        )
        self.drop_canvas.bind("<Button-1>", lambda _e: self.pick_file())
        self.file_var.trace_add("write", lambda *_: self._on_file_var())

        # SEND button
        send_bg = self._make_send_btn(500, 54)
        send_icon = None
        try:
            send_icon = Image.open(ASSETS / "icon_send.png").convert("RGBA")
        except OSError:
            pass
        if send_icon:
            send_bg.alpha_composite(send_icon, (168, 13))
        try:
            f_btn = ImageFont.truetype(font_path, 14)
        except OSError:
            f_btn = ImageFont.load_default()
        ImageDraw.Draw(send_bg).text((210, 27), "SEND TO PSP", fill=(242, 242, 242, 255), font=f_btn, anchor="lm")
        self._send_ph = self._photo_from(send_bg)

        self.send_canvas = tk.Canvas(right, width=500, height=54, bg=BG, highlightthickness=0, bd=0, cursor="hand2")
        self.send_canvas.pack(anchor=tk.W)
        self.send_canvas.create_image(0, 0, image=self._send_ph, anchor="nw")
        self.send_canvas.bind("<Button-1>", lambda _e: self.send_to_psp())

        self.progress = tk.Canvas(right, width=500, height=3, bg=BG, highlightthickness=0)
        self.progress.pack(anchor=tk.W, pady=(12, 0))
        self._prog_fill = self.progress.create_rectangle(0, 0, 0, 3, fill=GREEN, outline="")

        # FOOTER
        foot = tk.Frame(right, bg=BG, width=500, height=48)
        foot.pack(side=tk.BOTTOM, fill=tk.X, pady=(16, 0))
        foot.pack_propagate(False)

        info = self._photo(ASSETS / "icon_info.png", (22, 22))
        logo = self._photo(ASSETS / "logo_fog.png")

        left_f = tk.Frame(foot, bg=BG)
        left_f.pack(side=tk.LEFT)
        if info:
            tk.Label(left_f, image=info, bg=BG, bd=0).pack(side=tk.LEFT)
        tk.Label(left_f, text="  ONLY .GBA / .ZIP CAN BE SENT.", fg=MUTED, bg=BG, font=("Bahnschrift", 7)).pack(
            side=tk.LEFT
        )

        right_f = tk.Frame(foot, bg=BG)
        right_f.pack(side=tk.RIGHT)
        if info:
            i = tk.Label(right_f, image=info, bg=BG, bd=0, cursor="hand2")
            i.pack(side=tk.LEFT)
            i.bind("<Button-1>", lambda _e: self.show_help())

        if logo:
            tk.Label(foot, image=logo, bg=BG, bd=0).place(relx=0.5, rely=0.5, anchor="center")

    def _on_file_var(self) -> None:
        self.drop_canvas.itemconfigure(self._drop_name_id, text=self.file_var.get())

    def _set_connected(self, ok: bool) -> None:
        self._connected = ok
        color = GREEN if ok else "#333333"
        self._device_canvas.itemconfigure(self._dot_id, fill=color)
        self._device_canvas.itemconfigure(
            self._device_status_id, text="PSP CONNECTED" if ok else "PSP OFFLINE"
        )

    def _set_progress(self, done: int, total: int) -> None:
        if total <= 0:
            return
        x = int(500 * done / total)
        self.progress.coords(self._prog_fill, 0, 0, x, 3)

    def _try_enable_drop(self) -> None:
        try:
            from .dnd import enable_file_drop

            enable_file_drop(self, self._on_files_dropped)
        except Exception:
            pass

    def _on_files_dropped(self, paths: list) -> None:
        roms = [Path(p) for p in paths if str(p).lower().endswith((".gba", ".zip"))]
        if not roms:
            messagebox.showinfo("FogConnect", "Only .gba / .zip files")
            return
        self._pending = roms
        self.file_var.set(roms[0].name if len(roms) == 1 else f"{len(roms)} files")
        self.send_to_psp()

    def _port(self) -> int:
        return int(self.port_var.get().strip() or FOG_PORT)

    def _apply_addr_field(self) -> bool:
        """Parse inline host:port from the device card into host_var / port_var."""
        raw = self.addr_var.get().strip().replace(" ", "")
        if not raw:
            return False
        if ":" in raw:
            host, _, port_s = raw.rpartition(":")
            host = host.strip()
            port_s = port_s.strip()
        else:
            host, port_s = raw, str(FOG_PORT)
        if not host:
            return False
        try:
            port = int(port_s or FOG_PORT)
        except ValueError:
            return False
        if not (1 <= port <= 65535):
            return False
        self.host_var.set(host)
        self.port_var.set(str(port))
        self.addr_var.set(f"{host}:{port}")
        return True

    def _set_addr(self, host: str, port: int | str) -> None:
        self.host_var.set(host)
        self.port_var.set(str(port))
        self.addr_var.set(f"{host}:{port}")

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "connected":
                    self._set_connected(bool(payload))
                elif kind == "status":
                    self.file_var.set(payload)
                elif kind == "progress":
                    done, total = payload
                    self._set_progress(done, total)
                elif kind == "progress_reset":
                    self._set_progress(0, 1)
                elif kind == "error":
                    self._busy = False
                    messagebox.showerror("FogConnect", payload)
                elif kind == "info":
                    self._busy = False
                    messagebox.showinfo("FogConnect", payload)
                elif kind == "scan_done":
                    self._busy = False
                    found = payload
                    if not found:
                        self._set_connected(False)
                        messagebox.showinfo(
                            "FogConnect",
                            "FogGBA not found.\n\n"
                            "RU: Откройте FogGBA → Wi-Fi Receive\n"
                            "EN: Open FogGBA → Wi-Fi Receive",
                        )
                    else:
                        host, port = found[0]
                        self._set_addr(host, port)
                        self._set_connected(True)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def show_help(self) -> None:
        win = tk.Toplevel(self)
        win.title("Help")
        win.geometry("520x400")
        win.configure(bg=BG)
        text = tk.Text(win, wrap=tk.WORD, bg=PANEL, fg=TEXT, relief=tk.FLAT, font=("Consolas", 10), padx=14, pady=14)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", HELP_TEXT)
        text.configure(state=tk.DISABLED)

    def save_settings(self) -> None:
        if not self._apply_addr_field():
            messagebox.showerror("FogConnect", "Invalid IP:port")
            return
        self.cfg["host"] = self.host_var.get().strip()
        self.cfg["fog_port"] = self._port()
        config.save(self.cfg)
        self.check_fog()

    def check_fog_silent(self) -> None:
        if not self._apply_addr_field():
            return
        host = self.host_var.get().strip()
        try:
            port = self._port()
        except ValueError:
            return

        def work() -> None:
            self._ui_queue.put(("connected", probe_fog(host, port, timeout=1.2)))

        threading.Thread(target=work, daemon=True).start()

    def check_fog(self) -> None:
        if not self._apply_addr_field():
            messagebox.showerror("FogConnect", "Invalid IP:port")
            return
        host = self.host_var.get().strip()
        try:
            port = self._port()
        except ValueError:
            messagebox.showerror("FogConnect", "Invalid port")
            return

        def work() -> None:
            ok = probe_fog(host, port)
            self._ui_queue.put(("connected", ok))
            if ok:
                self._ui_queue.put(("info", f"PSP connected\n{host}:{port}"))
            else:
                self._ui_queue.put(
                    (
                        "error",
                        f"No reply from {host}:{port}\n\n"
                        "RU: FogGBA → Wi-Fi Receive\n"
                        "EN: Open FogGBA → Wi-Fi Receive",
                    )
                )

        threading.Thread(target=work, daemon=True).start()

    def find_fog(self) -> None:
        if self._busy:
            return
        self._apply_addr_field()
        self._busy = True
        self._device_canvas.itemconfigure(self._device_status_id, text="SCANNING…")

        def work() -> None:
            try:
                port = self._port()
            except ValueError:
                port = FOG_PORT
            hosts: list[str] = []
            for ip in local_ipv4_addresses():
                hosts.extend(subnet_hosts(ip))
            seen: set[str] = set()
            uniq = []
            for h in hosts:
                if h not in seen:
                    seen.add(h)
                    uniq.append(h)
            found: list[tuple[str, int]] = []
            for host in uniq:
                if self._cancel_scan:
                    break
                if probe_fog(host, port, timeout=0.35):
                    found.append((host, port))
            self._ui_queue.put(("scan_done", found))

        threading.Thread(target=work, daemon=True).start()

    def pick_file(self) -> None:
        paths = filedialog.askopenfilenames(
            title="ROM for FogGBA",
            filetypes=[("GBA ROM", "*.gba *.zip *.GBA *.ZIP"), ("All", "*.*")],
        )
        if not paths:
            return
        self._pending = [Path(p) for p in paths]
        self.file_var.set(self._pending[0].name if len(self._pending) == 1 else f"{len(self._pending)} files")

    def send_to_psp(self) -> None:
        if self._busy:
            return
        if not self._pending:
            self.pick_file()
            if not self._pending:
                return

        if not self._apply_addr_field():
            messagebox.showerror("FogConnect", "Invalid IP:port")
            return
        host = self.host_var.get().strip()
        try:
            port = self._port()
        except ValueError:
            messagebox.showerror("FogConnect", "Invalid port")
            return

        paths = list(self._pending)
        self._busy = True
        self._set_progress(0, 1)

        def progress(name: str, done: int, total: int) -> None:
            self._ui_queue.put(("progress", (done, total)))
            self._ui_queue.put(("status", f"Sending {name}…"))

        def work() -> None:
            try:
                for p in paths:
                    send_rom(host, p, port=port, progress=progress)
                self.cfg["host"] = host
                self.cfg["fog_port"] = port
                config.save(self.cfg)
                self._ui_queue.put(("connected", True))
                self._ui_queue.put(("progress_reset", None))
                self._ui_queue.put(("status", "Sent"))
                self._ui_queue.put(
                    (
                        "info",
                        f"Sent: {len(paths)}\n\n"
                        "RU: Файл в roms/ на PSP\n"
                        "EN: File is in roms/ on the PSP",
                    )
                )
                self._pending = []
            except (FogTransferError, OSError) as e:
                self._ui_queue.put(("progress_reset", None))
                self._ui_queue.put(("connected", False))
                self._ui_queue.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _on_close(self) -> None:
        self._cancel_scan = True
        self.destroy()


def run() -> None:
    app = FogConnectApp()
    app.mainloop()
