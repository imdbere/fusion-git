"""User-facing workflows. Each runs on the main thread, outside command events."""

import os
import re
import tempfile
from dataclasses import dataclass

import adsk.core
import adsk.fusion

from . import design as fdesign
from . import log, store
from .gitcli import lfs_available, normalize_remote, remote_urls, repo_root
from .log import UserError
from .mainthread import notify, run_in_background
from .repo import MergeBlocked, Repo, content_hash, init_repo

_app = adsk.core.Application.get()
_ui = _app.userInterface

# link key -> PullPlan awaiting a conflict decision
pending_conflicts = {}

EXPORT_EXTENSIONS = (".step", ".stl", ".png")

# Files Fusion and the OS leave next to design files.
IGNORED = ["_XRef_/", ".DS_Store"]


def _confirm(message, title="Git"):
    answer = _ui.messageBox(message, title, adsk.core.MessageBoxButtonTypes.YesNoButtonType,  # pyright: ignore[reportArgumentType]
                            adsk.core.MessageBoxIconTypes.WarningIconType)  # pyright: ignore[reportArgumentType]
    return answer == adsk.core.DialogResults.DialogYes


def _info(message, title="Git"):
    _ui.messageBox(message, title)


def safe_file_name(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "design"


# --- context -----------------------------------------------------------------

@dataclass
class Context:
    doc: adsk.core.Document
    design: adsk.fusion.Design
    key: str
    meta: dict
    link: dict
    repo: Repo

    @property
    def path(self):
        return self.meta["pathInRepo"]

    @property
    def file(self):
        return self.repo.abspath(self.path)

    @property
    def design_files(self):
        """The design file and the exports written next to it, which always travel together."""
        stem = os.path.splitext(self.path)[0]
        return [self.path] + [stem + ext for ext in EXPORT_EXTENSIONS]


@dataclass
class ActiveState:
    state: str  # none | unlinked | relink | linked
    has_remote: bool = False
    meta: dict | None = None


def active_state():
    design = fdesign.active_design()
    if not design:
        return ActiveState("none")
    meta = fdesign.get_metadata(design)
    if not meta:
        return ActiveState("unlinked")
    key = fdesign.link_key(design)
    link = store.get_link(key or "")
    if not link or not os.path.isdir(link.get("repoPath", "")):
        return ActiveState("relink", meta=meta)
    _remember_lineage(key, link, design.parentDocument)
    return ActiveState("linked", has_remote=bool(Repo(link["repoPath"]).remotes()), meta=meta)


def linked_context():
    design = fdesign.active_design()
    if not design:
        raise UserError("Open a design first.")
    meta = fdesign.get_metadata(design)
    if not meta:
        raise UserError("This design is not connected to a git repository. Use Init first.")
    doc = design.parentDocument
    key = fdesign.link_key(design)
    link = store.get_link(key or "")
    if not key or not link or not os.path.isdir(link.get("repoPath", "")):
        raise UserError("The repository for this design was not found on this computer. "
                        "Use Git → Locate repository.")
    link = _remember_lineage(key, link, doc)
    return Context(doc, design, key, meta, link, Repo(link["repoPath"]))


def _remember_lineage(key, link, doc):
    """Keep the design's current cloud id in its link, so Open from git repository can
    open the existing design instead of creating a second one."""
    lineage = fdesign.lineage(doc)
    if lineage and link.get("lineage") != lineage:
        return store.set_link(key, lineage=lineage)
    return link


def uncommitted_reasons(ctx):
    reasons = []
    if ctx.doc.isModified:
        reasons.append("the design has unsaved changes")
    if ctx.link.get("dirty"):
        reasons.append("the design was saved since the last commit")
    return reasons


def repo_differs(ctx):
    return os.path.exists(ctx.file) and content_hash(ctx.file) != ctx.link.get("syncedHash")


def _record_sync(key, repo, path):
    file_path = repo.abspath(path)
    store.set_link(key, repoPath=repo.root, pathInRepo=path, dirty=False,
                   syncedHash=content_hash(file_path) if os.path.exists(file_path) else None)


def on_saved(doc, own_save):
    """documentSaved hook. Returns True if the commit dialog should be shown."""
    design = fdesign.design_of(doc)
    key = fdesign.link_key(design) if design else None
    link = store.get_link(key) if key else None
    if not link:
        return False
    _remember_lineage(key, link, doc)
    if own_save:
        return False
    store.set_link(key, dirty=True)
    return store.load_settings()["promptCommitOnSave"]


def _failed_exports_note(formats):
    return (f"The {', '.join(formats)} export failed, so the previous {'file was' if len(formats) == 1 else 'files were'} "
            "kept. The design itself was committed. Check the timeline for features with warnings.")


def _with_real_file(repo, path, then):
    """Run `then()` once `path` holds the real design: a clone made without Git LFS active
    contains small pointer files instead, which are downloaded first."""
    if not repo.is_lfs_pointer(path):
        then()
        return
    if not lfs_available():
        raise UserError(f"{path} is stored with Git LFS, but git-lfs is not installed, so only a "
                        "placeholder was downloaded. Install git-lfs (brew install git-lfs on macOS), "
                        "restart Fusion and try again.")
    run_in_background(lambda: repo.fetch_lfs_file(path), lambda _: then(), "Downloading design (Git LFS)…")


def _prepare_repo(repo):
    """Make future checkouts and merges in this clone write real files, not LFS pointers."""
    if repo.uses_lfs() and lfs_available():
        repo.enable_lfs()


# --- init / open -------------------------------------------------------------

@dataclass
class InitRequest:
    repo_dir: str        # folder of the repository (created if create_new)
    path_in_repo: str    # design file, relative to repo_dir
    create_new: bool
    break_links: bool


def init_defaults(design):
    return {
        "name": safe_file_name(design.parentDocument.name),
        "location": store.load_settings()["defaultRepoDir"],
        "designsDir": store.REPO_CONFIG_DEFAULTS["designsDir"],
        "externalRefs": len(fdesign.external_references(design)),
        "isPart": fdesign.is_part_design(design),
    }


def init(request):
    design = fdesign.active_design()
    if not design:
        raise UserError("Open a design first.")
    doc = design.parentDocument
    lineage = fdesign.lineage(doc)
    if not doc.isSaved:
        raise UserError("Save the design to the Fusion cloud once before connecting it to git.")
    if fdesign.get_metadata(design):
        raise UserError("This design is already connected to a git repository.")
    if doc.isModified:
        raise UserError("Save the design first. Init restructures it, and the saved version is "
                        "what you can go back to in the version history.")
    if fdesign.external_references(design) and not request.break_links:
        raise UserError("This design inserts other designs as external references, which are not "
                        "supported yet. Enable 'Convert external references' to embed them.")

    target = os.path.normpath(os.path.expanduser(request.repo_dir))
    if request.create_new:
        existing_root = repo_root(target) if os.path.isdir(target) else None
        if existing_root and os.path.normcase(existing_root) == os.path.normcase(target):
            repo = Repo(target)
        elif os.path.isdir(target) and os.listdir(target):
            raise UserError(f"{target} already exists and is not empty. Choose another name, "
                            "or use an existing repository.")
        else:
            repo = None
    else:
        root = repo_root(target)
        if not root:
            raise UserError(f"{target} is not inside a git repository.")
        repo = Repo(root)

    path = request.path_in_repo.strip().replace("\\", "/").lstrip("/")
    if not path.lower().endswith(".f3d"):
        path += ".f3d"
    if repo and os.path.exists(repo.abspath(path)):
        raise UserError(f"{path} already exists in the repository. Choose another file name, "
                        "or use File → Open from git repository to open that design.")

    if fdesign.external_references(design):
        fdesign.break_external_links(design)
    occ = fdesign.wrap_existing(design, os.path.splitext(os.path.basename(path))[0])

    # The design is wrapped; from here on only repository work remains.
    repo = repo or init_repo(target)
    config_created = store.ensure_repo_config(repo.root)
    config = store.load_repo_config(repo.root)
    use_lfs = lfs_available()
    repo.ensure_attributes(config["lfs"], use_lfs)
    repo.ensure_ignored(IGNORED)
    remotes = remote_urls(repo.root)
    key = fdesign.new_link_id()
    fdesign.set_metadata(design, path, remotes[0] if remotes else "", key)
    files, failed_exports = fdesign.export_wrapper(design, occ, repo.abspath(path), config["exports"])
    store.set_link(key, repoPath=repo.root, pathInRepo=path, dirty=False, lineage=lineage)
    fdesign.save(doc, "Connected to git")
    extra = [p for p in [".gitattributes", ".gitignore"] + ([store.REPO_CONFIG_NAME] if config_created else [])
             if os.path.exists(repo.abspath(p))]
    sha = repo.commit_files([repo.relpath(f) for f in files] + extra, f"Add {os.path.basename(path)}")
    _record_sync(key, repo, path)

    notify(f"Connected to git — committed {sha} in {os.path.basename(repo.root)}")
    warnings = []
    if failed_exports:
        warnings.append(_failed_exports_note(failed_exports))
    if not use_lfs:
        warnings.append("Git LFS is not installed, so design files are stored as regular binary files. "
                        "Install git-lfs (brew install git-lfs) to keep the repository small.")
    if not remotes:
        warnings.append("The repository has no remote yet. Add one in Git → Settings to push.")
    if warnings:
        _info("\n\n".join(warnings), "Connected to git")


def open_target(file_path):
    """First half of File → Open from git repository: validate the file and create or open
    the document. Returns (document, repo, path) when an import is still needed, else None."""
    log.info("open from repo: %s", file_path)
    root = repo_root(file_path)
    if not root:
        raise UserError("The selected file is not inside a git repository.")
    repo = Repo(root)
    path = repo.relpath(file_path)
    if repo.is_lfs_pointer(path) and not lfs_available():
        raise UserError(f"{path} is stored with Git LFS, but git-lfs is not installed, so only a "
                        "placeholder was downloaded. Install git-lfs (brew install git-lfs on macOS), "
                        "restart Fusion and try again.")

    key, link = store.find_link(repo.root, path)
    if key and link:
        cloud_id = link.get("lineage") or (key if key.startswith("urn:") else None)
        if not cloud_id:
            raise UserError(f"{path} is already open as the design '{link.get('designName', path)}', "
                            "which Fusion is still uploading. Open it from the Data Panel, or try again "
                            "in a moment.")
        try:
            data_file = _app.data.findFileById(cloud_id)
        except RuntimeError:
            data_file = None
        if data_file:
            _app.documents.open(data_file)
            return None
        store.remove_link(key)  # the linked design was deleted from the Fusion cloud
    doc = _app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)  # pyright: ignore[reportArgumentType]
    return doc, repo, path


def import_into_new(doc, repo, path):
    """Second half: import the file into the new document (not allowed inside command events)."""
    _prepare_repo(repo)
    _with_real_file(repo, path, lambda: _import_into_new(doc, repo, path))


def _import_into_new(doc, repo, path):
    design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    fdesign.ensure_parametric(design)
    fdesign.allow_components(design, force_hybrid=True)
    occ = fdesign.import_wrapper(design, repo.abspath(path))
    occ.activate()
    remotes = remote_urls(repo.root)
    key = fdesign.new_link_id()
    fdesign.set_metadata(design, path, remotes[0] if remotes else "", key)
    name = os.path.splitext(os.path.basename(path))[0]
    project = _app.data.activeProject
    fdesign.save_as(doc, name, project.rootFolder, "Opened from git")
    _record_sync(key, repo, path)
    store.set_link(key, designName=name)
    notify(f"Opened {path} as '{name}' in project '{project.name}'")


# --- commit / push -----------------------------------------------------------

def commit(message, push_after=False):
    ctx = linked_context()
    outside = fdesign.content_outside_wrapper(ctx.design)
    if outside and not _confirm("These items are outside the synced component and will NOT be committed:\n\n"
                                + "\n".join(outside) + "\n\nCommit anyway?"):
        return
    wrapper = fdesign.wrapper_occurrence(ctx.design)
    if not wrapper:
        raise UserError("The synced component of this design is missing. Use Reimport to restore it.")
    if ctx.doc.isModified:
        fdesign.save(ctx.doc, message)
    config = store.load_repo_config(ctx.repo.root)
    written, failed_exports = fdesign.export_wrapper(ctx.design, wrapper, ctx.file, config["exports"])
    files = [ctx.repo.relpath(f) for f in written]
    if os.path.exists(ctx.repo.abspath(store.REPO_CONFIG_NAME)):
        files.append(store.REPO_CONFIG_NAME)  # carries settings changes made in Git → Settings
    sha = ctx.repo.commit_files(files, message)
    _record_sync(ctx.key, ctx.repo, ctx.path)
    summary = f"Committed {sha} on {ctx.repo.branch()}" if sha else "Nothing changed since the last commit"
    if failed_exports:
        _info(_failed_exports_note(failed_exports), "Committed with warnings")
    if push_after:
        run_in_background(ctx.repo.push, lambda _: notify(f"{summary} and pushed"), "Pushing…")
    else:
        notify(summary)


def push():
    ctx = linked_context()
    if uncommitted_reasons(ctx) and not _confirm("The design has changes that are not committed; "
                                                 "they will not be pushed. Push anyway?"):
        return
    run_in_background(ctx.repo.push, lambda _: notify("Pushed"), "Pushing…")


# --- reimport / pull ---------------------------------------------------------

def _reimport(ctx, description, on_done):
    if not os.path.exists(ctx.file):
        raise UserError(f"{ctx.path} does not exist in the repository (wrong branch?).")

    def load():
        fdesign.replace_contents(ctx.design, ctx.file)
        fdesign.save(ctx.doc, description)
        _record_sync(ctx.key, ctx.repo, ctx.path)
        on_done()

    _prepare_repo(ctx.repo)
    _with_real_file(ctx.repo, ctx.path, load)


def reimport():
    ctx = linked_context()
    reasons = uncommitted_reasons(ctx)
    if reasons and not _confirm("Reimporting discards changes that are not committed:\n\n- "
                                + "\n- ".join(reasons) + "\n\nDiscard them?"):
        return
    outside = fdesign.content_outside_wrapper(ctx.design)
    if outside and not _confirm("These items outside the synced component will be removed:\n\n"
                                + "\n".join(outside) + "\n\nContinue?"):
        return
    _reimport(ctx, f"Reimported {ctx.path} at {ctx.repo.status(ctx.path).head}",
              lambda: notify(f"Reimported {ctx.path}"))


def pull():
    ctx = linked_context()
    reasons = uncommitted_reasons(ctx)
    if reasons:
        raise UserError("Commit first. Pull needs a committed design, but:\n\n- " + "\n- ".join(reasons)
                        + "\n\n(Reimport discards local changes instead.)")
    if not ctx.repo.remotes():
        raise UserError("This repository has no remote to pull from. Add one in Git → Settings.")
    _prepare_repo(ctx.repo)
    run_in_background(ctx.repo.fetch, lambda _: _after_fetch(ctx), "Fetching…")


def _after_fetch(ctx):
    plan = ctx.repo.plan_pull(ctx.design_files)
    if plan.kind == "no_upstream":
        raise UserError(f"The branch '{ctx.repo.branch()}' has no upstream branch yet. Push once to create it.")
    if plan.kind == "conflict":
        pending_conflicts[ctx.key] = plan
        from . import ui  # circular at import time
        ui.show_conflict_dialog()
        return
    if plan.kind in ("fast_forward", "merge"):
        try:
            ctx.repo.apply_pull(plan)
        except MergeBlocked as e:
            raise UserError(f"Pull stopped, nothing was changed.\n\n{e}\n\n"
                            "Resolve this in your git tool, then use Reimport.") from e
    summary = {"up_to_date": "Already up to date", "fast_forward": "Pulled", "merge": "Pulled and merged"}[plan.kind]
    if repo_differs(ctx):
        _reimport(ctx, f"Pulled {ctx.path} at {ctx.repo.status(ctx.path).head}",
                  lambda: notify(summary + " — design updated"))
    else:
        notify(summary)


def resolve_conflict(choice):
    ctx = linked_context()
    plan = pending_conflicts.get(ctx.key)
    if not plan:
        raise UserError("No pending conflict for this design. Run Pull again.")
    if choice == "compare":
        data = ctx.repo.file_at(plan.upstream, ctx.path)
        stem = os.path.splitext(os.path.basename(ctx.path))[0]
        preview = os.path.join(tempfile.mkdtemp(prefix="fusiongit-"), stem + ".f3d")
        with open(preview, "wb") as f:
            f.write(data)
        fdesign.open_preview(preview, f"{stem} ({plan.upstream})")
        _info("The remote version is open in a new, unsaved tab. Close it without saving when done, "
              "then run Pull again on your design to choose a version.")
        return
    pending_conflicts.pop(ctx.key, None)
    try:
        ctx.repo.resolve_conflict(plan, ctx.design_files, "ours" if choice == "mine" else "theirs")
    except MergeBlocked as e:
        raise UserError(f"Pull stopped, nothing was changed.\n\n{e}\n\nResolve this in your git tool.") from e
    if choice == "theirs":
        _reimport(ctx, f"Took remote version of {ctx.path}",
                  lambda: notify("Merged, keeping the remote version — push when ready"))
    else:
        _record_sync(ctx.key, ctx.repo, ctx.path)
        notify("Merged, keeping your version — push when ready")


# --- status / relink ---------------------------------------------------------

def status():
    design = fdesign.active_design()
    if not design:
        raise UserError("Open a design first.")
    state = active_state()
    if state.state == "unlinked":
        _info("This design is not connected to a git repository.", "Git status")
        return
    if state.state == "relink" and state.meta:
        _info(f"Connected to {state.meta['pathInRepo']} in {state.meta['remoteUrl'] or 'a local repository'}, "
              "but that repository was not found on this computer. Use Git → Locate repository.", "Git status")
        return
    ctx = linked_context()
    s = ctx.repo.status(ctx.path)
    lines = [f"Repository: {ctx.repo.root}", f"File: {ctx.path}",
             f"Branch: {s.branch or '(detached)'} at {s.head or '(no commits)'}"]
    if s.upstream:
        lines.append(f"Upstream: {s.upstream} — {s.ahead} ahead, {s.behind} behind (as of last fetch)")
    elif s.remotes:
        lines.append("Upstream: none yet (push once to create it)")
    else:
        lines.append("Remote: none (add one in Git → Settings)")
    reasons = uncommitted_reasons(ctx)
    lines.append("Design: " + ("; ".join(reasons) if reasons else "committed"))
    if repo_differs(ctx):
        lines.append("\nThe file in the repository differs from the design. Use Reimport to load it.")
    _info("\n".join(lines), "Git status")


def locate():
    design = fdesign.active_design()
    meta = fdesign.get_metadata(design) if design else None
    if not design or not meta:
        raise UserError("Open a design that is connected to git.")
    dialog = _ui.createFolderDialog()
    dialog.title = f"Locate the repository containing {meta['pathInRepo']}"
    if dialog.showDialog() != adsk.core.DialogResults.DialogOK:
        return
    root = repo_root(dialog.folder)
    if not root:
        raise UserError(f"{dialog.folder} is not inside a git repository. Clone the repository "
                        f"({meta['remoteUrl'] or 'ask its owner for the URL'}) with your git tool first.")
    repo = Repo(root)
    if not os.path.exists(repo.abspath(meta["pathInRepo"])):
        raise UserError(f"{meta['pathInRepo']} was not found in {root}.")
    expected = normalize_remote(meta["remoteUrl"])
    if expected and expected not in {normalize_remote(u) for u in remote_urls(root)}:
        if not _confirm(f"This repository's remotes do not match {meta['remoteUrl']}. Link it anyway?"):
            return
    _prepare_repo(repo)
    store.set_link(fdesign.link_key(design), repoPath=repo.root, pathInRepo=meta["pathInRepo"],
                   dirty=False, syncedHash=None, lineage=fdesign.lineage(design.parentDocument))
    notify("Repository linked — use Reimport to load the repository version")


# --- settings ----------------------------------------------------------------

def settings_values():
    """Current settings for the settings dialog; repo fields only for a linked design."""
    values = {"machine": store.load_settings(), "repo": None}
    state = active_state()
    if state.state == "linked":
        ctx = linked_context()
        origin = ctx.repo.git.run("remote", "get-url", "origin") if "origin" in ctx.repo.remotes() else ""
        values["repo"] = {"root": ctx.repo.root, "config": store.load_repo_config(ctx.repo.root), "remote": origin}
    return values


def save_settings(machine, repo_config=None, remote=None):
    settings = store.load_settings()
    settings.update(machine)
    store.save_settings(settings)
    if repo_config is not None:
        ctx = linked_context()
        store.save_repo_config(ctx.repo.root, repo_config)
        remotes = ctx.repo.remotes()
        current = ctx.repo.git.run("remote", "get-url", "origin") if "origin" in remotes else ""
        if remote != current:
            if not remote:
                ctx.repo.git.run("remote", "remove", "origin")
            elif "origin" in remotes:
                ctx.repo.git.run("remote", "set-url", "origin", remote)
            else:
                ctx.repo.git.run("remote", "add", "origin", remote)
            fdesign.set_metadata(ctx.design, ctx.path, remote)
    notify("Settings saved")
