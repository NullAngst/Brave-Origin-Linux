#!/usr/bin/env python3
"""Find the newest stable Brave release that ships Brave Origin for Linux,
and decide whether this repo still needs to build it.

Brave publishes Origin on the same GitHub release tags as regular Brave
(github.com/brave/brave-browser/releases). This script looks for the Origin
Linux assets on those releases instead of hardcoding file names, prefers the
.zip over the .deb, and gets a sha256 for each one.

Outputs (written to $GITHUB_OUTPUT when --github-output is given):
  version       e.g. 1.96.61
  upstream_tag  e.g. v1.96.61
  build         "true" or "false"
  matrix        JSON list, one entry per architecture
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

UPSTREAM = "brave/brave-browser"
API = "https://api.github.com"

# Matches brave-origin-1.96.61-linux-amd64.zip and brave-origin_1.96.61_amd64.deb.
# brave-origin-beta-* and brave-origin-nightly-* do not match, since a digit
# has to follow the first separator.
ASSET_RE = re.compile(
    r"^brave-origin[-_]v?(?P<ver>\d+\.\d+\.\d+)[-_](?:linux-)?"
    r"(?P<arch>amd64|arm64|x86_64|aarch64)\.(?P<kind>zip|deb)$"
)
FLATPAK_ARCH = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
RUNNER = {"x86_64": "ubuntu-24.04", "aarch64": "ubuntu-24.04-arm"}
KIND_PREFERENCE = {"zip": 0, "deb": 1}


def token():
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def request(url, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "brave-origin-packages",
    })
    tok = token()
    if tok and url.startswith(API):
        req.add_header("Authorization", f"Bearer {tok}")
    return urllib.request.urlopen(req, timeout=60)


def api(path):
    with request(API + path) as resp:
        return json.load(resp)


def origin_assets(release):
    """Return {arch: asset_info} for the Origin Linux assets on a release."""
    found = {}
    for asset in release.get("assets", []):
        m = ASSET_RE.match(asset["name"])
        if not m:
            continue
        arch = FLATPAK_ARCH[m["arch"]]
        info = {
            "arch": arch,
            "runner": RUNNER[arch],
            "kind": m["kind"],
            "asset": asset["name"],
            "url": asset["browser_download_url"],
            "version": m["ver"],
            "digest": asset.get("digest") or "",
        }
        current = found.get(arch)
        if current is None or KIND_PREFERENCE[info["kind"]] < KIND_PREFERENCE[current["kind"]]:
            found[arch] = info
    return found


def find_release():
    """Newest non-prerelease, non-draft release with Origin Linux assets."""
    candidates = []
    try:
        candidates.append(api(f"/repos/{UPSTREAM}/releases/latest"))
    except urllib.error.HTTPError as e:
        print(f"warning: /releases/latest failed: {e}", file=sys.stderr)

    for rel in candidates:
        assets = origin_assets(rel)
        if assets:
            return rel, assets

    # Brave cuts a lot of beta and nightly prereleases, so page through a few.
    for page in range(1, 6):
        for rel in api(f"/repos/{UPSTREAM}/releases?per_page=100&page={page}"):
            if rel.get("draft") or rel.get("prerelease"):
                continue
            assets = origin_assets(rel)
            if assets:
                return rel, assets

    latest = candidates[0] if candidates else {}
    names = [a["name"] for a in latest.get("assets", []) if "linux" in a["name"] or "origin" in a["name"]]
    print("error: no Brave Origin Linux assets found on any recent stable release.", file=sys.stderr)
    print(f"Linux/Origin assets on {latest.get('tag_name', '?')}:", file=sys.stderr)
    for n in names:
        print(f"  {n}", file=sys.stderr)
    print("If the naming changed, update ASSET_RE in scripts/resolve-release.py.", file=sys.stderr)
    sys.exit(1)


def sha256_of(info):
    digest = info.pop("digest", "")
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1]
    # Older releases have no digest field, so hash the download.
    print(f"hashing {info['asset']} (no digest from the API)", file=sys.stderr)
    h = hashlib.sha256()
    with request(info["url"], accept="application/octet-stream") as resp:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def already_released(repo, tag):
    if not repo:
        return False
    try:
        api(f"/repos/{repo}/releases/tags/{tag}")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--github-output", help="append outputs to this file ($GITHUB_OUTPUT)")
    ap.add_argument("--force", action="store_true", default=os.environ.get("FORCE", "false") == "true",
                    help="build even if this repo already has a release for the version")
    args = ap.parse_args()

    release, assets = find_release()
    versions = {a["version"] for a in assets.values()}
    if len(versions) != 1:
        print(f"error: Origin assets disagree on version: {sorted(versions)}", file=sys.stderr)
        sys.exit(1)
    version = versions.pop()
    tag = f"v{version}"

    matrix = []
    for arch in sorted(assets):
        info = assets[arch]
        info["sha256"] = sha256_of(info)
        info.pop("version")
        matrix.append(info)

    exists = already_released(os.environ.get("GITHUB_REPOSITORY", ""), tag)
    build = args.force or not exists

    summary = [
        f"Upstream release: {release['tag_name']} ({release.get('html_url', '')})",
        f"Brave Origin version: {version}",
        f"Architectures: {', '.join(m['arch'] for m in matrix)}",
        f"Already released here: {'yes' if exists else 'no'}",
        f"Building: {'yes' if build else 'no'}",
    ]
    for line in summary:
        print(line, file=sys.stderr)
    for m in matrix:
        print(f"  {m['arch']}: {m['asset']} sha256={m['sha256']}", file=sys.stderr)

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("\n".join(f"- {s}" for s in summary) + "\n")

    outputs = {
        "version": version,
        "upstream_tag": release["tag_name"],
        "build": "true" if build else "false",
        "matrix": json.dumps(matrix, separators=(",", ":")),
    }
    if args.github_output:
        with open(args.github_output, "a") as f:
            for k, v in outputs.items():
                f.write(f"{k}={v}\n")
    else:
        print(json.dumps({**outputs, "matrix": matrix}, indent=2))


if __name__ == "__main__":
    main()
