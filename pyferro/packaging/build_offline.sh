#!/usr/bin/env bash
# Build the offline Windows bundle: dist/FERRO-<version>-win64-offline.zip
# Needs internet on the build machine only. Requires pixi and pixi-pack
# (pixi global install pixi-pack).
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' ferro/__init__.py)
PACK_VERSION=$(pixi-pack --version | awk '{print $2}')
NAME="PyFERRO-${VERSION}-win64-offline"
OUT="dist/${NAME}"

rm -rf "$OUT" "dist/${NAME}.zip"
mkdir -p "$OUT/app" "$OUT/tools"

pixi lock
pixi-pack pixi.toml --platform win-64 --environment default \
    --use-cache dist/.pixi-pack-cache --output-file "$OUT/environment-win-64.tar"
curl -fsSL -o "$OUT/tools/pixi-unpack.exe" \
    "https://github.com/Quantco/pixi-pack/releases/download/v${PACK_VERSION}/pixi-unpack-x86_64-pc-windows-msvc.exe"

# pixi-unpack.exe is an MSVC build and needs VCRUNTIME140.dll, which a bare Windows
# install does not have and which normally means an admin-rights redistributable.
# The same DLLs are in the environment just packed, and Windows searches an
# executable's own folder first, so put a copy beside the unpacker.
VC_TMP=$(mktemp -d)
VC_PKG=$(tar -tf "$OUT/environment-win-64.tar" | grep -m1 'channel/win-64/vc14_runtime-')
tar -xf "$OUT/environment-win-64.tar" -C "$VC_TMP" "$VC_PKG"
unzip -o -q "$VC_TMP/$VC_PKG" -d "$VC_TMP"
tar -xf "$VC_TMP"/pkg-vc14_runtime-*.tar.zst -C "$VC_TMP" vcruntime140.dll vcruntime140_1.dll
cp "$VC_TMP/vcruntime140.dll" "$VC_TMP/vcruntime140_1.dll" "$OUT/tools/"
rm -rf "$VC_TMP"

cp -R ferro "$OUT/app/ferro"
find "$OUT/app" -name __pycache__ -prune -exec rm -rf {} +
# cmd.exe mis-parses an LF-only .bat, and these are edited on macOS
for bat in packaging/windows/*.bat; do
    sed 's/$/\r/; s/\r\r$/\r/' "$bat" > "$OUT/$(basename "$bat")"
done
cp README.md "$OUT/README.md"
cp -R docs/manuals "$OUT/manuals"
cp docs/wiring-diagram.pdf "$OUT/wiring-diagram.pdf"
mkdir -p "$OUT/drivers"
cat > "$OUT/drivers/PUT_DRIVER_INSTALLERS_HERE.txt" <<'EOF'
Copy the offline installers here before taking the bundle to the lab PC
(see README.md, "Drivers"):
  - NI-VISA and NI-488.2 (for the NI GPIB adapter)
  - FTDI CDM VCP driver (for the Dtech USB-RS485 adapter), if Windows does not detect it
EOF

(cd dist && zip -qr "${NAME}.zip" "${NAME}")
echo "Built dist/${NAME}.zip ($(du -h "dist/${NAME}.zip" | cut -f1))"
