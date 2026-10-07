# How fusion-git works

This page explains what happens under the hood and where the limitations come from.
If you know a way around any of them, issues and pull requests are very welcome.

## The design and the file

A design that is synced with fusion-git has two halves:

- **The Fusion design** in your Fusion cloud project. This is where you work.
- **The `.f3d` file** in the git repository. This is what gets committed, pushed,
  pulled and shared.

Committing exports the design to the file. Pulling (or Reimport) loads the file into
the design. Each collaborator has their own Fusion design, possibly in a different
Fusion account; the file in git is the shared truth.

The design remembers which file it belongs to (stored as attributes inside the
design, so it travels with it through the Fusion cloud). Where that repository lives
on your computer is stored per machine in `~/.fusiongit/links.json`. That's why a
design opened on a second computer asks you to *Locate repository* once.

## Why everything lives in one component

Loading a file into an existing design is the hard part. The obvious ways don't exist
or aren't open to add-ins:

| Approach | Result |
|---|---|
| Fusion's *Import New Change* (Data Panel) | Exactly right: replaces the design, keeps its history. But it is UI only, there is no API for it. |
| Driving that command from an add-in (`UploadCommand` via text commands) | The upload dialog opens, but the "new version" flag is ignored and a separate file is created. |
| Autodesk Platform Services, Data Management API (`POST /versions`) | Rejected for Fusion designs: *"The new version's MIME type must match the previous version's MIME type."* |
| Fusion's internal storage service (what *Import New Change* uses) | Not available to third-party apps (`403 AUTH-001`). |
| Opening the file as a new document each time | Works, but every pull creates a new cloud document, loses the version history and quickly hits the free tier's 10 editable documents. |

What *is* possible is importing a file into an open design. Fusion always imports a
file as **one component**, so fusion-git keeps the whole design inside that one
top-level component:

- **Init** exports the design and imports it back, which moves everything into one
  component named after the file.
- **Commit** exports that component.
- **Pull / Reimport** import the new file first and then delete the old contents.
  If the import fails, the design is left untouched.

Moving the imported content back up to the root component isn't possible without
losing its timeline (we tried), so the extra component level stays.

## Consequences

- Anything outside the top-level component isn't committed. Fusion-git warns you
  when you commit with content outside it.
- **Part** designs can only hold a single component, so Init turns them into **Hybrid**
  designs. Designs opened from a repository are always Hybrid. **Assembly** designs
  aren't supported.
- Every pull replaces the geometry. Drawings, manufacturing setups and joints in other
  designs that point at this design's geometry need fixing afterwards.
- User parameters that no feature uses aren't exported, so they don't sync.
- Designs that insert other designs as external references aren't supported yet; Init
  offers to embed them instead. Syncing each referenced design as its own file is
  planned.

## Tracking changes

- A design has uncommitted changes when it has unsaved edits, or when it was saved
  since the last commit (fusion-git notices saves). This is tracked per computer, so a
  save made on another computer with the same Fusion account isn't detected.
- The repository version differs from the design when the `.f3d` file in the working
  tree changed since the last sync (after a pull, checkout or merge). *Reimport*
  loads it.

## Git details

- Commits use `git commit --only`, so only the design's file and its exports are
  committed, whatever else is staged.
- Pull runs `git fetch` and `git merge` explicitly, so your `pull.rebase` setting
  doesn't change the behaviour.
- A conflict is resolved for the design file and its exports (`.step`, `.stl`, `.png`)
  together, so the exports always match the design. Conflicts in any other file stop
  the pull without changing anything.
- Design files are stored with Git LFS. Clones made without LFS active contain small
  pointer files; fusion-git downloads the real file before importing.
- Git runs with `GIT_TERMINAL_PROMPT=0`, so missing credentials fail with a message
  instead of hanging Fusion.
