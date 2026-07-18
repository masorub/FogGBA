#!/bin/bash

# FogGBA Build Script (Frog resurrected)
# Compiles the FogGBA emulator for PSP

set -e

echo "=== FogGBA Build Script ==="
echo "Setting up PSP development environment..."

export PSPDEV=/usr/local/pspdev
export PATH=$PATH:$PSPDEV/bin
export PSPSDK=$PSPDEV/psp/sdk

echo "Checking PSP toolchain..."
if ! command -v psp-gcc &> /dev/null; then
    echo "ERROR: PSP toolchain not found. Make sure the Docker image was built correctly."
    exit 1
fi

echo "PSP GCC version:"
psp-gcc --version | head -1

echo "PSP SDK path: $PSPSDK"
echo "PSP DEV path: $PSPDEV"

cd source

echo "=== Building FogGBA ==="
echo "Cleaning previous build..."
make clean || true

echo "Starting compilation..."
make

if [ -f "FogGBA.prx" ] && [ -f "EBOOT.PBP" ]; then
    echo "=== BUILD SUCCESSFUL ==="
    echo "Generated files:"
    ls -la FogGBA.prx EBOOT.PBP

    # SDK pack-pbp uses TITLE max_len=8 and omits TITLE_8; patch PARAM.SFO for XMB text.
    ROOT="$(cd .. && pwd)"
    PATCH="$ROOT/tools/make_param_sfo.py"
    if command -v python3 &> /dev/null && [ -f "$PATCH" ]; then
        echo "=== Patching PARAM.SFO (TITLE/TITLE_8/MEMSIZE/DISC_ID) ==="
        python3 "$PATCH" --eboot-in EBOOT.PBP --eboot-out EBOOT.PBP \
            --sfo-out PARAM.SFO res/PARAM.SFO
    elif command -v python &> /dev/null && [ -f "$PATCH" ]; then
        echo "=== Patching PARAM.SFO (TITLE/TITLE_8/MEMSIZE/DISC_ID) ==="
        python "$PATCH" --eboot-in EBOOT.PBP --eboot-out EBOOT.PBP \
            --sfo-out PARAM.SFO res/PARAM.SFO
    else
        echo "WARNING: python3 not found or $PATCH missing."
        echo "  EBOOT may show no XMB title (TITLE max_len=8). Install python3 or run:"
        echo "  python3 tools/make_param_sfo.py --eboot-in source/EBOOT.PBP --eboot-out source/EBOOT.PBP"
        echo "  Makefile already sets PSP_LARGE_MEMORY=1 (MEMSIZE)."
    fi

    mkdir -p ../build
    cp FogGBA.prx ../build/
    cp EBOOT.PBP ../build/
    cp -f PARAM.SFO ../build/ 2>/dev/null || true

    echo "Build artifacts copied to build/ directory"
    echo "Copy BOTH EBOOT.PBP and FogGBA.prx to PSP/GAME/FogGBA/"
else
    echo "=== BUILD FAILED ==="
    echo "Expected output files not found"
    exit 1
fi
