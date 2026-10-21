#!/bin/bash
# Arma la imagen MesaDeAccion-IRTP-<versión>-<arquitectura>.dmg con la app y un acceso a Aplicaciones.
#   mac/dmg.sh <versión>          (desde collector/, después de PyInstaller)
set -euo pipefail
VER="$1"; ARQ="$(uname -m | sed 's/x86_64/intel/;s/arm64/apple-silicon/')"
TMP="$(mktemp -d)"
cp -R "dist/Mesa de Acción.app" "$TMP/"
ln -s /Applications "$TMP/Aplicaciones"
cp mac/LEAME.txt "$TMP/Léame.txt"
mkdir -p dist/dmg
hdiutil create -volname "Mesa de Acción" -srcfolder "$TMP" -fs HFS+ -format UDZO -ov "dist/dmg/MesaDeAccion-IRTP-$VER-$ARQ.dmg"
