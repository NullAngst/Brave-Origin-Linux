#!/usr/bin/env bash
# Build a Brave Origin AppImage for one channel from Brave's own Linux release
# asset.
#
# Usage: build-appimage.sh <channel> <arch> <url> <sha256> <zip|deb> <version> [owner/repo]
#   channel is stable, beta or nightly (see channels.json), arch is x86_64 or
#   aarch64. If owner/repo is given, update info for AppImageUpdate is
#   embedded and a .zsync file is written next to the image.
#
# Needs: curl, sha256sum, bsdtar (libarchive-tools), jq. Downloads the latest
# appimagetool on each run.
set -euo pipefail

channel=$1 arch=$2 url=$3 sha=$4 kind=$5 version=$6 repo=${7:-}
here=$(cd "$(dirname "$0")" && pwd)
root=$here/..
work=${WORK_DIR:-$PWD/appimage-build}
out=${OUT_DIR:-$PWD/dist}

cfg() { jq -er --arg c "$channel" --arg k "$1" '.[$c][$k]' "$root/channels.json"; }
name=$(cfg name)
slug=$(cfg slug)
file_prefix=$(cfg appimage_name)
profile_dir=$(cfg profile_dir)
icon_suffix=$(cfg icon_suffix)
release_tag=$(cfg release_tag)

appdir=$work/AppDir
app=$appdir/opt/$slug
image=$file_prefix-$version-$arch.AppImage

rm -rf "$work"
mkdir -p "$work" "$out" "$app"

echo ">> downloading $url"
curl -fL --retry 3 -o "$work/pkg.$kind" "$url"
echo "$sha  $work/pkg.$kind" | sha256sum -c -

echo ">> unpacking"
if [ "$kind" = zip ]; then
  bsdtar --no-same-owner -xf "$work/pkg.zip" -C "$app"
else
  mkdir -p "$work/deb"
  bsdtar -xf "$work/pkg.deb" -C "$work/deb"
  bsdtar --no-same-owner -xf "$work"/deb/data.tar.* -C "$work/deb"
  cp -a "$work/deb/opt/brave.com/$slug/." "$app/"
fi
test -x "$app/brave" || { echo "no brave binary in the package, layout changed?" >&2; exit 1; }

# The SUID sandbox helper cannot work from a squashfs mounted nosuid, and if
# Chromium finds it but it is not setuid root, it aborts with a confusing
# error. Without it, Chromium uses the user namespace sandbox, which is what
# we want. The cron job and AppArmor profile are for the .deb/.rpm installs.
rm -rf "$app/chrome-sandbox" "$app/cron" "$app/apparmor.d"

fill() {
  sed -e "s|@NAME@|$name|g" -e "s|@SLUG@|$slug|g" -e "s|@CHANNEL@|$channel|g" \
      -e "s|@PROFILE_DIR@|$profile_dir|g" "$1"
}
fill "$root/appimage/AppRun.in" > "$appdir/AppRun"
chmod 755 "$appdir/AppRun"
fill "$root/appimage/brave-origin.desktop.in" > "$appdir/$slug.desktop"
install -Dm644 "$appdir/$slug.desktop" "$appdir/usr/share/applications/$slug.desktop"

icon=$app/product_logo_256$icon_suffix.png
test -f "$icon" || { echo "no $icon in the package, icon naming changed?" >&2; exit 1; }
install -Dm644 "$icon" "$appdir/$slug.png"
ln -sf "$slug.png" "$appdir/.DirIcon"
for s in 16 24 32 48 64 128 256; do
  if [ -f "$app/product_logo_$s$icon_suffix.png" ]; then
    install -Dm644 "$app/product_logo_$s$icon_suffix.png" "$appdir/usr/share/icons/hicolor/${s}x${s}/apps/$slug.png"
  fi
done

echo ">> fetching appimagetool"
tool=$work/appimagetool
curl -fL --retry 3 -o "$tool" \
  "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$(uname -m).AppImage"
chmod +x "$tool"

args=()
if [ -n "$repo" ]; then
  # Stable releases are versioned and marked latest. Beta and nightly each
  # live on one rolling release tagged with the channel name.
  args+=(-u "gh-releases-zsync|${repo%%/*}|${repo#*/}|${release_tag:-latest}|$file_prefix-*-$arch.AppImage.zsync")
fi

echo ">> building $image"
# APPIMAGE_EXTRACT_AND_RUN avoids needing FUSE on the build machine.
(cd "$out" && APPIMAGE_EXTRACT_AND_RUN=1 ARCH=$arch VERSION=$version \
  "$tool" --no-appstream "${args[@]}" "$appdir" "$image")

ls -l "$out"
