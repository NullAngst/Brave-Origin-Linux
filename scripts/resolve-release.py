#!/usr/bin/env python3
"""Find the newest Brave Origin Linux build for each channel (stable, beta,
nightly) and decide which ones this repo still needs to build and release.

Brave publishes Origin on the same GitHub releases as regular Brave
(github.com/brave/brave-browser/releases). Stable lands on normal releases,
beta and nightly on prereleases. The channel comes from the asset name:

  brave-origin-1.96.61-linux-amd64.zip           stable
  brave-origin-beta-1.98.51-linux-amd64.zip      beta
  brave-origin-nightly-1.99.20-linux-arm64.zip   nightly

A release only counts for a channel once it has assets for every
architecture, since Brave uploads them a few minutes apart.

Outputs (to $GITHUB_OUTPUT with --github-output, otherwise printed as JSON):
  matrix            JSON list of {channel, arch, ...} entries to build
  release_channels  space-separated channels to publish this run
  versions          space-separated channel=version pairs for those channels
  upstream_tags     space-separated channel=tag pairs, the brave-browser release tags
  any_release       "true" if there is anything to publish
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

UPSTREAM = "brave/brave-browser"
API = "https://api.github.com"
CHANNELS = json.loads((Path(__file__).resolve().parent.parent / "channels.json").read_text())
ARCHES = {"x86_64", "aarch64"}

ASSET_RE = re.compile(
    r"^brave-origin(?:-(?P<channel>beta|nightly))?[-_]v?(?P<ver>\d+\.\d+\.\d+)[-_](?:linux-)?"
    r"(?P<arch>amd64|arm64|x86_64|aarch64)\.(?P<kind>zip|deb)$"
)
FLATPAK_ARCH = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
RUNNER = {"x86_64": "ubuntu-24.04", "aarch64": "ubuntu-24.04-arm"}
KIND_PREFERENCE = {"zip": 0, "deb": 1}


def log(msg):
    print(msg, file=sys.stderr)


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


def vkey(version):
    return tuple(int(x) for x in version.split("."))


def origin_assets(release):
    """Return {channel: {version, assets: {arch: info}}} for one release."""
    found = {}
    for asset in release.get("assets", []):
        m = ASSET_RE.match(asset["name"])
        if not m:
            continue
        channel = m["channel"] or "stable"
        # Stable assets on a prerelease would be odd, skip them to be safe.
        if channel == "stable" and release.get("prerelease"):
            continue
        arch = FLATPAK_ARCH[m["arch"]]
        info = {
            "kind": m["kind"],
            "asset": asset["name"],
            "url": asset["browser_download_url"],
            "digest": asset.get("digest") or "",
        }
        entry = found.setdefault(channel, {"version": m["ver"], "tag": release["tag_name"], "assets": {}})
        if entry["version"] != m["ver"]:
            continue
        current = entry["assets"].get(arch)
        if current is None or KIND_PREFERENCE[info["kind"]] < KIND_PREFERENCE[current["kind"]]:
            entry["assets"][arch] = info
    return {c: e for c, e in found.items() if set(e["assets"]) >= ARCHES}


def find_releases(max_pages):
    """Newest complete build per channel, scanning the most recent releases."""
    best = {}
    for page in range(1, max_pages + 1):
        releases = api(f"/repos/{UPSTREAM}/releases?per_page=100&page={page}")
        if not releases:
            break
        for rel in releases:
            if rel.get("draft"):
                continue
            for channel, entry in origin_assets(rel).items():
                if channel not in best or vkey(entry["version"]) > vkey(best[channel]["version"]):
                    best[channel] = entry
        if set(best) >= set(CHANNELS):
            break
    return best


def sha256_of(info):
    digest = info.pop("digest", "")
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1]
    log(f"hashing {info['asset']} (no digest from the API)")
    h = hashlib.sha256()
    with request(info["url"], accept="application/octet-stream") as resp:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def already_released(repo, channel, version):
    """Stable gets a v<version> tag per release. Beta and nightly each have
    one rolling release whose title ends with the version it holds."""
    if not repo:
        return False
    tag = CHANNELS[channel]["release_tag"] or f"v{version}"
    try:
        rel = api(f"/repos/{repo}/releases/tags/{tag}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise
    if CHANNELS[channel]["release_tag"]:
        return (rel.get("name") or "").endswith(f" {version}")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--github-output", help="append outputs to this file ($GITHUB_OUTPUT)")
    ap.add_argument("--force", action="store_true", default=os.environ.get("FORCE", "false") == "true",
                    help="release every channel even if this repo already has that version")
    ap.add_argument("--build-all", action="store_true", default=os.environ.get("BUILD_ALL", "false") == "true",
                    help="build every channel (to test changes) but still only release new versions")
    ap.add_argument("--channels", default=os.environ.get("CHANNELS_FILTER", ""),
                    help="space-separated channels to consider, default all")
    ap.add_argument("--max-pages", type=int, default=5)
    args = ap.parse_args()

    wanted = args.channels.split() or list(CHANNELS)
    unknown = set(wanted) - set(CHANNELS)
    if unknown:
        log(f"error: unknown channels {sorted(unknown)}")
        sys.exit(1)

    best = find_releases(args.max_pages)
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    matrix, release, summary = [], [], []
    for channel in wanted:
        entry = best.get(channel)
        if not entry:
            log(f"warning: no complete Brave Origin {channel} build found in the last {args.max_pages * 100} releases")
            summary.append(f"{channel}: no build found upstream")
            continue
        version = entry["version"]
        exists = already_released(repo, channel, version)
        do_release = args.force or not exists
        if do_release:
            release.append((channel, version, entry["tag"]))
        summary.append(f"{channel}: {version} from {entry['tag']}, "
                       f"{'already released' if exists else 'new'}, "
                       f"{'releasing' if do_release else 'skipping'}")
        if not (do_release or args.build_all):
            continue
        for arch in sorted(entry["assets"]):
            info = entry["assets"][arch]
            info["sha256"] = sha256_of(info)
            matrix.append({
                "channel": channel,
                "arch": arch,
                "runner": RUNNER[arch],
                "version": version,
                **info,
            })

    for line in summary:
        log(line)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("\n".join(f"- {s}" for s in summary) + "\n")

    outputs = {
        "matrix": json.dumps(matrix, separators=(",", ":")),
        "release_channels": " ".join(c for c, _, _ in release),
        "versions": " ".join(f"{c}={v}" for c, v, _ in release),
        "upstream_tags": " ".join(f"{c}={t}" for c, _, t in release),
        "any_release": "true" if release else "false",
    }
    if args.github_output:
        with open(args.github_output, "a") as f:
            for k, v in outputs.items():
                f.write(f"{k}={v}\n")
    else:
        print(json.dumps({**outputs, "matrix": matrix}, indent=2))


if __name__ == "__main__":
    main()
