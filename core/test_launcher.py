"""Desktop launch isolation and persistent data regression tests."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import launcher


class LauncherDataTests(TestCase):
    def test_explicit_data_directory_does_not_access_default_or_legacy(self):
        with TemporaryDirectory() as tmp:
            expected = Path(tmp) / "isolated-data"
            with patch.dict(os.environ, {"SARRAFI_DATA_DIR": str(expected)}), \
                    patch.object(launcher, "_migrate_legacy_data") as legacy:
                self.assertEqual(launcher.data_dir(), expected)
                self.assertTrue(expected.is_dir())
                legacy.assert_not_called()
