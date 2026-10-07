# Brave Origin Linux

This repo builds [Brave Origin](https://brave.com/origin/linux/) Stable, Beta and Nightly as Flatpaks and AppImages for x86_64 and aarch64, using GitHub Actions to watch Brave's releases and rebuild automatically. It's for anyone who wants Origin without adding Brave's apt/rpm repo, or on a distro that repo doesn't cover.

It's unofficial and not affiliated with Brave Software. Nothing here is compiled from source: the browser inside every package is Brave's own Linux build, taken unmodified from the [brave/brave-browser releases](https://github.com/brave/brave-browser/releases).

## Channels

| Channel | Flatpak app ID | Flatpak updates | AppImage updates | Release |
|---|---|---|---|---|
| Stable | `com.brave.Origin` | `flatpak update` | zsync | versioned, e.g. `v1.96.61` |
| Beta | `com.brave.OriginBeta` | reinstall the newer bundle | zsync | rolling `beta` prerelease |
| Nightly | `com.brave.OriginNightly` | `flatpak update` | zsync | rolling `nightly` prerelease |

Each channel is its own app with its own profile, so you can have all three installed side by side.

Why no `flatpak update` for Beta? GitHub Pages caps a site at 1 GB, and each channel is about 180 MB per architecture. All three would come to about 1.05 GB. Stable and Nightly together are about 720 MB. If you'd rather host Beta than Nightly, change `HOSTED_CHANNELS` at the top of `.github/workflows/build.yml`.

## How it works

Every 6 hours the workflow in `.github/workflows/build.yml` does this:

1. `scripts/resolve-release.py` finds the newest Origin build for each channel on Brave's releases (stable on normal releases, beta and nightly on prereleases, told apart by the asset name) and gets the sha256 of each file. A channel that this repo already has at that version gets skipped.
2. `scripts/gen-flatpak-manifest.py` clones Flathub's [com.brave.Browser](https://github.com/flathub/com.brave.Browser) manifest and rewrites it for the channel: its app ID, the Origin download instead of regular Brave, the channel's profile dir and icons, `CHROME_VERSION_EXTRA` set to the channel, and no Tor launcher action (Origin has no Tor). Everything else (runtime, Chromium BaseApp, zypak, permissions) comes straight from Flathub, so when they bump something, the next build here picks it up with no edits.
3. `flatpak-builder` builds that manifest, natively on an x86_64 runner and an arm64 runner.
4. `scripts/build-appimage.sh` unpacks the same Origin download into an AppDir and runs the latest `appimagetool` on it.
5. `scripts/publish-flatpak-repo.sh` puts the hosted channels into one OSTree repo, signs it with your GPG key, and makes the `.flatpak` bundles. A hosted channel that didn't change this run gets copied over from the live Pages site unchanged, since a Pages deploy replaces the whole site.
6. `scripts/publish-releases.sh` uploads the bundles, AppImages and a `SHA256SUMS` file. Stable gets a versioned release marked latest. Beta and Nightly each have one rolling prerelease that gets replaced, so the releases page doesn't fill up with a nightly every day.
7. The signed repo gets deployed to GitHub Pages.

Pages only ever holds the latest build of each hosted channel. Clients don't need the old versions to update, they just pull what changed.

Nothing is version-pinned in this repo. The Brave versions, the Flathub manifest and appimagetool are all resolved fresh on each run.

The channel names, app IDs and profile dirs all live in `channels.json`.

## Setting up the repo

Prerequisites:

- A public GitHub repo. The arm64 build uses the `ubuntu-24.04-arm` runner, which is free for public repos only, and GitHub Pages on a free account also needs a public repo. On a private repo, delete the aarch64 entry from `RUNNER` in `scripts/resolve-release.py` or you'll get stuck jobs.
- `gpg` on your machine, for the one-time key setup.

Steps:

1. Create the repo on GitHub, I use `NullAngst/Brave-Origin-Linux`, name yours whatever you like. Don't push yet.
2. Make a signing key with no passphrase, since the workflow can't type one: `gpg --batch --passphrase '' --quick-gen-key "Brave Origin Flatpak" rsa4096 sign never`
3. Export it to a file outside the repo folder: `gpg --armor --export-secret-keys "Brave Origin Flatpak" > ~/origin-key.asc`
4. In the repo on GitHub, go to Settings, Secrets and variables, Actions, and click "New repository secret". Name it `FLATPAK_GPG_PRIVATE_KEY` and paste in the whole file, from `-----BEGIN PGP PRIVATE KEY BLOCK-----` through `-----END PGP PRIVATE KEY BLOCK-----`. `wl-copy < ~/origin-key.asc` (or `xclip -selection clipboard < ~/origin-key.asc` on X11) puts it on your clipboard.
4.5. Move `~/origin-key.asc` somewhere safe as your backup, or `shred -u` it once it's backed up some other way.
5. In the repo on GitHub, go to Settings, Pages, and set Source to "GitHub Actions".
6. Push the contents of this folder to the `main` branch. The push itself triggers a build of every channel, since the workflow file changed.
7. Watch the run under the Actions tab. The first Flatpak builds take a while since they download the Freedesktop SDK and the Chromium BaseApp.
8. Once it finishes, the releases show up under Releases, and `https://<you>.github.io/<repo>/` shows the install commands.

DON'T LOSE OR REPLACE THE SIGNING KEY. Every installed copy pins the public key, so a new key means every user has to remove and re-add the remote before they get updates again. And never commit `origin-key.asc`, it has no passphrase.

If you skip steps 2 to 5, everything still builds. You just get bundles that don't update and no Pages repo, and the run shows a warning saying so.

If you put Pages on a custom domain, set a repo variable named `PAGES_URL` (Settings, Secrets and variables, Actions, Variables) to the full URL with a trailing slash, since the default `https://<owner>.github.io/<repo>/` guess won't match.

To rebuild versions that are already released (say Flathub changed a permission and you want it now), go to Actions, pick "Build Brave Origin", hit "Run workflow" and tick "force". The channels box limits it, e.g. `stable` or `beta nightly`. Blank means all three.

A push to `main` that touches the scripts builds every channel as a test, but only releases channels that have a new version.

GITHUB DISABLES SCHEDULED WORKFLOWS AFTER 60 DAYS WITHOUT A COMMIT TO THE REPO. Releases made by the workflow don't count. You get an email before it happens. If it does get disabled, go to Actions, pick the workflow and click "Enable workflow", or just push any commit.

## Installing the Flatpak

You need Flathub set up as a remote, since Origin pulls its runtime from there. Most distros that ship Flatpak already have it. I'm using my own Pages address below, swap in yours.

Stable:

```sh
flatpak install --user https://nullangst.github.io/Brave-Origin-Linux/brave-origin.flatpakref
```

Nightly:

```sh
flatpak install --user https://nullangst.github.io/Brave-Origin-Linux/brave-origin-nightly.flatpakref
```

Either one adds a remote called `brave-origin`, so `flatpak update`, GNOME Software and Discover all see new versions like they would for Flathub apps. Every update is checked against the GPG key, and Flatpak refuses anything unsigned.

Beta: download `brave-origin-beta-<version>-x86_64.flatpak` (or `aarch64`) from the `beta` release and run `flatpak install --user ./brave-origin-beta-<version>-x86_64.flatpak`. To update, download the newer one and run the same command, it replaces the old one and keeps your profile.

The Stable and Nightly bundles on the releases page work too, for offline installs. They carry the repo address and key, so a bundle install also updates through `flatpak update` afterwards.

Profiles live in `~/.var/app/<app ID>/config/BraveSoftware/`, as `Brave-Origin`, `Brave-Origin-Beta` or `Brave-Origin-Nightly`, separate from any regular Brave or native Origin install. Policies in `/etc/brave/policies` on the host are picked up the same way as Flathub's Brave.

## Installing the AppImage

1. Download the AppImage for your channel and architecture: `Brave-Origin-<version>-x86_64.AppImage` from the latest release, or `Brave-Origin-Beta-...` / `Brave-Origin-Nightly-...` from the `beta` / `nightly` releases.
2. Make it executable: `chmod +x Brave-Origin-*.AppImage`
3. Run it: `./Brave-Origin-<version>-x86_64.AppImage`

Updates: each AppImage has update info embedded that points at its channel's release here. AppImageUpdate, or Gear Lever, or whichever AppImage manager you use, can read it and download only the changed blocks.

Profiles live in `~/.config/BraveSoftware/Brave-Origin` (or `-Beta`, `-Nightly`), the same place a native rpm/deb install of that channel would use. If you have both, they share it.

Two things to know about the AppImage:

- It uses your system's libraries (NSS, GTK, ALSA, libgbm and so on), the same ones Brave's own .deb and .rpm depend on. Any normal desktop install already has them. A minimal or server install might not.
- Chromium's sandbox needs unprivileged user namespaces, since the setuid sandbox helper can't work inside an AppImage. openSUSE, Fedora, Arch and Debian allow them by default. Ubuntu 24.04 and later block them through AppArmor unless the app has a profile, so on Ubuntu you'll get a "No usable sandbox" error. The fix is an AppArmor profile for the AppImage path, or use the Flatpak instead. Running with `--no-sandbox` also "works", but it turns off the browser's main defense against hostile web pages. Don't.

## Verifying downloads

The build checks each Brave download against the sha256 GitHub reports for that release asset. It does not check any GPG signature, so you're trusting GitHub to serve what Brave uploaded. `SHA256SUMS` on each release covers the files this repo built: `sha256sum -c SHA256SUMS --ignore-missing`

## When a build breaks

- "no complete Brave Origin ... build found": Brave changed the asset naming, or stopped shipping a channel or architecture. Check the assets on a recent [Brave release](https://github.com/brave/brave-browser/releases) and update `ASSET_RE` in `scripts/resolve-release.py` to match. The other channels keep building in the meantime.
- "no ... in the package, icon naming changed?" or "no brave binary in the package": Brave changed what's inside the zip. Unzip one and compare with `scripts/build-appimage.sh` and `scripts/gen-flatpak-manifest.py`.
- "expected exactly one module named 'brave'" or "could not find ...": Flathub restructured their manifest. Look at what changed in [flathub/com.brave.Browser](https://github.com/flathub/com.brave.Browser) and adjust `scripts/gen-flatpak-manifest.py`.
- `flatpak-builder` failing on dconf or zypak: that's Flathub's part of the manifest, check whether their own build is failing too.
- A warning that a channel "is not on the Pages repo": the live site didn't have it and this run didn't build it. Run the workflow with force and that channel.

## Building locally

Flatpak, on a machine with `flatpak-builder`, `jq` and Python with PyYAML. This builds Nightly, use `stable` or `beta` for the others:

```sh
git clone --depth 1 https://github.com/flathub/com.brave.Browser.git flathub-brave
python3 scripts/resolve-release.py --build-all > release.json
python3 scripts/gen-flatpak-manifest.py --flathub-dir flathub-brave --out-dir flatpak \
  --channel nightly --matrix "$(jq -c .matrix release.json)"
flatpak-builder --user --install-deps-from=flathub --force-clean --install build flatpak/com.brave.OriginNightly.yaml
```

Set `GH_TOKEN` to a GitHub token if you hit the API rate limit, it's optional otherwise.

AppImage, with `curl`, `bsdtar` (libarchive-tools, or whichever package has it on your distro) and `jq`:

```sh
m=$(jq -c '.matrix[] | select(.channel=="nightly" and .arch=="x86_64")' release.json)
bash scripts/build-appimage.sh nightly x86_64 "$(jq -r .url <<<"$m")" "$(jq -r .sha256 <<<"$m")" \
  "$(jq -r .kind <<<"$m")" "$(jq -r .version <<<"$m")"
```

The AppImage ends up in `dist/`. Leave off the last `owner/repo` argument for local builds, so it doesn't embed update info pointing at the GitHub releases.
