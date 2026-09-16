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

cp -R ferro "$OUT/app/ferro"
find "$OUT/app" -name __pycache__ -prune -exec rm -rf {} +
cp packaging/windows/*.bat "$OUT/"
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
