# fusion-git — specification

A Fusion add-in that keeps Fusion designs in a git repository, so mechanical
design can be versioned and shared with the same tooling as the software and PCB
parts of a project. Collaboration is **asynchronous**: one person edits a design
at a time; git cannot merge `.f3d` files, so conflicts are resolved by choosing a
side.

Works with the free personal-use tier: no shared Fusion hub is needed. Each
collaborator has their own Fusion account and their own cloud copy of each design;
the git repository is the source of truth.

## Core mechanism

A synced design has exactly one top-level component, the **wrapper**, which holds
the whole design. The design root contains nothing else.

- **Commit** exports the wrapper component (`ExportManager.createFusionArchiveExportOptions(path, wrapper)`)
  to the design's file in the repo.
- **Reimport** deletes the wrapper's timeline group (`TimelineGroup.deleteMe(True)`),
  deletes the user parameters that import brought in, imports the file into the root
  (`ImportManager.importToTarget2`) and saves. The cloud document keeps its identity;
  each pull is a new version of the same design.
- The wrapper is created once, at Init (or when opening a design from a repo), and is
  stable across every round trip.

Validated in spikes (Fusion 2705.1.15, see *Evidence*): timeline, sketches, features,
user parameters that features use, joints, repeated components and component
attributes survive; geometry stays parametric; no extra nesting after round trips.

## UI

- A **Git** tab in the Design workspace with two sections, *Sync* and *Repository*.
  Buttons are shown/hidden (via their control definitions) on `documentActivated`
  depending on the active design's state; Pull/Push are disabled without a remote.
- **Open from git repository…** lives in the File menu, because it does not need an open design.
- Success messages appear briefly in Fusion's status area (the API has no toast);
  errors and questions use dialogs. Git failures are translated into explanations
  (authentication, rejected push, no remote, network, LFS missing, merge in progress).

| Design state | Buttons |
|---|---|
| Not linked | Init · Status, Settings |
| Linked, repo not found on this machine | Locate repository · Status, Settings (plus a status message on activation) |
| Linked | Commit, Pull, Push, Reimport · Status, Settings |

## Flows

### Init — new repo
1. Precondition checks: parametric design; external references handled (see below).
2. One-time warning: the design is restructured into a wrapper; drawings, CAM setups
   and joints from other designs that point into its geometry break. The pre-Init
   state stays in Fusion's version history.
3. Create `~/Documents/FusionGit/<design name>/`, `git init`, `git lfs track "*.f3d"`,
   write `.fusiongit.json` with defaults.
4. Export the root to `<designs dir>/<design name>.f3d`, clear the design (timeline
   delete all, user parameters), import the file → wrapper. Tag the wrapper with an
   attribute (found by tag, not name). Activate the wrapper so new work lands inside it.
5. Save, commit only the design file + `.gitattributes` + `.fusiongit.json`
   ("Add <design name>"). Optional: add a remote URL.
6. Write design metadata and the local link.

### Init — existing repo
Same as above, but pick a repo folder (must be a git work tree), a target folder
inside it (default from `.fusiongit.json`) and a file name (default: design name).
If the repo lacks LFS tracking for `*.f3d`, offer to add it.

### Open from repo (collaborators)
File menu → Open from repo… → pick an `.f3d` inside a cloned repo → choose Fusion
project/folder and name → the add-in creates the document, imports the file
(wrapper), saves, records metadata and the local link. If the file already has a
link on this machine, open that design instead of creating a duplicate.

### Commit (on save)
- `documentSaved` handler (skipped for the add-in's own saves) asks for a commit
  message: **Commit** / **Skip**. A per-machine setting turns the prompt off; the
  Commit button is always available.
- Warn if the root has content outside the wrapper (it would not be committed).
- Export wrapper → design file, extra exports per `.fusiongit.json`,
  `git add` those files, `git commit --only <files> -m <message>`.
- Record the new sync state.

### Pull
1. Block if the design has uncommitted changes ("commit first", or Reimport with discard).
2. `git fetch`; compare `HEAD`, `@{u}` and the merge base for the design file.
3. Fast-forward: `git merge --ff-only @{u}`, then Reimport if the design file changed.
4. Diverged, design file changed only remotely, or only locally: `git merge @{u}`
   (normal merge commit), Reimport if needed.
5. Diverged and the design file changed on both sides → conflict dialog (below).
6. Explicit fetch + merge instead of `git pull`, so user git config (e.g. `pull.rebase`)
   cannot change the behaviour.

### Conflict resolution
Dialog: **Compare** / **Keep mine** / **Take theirs** / **Cancel**.
- Compare: extract the remote file (`git show @{u}:<path>`, through `git lfs smudge`
  for LFS pointers) to a temp file and open it with `importToNewDocument` as an
  **unsaved** document (does not count toward the 10 editable document limit; closing
  discards it).
- Keep mine / Take theirs: `git merge @{u}`, `git checkout --ours|--theirs <path>`,
  `git add`, complete the merge commit, Reimport for "theirs".
- If any other file in the repo conflicts: `git merge --abort` and tell the user to
  resolve in their git tool.

### Push
`git push`; with no upstream, `git push -u origin HEAD`. Non-fast-forward → "pull first".

### Reimport
Load the design file from the working tree into the design. If the design has
uncommitted changes, require confirmation ("discard my changes"). Covers branch
switches and checkouts done outside Fusion.

### Relink (design opened on a machine without the repo)
The design metadata travels with the cloud document, the local link does not. On
activation of a linked design without a local link: warning state with
**Locate repository** (pick folder, verify a remote matches the stored URL).
Cloning is left to the user's git tool.

## State and metadata

**In the design** (attributes, synced through Fusion's cloud):
`remoteUrl`, `pathInRepo`, `linkId`, `formatVersion`; the wrapper component carries a `wrapper` tag.

**Per machine** (`~/.fusiongit/links.json`, keyed by the design's `linkId` — not the cloud
lineage id, which changes after a new design's first upload):
`repoPath`, `pathInRepo`, `lineage` (latest cloud id, refreshed on activation/save, used to
open an already-linked design), `syncedHash` (sha256 of the design file at last sync),
`dirty` (saved by the user since the last commit).

**Per machine settings** (`~/.fusiongit/settings.json`): commit prompt on save,
default repo folder.

Derived state:
- *Uncommitted design changes*: `document.isModified`, or `dirty` (set by the
  `documentSaved` hook for user saves, cleared by commit/pull/reimport). Comparing
  Fusion version numbers was dropped: `DataFile.versionNumber` lags behind the save.
- *Repo differs from design*: hash of the design file in the working tree ≠ `syncedHash`
  (after a pull, checkout, or edit outside Fusion) → offer Reimport.
- Commits that do not touch the design file (firmware, PCB, docs) never mark it out of date.

## Repo configuration — `.fusiongit.json`

Committed at the repo root, shared by all collaborators.

```json
{
  "designsDir": "mechanical",
  "exports": {
    "step": true,
    "stl": false,
    "thumbnail": true
  },
  "lfs": ["*.f3d", "*.step", "*.stl"]
}
```

Extra exports are written next to the design file (`<name>.step`, `<name>.png`, …)
and committed together with it, for collaborators without Fusion and for previews on
the git host.

## External references (designs inserted into other designs)

Inserting a design from the same project ("Insert into Current Design") creates an
external reference. Plan: **every referenced design is its own synced file in the
same repo**, and an assembly's file gets a sidecar manifest
(`<name>.fusiongit-refs.json`) mapping each referencing occurrence to the repo file
it references.

- Init of an assembly walks `documentReferences` recursively and offers to Init each
  unlinked child into the same repo.
- On open/pull, children are imported before parents. Each reference is re-pointed to
  the collaborator's own cloud document for that repo file (`Occurrence.replace(dataFile, replaceAll)`,
  or `occurrences.addByInsert(dataFile, transform, True)`; both require the same
  Fusion project), then `Document.updateAllReferences()`.

Open questions, to be answered by a spike before implementation:
1. What does an `.f3d` export contain for a referencing occurrence: an embedded copy,
   or a link to the author's cloud document?
2. Does `replace()` keep joints when re-pointing to a different lineage of the same part?

Until then: v1 detects external references and offers **Break link**
(`Occurrence.breakLink()`, embeds the part) or abort.

## Limitations

- Asynchronous collaboration only; no locking in v1 (Git LFS locking is a possible later addition).
- Root-level content ends up one level deeper (inside the wrapper) after Init.
- Drawings, CAM setups and joints from other designs that point into the design's
  geometry break on every reimport.
- User parameters not used by any feature are not synced.
- Saves made on another computer without committing are not detected as uncommitted
  changes (the `dirty` flag is per machine).
- Part designs are converted to Hybrid at Init (they can only hold one component).
- Every commit stores the full binary; Git LFS keeps clones small but storage grows
  (check the git host's LFS quota).

## Git environment

- Find git (and git-lfs) explicitly; Fusion does not inherit the shell `PATH`
  (Homebrew: `/opt/homebrew/bin`).
- Run git off the UI thread with timeouts; `GIT_TERMINAL_PROMPT=0`, so a missing
  credential fails fast instead of hanging. Credentials come from the user's git
  credential helper / SSH agent.

## Out of scope (v1)

- Branch management (use a terminal or git tool, then Reimport).
- Concurrent editing / locking.
- Driving Fusion's "Import New Change" (no API; investigated and rejected, see below).

## Evidence (spikes, 2026-10-06)

| Question | Result |
|---|---|
| `importToTarget2` of an exported component keeps the timeline? | Yes, as one timeline group; parameters still drive geometry |
| Replace a previous import in place | Delete its timeline group + orphaned user parameters, re-import: no duplicates |
| Round trip nesting | Stable when exporting the wrapper component |
| Imported component name | Taken from the `.f3d` file name |
| Multi-component root design | Wrapped into one component; joints, repeated components, parameters intact |
| Unwrap back to root (`moveToComponent`) | Fails / breaks history — not viable |
| Fusion "Import New Change" (manual) | Full fidelity, same document — but no API |
| `UploadCommand` via `Diagnostics.HomeTabExecuteCommand` | Dialog opens, `reImport` ignored, new file created |
| APS Data Management `POST /versions` on a Fusion design | 400 "MIME type must match the previous version's MIME type" |
| Private `wipdata-serv` storage API with own APS app | 403 AUTH-001, client id not allowed |
