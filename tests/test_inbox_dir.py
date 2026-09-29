"""Where the dashboard looks for freshly downloaded exports.

On Windows the Downloads folder can be relocated (OneDrive, a second
drive); the registry records where. These tests stand in a fake winreg so
the lookup runs on any platform.
"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402

DOWNLOADS_GUID = "{374DE290-123F-4565-9164-39C4925E467B}"


def fake_winreg(value, raise_on_open=False):
    """A stand-in for the winreg module returning `value` for the GUID."""
    mod = types.ModuleType("winreg")
    mod.HKEY_CURRENT_USER = object()

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(root, path):
        if raise_on_open:
            raise OSError("no such key")
        return Key()

    def QueryValueEx(key, name):
        assert name == DOWNLOADS_GUID
        return value, 2

    mod.OpenKey = OpenKey
    mod.QueryValueEx = QueryValueEx
    return mod


class DefaultInboxDirTest(unittest.TestCase):
    def test_non_windows_uses_home_downloads(self):
        with mock.patch.object(dashboard.sys, "platform", "darwin"):
            self.assertEqual(dashboard.default_inbox_dir(), Path.home() / "Downloads")

    def test_windows_reads_registry_and_expands_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            moved = Path(tmp) / "OneDrive" / "Downloads"
            moved.mkdir(parents=True)
            env = {"USERPROFILE": tmp}
            with mock.patch.object(dashboard.sys, "platform", "win32"), \
                    mock.patch.dict(sys.modules, {"winreg": fake_winreg("%USERPROFILE%/OneDrive/Downloads")}), \
                    mock.patch.dict(os.environ, env):
                self.assertEqual(dashboard.default_inbox_dir(), moved)

    def test_windows_falls_back_when_registry_folder_is_missing(self):
        with mock.patch.object(dashboard.sys, "platform", "win32"), \
                mock.patch.dict(sys.modules, {"winreg": fake_winreg("Z:/does/not/exist")}):
            self.assertEqual(dashboard.default_inbox_dir(), Path.home() / "Downloads")

    def test_windows_falls_back_when_key_is_absent(self):
        with mock.patch.object(dashboard.sys, "platform", "win32"), \
                mock.patch.dict(sys.modules, {"winreg": fake_winreg("", raise_on_open=True)}):
            self.assertEqual(dashboard.default_inbox_dir(), Path.home() / "Downloads")


if __name__ == "__main__":
    unittest.main()
