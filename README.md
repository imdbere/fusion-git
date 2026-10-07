<p align="center"><img src="docs/logo.svg" width="112" alt=""></p>

# fusion-git

fusion-git is an add-in for Autodesk Fusion that lets you collaborate on designs
through git, also on Fusion's free personal tier. Collaborators can use different
Fusion accounts, and the source of truth is the git repository, right next to the
rest of your project: firmware, PCB files, documentation. Design files are stored
with Git LFS, so the repository stays small.

## Features

- **Init, Pull, Push and Commit from a Git tab in Fusion**, no terminal needed for
  everyday work.
- **Commit on save**: after saving, Fusion asks for a commit message. Optionally push
  right away.
- **Conflict resolution**: if you and a collaborator changed the same design, open the
  remote version in a second tab to compare, then keep yours or take theirs.
- **Automatic exports**: every commit can also write a STEP file and a PNG preview (and
  STL), configurable per repository, so people without Fusion can use the parts.
- **Open designs from any clone** with *File → Open from git repository*.

## Limitations

- **The whole design lives inside one top-level component.** Init sets this up
  automatically. It's a consequence of what Fusion's API allows; the
  [technical details](docs/how-it-works.md) explain why. If you know a way around it,
  pull requests are very welcome.
- **Part and Hybrid designs only.** Init turns Part designs into Hybrid designs.
  Assembly designs aren't supported.
- **Collaboration is turn-based.** Git can't merge Fusion designs and Fusion has no way
  to compare or merge two versions, so a conflict means picking one version. Agree on
  who works on which design.
- **Designs that insert other designs (external references) aren't synced yet.** Init
  can embed them instead. Syncing them as separate files is planned.
- **Pulling replaces the geometry**, so drawings, manufacturing setups and joints in
  other designs that reference it may need fixing afterwards.

## Install

1. Install [git](https://git-scm.com/downloads) and [Git LFS](https://git-lfs.com)
   (macOS: `brew install git git-lfs`) and run `git lfs install` once.
2. Download `FusionGit-<version>.zip` from the
   [latest release](https://github.com/imdbere/fusion-git/releases/latest) and unzip it
   somewhere permanent.
3. In Fusion, open **Utilities → Add-Ins → Scripts and Add-Ins**, click **+** and choose
   **Script or add-in from device**. Select the unzipped `FusionGit` folder.
4. Turn FusionGit on and tick **Run on Startup**.

A new **Git** tab appears in the Design workspace; that's where you'll find all of
fusion-git's features. Opening a design from a repository is in the **File** menu.

Git uses your normal credentials (credential manager or SSH agent). If git has never
pushed to your host from this computer, push once from a terminal to set that up.

## How to use

### How it fits together

Each synced design has two halves: the **design** in your Fusion project, where you
work, and its **`.f3d` file** in the repository, which is what gets committed and
shared.

- **Commit** exports the design to the file and commits it.
- **Pull** fetches and merges, then loads the file into your design. Your design stays
  the same Fusion document; the pull shows up as a new version in its history.
- **Reimport** only does the second half: it loads the file from your working copy into
  the design. Use it after you changed the repository outside Fusion, for example after
  switching branches, or to throw away changes you haven't committed.

### Put an existing design on git

1. Open the design (save it first if it's new) and click **Git → Init**.
2. Choose **New repository** (name and location) or **Existing repository** (pick where
   the file should go in a repo you already have).
3. fusion-git moves the design into one component, writes the file, sets up Git LFS and
   makes the first commit. Add a remote in **Repository → Settings** and click **Push**.

### Open a design from git

1. Clone the repository as usual (`git clone …`).
2. In Fusion, choose **File → Open from git repository…** and select the `.f3d` file.
3. fusion-git creates a new design in the active project and links it to the file.
   From then on, Pull and Commit work in both directions.

### Day to day

Work and save as usual. When fusion-git asks for a commit message, describe the change
(or cancel and commit later with **Commit**). **Pull** before you start working on a
design someone else changed, **Push** when you're done.

**Repository → Settings** controls the exported files, the folder for new design files
and the remote. These are stored in `.fusiongit.json` and shared with everyone using
the repository.

## FAQ

**What about branches, rebasing, tags and other git features?**
Use your usual git tool or the command line; fusion-git doesn't get in the way. After
anything that changes the design file in your working copy (checkout, merge, reset),
click **Reimport** to load it into Fusion.

**Which git hosts work?**
Any: GitHub, GitLab, Gitea, a server of your own. The host needs to support Git LFS
for the design files.

**Do collaborators need a paid Fusion subscription?**
No. Every collaborator works in their own Fusion account; nothing is shared through
Fusion's cloud.

**I opened a synced design on another computer and it asks me to locate the
repository.**
The design knows which file it belongs to, but not where the repository is cloned on
that computer. Clone it and click **Locate repository**.

**Can I edit the `.f3d` file outside Fusion?**
No. Treat it as Fusion's output. Change the design in Fusion and commit.

**Where are the logs?**
In `~/.fusiongit/fusiongit.log`, together with the per-computer settings.

## License

MIT, see [LICENSE](LICENSE). Thanks to
[FusionToGitHub](https://github.com/zcohen-nerd/FusionToGitHub) for the idea.

## Develop

```bash
git clone https://github.com/imdbere/fusion-git.git && cd fusion-git
git submodule update --init --depth 1   # Autodesk's API type stubs, for type checking
python3 -m unittest discover -s tests
npx pyright
```

Add the `FusionGit` folder in Fusion's Scripts and Add-Ins dialog (**+ → Script or
add-in from device**); Stop and Run reloads the code. `python3 tools/build_icons.py`
rebuilds the toolbar icons from `tools/icons`, `python3 tools/package.py` builds the
release zip. Pushing a `v<version>` tag that matches the manifest publishes a release.
