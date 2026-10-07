#!/usr/bin/env bash
# Build a Brave Origin AppImage from Brave's own Linux release asset.
#
# Usage: build-appimage.sh <arch> <url> <sha256> <zip|deb> <version> [owner/repo]
#   arch is x86_64 or aarch64. If owner/repo is given, update info for
#   AppImageUpdate is embedded and a .zsync file is written next to the image.
#
# Needs: curl, sha256sum, bsdtar (libarchive-tools), file. Downloads the
# latest appimagetool on each run.
set -euo pipefail

arch=$1 url=$2 sha=$3 kind=$4 version=$5 repo=${6:-}
here=$(cd "$(dirname "$0")" && pwd)
work=${WORK_DIR:-$PWD/appimage-build}
out=${OUT_DIR:-$PWD/dist}
appdir=$work/AppDir
name=Brave-Origin-$version-$arch.AppImage

rm -rf "$work"
mkdir -p "$work" "$out" "$appdir/opt/brave-origin"

echo ">> downloading $url"
curl -fL --retry 3 -o "$work/pkg.$kind" "$url"
echo "$sha  $work/pkg.$kind" | sha256sum -c -

echo ">> unpacking"
if [ "$kind" = zip ]; then
  bsdtar --no-same-owner -xf "$work/pkg.zip" -C "$appdir/opt/brave-origin"
else
  mkdir -p "$work/deb"
  bsdtar -xf "$work/pkg.deb" -C "$work/deb"
  bsdtar --no-same-owner -xf "$work"/deb/data.tar.* -C "$work/deb"
  cp -a "$work/deb/opt/brave.com/brave-origin/." "$appdir/opt/brave-origin/"
fi
test -x "$appdir/opt/brave-origin/brave" || { echo "no brave binary in the package, layout changed?" >&2; exit 1; }

# The SUID sandbox helper cannot work from a squashfs mounted nosuid, and if
# Chromium finds it but it is not setuid root, it aborts with a confusing
# error. Without it, Chromium uses the user namespace sandbox, which is what
# we want.
rm -f "$appdir/opt/brave-origin/chrome-sandbox" "$appdir/opt/brave-origin/brave-sandbox" \
      "$appdir/opt/brave-origin/chrome_sandbox"

install -Dm755 "$here/../appimage/AppRun" "$appdir/AppRun"
install -Dm644 "$here/../appimage/brave-origin.desktop" "$appdir/brave-origin.desktop"
install -Dm644 "$here/../appimage/brave-origin.desktop" "$appdir/usr/share/applications/brave-origin.desktop"

icon=""
for s in 256 128 64 48; do
  if [ -f "$appdir/opt/brave-origin/product_logo_$s.png" ]; then
    icon=$appdir/opt/brave-origin/product_logo_$s.png
    break
  fi
done
if [ -z "$icon" ]; then
  echo ">> no icon in the package, fetching from brave-core"
  curl -fL --retry 3 -o "$work/icon.png" \
    https://raw.githubusercontent.com/brave/brave-core/master/app/theme/brave_origin/linux/product_logo_256.png
  icon=$work/icon.png
fi
install -Dm644 "$icon" "$appdir/brave-origin.png"
ln -sf brave-origin.png "$appdir/.DirIcon"
for s in 16 24 32 48 64 128 256; do
  if [ -f "$appdir/opt/brave-origin/product_logo_$s.png" ]; then
    install -Dm644 "$appdir/opt/brave-origin/product_logo_$s.png" \
      "$appdir/usr/share/icons/hicolor/${s}x${s}/apps/brave-origin.png"
  fi
done

echo ">> fetching appimagetool"
tool=$work/appimagetool
curl -fL --retry 3 -o "$tool" \
  "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$(uname -m).AppImage"
chmod +x "$tool"

args=()
if [ -n "$repo" ]; then
  args+=(-u "gh-releases-zsync|${repo%%/*}|${repo#*/}|latest|Brave-Origin-*-$arch.AppImage.zsync")
fi

echo ">> building $name"
# APPIMAGE_EXTRACT_AND_RUN avoids needing FUSE on the build machine.
(cd "$out" && APPIMAGE_EXTRACT_AND_RUN=1 ARCH=$arch VERSION=$version \
  "$tool" --no-appstream "${args[@]}" "$appdir" "$name")

ls -l "$out"
