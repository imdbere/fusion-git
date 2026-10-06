import importlib
import os
import sys

import adsk.core

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_modules = {}


def _load():
    """Import the package fresh. Fusion keeps its Python interpreter alive between
    Stop and Run, so without dropping the cached modules a re-run keeps the old code."""
    for name in [m for m in sys.modules if m == "fusiongit" or m.startswith("fusiongit.")]:
        del sys.modules[name]
    for name in ("log", "mainthread", "ui"):
        _modules[name] = importlib.import_module(f"fusiongit.{name}")
    return _modules["log"], _modules["mainthread"], _modules["ui"]


def run(context):
    try:
        log, mainthread, ui = _load()
        log.setup()
        mainthread.start()
        ui.start()
        log.info("FusionGit started")
    except Exception:  # noqa: BLE001 - report start-up failures instead of failing silently
        import traceback
        adsk.core.Application.get().userInterface.messageBox(traceback.format_exc(), "FusionGit")


def stop(context):
    try:
        if _modules:
            _modules["ui"].stop()
            _modules["mainthread"].stop()
            _modules["log"].info("FusionGit stopped")
    except Exception:  # noqa: BLE001
        import traceback
        adsk.core.Application.get().userInterface.messageBox(traceback.format_exc(), "FusionGit")
