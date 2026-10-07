#!/usr/bin/env python3
"""Turn Flathub's com.brave.Browser manifest into a com.brave.Origin manifest.

Flathub's manifest already does the hard parts: the Chromium BaseApp, zypak,
the dconf patch, the sandbox permissions and the host policy symlinks. Origin
is the same browser build with different branding, so instead of maintaining
a copy of all that, this script reads Flathub's current manifest and swaps out
only the Brave-specific pieces:

  - app-id becomes com.brave.Origin
  - the "brave" module installs the Origin zip (or .deb) from Brave's GitHub
    release instead of the regular Brave zip
  - cobalt.ini points at the Origin profile dir (BraveSoftware/Brave-Origin)
  - the desktop file is renamed and loses the Tor action (Origin has no Tor)
  - a fresh metainfo file and the Origin icon from brave-core

Every other module and every finish-arg is passed through untouched, so when
Flathub bumps the runtime, zypak or permissions, the next build picks it up.
"""

import argparse
import datetime
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

APP_ID = "com.brave.Origin"
FLATHUB_ID = "com.brave.Browser"
ICON_URL = "https://raw.githubusercontent.com/brave/brave-core/master/app/theme/brave_origin/product_logo.svg"
WM_CLASS = "brave-origin"
PROFILE_DIR = "BraveSoftware/Brave-Origin"

SUMMARY = "Brave's privacy browser without the extra features"
DESCRIPTION = (
    "Brave Origin keeps Brave's privacy protections, ad blocking and speed "
    "(Brave Shields) and leaves out the extra features such as Rewards, "
    "Wallet, Leo, News, VPN and Tor."
)


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def fetch_sha256(url):
    req = urllib.request.Request(url, headers={"User-Agent": "brave-origin-packages"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return hashlib.sha256(resp.read()).hexdigest()


def copy_local_sources(modules, flathub_dir, out_dir):
    """Copy any `path:` sources the passed-through modules reference."""
    for module in modules:
        if isinstance(module, str):
            die(f"module '{module}' is an external file include, which this script does not handle yet")
        for src in module.get("sources", []):
            if isinstance(src, dict) and "path" in src:
                rel = Path(src["path"])
                (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(flathub_dir / rel, out_dir / rel)
        copy_local_sources(module.get("modules", []), flathub_dir, out_dir)


def convert_desktop(text):
    out = []
    section = None
    skip = False
    for line in text.splitlines():
        header = re.match(r"^\[(.+)\]$", line)
        if header:
            section = header.group(1)
            skip = section == "Desktop Action new-tor-window"
            if skip:
                # Drop the blank line that separated it from the previous section.
                while out and out[-1] == "":
                    out.pop()
                continue
        if skip:
            continue
        if section == "Desktop Entry":
            if re.match(r"^Name(\[[^\]]+\])?=Brave$", line):
                line = line[: line.index("=") + 1] + "Brave Origin"
            elif line.startswith("StartupWMClass="):
                line = f"StartupWMClass={WM_CLASS}"
            elif line.startswith("Icon="):
                line = f"Icon={APP_ID}"
            elif line.startswith("Actions="):
                actions = [a for a in line[len("Actions="):].split(";") if a and a != "new-tor-window"]
                line = "Actions=" + ";".join(actions) + ";"
        out.append(line)
    result = "\n".join(out) + "\n"
    if "Name=Brave Origin" not in result:
        die("could not find the Name=Brave line in Flathub's desktop file")
    return result


def convert_cobalt(text):
    text, n = re.subn(r"(?m)^ConfigDir=.*$", f"ConfigDir={PROFILE_DIR}", text)
    if n != 1:
        die("could not find ConfigDir= in Flathub's cobalt.ini")
    return text


def metainfo(version, date):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>{APP_ID}</id>
  <name>Brave Origin</name>
  <developer id="com.brave">
    <name>Brave Software</name>
  </developer>
  <summary>{escape(SUMMARY)}</summary>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>MPL-2.0</project_license>
  <description>
    <p>{escape(DESCRIPTION)}</p>
    <p>This is an unofficial Flatpak repackaging of the Brave Origin Linux build that Brave publishes on GitHub. It is not affiliated with Brave Software.</p>
  </description>
  <launchable type="desktop-id">{APP_ID}.desktop</launchable>
  <url type="homepage">https://brave.com/origin/</url>
  <url type="bugtracker">https://github.com/brave/brave-browser/issues</url>
  <categories>
    <category>Network</category>
    <category>WebBrowser</category>
  </categories>
  <content_rating type="oars-1.1"/>
  <releases>
    <release version="{version}" date="{date}">
      <url type="details">https://github.com/brave/brave-browser/blob/master/CHANGELOG_DESKTOP_ORIGIN.md</url>
    </release>
  </releases>
</component>
"""


def origin_module(matrix):
    sources = []
    for entry in matrix:
        sources.append({
            "type": "file",
            "url": entry["url"],
            "sha256": entry["sha256"],
            "dest-filename": f"brave-origin.{entry['kind']}",
            "only-arches": [entry["arch"]],
        })
    sources += [
        {"type": "file", "path": "cobalt.ini"},
        {"type": "file", "path": "brave.sh"},
        {"type": "file", "path": f"{APP_ID}.desktop"},
        {"type": "file", "path": f"{APP_ID}.metainfo.xml"},
        {"type": "file", "url": ICON_URL, "sha256": fetch_sha256(ICON_URL), "dest-filename": "origin.svg"},
    ]
    return {
        "name": "brave",
        "buildsystem": "simple",
        "build-commands": [
            "mkdir -p /app/brave",
            # Brave publishes a zip for most releases. The .deb branch is a
            # fallback in case a release only has the .deb for some arch.
            "if [ -f brave-origin.zip ]; then"
            "  bsdtar --no-same-owner -xf brave-origin.zip -C /app/brave;"
            " else"
            "  mkdir -p deb && bsdtar -xf brave-origin.deb -C deb"
            "  && bsdtar --no-same-owner -xf deb/data.tar.* -C deb"
            "  && cp -a deb/opt/brave.com/brave-origin/. /app/brave/;"
            " fi",
            "test -x /app/brave/brave",
            f"install -Dm 644 {APP_ID}.desktop /app/share/applications/{APP_ID}.desktop",
            f"install -Dm 644 origin.svg /app/share/icons/hicolor/scalable/apps/{APP_ID}.svg",
            "for s in 16 24 32 48 64 128 256; do"
            "  if [ -f /app/brave/product_logo_$s.png ]; then"
            f"    install -Dm 644 /app/brave/product_logo_$s.png /app/share/icons/hicolor/${{s}}x${{s}}/apps/{APP_ID}.png;"
            "  fi;"
            " done",
            "install -Dm 755 brave.sh /app/bin/brave",
            "install -Dm 644 -t /app/etc cobalt.ini",
            f"install -Dm 644 -t /app/share/metainfo {APP_ID}.metainfo.xml",
        ],
        "sources": sources,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flathub-dir", required=True, type=Path, help="checkout of flathub/com.brave.Browser")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--version", required=True)
    ap.add_argument("--matrix", required=True, help="matrix JSON from resolve-release.py")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    args = ap.parse_args()

    flathub = args.flathub_dir
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    matrix = json.loads(args.matrix)

    manifest = yaml.safe_load((flathub / f"{FLATHUB_ID}.yaml").read_text())
    if manifest.get("app-id", manifest.get("id")) != FLATHUB_ID:
        die("Flathub manifest has an unexpected app-id")

    modules = manifest["modules"]
    idx = [i for i, m in enumerate(modules) if isinstance(m, dict) and m.get("name") == "brave"]
    if len(idx) != 1:
        die("expected exactly one module named 'brave' in Flathub's manifest; the layout changed")
    passthrough = [m for i, m in enumerate(modules) if i != idx[0]]
    copy_local_sources(passthrough, flathub, out)

    manifest.pop("id", None)
    manifest["app-id"] = APP_ID
    manifest["modules"] = passthrough[: idx[0]] + [origin_module(matrix)] + passthrough[idx[0]:]

    (out / f"{APP_ID}.yaml").write_text(
        "# Generated by scripts/gen-flatpak-manifest.py from Flathub's com.brave.Browser.yaml.\n"
        "# Do not edit by hand, edit the generator.\n"
        + yaml.safe_dump(manifest, sort_keys=False, width=1000)
    )
    shutil.copy2(flathub / "brave.sh", out / "brave.sh")
    (out / "cobalt.ini").write_text(convert_cobalt((flathub / "cobalt.ini").read_text()))
    (out / f"{APP_ID}.desktop").write_text(convert_desktop((flathub / "brave-browser.desktop").read_text()))
    (out / f"{APP_ID}.metainfo.xml").write_text(metainfo(args.version, args.date))
    print(f"wrote {out / (APP_ID + '.yaml')}")


if __name__ == "__main__":
    main()
