# FogGBA — Frog resurrected

**FogGBA** is a revived fork of FrogGBA 0.3.3 for PlayStation Portable.  
Author: **masorub**

![FogGBA Icon](source/res/ICON0.png)

Based on FrogGBA / TempGBA / gpSP Kai / gpSP. Original authors remain credited in source headers (Exophase, takka, Nebuleon, Prosty / tzubertowski, and others).

## Install

CFW required.

1. Build from source (see [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md)) or use your own binaries
2. Copy the whole `FogGBA` folder to `ms0:/PSP/GAME/`
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

## Fixes in this fork (vs FrogGBA 0.3.3)

- Savestate load no longer Bus Errors (dynarec cache flush after load)
- Overlay pause/resume fixed (no double-free crash)
- Savestate details menu no longer crashes on button press (null trampoline / nested functions)
- LOAD ↔ SAVE indicator restored (Left/Right toggles, Circle executes)
- LOAD/SAVE label placed to the right of the save date (no overlap)
- XMB title (`PARAM.SFO`) fixed for system label next to icon

## Build

See [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md). Docker image + `build.sh`.

## Credits

- gpSP — Exophase  
- gpSP Kai — takka  
- TempGBA — Nebuleon et al.  
- FrogGBA — Prosty / tzubertowski  
- **FogGBA** — masorub

Upstream: [tzubertowski/FrogGBA](https://github.com/tzubertowski/FrogGBA)
