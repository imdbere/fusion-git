"""Logging to ~/.fusiongit/fusiongit.log and user-facing error reporting."""

import functools
import logging
import os
import subprocess
import traceback

from . import store
from .gitcli import GitError, describe_error

_logger = logging.getLogger("fusiongit")


def setup():
    if _logger.handlers:
        return
    os.makedirs(store.home(), exist_ok=True)
    handler = logging.FileHandler(os.path.join(store.home(), "fusiongit.log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)


def info(message, *args):
    _logger.info(message, *args)


class UserError(Exception):
    """An expected problem with a message meant for the user (no traceback)."""


def guarded(fn):
    """Wrap an entry point so failures end up in a message box and the log, never in Fusion."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except UserError as e:
            _show(str(e))
        except (GitError, subprocess.TimeoutExpired) as e:
            _logger.warning("%s", traceback.format_exc())
            _show(*reversed(describe_error(e)))
        except Exception as e:  # noqa: BLE001 - last line of defence for event handlers
            _logger.error("%s", traceback.format_exc())
            _show(f"{e}\n\nDetails: {os.path.join(store.home(), 'fusiongit.log')}")
    return wrapper


def _show(message, title="Git"):
    import adsk.core
    adsk.core.Application.get().userInterface.messageBox(
        message, title, adsk.core.MessageBoxButtonTypes.OKButtonType, adsk.core.MessageBoxIconTypes.WarningIconType)
