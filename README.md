# fusion-git

A Fusion add-in that keeps designs in a git repository, next to the firmware,
PCB and docs of a project. Commit on save, pull, push and reimport from a **Git**
tab. Works with the free personal-use tier: every collaborator has their own
Fusion copy of a design, and git is the source of truth.

Collaboration is asynchronous — git cannot merge `.f3d` files, so when two people
change the same design, one version is chosen (with a side-by-side compare).

See [SPEC.md](SPEC.md) for the design, the limitations and the experiments behind it.

## Install

1. Install [git](https://git-scm.com/downloads) and [Git LFS](https://git-lfs.com)
   (macOS: `brew install git git-lfs`), then run `git lfs install` once.
2. Download `FusionGit-<version>.zip` from the
   [latest release](https://github.com/imdbere/fusion-git/releases/latest) and unzip it.
3. Move the `FusionGit` folder into Fusion's add-ins folder:
   - macOS: `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns`
   - Windows: `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns`
4. In Fusion: **Utilities → Add-Ins → Scripts and Add-Ins → Add-Ins → FusionGit → Run**
   (tick *Run on Startup* to keep it loaded).

Git uses your normal credentials (credential manager or SSH agent). If a push asks
for a password, set up credentials once in a terminal first.

## Use

The **Git** tab shows only what applies to the open design:

| Section | Button | What it does |
|---|---|---|
| Sync | **Init** | Connect the design to a new repository (name + location) or save it into an existing one. Moves the design into one component, exports it, commits. Part designs become Hybrid designs. |
| Sync | **Commit** | Export the design and commit only its files to the current branch. Also offered after every save. *Push after committing* (remembered) makes it Commit and Push. |
| Sync | **Pull** / **Push** | Fetch + merge and load the design / push the branch. Disabled while the repository has no remote. |
| Sync ▾ | **Reimport** | Load the design file from the working tree (after a checkout, or to discard local changes). |
| Sync | **Locate repository** | Shown instead of the above when the design's repository isn't on this computer. |
| Repository | **Status** | Branch, ahead/behind, uncommitted changes. |
| Repository ▾ | **Settings** | Exported files, designs folder, remote URL (per repository); commit prompt, default location (per computer). |

**File → Open from git repository…** opens a `.f3d` from a cloned repository as a new (Hybrid) Fusion design, linked to that file.

Confirmations appear briefly in Fusion's status area (the API has no toast notifications).

Pull, Reimport and Init import the new version first and only then remove the old
contents, so a failed import leaves the design unchanged.

### Repo settings — `.fusiongit.json`

Created by Init at the repo root and shared through git:

```json
{
  "designsDir": "mechanical",
  "exports": { "step": true, "stl": false, "thumbnail": true },
  "lfs": ["*.f3d", "*.step", "*.stl"]
}
```

Per-machine state lives in `~/.fusiongit/` (`links.json`, `settings.json`, `fusiongit.log`).

## License

MIT — see [LICENSE](LICENSE). Thanks to
[FusionToGitHub](https://github.com/zcohen-nerd/FusionToGitHub) for the idea.

## Develop

Link the working copy into Fusion instead of installing a release
(Stop and Run in the Add-Ins dialog reloads the code):

```bash
ln -s "$PWD/FusionGit" "$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/FusionGit"
```

```bash
git submodule update --init --depth 1   # Autodesk's adsk type stubs (~70 MB, only needed for type checking)
npx pyright                             # type check against those stubs
python3 -m unittest discover -s tests   # tests (git logic against real repositories)
python3 tools/make_icons.py             # regenerate toolbar icons
python3 tools/package.py                # build dist/FusionGit-<version>.zip
```

Releases: bump `version` in `FusionGit/FusionGit.manifest`, update `CHANGELOG.md`,
then push a tag `v<version>`; CI tests on macOS and Windows and attaches the zip to
the GitHub release.

The git logic (`repo.py`, `gitcli.py`, `store.py`) has no Fusion dependency and is
tested against real repositories. Fusion-facing code (`design.py`, `actions.py`,
`ui.py`) is import-tested against a mocked API and verified manually:

1. Init a saved design with a few components → one component named after the file, commit in the repo.
2. Edit, save → commit prompt → commit contains only the design files.
3. Second account/machine: Open from repo → same structure and parameters.
4. Change and push on one side, Pull on the other → design updated in place (same cloud document, new version).
5. Change on both sides → Pull shows the conflict dialog; Compare, Keep mine, Take theirs.
6. Open a linked design on a computer without the repo → status message + Locate repository.
