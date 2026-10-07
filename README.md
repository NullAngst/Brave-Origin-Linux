# Brave Origin Linux

This repo builds [Brave Origin](https://brave.com/origin/linux/) as a Flatpak and an AppImage for x86_64 and aarch64, using GitHub Actions to watch Brave's releases and rebuild automatically. Both update themselves: the Flatpak through a signed repo hosted on GitHub Pages, the AppImage through zsync against the GitHub releases. It's for anyone who wants Origin without adding Brave's apt/rpm repo, or on a distro that repo doesn't cover.

It's unofficial and not affiliated with Brave Software. Nothing here is compiled from source: the browser inside both packages is Brave's own Linux build, taken unmodified from the [brave/brave-browser releases](https://github.com/brave/brave-browser/releases).

## How it works

Every 6 hours the workflow in `.github/workflows/build.yml` does this:

1. `scripts/resolve-release.py` finds the newest stable Brave release that has Brave Origin Linux assets attached, and gets the sha256 of each one. If this repo already has a release with that version, it stops here.
2. `scripts/gen-flatpak-manifest.py` clones Flathub's [com.brave.Browser](https://github.com/flathub/com.brave.Browser) manifest and rewrites it into `com.brave.Origin`: new app ID, the Origin download instead of regular Brave, the Origin profile dir, no Tor launcher action (Origin has no Tor). Everything else (runtime, Chromium BaseApp, zypak, permissions) comes straight from Flathub, so when they bump something, the next build here picks it up with no edits.
3. `flatpak-builder` builds that manifest, natively on an x86_64 runner and an arm64 runner.
4. `scripts/build-appimage.sh` unpacks the same Origin download into an AppDir and runs the latest `appimagetool` on it.
5. `scripts/publish-flatpak-repo.sh` merges both Flatpak builds into one OSTree repo, signs it with your GPG key, and makes the `.flatpak` bundles.
6. The bundles, AppImages and a `SHA256SUMS` file go up as a GitHub release tagged with Brave's version, and the signed repo gets deployed to GitHub Pages.

The Pages site only ever holds the latest version. That keeps it to a few hundred MB for both arches, under the 1 GB Pages limit. Clients don't need the old versions to update, they just pull what changed.

Nothing is version-pinned in this repo. The Brave version, the Flathub manifest and appimagetool are all resolved fresh on each run.

## Setting up the repo

Prerequisites:

- A public GitHub repo. The arm64 build uses the `ubuntu-24.04-arm` runner, which is free for public repos only, and GitHub Pages on a free account also needs a public repo. On a private repo, delete the aarch64 entry from `RUNNER` in `scripts/resolve-release.py` or you'll get stuck jobs.
- `gpg` and the `gh` CLI on your machine, for the one-time key setup. You can paste the key into the web UI instead of using `gh` if you prefer.

Steps:

1. Create the repo on GitHub, I use `NullAngst/Brave-Origin-Linux`, name yours whatever you like. Don't push yet.
2. Make a signing key with no passphrase, since the workflow can't type one: `gpg --batch --passphrase '' --quick-gen-key "Brave Origin Flatpak" rsa4096 sign never`
3. Store it as a repo secret: `gpg --armor --export-secret-keys "Brave Origin Flatpak" | gh secret set FLATPAK_GPG_PRIVATE_KEY -R NullAngst/Brave-Origin-Linux` (swap in your repo name).
3.5. Back up that key somewhere safe, then you can delete it from your keyring with `gpg --delete-secret-and-public-keys "Brave Origin Flatpak"` if you don't want it lying around.
4. In the repo on GitHub, go to Settings, Pages, and set Source to "GitHub Actions".
5. Push the contents of this folder to the `main` branch. The push itself triggers a build, since the workflow file changed.
6. Watch the run under the Actions tab. The first Flatpak build takes a while since it downloads the Freedesktop SDK and the Chromium BaseApp.
7. Once it finishes, the release shows up under Releases, and `https://<you>.github.io/<repo>/` shows the install command.

DON'T LOSE OR REPLACE THE SIGNING KEY. Every installed copy pins the public key, so a new key means every user has to remove and re-add the remote before they get updates again.

If you skip steps 2 to 4, everything still builds. You just get bundles that don't update and no Pages repo, and the run shows a warning saying so.

If you put Pages on a custom domain, set a repo variable named `PAGES_URL` (Settings, Secrets and variables, Actions, Variables) to the full URL with a trailing slash, since the default `https://<owner>.github.io/<repo>/` guess won't match.

To rebuild a version that's already released (say Flathub changed a permission and you want it now), go to Actions, pick "Build Brave Origin", hit "Run workflow" and tick "force". It replaces the files on the existing release.

GITHUB DISABLES SCHEDULED WORKFLOWS AFTER 60 DAYS WITHOUT A COMMIT TO THE REPO. Releases made by the workflow don't count. You get an email before it happens. If it does get disabled, go to Actions, pick the workflow and click "Enable workflow", or just push any commit.

## Installing the Flatpak

You need Flathub set up as a remote, since Origin pulls its runtime from there. Most distros that ship Flatpak already have it.

1. Install it: `flatpak install --user https://nullangst.github.io/Brave-Origin-Linux/brave-origin.flatpakref` (swap in your own Pages address).
2. Run it from your app menu, or `flatpak run com.brave.Origin`.

That adds a remote called `brave-origin`, so `flatpak update`, GNOME Software and Discover all see new versions like they would for Flathub apps. Every update is checked against the GPG key, and Flatpak refuses anything unsigned.

The `.flatpak` bundles on the releases page work too, for offline installs: `flatpak install --user ./brave-origin-<version>-x86_64.flatpak`. They carry the repo address and key, so a bundle install also updates through `flatpak update` afterwards.

Your profile lives in `~/.var/app/com.brave.Origin/config/BraveSoftware/Brave-Origin`, separate from any regular Brave or native Origin install. Policies in `/etc/brave/policies` on the host are picked up the same way as Flathub's Brave.

## Installing the AppImage

1. Download `Brave-Origin-<version>-x86_64.AppImage` from the latest release, or the `aarch64` one.
2. Make it executable: `chmod +x Brave-Origin-*.AppImage`
3. Run it: `./Brave-Origin-<version>-x86_64.AppImage`

Updates: the AppImage has update info embedded that points at this repo's latest release. AppImageUpdate, or Gear Lever, or whichever AppImage manager you use, can read it and download only the changed blocks.

Your profile lives in `~/.config/BraveSoftware/Brave-Origin`, the same place a native rpm/deb install of Origin would use. If you have both, they share it.

Two things to know about the AppImage:

- It uses your system's libraries (NSS, GTK, ALSA, libgbm and so on), the same ones Brave's own .deb and .rpm depend on. Any normal desktop install already has them. A minimal or server install might not.
- Chromium's sandbox needs unprivileged user namespaces, since the setuid sandbox helper can't work inside an AppImage. openSUSE, Fedora, Arch and Debian allow them by default. Ubuntu 24.04 and later block them through AppArmor unless the app has a profile, so on Ubuntu you'll get a "No usable sandbox" error. The fix is an AppArmor profile for the AppImage path, or use the Flatpak instead. Running with `--no-sandbox` also "works", but it turns off the browser's main defense against hostile web pages. Don't.

## Verifying downloads

The build checks each Brave download against the sha256 GitHub reports for that release asset. It does not check any GPG signature, so you're trusting GitHub to serve what Brave uploaded. `SHA256SUMS` on each release covers the files this repo built: `sha256sum -c SHA256SUMS --ignore-missing`

## When a build breaks

- "no Brave Origin Linux assets found": Brave changed the asset naming. The error lists the Linux assets it did see. Update `ASSET_RE` in `scripts/resolve-release.py` to match.
- "expected exactly one module named 'brave'" or "could not find ...": Flathub restructured their manifest. Look at what changed in [flathub/com.brave.Browser](https://github.com/flathub/com.brave.Browser) and adjust `scripts/gen-flatpak-manifest.py`.
- `flatpak-builder` failing on dconf or zypak: that's Flathub's part of the manifest, check whether their own build is failing too.

## Building locally

Flatpak, on a machine with `flatpak-builder` and Python with PyYAML:

```sh
git clone --depth 1 https://github.com/flathub/com.brave.Browser.git flathub-brave
GH_TOKEN=$(gh auth token) python3 scripts/resolve-release.py > release.json
python3 scripts/gen-flatpak-manifest.py --flathub-dir flathub-brave --out-dir flatpak \
  --version "$(jq -r .version release.json)" --matrix "$(jq -c .matrix release.json)"
flatpak-builder --user --install-deps-from=flathub --force-clean --install build flatpak/com.brave.Origin.yaml
```

`GH_TOKEN` is optional, it just avoids GitHub's unauthenticated rate limit.

AppImage, with `curl`, `bsdtar` (libarchive-tools, or whichever package has it on your distro) and `jq`:

```sh
m=$(jq -c '.matrix[] | select(.arch=="x86_64")' release.json)
bash scripts/build-appimage.sh x86_64 "$(jq -r .url <<<"$m")" "$(jq -r .sha256 <<<"$m")" \
  "$(jq -r .kind <<<"$m")" "$(jq -r .version release.json)"
```

The AppImage ends up in `dist/`. Leave off the last `owner/repo` argument for local builds, so it doesn't embed update info pointing at the GitHub releases.
