import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.runtime import prepare_runtime


class RuntimeTests(unittest.TestCase):
    def test_missing_parent_disables_only_process_keylog(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing" / "sslkeys.log")
            with patch.dict(os.environ, {"SSLKEYLOGFILE": missing, "OTHER_SETTING": "preserved"}):
                changes = prepare_runtime()
                self.assertNotIn("SSLKEYLOGFILE", os.environ)
                self.assertEqual(os.environ["OTHER_SETTING"], "preserved")
                self.assertEqual(changes, [{"variable": "SSLKEYLOGFILE", "reason": "parent_directory_missing"}])

    def test_existing_parent_preserves_intentional_tls_debugging(self):
        with tempfile.TemporaryDirectory() as directory:
            keylog = str(Path(directory) / "sslkeys.log")
            with patch.dict(os.environ, {"SSLKEYLOGFILE": keylog}):
                self.assertEqual(prepare_runtime(), [])
                self.assertEqual(os.environ["SSLKEYLOGFILE"], keylog)

    def test_unset_variable_remains_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(prepare_runtime(), [])
            self.assertNotIn("SSLKEYLOGFILE", os.environ)
