"""Thin wrapper around the git command line. No Fusion imports."""

import os
import shutil
import subprocess
import sys

# Fusion does not inherit the login shell's PATH, so git (and git-lfs, which git
# itself looks up on PATH when running the LFS filters) must be found explicitly.
_EXTRA_PATHS = {
    "darwin": ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"],
    "win32": [r"C:\Program Files\Git\cmd", r"C:\Program Files\Git\bin"],
}.get(sys.platform, ["/usr/local/bin", "/usr/bin"])

DEFAULT_TIMEOUT = 120
NETWORK_TIMEOUT = 600


class GitError(Exception):
    def __init__(self, args, returncode, stdout, stderr):
        self.git_args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        detail = (stderr or stdout or "").strip()
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {detail}")


def _env():
    env = dict(os.environ)
    path = env.get("PATH", "")
    extra = [p for p in _EXTRA_PATHS if p not in path.split(os.pathsep)]
    env["PATH"] = os.pathsep.join([path, *extra]) if path else os.pathsep.join(extra)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_MERGE_AUTOEDIT"] = "no"
    return env


def find_git():
    return shutil.which("git", path=_env()["PATH"])


def lfs_available():
    try:
        return Git(os.path.expanduser("~")).ok("lfs", "version")
    except (FileNotFoundError, OSError):
        return False


class Git:
    """Runs git in one working directory."""

    def __init__(self, cwd):
        self.cwd = cwd
        self.exe = find_git()
        if not self.exe:
            raise FileNotFoundError("git executable not found; install git and restart Fusion")

    def _exec(self, args, timeout, input_bytes=None):
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        return subprocess.run([self.exe, *args], cwd=self.cwd, env=_env(), input=input_bytes,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, **kwargs)

    def run(self, *args, timeout=DEFAULT_TIMEOUT, input_bytes=None, binary=False):
        proc = self._exec(args, timeout, input_bytes)
        stdout = proc.stdout if binary else proc.stdout.decode("utf-8", "replace")
        stderr = proc.stderr.decode("utf-8", "replace")
        if proc.returncode != 0:
            raise GitError(list(args), proc.returncode, "" if binary else stdout, stderr)
        return stdout if binary else stdout.strip()

    def ok(self, *args):
        """True if the command exits 0 (for yes/no queries)."""
        return self._exec(args, DEFAULT_TIMEOUT).returncode == 0


def repo_root(path):
    """Top level of the work tree containing `path`, or None."""
    directory = path if os.path.isdir(path) else os.path.dirname(path)
    if not os.path.isdir(directory):
        return None
    try:
        return os.path.normpath(Git(directory).run("rev-parse", "--show-toplevel"))
    except GitError:
        return None


def remote_urls(repo):
    git = Git(repo)
    names = git.run("remote").split()
    return [git.run("remote", "get-url", name) for name in names]


def normalize_remote(url):
    """Comparable form of a remote URL: host/path, no scheme, user, port or .git suffix."""
    if not url:
        return ""
    u = url.strip()
    if "://" in u:
        u = u.split("://", 1)[1]
        u = u.split("@", 1)[-1]
        host, _, path = u.partition("/")
        host = host.split(":", 1)[0]
    elif "@" in u and ":" in u:
        u = u.split("@", 1)[1]
        host, _, path = u.partition(":")
    else:
        return os.path.normcase(os.path.normpath(u))
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host.lower()}/{path.lower()}"


_OPERATION_TITLES = {"push": "Push failed", "fetch": "Pull failed", "merge": "Merge failed",
                     "commit": "Commit failed", "clone": "Clone failed", "lfs": "Git LFS failed"}

# (substring in git's output, explanation for the user); first match wins.
_KNOWN_PROBLEMS = [
    ("has no remote", "This repository has no remote yet. Add one in Git → Settings (Remote URL)."),
    ("git-lfs", "This repository uses Git LFS, but git-lfs is not installed. Install it "
                "(brew install git-lfs on macOS) and restart Fusion."),
    ("authentication failed", "Git could not sign in to the remote. Set up your credentials once in a "
                              "terminal (for example run git push there), then try again."),
    ("could not read username", "Git could not sign in to the remote. Set up your credentials once in a "
                                "terminal (for example run git push there), then try again."),
    ("terminal prompts disabled", "Git needs credentials for the remote. Set them up once in a terminal "
                                  "(for example run git push there), then try again."),
    ("permission denied (publickey)", "The remote rejected your SSH key. Check that your key is added to "
                                      "the git host and loaded in your SSH agent."),
    ("[rejected]", "The remote has commits you don't have yet. Pull first, then push again."),
    ("non-fast-forward", "The remote has commits you don't have yet. Pull first, then push again."),
    ("could not resolve host", "Could not reach the remote. Check your internet connection."),
    ("unable to access", "Could not reach the remote. Check your internet connection and the remote URL."),
    ("connection timed out", "Could not reach the remote. Check your internet connection."),
    ("would be overwritten", "Uncommitted changes in other files of the repository are in the way. Commit "
                             "or stash them in your git tool, then try again."),
    ("not concluded your merge", "A merge is in progress in this repository. Finish or abort it in your "
                                 "git tool first."),
    ("merge_head exists", "A merge is in progress in this repository. Finish or abort it in your git tool first."),
    ("please tell me who you are", "Git doesn't know your name and email yet. Run git config --global "
                                   "user.name \"Your Name\" and git config --global user.email you@example.com."),
]


def describe_error(error):
    """(title, message) for showing a git failure to the user."""
    if isinstance(error, subprocess.TimeoutExpired):
        return "Git did not respond", "Git took too long and was stopped. Check your connection and try again."
    title = _OPERATION_TITLES.get(error.git_args[0] if error.git_args else "", "Git failed")
    output = f"{error.stderr}\n{error.stdout}"
    explanation = next((text for needle, text in _KNOWN_PROBLEMS if needle in output.lower()), None)
    details = [line for line in output.strip().splitlines() if line.strip()][-3:]
    message = explanation or "Git reported an error."
    if details:
        message += "\n\nGit said:\n" + "\n".join(details)
    return title, message
