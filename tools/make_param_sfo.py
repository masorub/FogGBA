#!/usr/bin/env python3
"""Build PARAM.SFO and patch FogGBA EBOOT.PBP for correct XMB title display."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

# PSF / PARAM.SFO
SFO_MAGIC = b"\x00PSF"
SFO_VERSION = 0x00000101
FMT_UTF8 = 0x0204
FMT_UINT32 = 0x0404

# PBP
PBP_MAGIC = b"\x00PBP"
PBP_VERSION = 0x00010000
PBP_SLOTS = (
    "PARAM.SFO",
    "ICON0.PNG",
    "ICON1.PMF",
    "PIC0.PNG",
    "PIC1.PNG",
    "SND0.AT3",
    "DATA.PSP",
    "DATA.PSAR",
)


def align4(n: int) -> int:
    return (n + 3) & ~3


def build_sfo(entries: list[tuple[str, int, bytes, int]]) -> bytes:
    """Build PARAM.SFO.

    entries: list of (key, fmt, value_bytes_with_null_for_strings, max_len)
    Keys must be unique; they are sorted alphabetically.
    """
    entries = sorted(entries, key=lambda e: e[0])
    n = len(entries)

    key_blob = bytearray()
    key_offsets: list[int] = []
    for key, _fmt, _val, _mlen in entries:
        key_offsets.append(len(key_blob))
        key_blob.extend(key.encode("ascii") + b"\x00")

    # Align key table end to 4 bytes before data table
    key_pad = align4(len(key_blob)) - len(key_blob)
    key_blob.extend(b"\x00" * key_pad)

    data_blob = bytearray()
    data_offsets: list[int] = []
    data_lens: list[int] = []
    for _key, fmt, val, max_len in entries:
        if len(val) > max_len:
            raise ValueError(f"{_key}: data longer than max_len")
        data_offsets.append(len(data_blob))
        data_lens.append(len(val))
        padded = bytearray(val)
        # Pad each data field to max_len, then align to 4 within the stream
        if len(padded) < max_len:
            padded.extend(b"\x00" * (max_len - len(padded)))
        data_blob.extend(padded)
        # Some packers align between entries; keep contiguous max_len blocks
        # (Sony SFO uses data_offset into a flat table of max_len-sized slots)

    header_size = 20
    index_size = 16 * n
    key_table_off = header_size + index_size
    data_table_off = key_table_off + len(key_blob)

    out = bytearray()
    out.extend(struct.pack("<4sIIII", SFO_MAGIC, SFO_VERSION, key_table_off, data_table_off, n))

    for i, (key, fmt, val, max_len) in enumerate(entries):
        out.extend(
            struct.pack(
                "<HHIII",
                key_offsets[i],
                fmt,
                data_lens[i],
                max_len,
                data_offsets[i],
            )
        )

    out.extend(key_blob)
    out.extend(data_blob)
    return bytes(out)


def utf8_field(s: str, max_len: int) -> tuple[int, bytes, int]:
    raw = s.encode("utf-8") + b"\x00"
    if len(raw) > max_len:
        raise ValueError(f"string too long for max_len={max_len}: {s!r}")
    return FMT_UTF8, raw, max_len


def uint32_field(v: int) -> tuple[int, bytes, int]:
    return FMT_UINT32, struct.pack("<I", v), 4


def make_foggba_sfo() -> bytes:
    # Keys alphabetical: BOOTABLE, CATEGORY, DISC_ID, DISC_VERSION, MEMSIZE,
    # PARENTAL_LEVEL, PSP_SYSTEM_VER, REGION, TITLE, TITLE_8
    fields: list[tuple[str, int, bytes, int]] = []

    def add(key: str, fmt_val_mlen: tuple[int, bytes, int]) -> None:
        fmt, val, mlen = fmt_val_mlen
        fields.append((key, fmt, val, mlen))

    add("BOOTABLE", uint32_field(1))
    add("CATEGORY", utf8_field("MG", 4))
    add("DISC_ID", utf8_field("FOGGBA001", 16))  # 9 chars like UCJS10041
    add("DISC_VERSION", utf8_field("1.00", 8))
    add("MEMSIZE", uint32_field(1))
    add("PARENTAL_LEVEL", uint32_field(1))
    add("PSP_SYSTEM_VER", utf8_field("1.00", 8))
    add("REGION", uint32_field(32768))
    add("TITLE", utf8_field("FogGBA", 128))
    add("TITLE_8", utf8_field("FogGBA", 128))

    return build_sfo(fields)


def parse_sfo(data: bytes) -> dict:
    magic, ver, kt, dt, nent = struct.unpack_from("<4sIIII", data, 0)
    if magic != SFO_MAGIC:
        raise ValueError(f"bad SFO magic: {magic!r}")
    result = {}
    for i in range(nent):
        ko, fmt, dlen, mlen, doff = struct.unpack_from("<HHIII", data, 20 + i * 16)
        kend = data.index(b"\x00", kt + ko)
        key = data[kt + ko : kend].decode("ascii")
        val = data[dt + doff : dt + doff + dlen]
        if fmt == FMT_UINT32:
            result[key] = {
                "fmt": fmt,
                "data_len": dlen,
                "max_len": mlen,
                "value": struct.unpack_from("<I", val)[0],
            }
        else:
            result[key] = {
                "fmt": fmt,
                "data_len": dlen,
                "max_len": mlen,
                "value": val.rstrip(b"\x00").decode("utf-8", "replace"),
                "raw": val,
            }
    return result


def parse_pbp(data: bytes) -> tuple[int, list[int], list[bytes]]:
    magic, ver = struct.unpack_from("<4sI", data, 0)
    if magic != PBP_MAGIC:
        raise ValueError(f"bad PBP magic: {magic!r}")
    offs = list(struct.unpack_from("<8I", data, 8))
    parts: list[bytes] = []
    for i in range(8):
        start = offs[i]
        end = offs[i + 1] if i < 7 else len(data)
        # Empty slots: start == end (or start == next non-empty)
        parts.append(data[start:end])
    return ver, offs, parts


def pack_pbp(parts: list[bytes], version: int = PBP_VERSION) -> bytes:
    if len(parts) != 8:
        raise ValueError("PBP needs 8 parts")
    header_size = 40  # 4 + 4 + 8*4
    offsets = []
    cursor = header_size
    for p in parts:
        offsets.append(cursor)
        cursor += len(p)
    out = bytearray()
    out.extend(struct.pack("<4sI", PBP_MAGIC, version))
    out.extend(struct.pack("<8I", *offsets))
    for p in parts:
        out.extend(p)
    return bytes(out)


def patch_eboot(src: Path, dst: Path, sfo: bytes) -> None:
    data = src.read_bytes()
    ver, _offs, parts = parse_pbp(data)
    parts[0] = sfo
    out = pack_pbp(parts, ver)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)


def verify_eboot(path: Path) -> dict:
    data = path.read_bytes()
    _ver, offs, parts = parse_pbp(data)
    sfo = parse_sfo(parts[0])
    return {
        "path": str(path),
        "size": len(data),
        "sfo_size": len(parts[0]),
        "TITLE": sfo.get("TITLE"),
        "TITLE_8": sfo.get("TITLE_8"),
        "MEMSIZE": sfo.get("MEMSIZE"),
        "DISC_ID": sfo.get("DISC_ID"),
        "CATEGORY": sfo.get("CATEGORY"),
        "keys": list(sfo.keys()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="FogGBA PARAM.SFO / EBOOT.PBP fixer")
    ap.add_argument(
        "--eboot-in",
        type=Path,
        default=None,
        help="Source EBOOT.PBP to patch (keep icon/pic/prx)",
    )
    ap.add_argument(
        "--eboot-out",
        type=Path,
        nargs="*",
        default=[],
        help="Destination EBOOT.PBP path(s)",
    )
    ap.add_argument(
        "--sfo-out",
        type=Path,
        nargs="*",
        default=[],
        help="Write standalone PARAM.SFO",
    )
    ap.add_argument("--verify", type=Path, nargs="*", default=[], help="Verify EBOOT(s)")
    ap.add_argument("--dump-sfo", type=Path, default=None, help="Dump SFO from EBOOT")
    args = ap.parse_args()

    sfo = make_foggba_sfo()

    if args.dump_sfo:
        data = args.dump_sfo.read_bytes()
        if data[:4] == PBP_MAGIC:
            _v, _o, parts = parse_pbp(data)
            info = parse_sfo(parts[0])
        else:
            info = parse_sfo(data)
        for k, v in info.items():
            print(f"{k}: {v}")
        return 0

    for p in args.sfo_out:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(sfo)
        print(f"Wrote PARAM.SFO ({len(sfo)} bytes) -> {p}")

    if args.eboot_in:
        if not args.eboot_out:
            print("--eboot-in requires --eboot-out", file=sys.stderr)
            return 1
        raw = args.eboot_in.read_bytes()
        ver, _offs, parts = parse_pbp(raw)
        parts[0] = sfo
        out = pack_pbp(parts, ver)
        for dst in args.eboot_out:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(out)
            print(f"Wrote EBOOT.PBP ({len(out)} bytes) -> {dst}")

    verify_paths = list(args.verify)
    if not verify_paths and args.eboot_out:
        verify_paths = list(args.eboot_out)

    for vp in verify_paths:
        info = verify_eboot(vp)
        print("--- verify", info["path"])
        print("  size:", info["size"], "sfo_size:", info["sfo_size"])
        print("  keys:", ", ".join(info["keys"]))
        for k in ("TITLE", "TITLE_8", "MEMSIZE", "DISC_ID", "CATEGORY"):
            e = info.get(k)
            if e is None:
                print(f"  {k}: MISSING")
            else:
                print(
                    f"  {k}: value={e['value']!r} data_len={e['data_len']} max_len={e['max_len']}"
                )

    if not (args.sfo_out or args.eboot_in or args.verify or args.dump_sfo):
        # Default: write SFO to stdout path hint
        sys.stdout.buffer.write(sfo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
