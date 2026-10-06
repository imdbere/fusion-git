"""Per-machine state (~/.fusiongit) and the shared repo config (.fusiongit.json). No Fusion imports."""

import copy
import json
import os
import tempfile

REPO_CONFIG_NAME = ".fusiongit.json"

REPO_CONFIG_DEFAULTS = {
    "designsDir": "mechanical",
    "exports": {"step": True, "stl": False, "thumbnail": True},
    "lfs": ["*.f3d", "*.step", "*.stl"],
}

SETTINGS_DEFAULTS = {
    "promptCommitOnSave": True,
    "pushAfterCommit": False,
    "defaultRepoDir": os.path.join(os.path.expanduser("~"), "Documents", "FusionGit"),
}


def home():
    return os.environ.get("FUSIONGIT_HOME") or os.path.join(os.path.expanduser("~"), ".fusiongit")


def _load(file_path, default):
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(default)


def _save(file_path, data):
    directory = os.path.dirname(file_path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, file_path)


def _merged(defaults, overrides):
    result = copy.deepcopy(defaults)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merged(result[key], value)
        else:
            result[key] = value
    return result


# --- repo config -------------------------------------------------------------

def load_repo_config(repo_root):
    return _merged(REPO_CONFIG_DEFAULTS, _load(os.path.join(repo_root, REPO_CONFIG_NAME), {}))


def save_repo_config(repo_root, config):
    _save(os.path.join(repo_root, REPO_CONFIG_NAME), _merged(REPO_CONFIG_DEFAULTS, config))


def ensure_repo_config(repo_root):
    """Write the default config if the repo has none. Returns True if it was created."""
    file_path = os.path.join(repo_root, REPO_CONFIG_NAME)
    if os.path.exists(file_path):
        return False
    _save(file_path, REPO_CONFIG_DEFAULTS)
    return True


# --- per-machine settings ----------------------------------------------------

def load_settings():
    return _merged(SETTINGS_DEFAULTS, _load(os.path.join(home(), "settings.json"), {}))


def save_settings(settings):
    _save(os.path.join(home(), "settings.json"), settings)


# --- links: link id (stored in the design) -> local repo + sync state ---------

def _links_path():
    return os.path.join(home(), "links.json")


def get_link(key):
    return _load(_links_path(), {}).get(key)


def set_link(key, **fields):
    links = _load(_links_path(), {})
    link = links.get(key, {})
    link.update(fields)
    links[key] = link
    _save(_links_path(), links)
    return link


def remove_link(key):
    links = _load(_links_path(), {})
    if links.pop(key, None) is not None:
        _save(_links_path(), links)


def find_link(repo_root, path_in_repo):
    """(key, link) of the design already linked to this file on this machine, or (None, None)."""
    root = os.path.normcase(os.path.normpath(repo_root))
    for key, link in _load(_links_path(), {}).items():
        if (os.path.normcase(os.path.normpath(link.get("repoPath", ""))) == root
                and link.get("pathInRepo") == path_in_repo):
            return key, link
    return None, None
