#!/usr/bin/env bash
# Rebuilds the app icon's raster files from its two SVG sources in public/.
#
#   favicon.svg   the rounded square, for browser tabs (and favicon.ico)
#   app-icon.svg  the same mark full-bleed, for home screens: iOS and Android
#                 round the corners themselves, and Android's maskable crop
#                 keeps the central 80%, which is where the mark sits.
#
# The PNGs and the .ico are committed, so the image build needs neither tool.
# Run this after changing either SVG. Needs rsvg-convert (librsvg) and
# ImageMagick 7 (`magick`). Same recipe as Family Finances'.
#
#     frontend/scripts/build-app-icons.sh
set -euo pipefail

cd "$(dirname "$0")/../public"
for tool in rsvg-convert magick; do
  command -v "$tool" >/dev/null || { echo "build-app-icons: $tool not found" >&2; exit 1; }
done

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

for size in 16 32 48; do
  rsvg-convert -w "$size" -h "$size" favicon.svg -o "$tmp/favicon-$size.png"
done
magick "$tmp/favicon-16.png" "$tmp/favicon-32.png" "$tmp/favicon-48.png" favicon.ico

rsvg-convert -w 180 -h 180 app-icon.svg -o apple-touch-icon.png
rsvg-convert -w 192 -h 192 app-icon.svg -o icons/icon-192.png
rsvg-convert -w 512 -h 512 app-icon.svg -o icons/icon-512.png
# Maskable: the same full-bleed file. The mark already sits in the central 80%.
cp icons/icon-512.png icons/icon-512-maskable.png

echo "build-app-icons: wrote favicon.ico, apple-touch-icon.png, icons/icon-{192,512,512-maskable}.png"
