import json
import tempfile
import unittest
from pathlib import Path
from tools.browser_runtime import bundled_browser


class BrowserRuntimeTests(unittest.TestCase):
    def test_matching_browser_only_and_missing_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'browser'
            folder.mkdir()
            binary = folder / 'chrome.exe'
            binary.touch()
            manifest = folder / 'browser-manifest.json'
            manifest.write_text(json.dumps({'playwright_version':'1.63.0','executable':'chrome.exe'}))
            self.assertEqual(bundled_browser(root, '1.63.0'), str(binary.resolve()))
            self.assertIsNone(bundled_browser(root, '1.62.0'))
            binary.unlink()
            self.assertIsNone(bundled_browser(root, '1.63.0'))

    def test_manifest_cannot_escape_browser_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'browser'
            folder.mkdir()
            (root / 'outside.exe').touch()
            (folder / 'browser-manifest.json').write_text(json.dumps({'playwright_version':'1.63.0','executable':'../outside.exe'}))
            with self.assertRaisesRegex(ValueError, '无效'):
                bundled_browser(root, '1.63.0')
