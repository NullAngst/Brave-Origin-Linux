#!/usr/bin/env bash
# Merge the per-arch Flatpak build repos into one OSTree repo that GitHub
# Pages can serve, sign it, and write the .flatpakref/.flatpakrepo files that
# let `flatpak update` see new versions. Also makes the .flatpak bundles for
# the release, pointing them at the hosted repo so bundle installs update too.
#
# Usage: publish-flatpak-repo.sh <version> <site-url> <site-dir> <dist-dir> <repo.tar>...
#   Each repo.tar is a flatpak-builder --repo directory, tarred from inside it.
#
# Signing: set GPG_KEY_ID and GPG_HOMEDIR. Without them you only get plain
# bundles that do not update, and the site dir should not be published.
# Prints "signed=true" or "signed=false" as its last line.
set -euo pipefail

# Keep stdout clean for the final key=value line (it goes into
# $GITHUB_OUTPUT), and send all tool chatter to stderr.
exec 3>&1 1>&2

version=$1 url=${2%/}/ site=$3 dist=$4
shift 4

app=com.brave.Origin
branch=stable
runtime_repo=https://dl.flathub.org/repo/flathub.flatpakrepo
repo=$site/repo

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

rm -rf "$repo"
mkdir -p "$site" "$dist"
ostree init --mode=archive-z2 --repo="$repo"

# Pull only the app refs. The appstream refs get regenerated (and signed)
# by build-update-repo below, so the old unsigned ones are left behind.
for tarball in "$@"; do
  src=$(mktemp -d -p "$work")
  tar -xf "$tarball" -C "$src"
  mapfile -t refs < <(ostree --repo="$src" refs | grep "^app/$app/")
  [ ${#refs[@]} -gt 0 ] || { echo "no app refs in $tarball" >&2; exit 1; }
  ostree --repo="$repo" pull-local "$src" "${refs[@]}"
done

mapfile -t arches < <(ostree --repo="$repo" refs | awk -F/ -v a="$app" -v b="$branch" '$1=="app" && $2==a && $4==b {print $3}' | sort)
echo "arches: ${arches[*]}"

sign=()
signed=false
if [ -n "${GPG_KEY_ID:-}" ] && [ -n "${GPG_HOMEDIR:-}" ]; then
  signed=true
  sign=(--gpg-sign="$GPG_KEY_ID" --gpg-homedir="$GPG_HOMEDIR")
  gpg --homedir "$GPG_HOMEDIR" --export "$GPG_KEY_ID" > "$work/key.gpg"
  for arch in "${arches[@]}"; do
    flatpak build-sign "${sign[@]}" --arch="$arch" "$repo" "$app" "$branch"
  done
fi

flatpak build-update-repo "${sign[@]}" --title="Brave Origin (unofficial)" \
  --default-branch="$branch" "$repo"

if $signed; then
  key=$(base64 -w0 < "$work/key.gpg")
  cat > "$site/brave-origin.flatpakref" <<EOF
[Flatpak Ref]
Name=$app
Branch=$branch
Title=Brave Origin (unofficial)
Url=${url}repo/
SuggestRemoteName=brave-origin
IsRuntime=false
RuntimeRepo=$runtime_repo
Homepage=https://brave.com/origin/
GPGKey=$key
EOF
  cat > "$site/brave-origin.flatpakrepo" <<EOF
[Flatpak Repo]
Title=Brave Origin (unofficial)
Url=${url}repo/
Homepage=https://brave.com/origin/
Comment=Unofficial Flatpak builds of Brave Origin from Brave's own Linux releases
GPGKey=$key
EOF
  cat > "$site/index.html" <<EOF
<!doctype html>
<meta charset="utf-8">
<title>Brave Origin Flatpak</title>
<h1>Brave Origin Flatpak (unofficial)</h1>
<p>Current version: $version</p>
<p>Install, with updates through <code>flatpak update</code>:</p>
<pre>flatpak install --user ${url}brave-origin.flatpakref</pre>
<p>Or add just the remote: <code>flatpak remote-add --user brave-origin ${url}brave-origin.flatpakrepo</code></p>
EOF
fi

for arch in "${arches[@]}"; do
  extra=()
  if $signed; then
    extra=(--repo-url="${url}repo/" --gpg-keys="$work/key.gpg")
  fi
  flatpak build-bundle --arch="$arch" --runtime-repo="$runtime_repo" "${extra[@]}" \
    "$repo" "$dist/brave-origin-$version-$arch.flatpak" "$app" "$branch"
done

du -sh "$repo"
echo "signed=$signed" >&3
