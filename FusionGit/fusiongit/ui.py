"""Git tab, commands, dialogs and document event hooks."""

import os

import adsk.core

from . import actions, log, store
from . import design as fdesign
from .gitcli import repo_root
from .mainthread import notify, run_on_main

_app = adsk.core.Application.get()
_ui = _app.userInterface

WORKSPACE_ID = "FusionSolidEnvironment"
TAB_ID = "FusionGitTab"
# Fusion caches command icons by folder path until it restarts; change this folder
# (and OUT in tools/make_icons.py) when the icons change so updates show up.
ICONS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "icons")
OPEN_ID = "FusionGitOpen"
CONFLICT_ID = "FusionGitConflict"
F3D_FILTER = "Fusion archive (*.f3d)"
# Ids used by earlier versions; removed on start so stale toolbar entries disappear.
LEGACY_PANELS = ["FusionGitPanel"]
LEGACY_COMMANDS = ["FusionGitClone"]


class Button:
    def __init__(self, cmd_id, label, tooltip, panel, states, promoted=True, needs_remote=False):
        self.id, self.label, self.tooltip = cmd_id, label, tooltip
        self.panel, self.states, self.promoted, self.needs_remote = panel, states, promoted, needs_remote


PANELS = [("FusionGitSyncPanel", "Sync"), ("FusionGitRepoPanel", "Repository")]
BUTTONS = [
    Button("FusionGitInit", "Init", "Connect this design to a git repository",
           "FusionGitSyncPanel", {"unlinked"}),
    Button("FusionGitLocate", "Locate repository",
           "This design's repository was not found on this computer. Choose your clone of it.",
           "FusionGitSyncPanel", {"relink"}),
    Button("FusionGitCommit", "Commit", "Export the design and commit it to the current branch",
           "FusionGitSyncPanel", {"linked"}),
    Button("FusionGitPull", "Pull", "Fetch and merge, then load the design from the repository",
           "FusionGitSyncPanel", {"linked"}, needs_remote=True),
    Button("FusionGitPush", "Push", "Push the current branch", "FusionGitSyncPanel", {"linked"}, needs_remote=True),
    Button("FusionGitReimport", "Reimport",
           "Load the design file from the repository, e.g. after switching branches. Discards uncommitted changes.",
           "FusionGitSyncPanel", {"linked"}, promoted=False),
    Button("FusionGitStatus", "Status", "Branch, sync and change status",
           "FusionGitRepoPanel", {"linked", "relink", "unlinked"}),
    Button("FusionGitSettings", "Settings", "Exported files, remote, commit prompt",
           "FusionGitRepoPanel", {"linked", "relink", "unlinked"}, promoted=False),
]

SIMPLE_ACTIONS = {
    "FusionGitLocate": actions.locate,
    "FusionGitPull": actions.pull,
    "FusionGitPush": actions.push,
    "FusionGitReimport": actions.reimport,
    "FusionGitStatus": actions.status,
}

_handlers = []
_file_menu_control = None
_relink_noticed = set()


def _handler(base, fn):
    class Handler(base):
        def notify(self, args):
            log.guarded(fn)(args)
    handler = Handler()
    _handlers.append(handler)
    return handler


def _icon_folder(cmd_id):
    folder = os.path.join(ICONS, cmd_id.replace("FusionGit", "").lower())
    return folder if os.path.isdir(folder) else ""


def _definition(cmd_id, label, tooltip, created):
    defs = _ui.commandDefinitions
    cmd_def = defs.itemById(cmd_id) or defs.addButtonDefinition(cmd_id, label, tooltip, _icon_folder(cmd_id))
    cmd_def.commandCreated.add(_handler(adsk.core.CommandCreatedEventHandler, created))
    return cmd_def


def _run(action, *args):
    """Run an action on the main thread outside the command, then refresh the tab."""
    run_on_main(lambda: (log.guarded(action)(*args), refresh()))


# --- setup -------------------------------------------------------------------

def _remove_legacy():
    tab = _ui.workspaces.itemById(WORKSPACE_ID).toolbarTabs.itemById(TAB_ID)
    for panel_id in LEGACY_PANELS:
        panel = tab.toolbarPanels.itemById(panel_id) if tab else None
        if panel:
            panel.deleteMe()
    for cmd_id in LEGACY_COMMANDS:
        cmd_def = _ui.commandDefinitions.itemById(cmd_id)
        if cmd_def:
            cmd_def.deleteMe()


def start():
    global _file_menu_control
    _remove_legacy()
    for button in BUTTONS:
        _definition(button.id, button.label, button.tooltip, _created_for(button.id))
    _definition(CONFLICT_ID, "Resolve design conflict", "", _conflict_created)
    open_def = _definition(OPEN_ID, "Open from git repository…",
                           "Open a design file from a cloned git repository", _open_created)

    tabs = _ui.workspaces.itemById(WORKSPACE_ID).toolbarTabs
    tab = tabs.itemById(TAB_ID) or tabs.add(TAB_ID, "Git")
    for panel_id, name in PANELS:
        panel = tab.toolbarPanels.itemById(panel_id) or tab.toolbarPanels.add(panel_id, name)
        for button in [b for b in BUTTONS if b.panel == panel_id]:
            control = adsk.core.CommandControl.cast(
                panel.controls.itemById(button.id)
                or panel.controls.addCommand(_ui.commandDefinitions.itemById(button.id)))
            control.isPromoted = control.isPromotedByDefault = button.promoted

    file_menu = _ui.toolbars.itemById("QAT").controls.itemById("FileSubMenuCommand")
    if file_menu:
        menu = adsk.core.DropDownControl.cast(file_menu).controls
        _file_menu_control = menu.itemById(OPEN_ID) or menu.addCommand(open_def)

    for event, fn in ((_app.documentActivated, _on_activated), (_app.documentOpened, _on_activated),
                      (_app.documentClosed, _on_activated), (_app.documentSaved, _on_saved)):
        event.add(_handler(adsk.core.DocumentEventHandler, fn))
    refresh()


def stop():
    global _file_menu_control
    if _file_menu_control and _file_menu_control.isValid:
        _file_menu_control.deleteMe()
    _file_menu_control = None
    workspace = _ui.workspaces.itemById(WORKSPACE_ID)
    tab = workspace.toolbarTabs.itemById(TAB_ID) if workspace else None
    if tab:
        for panel_id, _ in PANELS:
            panel = tab.toolbarPanels.itemById(panel_id)
            if panel:
                panel.deleteMe()
        tab.deleteMe()
    for cmd_id in [b.id for b in BUTTONS] + [CONFLICT_ID, OPEN_ID]:
        cmd_def = _ui.commandDefinitions.itemById(cmd_id)
        if cmd_def:
            cmd_def.deleteMe()
    _handlers.clear()


def refresh():
    """Show only the buttons that apply to the active design; disable those that can't work."""
    try:
        state = actions.active_state()
    except Exception:  # noqa: BLE001 - never break the toolbar over a state probe
        state = actions.ActiveState("none")
    for button in BUTTONS:
        cmd_def = _ui.commandDefinitions.itemById(button.id)
        if not cmd_def:
            continue
        control = cmd_def.controlDefinition
        control.isVisible = state.state in button.states
        control.isEnabled = not button.needs_remote or state.has_remote
    design = fdesign.active_design()
    if state.state == "relink" and state.meta and design:
        key = fdesign.link_key(design)
        if key not in _relink_noticed:
            _relink_noticed.add(key)
            notify(f"Git repository for {state.meta['pathInRepo']} not found on this computer — "
                   "use Git → Locate repository", seconds=8)


# --- document events ---------------------------------------------------------

def _on_activated(args):
    refresh()


def _on_saved(args):
    own = fdesign.consume_own_save()
    if actions.on_saved(args.document, own):
        run_on_main(lambda: _ui.commandDefinitions.itemById("FusionGitCommit").execute())
    refresh()


# --- commands ----------------------------------------------------------------

def _created_for(cmd_id):
    special = {"FusionGitInit": _init_created, "FusionGitCommit": _commit_created,
               "FusionGitSettings": _settings_created}
    if cmd_id in special:
        return special[cmd_id]
    action = SIMPLE_ACTIONS[cmd_id]

    def created(args):
        args.command.execute.add(_handler(adsk.core.CommandEventHandler, lambda _: _run(action)))
    return created


def _open_created(args):
    # Everything up to creating the document happens right here, not in execute or a
    # deferred call, so it also works from the Home screen with no design open.
    log.info("open from repo: command created")
    dialog = _ui.createFileDialog()
    dialog.title = "Open design from git repository"
    dialog.filter = F3D_FILTER
    dialog.initialDirectory = store.load_settings()["defaultRepoDir"]
    if dialog.showOpen() != adsk.core.DialogResults.DialogOK:
        return
    target = actions.open_target(dialog.filename)
    if target:
        _run(actions.import_into_new, *target)


def _full_width_text(inputs, input_id, text, rows=1):
    box = inputs.addTextBoxCommandInput(input_id, "", text, rows, True)
    box.isFullWidth = True
    return box


def _short_path(path, limit=42):
    home = os.path.expanduser("~")
    if path.startswith(home):
        path = "~" + path[len(home):]
    return path if len(path) <= limit else "…" + path[-(limit - 1):]


def _path_button(inputs, input_id, label, path, placeholder):
    """A labelled row whose button shows the current path and opens a picker when clicked."""
    button = inputs.addBoolValueInput(input_id, label, False, "", False)
    button.text = _short_path(path) if path else placeholder
    button.tooltip = path or placeholder
    return button


def _init_created(args):
    command = args.command
    design = fdesign.active_design()
    if not design:
        raise log.UserError("Open a design first.")
    defaults = actions.init_defaults(design)
    inputs = command.commandInputs
    command.okButtonText = "Init"
    command.setDialogInitialSize(440, 300)
    command.setDialogMinimumSize(400, 260)
    chosen = {"location": defaults["location"], "path": "", "root": ""}

    mode = inputs.addDropDownCommandInput("mode", "Repository", adsk.core.DropDownStyles.TextListDropDownStyle)
    mode.listItems.add("New repository", True)
    mode.listItems.add("Existing repository", False)

    name = inputs.addStringValueInput("name", "Name", defaults["name"])
    name.tooltip = "Name of the new repository folder"
    location = _path_button(inputs, "location", "Location", chosen["location"], "Choose…")
    new_file = inputs.addStringValueInput("newFile", "File", f"{defaults['designsDir']}/{defaults['name']}.f3d")
    new_file.tooltip = "Path of the design file inside the repository"

    existing_file = _path_button(inputs, "existingFile", "File", "", "Choose file in repository…")
    existing_note = _full_width_text(inputs, "existingNote", "", 1)
    new_inputs, existing_inputs = (name, location, new_file), (existing_file, existing_note)

    if defaults["externalRefs"]:
        inputs.addBoolValueInput("breakLinks", f"Embed {defaults['externalRefs']} external reference(s)",
                                 True, "", False)
    notes = "Init moves the design into one component named after the file and commits it. " \
            "The current version stays in the design's history."
    if defaults["isPart"]:
        notes += " This Part design becomes a Hybrid design."
    _full_width_text(inputs, "info", notes, 3)

    def show_mode():
        creating = mode.selectedItem.index == 0
        for item in new_inputs:
            item.isVisible = creating
        for item in existing_inputs:
            item.isVisible = not creating
        existing_note.isVisible = not creating and bool(chosen["path"])

    def choose_location():
        dialog = _ui.createFolderDialog()
        dialog.title = "Create the repository in"
        dialog.initialDirectory = chosen["location"]
        if dialog.showDialog() == adsk.core.DialogResults.DialogOK:
            chosen["location"] = dialog.folder
            location.text, location.tooltip = _short_path(dialog.folder), dialog.folder

    def choose_existing():
        dialog = _ui.createFileDialog()
        dialog.title = "Save the design into a repository"
        dialog.filter = F3D_FILTER
        dialog.initialFilename = f"{defaults['name']}.f3d"
        dialog.initialDirectory = os.path.dirname(chosen["path"]) if chosen["path"] else chosen["location"]
        if dialog.showSave() != adsk.core.DialogResults.DialogOK:
            return
        chosen["path"] = dialog.filename
        chosen["root"] = repo_root(os.path.dirname(dialog.filename)) or ""
        if chosen["root"]:
            existing_file.text = os.path.relpath(chosen["path"], chosen["root"]).replace(os.sep, "/")
            existing_note.text = f"in {_short_path(chosen['root'], 60)}"
        else:
            existing_file.text = os.path.basename(chosen["path"])
            existing_note.text = "This folder is not inside a git repository."
        existing_file.tooltip = chosen["path"]
        show_mode()

    def changed(event_args):
        handlers = {"mode": show_mode, "location": choose_location, "existingFile": choose_existing}
        handler = handlers.get(event_args.input.id)
        if handler:
            handler()

    def validate(event_args):
        if mode.selectedItem.index == 0:
            event_args.areInputsValid = bool(name.value.strip() and new_file.value.strip())
        else:
            event_args.areInputsValid = bool(chosen["root"])

    def execute(_):
        break_links = inputs.itemById("breakLinks")
        embed = bool(break_links and break_links.value)
        if mode.selectedItem.index == 0:
            request = actions.InitRequest(
                repo_dir=os.path.join(chosen["location"], actions.safe_file_name(name.value)),
                path_in_repo=new_file.value, create_new=True, break_links=embed)
        else:
            request = actions.InitRequest(
                repo_dir=chosen["root"], path_in_repo=os.path.relpath(chosen["path"], chosen["root"]),
                create_new=False, break_links=embed)
        _run(actions.init, request)

    show_mode()
    command.inputChanged.add(_handler(adsk.core.InputChangedEventHandler, changed))
    command.validateInputs.add(_handler(adsk.core.ValidateInputsEventHandler, validate))
    command.execute.add(_handler(adsk.core.CommandEventHandler, execute))


def _commit_created(args):
    command = args.command
    ctx = actions.linked_context()
    has_remote = bool(ctx.repo.remotes())
    settings = store.load_settings()
    inputs = command.commandInputs
    command.cancelButtonText = "Cancel"
    command.setDialogInitialSize(420, 240)
    command.setDialogMinimumSize(360, 220)
    _full_width_text(inputs, "target", f"{ctx.path} → {ctx.repo.branch() or 'detached HEAD'}")
    message = inputs.addTextBoxCommandInput("message", "", "", 4, False)
    message.isFullWidth = True
    message.tooltip = "Commit message"
    push = inputs.addBoolValueInput("push", "Push after committing", True, "",
                                    has_remote and settings["pushAfterCommit"])
    push.isEnabled = has_remote
    if not has_remote:
        push.tooltip = "The repository has no remote. Add one in Git → Settings."

    def update_ok_text():
        command.okButtonText = "Commit and Push" if push.value else "Commit"

    def changed(event_args):
        if event_args.input.id == "push":
            update_ok_text()

    def execute(_):
        if has_remote and push.value != settings["pushAfterCommit"]:
            store.save_settings({**store.load_settings(), "pushAfterCommit": push.value})
        text = message.text.strip() or f"Update {os.path.basename(ctx.path)}"
        _run(actions.commit, text, push.value)

    update_ok_text()
    command.inputChanged.add(_handler(adsk.core.InputChangedEventHandler, changed))
    command.execute.add(_handler(adsk.core.CommandEventHandler, execute))


def _settings_created(args):
    command = args.command
    values = actions.settings_values()
    machine, repo = values["machine"], values["repo"]
    inputs = command.commandInputs
    command.okButtonText = "Save"
    command.setDialogInitialSize(460, 420 if repo else 220)

    computer = inputs.addGroupCommandInput("computer", "This computer").children
    prompt = computer.addBoolValueInput("prompt", "Ask for a commit message after saving", True, "",
                                        machine["promptCommitOnSave"])
    repo_dir = {"value": machine["defaultRepoDir"]}
    repo_dir_button = _path_button(computer, "repoDir", "New repositories in", repo_dir["value"], "Choose…")

    if repo:
        group = inputs.addGroupCommandInput("repository", "This repository").children
        _full_width_text(group, "repoRoot", repo["root"])
        exports = repo["config"]["exports"]
        step = group.addBoolValueInput("step", "Export STEP", True, "", bool(exports.get("step")))
        stl = group.addBoolValueInput("stl", "Export STL", True, "", bool(exports.get("stl")))
        thumbnail = group.addBoolValueInput("thumbnail", "Export PNG thumbnail", True, "",
                                            bool(exports.get("thumbnail")))
        designs_dir = group.addStringValueInput("designsDir", "Designs folder", repo["config"]["designsDir"])
        remote = group.addStringValueInput("remote", "Remote URL", repo["remote"])
        remote.tooltip = "URL of the 'origin' remote, e.g. git@github.com:you/project.git"
        _full_width_text(group, "repoNote", "Repository settings are stored in .fusiongit.json and "
                         "committed with the next commit.", 2)

    def changed(event_args):
        if event_args.input.id == "repoDir":
            dialog = _ui.createFolderDialog()
            dialog.initialDirectory = repo_dir["value"]
            if dialog.showDialog() == adsk.core.DialogResults.DialogOK:
                repo_dir["value"] = dialog.folder
                repo_dir_button.text, repo_dir_button.tooltip = _short_path(dialog.folder), dialog.folder

    def execute(_):
        machine_values = {"promptCommitOnSave": prompt.value, "defaultRepoDir": repo_dir["value"]}
        if repo:
            config = dict(repo["config"])
            config["exports"] = {"step": step.value, "stl": stl.value, "thumbnail": thumbnail.value}
            config["designsDir"] = designs_dir.value.strip().strip("/") or config["designsDir"]
            _run(actions.save_settings, machine_values, config, remote.value.strip())
        else:
            _run(actions.save_settings, machine_values)

    command.inputChanged.add(_handler(adsk.core.InputChangedEventHandler, changed))
    command.execute.add(_handler(adsk.core.CommandEventHandler, execute))


def show_conflict_dialog():
    _ui.commandDefinitions.itemById(CONFLICT_ID).execute()


def _conflict_created(args):
    command = args.command
    ctx = actions.linked_context()
    inputs = command.commandInputs
    command.okButtonText = "Continue"
    command.setDialogInitialSize(420, 260)
    _full_width_text(inputs, "info", f"You and the remote branch both changed {ctx.path}. Git cannot "
                     "merge Fusion designs, so one version has to be chosen.", 3)
    choice = inputs.addRadioButtonGroupCommandInput("choice", "")
    choice.isFullWidth = True
    choice.listItems.add("Compare: open the remote version in a new tab", True)
    choice.listItems.add("Keep mine", False)
    choice.listItems.add("Take theirs (replaces the design)", False)

    def execute(_):
        _run(actions.resolve_conflict, ("compare", "mine", "theirs")[choice.selectedItem.index])

    command.execute.add(_handler(adsk.core.CommandEventHandler, execute))
