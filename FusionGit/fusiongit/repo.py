"""Git operations on a repository that holds synced design files. No Fusion imports.

Paths inside the repo (`path`) are POSIX-style and relative to the repo root,
which is how they are stored in design metadata.
"""

import hashlib
import os
from dataclasses import dataclass, field

from .gitcli import NETWORK_TIMEOUT, Git, GitError

LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec"


class MergeBlocked(Exception):
    """A merge could not be completed automatically; the repo was left unchanged."""

    def __init__(self, message, conflicting=()):
        super().__init__(message)
        self.conflicting = list(conflicting)


@dataclass
class PullPlan:
    kind: str  # no_upstream | up_to_date | fast_forward | merge | conflict
    upstream: str = ""
    local_changed: bool = False
    remote_changed: bool = False


@dataclass
class Status:
    branch: str
    head: str
    upstream: str
    ahead: int
    behind: int
    file_tracked: bool
    file_modified: bool
    remotes: list = field(default_factory=list)


def content_hash(file_path):
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Repo:
    def __init__(self, root):
        self.root = os.path.normpath(root)
        self.git = Git(self.root)

    def abspath(self, path):
        return os.path.join(self.root, *path.split("/"))

    def relpath(self, file_path):
        return os.path.relpath(os.path.normpath(file_path), self.root).replace(os.sep, "/")

    # --- queries -------------------------------------------------------------

    def has_commits(self):
        return self.git.ok("rev-parse", "--verify", "HEAD")

    def branch(self):
        return self.git.run("symbolic-ref", "--short", "-q", "HEAD") if self.git.ok("symbolic-ref", "-q", "HEAD") else ""

    def upstream(self):
        if not self.git.ok("rev-parse", "--verify", "-q", "@{u}"):
            return ""
        return self.git.run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")

    def remotes(self):
        return self.git.run("remote").split()

    def status(self, path):
        upstream = self.upstream()
        ahead = behind = 0
        if upstream:
            counts = self.git.run("rev-list", "--left-right", "--count", "HEAD...@{u}").split()
            ahead, behind = int(counts[0]), int(counts[1])
        has_commits = self.has_commits()
        tracked = has_commits and self.git.ok("cat-file", "-e", f"HEAD:{path}")
        modified = bool(self.git.run("status", "--porcelain", "--", path))
        return Status(
            branch=self.branch(),
            head=self.git.run("rev-parse", "--short", "HEAD") if has_commits else "",
            upstream=upstream, ahead=ahead, behind=behind,
            file_tracked=tracked, file_modified=modified, remotes=self.remotes())

    def _changed_between(self, a, b, paths):
        return not self.git.ok("diff", "--quiet", a, b, "--", *paths)

    # --- pull ----------------------------------------------------------------

    def fetch(self):
        self.git.run("fetch", "--quiet", timeout=NETWORK_TIMEOUT)

    def plan_pull(self, paths):
        """Decide how to integrate the upstream branch for a design and its exported
        files (`paths`, the design file first). Call after fetch()."""
        paths = [paths] if isinstance(paths, str) else list(paths)
        upstream = self.upstream()
        if not upstream:
            return PullPlan("no_upstream")
        head = self.git.run("rev-parse", "HEAD")
        up = self.git.run("rev-parse", "@{u}")
        if head == up:
            return PullPlan("up_to_date", upstream)
        base = self.git.run("merge-base", head, up)
        if base == up:
            return PullPlan("up_to_date", upstream)
        remote_changed = self._changed_between(base, up, paths)
        if base == head:
            return PullPlan("fast_forward", upstream, remote_changed=remote_changed)
        local_changed = self._changed_between(base, head, paths)
        kind = "conflict" if local_changed and remote_changed else "merge"
        return PullPlan(kind, upstream, local_changed, remote_changed)

    def apply_pull(self, plan):
        if plan.kind == "fast_forward":
            self.git.run("merge", "--ff-only", plan.upstream)
        elif plan.kind == "merge":
            self._merge_or_abort(plan.upstream, allowed=set())
        elif plan.kind == "conflict":
            raise ValueError("conflicts must be resolved with resolve_conflict()")

    def resolve_conflict(self, plan, paths, keep):
        """Merge upstream, resolving a design and its exported files (`paths`) together by
        taking `keep` ('ours' or 'theirs'), so the exports always match the design."""
        if keep not in ("ours", "theirs"):
            raise ValueError(keep)
        paths = [paths] if isinstance(paths, str) else list(paths)
        unmerged = self._merge_or_abort(plan.upstream, allowed=set(paths))
        for path in sorted(unmerged):
            try:
                self.git.run("checkout", f"--{keep}", "--", path)
                self.git.run("add", "--", path)
            except GitError:
                self.git.run("rm", "--quiet", "--", path)  # the kept side deleted it
        if unmerged:
            self.git.run("commit", "--no-edit")

    def _merge_or_abort(self, upstream, allowed):
        """Run the merge. Returns the unmerged paths if they are all in `allowed`;
        otherwise aborts the merge and raises MergeBlocked."""
        try:
            self.git.run("merge", "--no-edit", upstream)
            return set()
        except GitError as e:
            unmerged = set(self.git.run("diff", "--name-only", "--diff-filter=U").splitlines())
            if unmerged and unmerged <= allowed:
                return unmerged
            if self.git.ok("rev-parse", "-q", "--verify", "MERGE_HEAD"):
                self.git.run("merge", "--abort")
            others = sorted(unmerged - allowed)
            if others:
                raise MergeBlocked("Other files in the repository conflict: " + ", ".join(others), others) from e
            raise MergeBlocked(str(e)) from e

    # --- Git LFS -------------------------------------------------------------

    def uses_lfs(self):
        return "filter=lfs" in _read(os.path.join(self.root, ".gitattributes"))

    def is_lfs_pointer(self, path):
        try:
            with open(self.abspath(path), "rb") as f:
                return f.read(len(LFS_POINTER_PREFIX)) == LFS_POINTER_PREFIX
        except FileNotFoundError:
            return False

    def enable_lfs(self):
        """Install the LFS filters for this clone, so checkouts and merges write real files."""
        self.git.run("lfs", "install", "--local")

    def fetch_lfs_file(self, path):
        """Replace the LFS pointer at `path` with the real file (network)."""
        self.enable_lfs()
        self.git.run("lfs", "pull", f"--include={path}", "--exclude=", timeout=NETWORK_TIMEOUT)

    def file_at(self, ref, path):
        """Contents of `path` at `ref`, with Git LFS pointers resolved."""
        data = self.git.run("show", f"{ref}:{path}", binary=True)
        if data.startswith(LFS_POINTER_PREFIX):
            data = self.git.run("lfs", "smudge", "--", path, input_bytes=data, binary=True,
                                timeout=NETWORK_TIMEOUT)
        return data

    # --- commit / push -------------------------------------------------------

    def commit_files(self, paths, message):
        """Commit exactly `paths`, leaving anything else staged or modified untouched.
        Returns the new short commit id, or "" if nothing changed."""
        self.git.run("add", "--", *paths)
        if self.has_commits() and self.git.ok("diff", "--cached", "--quiet", "HEAD", "--", *paths):
            return ""
        self.git.run("commit", "--only", "-m", message, "--", *paths)
        return self.git.run("rev-parse", "--short", "HEAD")

    def push(self):
        if self.upstream():
            self.git.run("push", timeout=NETWORK_TIMEOUT)
            return
        remotes = self.remotes()
        if not remotes:
            raise GitError(["push"], 1, "", "This repository has no remote to push to.")
        remote = "origin" if "origin" in remotes else remotes[0]
        self.git.run("push", "-u", remote, "HEAD", timeout=NETWORK_TIMEOUT)

    # --- setup ---------------------------------------------------------------

    def ensure_attributes(self, patterns, use_lfs):
        """Make sure `patterns` are tracked by Git LFS (or at least marked binary).
        Returns True if .gitattributes changed."""
        attributes = os.path.join(self.root, ".gitattributes")
        before = _read(attributes)
        if use_lfs:
            self.git.run("lfs", "install", "--local")
            for pattern in patterns:
                self.git.run("lfs", "track", pattern)
        else:
            existing = {line.split()[0] for line in before.splitlines() if line.strip()}
            missing = [p for p in patterns if p not in existing]
            if missing:
                with open(attributes, "a", encoding="utf-8") as f:
                    if before and not before.endswith("\n"):
                        f.write("\n")
                    f.writelines(f"{p} binary\n" for p in missing)
        return _read(attributes) != before


    def ensure_ignored(self, patterns):
        """Add `patterns` to .gitignore if missing. Returns True if .gitignore changed."""
        ignore_file = os.path.join(self.root, ".gitignore")
        before = _read(ignore_file)
        missing = [p for p in patterns if p not in {line.strip() for line in before.splitlines()}]
        if missing:
            with open(ignore_file, "a", encoding="utf-8") as f:
                if before and not before.endswith("\n"):
                    f.write("\n")
                f.writelines(f"{p}\n" for p in missing)
        return bool(missing)


def init_repo(path):
    os.makedirs(path, exist_ok=True)
    git = Git(path)
    git.run("init", "-q")
    git.run("symbolic-ref", "HEAD", "refs/heads/main")
    return Repo(path)


def clone_repo(url, dest):
    parent = os.path.dirname(os.path.normpath(dest))
    os.makedirs(parent, exist_ok=True)
    Git(parent).run("clone", url, dest, timeout=NETWORK_TIMEOUT)
    return Repo(dest)


def _read(file_path):
    try:
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
