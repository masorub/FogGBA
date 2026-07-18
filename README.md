# FogGBA — Frog resurrected

**FogGBA** is a revived fork of FrogGBA 0.3.3 for PlayStation Portable.  
Author: **masorub**

![FogGBA Icon](source/res/ICON0.png)

Based on FrogGBA / TempGBA / gpSP Kai / gpSP. Original authors remain credited in source headers (Exophase, takka, Nebuleon, Prosty / tzubertowski, and others).

## Install

CFW required.

1. Download the latest release from [Releases](https://github.com/masorub/FogGBA/releases)
2. Unzip to the Memory Stick root (creates `PSP/GAME/FogGBA/`)  
   or copy the whole `FogGBA` folder to `ms0:/PSP/GAME/`
3. You need **all** of: `EBOOT.PBP`, `FogGBA.prx`, `ku_bridge.prx`, `exception.prx`
4. The emulator will search for BIOS file next to `EBOOT.PBP`
5. Put ROMs in `roms/`
6. Launch **FogGBA** from the Games menu

### Folder layout

| Folder | Purpose |
|--------|---------|
| `roms/` | GBA ROMs (`.gba`, `.zip`) |
| `save/` | saves |
| `state/` | Savestates (`.svs`) |
| `cfg/` | Per-game configs |
| `cheat/` | Cheat files |
| `overlays/` | Screen overlays (`.ovl`) |

`dir.ini` wires these paths. Screenshots go to `ms0:/PICTURE`.

## Wi-Fi Receive (FogTransfer)

Send ROMs from PC without CM File Manager:

1. On PSP: FogGBA menu → **Wi-Fi Receive** (note IP, port **2121**)
2. On PC: run the FogTransfer client (`psp-wireless-transfer`) → tab **FogGBA** → send `.gba` / `.zip`
3. File lands in `roms/`

PSP and PC must be on the same Wi-Fi network (router).

## Fixes in this fork (vs FrogGBA 0.3.3)

- Savestate load no longer Bus Errors (dynarec cache flush after load)
- Overlay pause/resume fixed (no double-free crash)
- Savestate details menu no longer crashes on button press (null trampoline / nested functions)
- LOAD ↔ SAVE indicator restored (Left/Right toggles, Circle executes)
- LOAD/SAVE label placed to the right of the save date (no overlap)
- XMB title (`PARAM.SFO`) fixed for system label next to icon
- **Wi-Fi Receive** — built-in ROM transfer (FogTransfer protocol)

## Credits

- gpSP — Exophase  
- gpSP Kai — takka  
- TempGBA — Nebuleon et al.  
- FrogGBA — Prosty / tzubertowski  
- **FogGBA** — masorub

Upstream: [tzubertowski/FrogGBA](https://github.com/tzubertowski/FrogGBA)
