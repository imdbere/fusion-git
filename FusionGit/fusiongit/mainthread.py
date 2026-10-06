"""Run work on Fusion's main thread, outside command events, and in the background.

`ImportManager.importToTarget` and uploads cannot run inside command events, and
worker threads must not touch the Fusion API. Both go through one custom event:
callables are parked in `_pending` and executed by the event handler.
"""

import threading
import uuid

import adsk.core

from . import log

EVENT_ID = "FusionGitMainThread"

_app = adsk.core.Application.get()
_pending = {}
_handlers = []
_event = None
_message_token = None


class _Handler(adsk.core.CustomEventHandler):
    def notify(self, args):
        fn = _pending.pop(args.additionalInfo, None)
        if fn:
            log.guarded(fn)()


def start():
    global _event
    _event = _app.registerCustomEvent(EVENT_ID)
    handler = _Handler()
    _event.add(handler)
    _handlers.append(handler)


def stop():
    global _event
    if _event:
        for handler in _handlers:
            _event.remove(handler)
        _handlers.clear()
        _app.unregisterCustomEvent(EVENT_ID)
        _event = None
    _pending.clear()


def run_on_main(fn):
    key = uuid.uuid4().hex
    _pending[key] = fn
    _app.fireCustomEvent(EVENT_ID, key)


def run_in_background(work, on_done, busy_message):
    """Run `work()` on a thread (no Fusion API calls allowed in it), then
    `on_done(result)` on the main thread. Exceptions from `work` are re-raised
    on the main thread, where log.guarded reports them."""
    global _message_token
    _message_token = None
    ui = _app.userInterface
    ui.progressBar.showBusy(busy_message)

    def finish(result=None, error=None):
        ui.progressBar.hide()
        if error:
            raise error
        on_done(result)

    def worker():
        try:
            result = work()
        except Exception as e:  # noqa: BLE001 - surfaced on the main thread
            run_on_main(lambda err=e: finish(error=err))
            return
        run_on_main(lambda: finish(result))

    threading.Thread(target=worker, daemon=True).start()


# --- status messages ---------------------------------------------------------

def notify(message, seconds=4.0):
    """Short non-blocking confirmation in Fusion's status area (the API has no toasts)."""
    global _message_token
    token = object()
    _message_token = token
    bar = _app.userInterface.progressBar
    bar.show(message, 0, 1, False)
    bar.progressValue = 1

    def hide_if_current():
        if _message_token is token:
            bar.hide()

    timer = threading.Timer(seconds, lambda: run_on_main(hide_if_current))
    timer.daemon = True
    timer.start()
