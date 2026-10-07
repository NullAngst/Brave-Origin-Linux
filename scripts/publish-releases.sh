#!/usr/bin/env bash
# Upload the built files to GitHub releases, one per channel.
#
#   stable   a versioned release (v1.96.61) marked latest, so old ones stay
#   beta     one rolling prerelease tagged "beta", replaced on every update
#   nightly  one rolling prerelease tagged "nightly", replaced on every update
#
# The rolling tags keep the releases page from filling up with a nightly a
# day, and give the AppImage update info a fixed tag to look at.
#
# Environment: RELEASE_CHANNELS, VERSIONS (same as publish-flatpak-repo.sh),
# UPSTREAM_TAGS (channel=tag pairs, for the notes), DIST_DIR (default dist), PAGES_URL and PAGES_DEPLOYED for the notes,
# HOSTED_CHANNELS, GH_TOKEN, GH_REPO, GITHUB_SHA.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
dist=${DIST_DIR:-dist}
read -r -a release_channels <<<"${RELEASE_CHANNELS:-}"
read -r -a hosted_channels <<<"${HOSTED_CHANNELS:-}"

cfg() { jq -er --arg c "$1" --arg k "$2" '.[$c][$k]' "$root/channels.json"; }
# lookup <key> <"k=v k=v ...">
lookup() {
  local pair
  for pair in $2; do
    [ "${pair%%=*}" = "$1" ] && { echo "${pair#*=}"; return; }
  done
}
in_list() { local x=$1; shift; for y in "$@"; do [ "$x" = "$y" ] && return 0; done; return 1; }

for c in "${release_channels[@]}"; do
  version=$(lookup "$c" "${VERSIONS:-}")
  [ -n "$version" ] || { echo "no version given for $c" >&2; exit 1; }
  name=$(cfg "$c" name)
  label=$(cfg "$c" label)
  # Always name the channel, including Stable, so the releases page is clear.
  title="Brave Origin $label $version"
  slug=$(cfg "$c" slug)
  prefix=$(cfg "$c" appimage_name)
  rolling=$(cfg "$c" release_tag)
  tag=${rolling:-v$version}

  files=$(mktemp -d)
  shopt -s nullglob
  for f in "$dist/$slug-$version-"*.flatpak "$dist/$prefix-$version-"*.AppImage "$dist/$prefix-$version-"*.AppImage.zsync; do
    cp "$f" "$files/"
  done
  shopt -u nullglob
  if [ -z "$(ls -A "$files")" ]; then
    echo "no files for $c $version in $dist" >&2
    exit 1
  fi
  (cd "$files" && sha256sum -- *.flatpak *.AppImage > SHA256SUMS && cat SHA256SUMS)

  upstream=$(lookup "$c" "${UPSTREAM_TAGS:-}")
  {
    echo "**Channel: $label**"
    echo
    echo "$name $version, repackaged unmodified from Brave's official Linux build${upstream:+ on the [$upstream release](https://github.com/brave/brave-browser/releases/tag/$upstream)}."
    echo
    echo "Changelog: [CHANGELOG_DESKTOP_ORIGIN.md](https://github.com/brave/brave-browser/blob/master/CHANGELOG_DESKTOP_ORIGIN.md)"
    echo
    if [ "${PAGES_DEPLOYED:-false}" = true ] && in_list "$c" "${hosted_channels[@]}"; then
      echo "Flatpak, with updates through \`flatpak update\`:"
      echo
      echo "\`\`\`"
      echo "flatpak install --user ${PAGES_URL%/}/$slug.flatpakref"
      echo "\`\`\`"
    else
      echo "Flatpak (no automatic updates for this channel, install the newer bundle to update):"
      echo
      echo "\`\`\`"
      echo "flatpak install --user ./$slug-$version-x86_64.flatpak"
      echo "\`\`\`"
    fi
    echo
    echo "AppImage: download, \`chmod +x\`, run. AppImageUpdate or Gear Lever can update it from this release."
  } > "$files/notes.md"

  if [ -n "$rolling" ]; then
    # Replace the rolling release, and move its tag to the current commit.
    gh release delete "$tag" --cleanup-tag --yes 2>/dev/null || true
    gh release create "$tag" "$files"/*.flatpak "$files"/*.AppImage "$files"/*.zsync "$files/SHA256SUMS" \
      --target "${GITHUB_SHA:-main}" --title "$title" --notes-file "$files/notes.md" \
      --prerelease --latest=false
  elif gh release view "$tag" >/dev/null 2>&1; then
    gh release upload "$tag" "$files"/*.flatpak "$files"/*.AppImage "$files"/*.zsync "$files/SHA256SUMS" --clobber
    gh release edit "$tag" --title "$title" --notes-file "$files/notes.md" --latest
  else
    gh release create "$tag" "$files"/*.flatpak "$files"/*.AppImage "$files"/*.zsync "$files/SHA256SUMS" \
      --target "${GITHUB_SHA:-main}" --title "$title" --notes-file "$files/notes.md" --latest
  fi
  rm -rf "$files"
done
