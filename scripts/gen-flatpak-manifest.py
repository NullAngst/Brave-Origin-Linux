#!/usr/bin/env python3
"""Turn Flathub's com.brave.Browser manifest into a Brave Origin manifest for
one channel (stable, beta or nightly).

Flathub's manifest already does the hard parts: the Chromium BaseApp, zypak,
the dconf patch, the sandbox permissions and the host policy symlinks. Origin
is the same browser build with different branding, so instead of maintaining
a copy of all that, this script reads Flathub's current manifest and swaps out
only the Brave-specific pieces:

  - app ID per channel (channels.json), and the Flatpak branch is the channel
  - CHROME_VERSION_EXTRA is set to the channel. Brave reads it at startup to
    pick its channel and profile dir, so a beta build without it would use
    the stable profile dir
  - the "brave" module installs the Origin zip (or .deb) from Brave's GitHub
    release instead of the regular Brave zip, with the channel's icons
  - cobalt.ini points at the channel's profile dir
  - the desktop file is renamed and loses the Tor action (Origin has no Tor)
  - a fresh metainfo file

Every other module and every other finish-arg is passed through untouched, so
when Flathub bumps the runtime, zypak or permissions, the next build picks it
up.
"""

import argparse
import datetime
import json
import re
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

FLATHUB_ID = "com.brave.Browser"
CHANNELS = json.loads((Path(__file__).resolve().parent.parent / "channels.json").read_text())

DESCRIPTION = (
    "Brave Origin keeps Brave's privacy protections, ad blocking and speed "
    "(Brave Shields) and leaves out the extra features such as Rewards, "
    "Wallet, Leo, News, VPN and Tor."
)
CHANNEL_NOTE = {
    "stable": "",
    "beta": "This is the Beta channel, which gets new features a few weeks before stable.",
    "nightly": "This is the Nightly channel, built from Brave's development branch every day or so. Expect bugs.",
}


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


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


def convert_desktop(text, ch):
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
                line = line[: line.index("=") + 1] + ch["name"]
            elif line.startswith("StartupWMClass="):
                line = f"StartupWMClass={ch['slug']}"
            elif line.startswith("Icon="):
                line = f"Icon={ch['app_id']}"
            elif line.startswith("Actions="):
                actions = [a for a in line[len("Actions="):].split(";") if a and a != "new-tor-window"]
                line = "Actions=" + ";".join(actions) + ";"
        out.append(line)
    result = "\n".join(out) + "\n"
    if f"Name={ch['name']}" not in result:
        die("could not find the Name=Brave line in Flathub's desktop file")
    return result


def convert_cobalt(text, ch):
    text, n = re.subn(r"(?m)^ConfigDir=.*$", f"ConfigDir={ch['profile_dir']}", text)
    if n != 1:
        die("could not find ConfigDir= in Flathub's cobalt.ini")
    return text


def convert_finish_args(args, channel):
    out, found = [], False
    for arg in args:
        if arg.startswith("--env=CHROME_VERSION_EXTRA="):
            arg = f"--env=CHROME_VERSION_EXTRA={channel}"
            found = True
        out.append(arg)
    if not found:
        out.append(f"--env=CHROME_VERSION_EXTRA={channel}")
    return out


def metainfo(ch, channel, version, date):
    note = f"\n    <p>{escape(CHANNEL_NOTE[channel])}</p>" if CHANNEL_NOTE[channel] else ""
    summary = "Brave's privacy browser without the extra features"
    if channel != "stable":
        summary += f" ({channel})"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>{ch['app_id']}</id>
  <name>{escape(ch['name'])}</name>
  <developer id="com.brave">
    <name>Brave Software</name>
  </developer>
  <summary>{escape(summary)}</summary>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>MPL-2.0</project_license>
  <description>
    <p>{escape(DESCRIPTION)}</p>{note}
    <p>This is an unofficial Flatpak repackaging of the Brave Origin Linux build that Brave publishes on GitHub. It is not affiliated with Brave Software.</p>
  </description>
  <launchable type="desktop-id">{ch['app_id']}.desktop</launchable>
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


def origin_module(ch, entries):
    app_id = ch["app_id"]
    sfx = ch["icon_suffix"]
    sources = []
    for entry in entries:
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
        {"type": "file", "path": f"{app_id}.desktop"},
        {"type": "file", "path": f"{app_id}.metainfo.xml"},
    ]
    return {
        "name": "brave",
        "buildsystem": "simple",
        "build-commands": [
            "mkdir -p /app/brave",
            # Brave publishes a zip for every release so far. The .deb branch
            # is a fallback in case some release only has the .deb.
            "if [ -f brave-origin.zip ]; then"
            "  bsdtar --no-same-owner -xf brave-origin.zip -C /app/brave;"
            " else"
            "  mkdir -p deb && bsdtar -xf brave-origin.deb -C deb"
            "  && bsdtar --no-same-owner -xf deb/data.tar.* -C deb"
            f"  && cp -a deb/opt/brave.com/{ch['slug']}/. /app/brave/;"
            " fi",
            "test -x /app/brave/brave",
            f"install -Dm 644 {app_id}.desktop /app/share/applications/{app_id}.desktop",
            # Brave ships the channel's icons in the package as
            # product_logo_<size><suffix>.png, e.g. product_logo_256_beta.png.
            f"test -f /app/brave/product_logo_256{sfx}.png",
            "for s in 16 24 32 48 64 128 256; do"
            f"  if [ -f /app/brave/product_logo_${{s}}{sfx}.png ]; then"
            f"    install -Dm 644 /app/brave/product_logo_${{s}}{sfx}.png /app/share/icons/hicolor/${{s}}x${{s}}/apps/{app_id}.png;"
            "  fi;"
            " done",
            "install -Dm 755 brave.sh /app/bin/brave",
            "install -Dm 644 -t /app/etc cobalt.ini",
            f"install -Dm 644 -t /app/share/metainfo {app_id}.metainfo.xml",
        ],
        "sources": sources,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flathub-dir", required=True, type=Path, help="checkout of flathub/com.brave.Browser")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--channel", required=True, choices=sorted(CHANNELS))
    ap.add_argument("--matrix", required=True, help="matrix JSON from resolve-release.py")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    args = ap.parse_args()

    ch = CHANNELS[args.channel]
    flathub = args.flathub_dir
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    entries = [e for e in json.loads(args.matrix) if e["channel"] == args.channel]
    if not entries:
        die(f"no {args.channel} entries in the matrix")
    versions = {e["version"] for e in entries}
    if len(versions) != 1:
        die(f"{args.channel} entries disagree on version: {sorted(versions)}")
    version = versions.pop()

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
    manifest["app-id"] = ch["app_id"]
    manifest["branch"] = args.channel
    manifest["finish-args"] = convert_finish_args(manifest.get("finish-args", []), args.channel)
    manifest["modules"] = passthrough[: idx[0]] + [origin_module(ch, entries)] + passthrough[idx[0]:]

    path = out / f"{ch['app_id']}.yaml"
    path.write_text(
        "# Generated by scripts/gen-flatpak-manifest.py from Flathub's com.brave.Browser.yaml.\n"
        "# Do not edit by hand, edit the generator.\n"
        + yaml.safe_dump(manifest, sort_keys=False, width=1000)
    )
    shutil.copy2(flathub / "brave.sh", out / "brave.sh")
    (out / "cobalt.ini").write_text(convert_cobalt((flathub / "cobalt.ini").read_text(), ch))
    (out / f"{ch['app_id']}.desktop").write_text(convert_desktop((flathub / "brave-browser.desktop").read_text(), ch))
    (out / f"{ch['app_id']}.metainfo.xml").write_text(metainfo(ch, args.channel, version, args.date))
    print(path)


if __name__ == "__main__":
    main()
