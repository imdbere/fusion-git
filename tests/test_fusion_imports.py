"""Smoke test: the Fusion-facing modules import and wire up against a mocked adsk API."""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "FusionGit"))


def _fake_adsk():
    adsk = types.ModuleType("adsk")
    core, fusion = mock.MagicMock(), mock.MagicMock()

    # Handler base classes must be real classes so the add-in can subclass them.
    for name in ("CustomEventHandler", "CommandCreatedEventHandler", "CommandEventHandler",
                 "InputChangedEventHandler", "DocumentEventHandler", "ValidateInputsEventHandler"):
        setattr(core, name, type(name, (), {}))
    setattr(adsk, "core", core)
    setattr(adsk, "fusion", fusion)
    setattr(adsk, "doEvents", lambda: None)
    return adsk


class FusionImportTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="fusiongit-home-")
        self.patches = [mock.patch.dict(os.environ, {"FUSIONGIT_HOME": self.home})]
        adsk = _fake_adsk()
        self.patches.append(mock.patch.dict(sys.modules, {
            "adsk": adsk, "adsk.core": adsk.core, "adsk.fusion": adsk.fusion}))
        for p in self.patches:
            p.start()
        for name in [m for m in sys.modules if m.startswith("fusiongit")]:
            del sys.modules[name]

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_modules_import_and_start(self):
        from fusiongit import actions, design, log, mainthread, ui  # noqa: F401
        log.setup()
        mainthread.start()
        ui.start()
        ui.refresh()
        ui.stop()
        mainthread.stop()

    def test_every_button_has_an_action_and_icon(self):
        from fusiongit import ui
        dialogs = ("FusionGitInit", "FusionGitCommit", "FusionGitSettings")
        for button in ui.BUTTONS:
            self.assertTrue(button.id in ui.SIMPLE_ACTIONS or button.id in dialogs, button.id)
            self.assertTrue(ui._icon_folder(button.id), f"missing icon for {button.id}")
            self.assertIn(button.panel, dict(ui.PANELS))
        self.assertTrue(ui._icon_folder(ui.OPEN_ID))

    def test_git_errors_are_explained(self):
        from fusiongit.gitcli import GitError, describe_error
        title, message = describe_error(GitError(["push", "origin"], 1, "", " ! [rejected] main -> main (fetch first)"))
        self.assertEqual(title, "Push failed")
        self.assertIn("Pull first", message)
        title, message = describe_error(GitError(["fetch"], 128, "", "fatal: Could not resolve host: github.com"))
        self.assertEqual(title, "Pull failed")
        self.assertIn("internet connection", message)

    def test_safe_file_name(self):
        from fusiongit.actions import safe_file_name
        self.assertEqual(safe_file_name('Bracket: v2/"final"'), "Bracket_ v2__final_")
        self.assertEqual(safe_file_name("..."), "design")


if __name__ == "__main__":
    unittest.main()
