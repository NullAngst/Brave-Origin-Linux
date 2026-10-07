#!/usr/bin/env bash
# Build the Flatpak side of a release: the signed OSTree repo that GitHub
# Pages serves (so `flatpak update` works), the .flatpakref files, and the
# .flatpak bundles for the release page.
#
# Usage: publish-flatpak-repo.sh <flatpak-repo-CHANNEL-ARCH.tar>...
#   Each tarball is a flatpak-builder --repo directory, tarred from inside it.
#
# Environment:
#   RELEASE_CHANNELS  channels being released this run, e.g. "stable nightly"
#   VERSIONS          channel=version pairs for those, e.g. "stable=1.96.61"
#   HOSTED_CHANNELS   channels served from Pages, e.g. "stable nightly"
#   SITE_URL          where Pages serves the site, with trailing slash
#   SITE_DIR, DIST_DIR  output dirs (default: site, dist)
#   GPG_KEY_ID, GPG_HOMEDIR  signing key. Without it there is no Pages repo,
#                     only bundles that do not update.
#
# Pages replaces the whole site on every deploy, so a hosted channel that is
# not being released this run gets pulled back from the live site unchanged.
#
# Writes key=value lines to $GITHUB_OUTPUT (or stdout when run by hand):
#   signed=true|false   deploy_pages=true|false
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
site=${SITE_DIR:-site}
dist=${DIST_DIR:-dist}
url=${SITE_URL%/}/
read -r -a release_channels <<<"${RELEASE_CHANNELS:-}"
read -r -a hosted_channels <<<"${HOSTED_CHANNELS:-}"
runtime_repo=https://dl.flathub.org/repo/flathub.flatpakrepo

cfg() { jq -er --arg c "$1" --arg k "$2" '.[$c][$k]' "$root/channels.json"; }
version_of() {
  local pair
  for pair in ${VERSIONS:-}; do
    [ "${pair%%=*}" = "$1" ] && { echo "${pair#*=}"; return; }
  done
  echo "no version given for $1" >&2
  exit 1
}
in_list() { local x=$1; shift; for y in "$@"; do [ "$x" = "$y" ] && return 0; done; return 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$dist"

# 1. Everything built this run goes into a staging repo first.
staging=$work/staging
ostree init --mode=archive-z2 --repo="$staging"
for tarball in "$@"; do
  src=$(mktemp -d -p "$work")
  tar -xf "$tarball" -C "$src"
  mapfile -t refs < <(ostree --repo="$src" refs | grep '^app/')
  [ ${#refs[@]} -gt 0 ] || { echo "no app refs in $tarball" >&2; exit 1; }
  ostree --repo="$staging" pull-local "$src" "${refs[@]}"
done

# refs_for <repo> <app_id> <branch>: the app's refs in that repo, one per arch
refs_for() {
  ostree --repo="$1" refs | grep -E "^app/$2/[^/]+/$3\$" || true
}

signed=false
sign=()
if [ -n "${GPG_KEY_ID:-}" ] && [ -n "${GPG_HOMEDIR:-}" ]; then
  signed=true
  sign=(--gpg-sign="$GPG_KEY_ID" --gpg-homedir="$GPG_HOMEDIR")
  gpg --homedir "$GPG_HOMEDIR" --export "$GPG_KEY_ID" > "$work/key.gpg"
fi

# 2. The Pages repo, only if we can sign it and a hosted channel changed.
deploy_pages=false
hosted_changed=false
for c in "${hosted_channels[@]}"; do
  in_list "$c" "${release_channels[@]}" && hosted_changed=true
done

if $signed && $hosted_changed; then
  repo=$site/repo
  rm -rf "$site"
  mkdir -p "$site"
  ostree init --mode=archive-z2 --repo="$repo"

  # Is there a live repo to carry unchanged channels over from?
  live=false
  code=$(curl -sS -o /dev/null -w '%{http_code}' "${url}repo/summary" || true)
  case $code in
    200) live=true ;;
    404) echo "no live repo at ${url}repo/ yet, starting fresh" ;;
    *) echo "could not check ${url}repo/summary (HTTP $code), refusing to publish a repo that might drop channels" >&2; exit 1 ;;
  esac
  if $live; then
    ostree --repo="$repo" remote add --no-gpg-verify live "${url}repo/"
  fi

  for c in "${hosted_channels[@]}"; do
    app=$(cfg "$c" app_id)
    if in_list "$c" "${release_channels[@]}"; then
      mapfile -t refs < <(refs_for "$staging" "$app" "$c")
      [ ${#refs[@]} -gt 0 ] || { echo "$c is being released but was not built" >&2; exit 1; }
      ostree --repo="$repo" pull-local "$staging" "${refs[@]}"
      for ref in "${refs[@]}"; do
        arch=$(cut -d/ -f3 <<<"$ref")
        flatpak build-sign "${sign[@]}" --arch="$arch" "$repo" "$app" "$c"
      done
    else
      refs=()
      if $live; then
        mapfile -t refs < <(ostree --repo="$repo" remote refs live | sed 's/^live://' | grep -E "^app/$app/[^/]+/$c\$" || true)
      fi
      if [ ${#refs[@]} -gt 0 ]; then
        echo "carrying $c over from the live site: ${refs[*]}"
        # --mirror keeps the commits and their signatures exactly as published.
        ostree --repo="$repo" pull --mirror --depth=0 live "${refs[@]}"
        continue
      fi
      # Not on the live site. Use this run's build if there is one (a push
      # builds everything), otherwise it stays missing until it's released.
      mapfile -t refs < <(refs_for "$staging" "$app" "$c")
      if [ ${#refs[@]} -gt 0 ]; then
        echo "$c is not on the live site, using this run's build"
        ostree --repo="$repo" pull-local "$staging" "${refs[@]}"
        for ref in "${refs[@]}"; do
          arch=$(cut -d/ -f3 <<<"$ref")
          flatpak build-sign "${sign[@]}" --arch="$arch" "$repo" "$app" "$c"
        done
      else
        echo "::warning::$c is not on the Pages repo. Run the workflow with force (and channels: $c) to add it."
      fi
    fi
  done
  if $live; then
    ostree --repo="$repo" remote delete live
  fi

  flatpak build-update-repo "${sign[@]}" --title="Brave Origin (unofficial)" "$repo"

  key=$(base64 -w0 < "$work/key.gpg")
  cat > "$site/brave-origin.flatpakrepo" <<EOF
[Flatpak Repo]
Title=Brave Origin (unofficial)
Url=${url}repo/
Homepage=https://brave.com/origin/
Comment=Unofficial Flatpak builds of Brave Origin from Brave's own Linux releases
GPGKey=$key
EOF

  rows=""
  for c in "${hosted_channels[@]}"; do
    app=$(cfg "$c" app_id)
    name=$(cfg "$c" name)
    slug=$(cfg "$c" slug)
    [ -n "$(refs_for "$repo" "$app" "$c")" ] || continue
    cat > "$site/$slug.flatpakref" <<EOF
[Flatpak Ref]
Name=$app
Branch=$c
Title=$name (unofficial)
Url=${url}repo/
SuggestRemoteName=brave-origin
IsRuntime=false
RuntimeRepo=$runtime_repo
Homepage=https://brave.com/origin/
GPGKey=$key
EOF
    rows+="<h2>$name</h2>
<pre>flatpak install --user ${url}$slug.flatpakref</pre>
"
  done
  cat > "$site/index.html" <<EOF
<!doctype html>
<meta charset="utf-8">
<title>Brave Origin Flatpak</title>
<h1>Brave Origin Flatpak (unofficial)</h1>
<p>Install a channel, then <code>flatpak update</code> keeps it current.</p>
$rows<p>Or add just the remote: <code>flatpak remote-add --user brave-origin ${url}brave-origin.flatpakrepo</code></p>
EOF
  du -sh "$repo"
  deploy_pages=true
elif ! $signed; then
  echo "no signing key, skipping the Pages repo"
else
  echo "no hosted channel changed this run, leaving Pages alone"
fi

# 3. Bundles for every channel being released. Bundles of hosted channels
#    carry the repo URL and key, so installing one sets up updates.
for c in "${release_channels[@]}"; do
  app=$(cfg "$c" app_id)
  slug=$(cfg "$c" slug)
  version=$(version_of "$c")
  mapfile -t refs < <(refs_for "$staging" "$app" "$c")
  [ ${#refs[@]} -gt 0 ] || { echo "$c is being released but was not built" >&2; exit 1; }
  for ref in "${refs[@]}"; do
    arch=$(cut -d/ -f3 <<<"$ref")
    src=$staging extra=()
    if $deploy_pages && in_list "$c" "${hosted_channels[@]}"; then
      src=$site/repo
      extra=(--repo-url="${url}repo/" --gpg-keys="$work/key.gpg")
    fi
    flatpak build-bundle --arch="$arch" --runtime-repo="$runtime_repo" "${extra[@]}" \
      "$src" "$dist/$slug-$version-$arch.flatpak" "$app" "$c"
  done
done

{
  echo "signed=$signed"
  echo "deploy_pages=$deploy_pages"
} >> "${GITHUB_OUTPUT:-/dev/stdout}"
