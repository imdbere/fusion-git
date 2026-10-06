# Changelog

## 0.1.0

First release.

- Git tab: Init, Commit, Pull, Push, Reimport, Status, Settings; Locate repository for designs whose repository is not on this computer.
- File → Open from git repository… opens a design file from a cloned repository.
- Commit on save (optional), Commit and Push, only the design's own files are committed.
- Pull with conflict resolution: Compare, Keep mine, Take theirs (design and its exports together).
- Safe replace: the new version is imported before the old contents are removed.
- Git LFS: tracked by default, pointer files from LFS-less clones are downloaded automatically.
- Repository settings in `.fusiongit.json` (STEP/STL/PNG exports, designs folder), remote URL from Settings.
- Readable explanations for common git errors.
