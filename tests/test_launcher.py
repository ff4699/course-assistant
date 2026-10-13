import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from tools import windows_launcher as launcher


class LauncherTests(unittest.TestCase):
    def make_project(self, root):
        for filename in ('requirements.txt', 'assistant/app.py', 'assistant/__main__.py'):
            path = root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

    def test_existing_service_reused_without_install_or_profile_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_project(root)
            profile = root / '.state/browser-profile/local-fixture'
            profile.parent.mkdir(parents=True)
            profile.write_text('kept')
            with patch.object(launcher, 'ROOT', root), patch.dict(os.environ, {'CUPK_STATE_DIR': str(root / '.state')}), \
                    patch.object(launcher, 'running_assistant', return_value=True), \
                    patch.object(launcher, 'prepare_python') as prepare, patch.object(launcher.webbrowser, 'open') as opened, \
                    patch.object(sys, 'argv', ['windows_launcher.py']), patch('builtins.print'):
                self.assertEqual(launcher.main(), 0)
                prepare.assert_not_called()
                opened.assert_called_once_with(launcher.URL)
            self.assertEqual(profile.read_text(), 'kept')
            self.assertTrue((root / '.state/startup.log').is_file())

    def test_missing_browser_downloads_before_app_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_project(root)
            with patch.object(launcher, 'ROOT', root), patch.dict(os.environ, {'CUPK_STATE_DIR': str(root / '.state'), 'CUPK_CHROMIUM': ''}), \
                    patch.object(launcher, 'running_assistant', return_value=False), \
                    patch.object(launcher, 'prepare_python', return_value=Path(sys.executable)), \
                    patch.object(launcher, 'probe', return_value=False), patch.object(launcher.shutil, 'which', return_value=None), \
                    patch.object(launcher, 'run', return_value=0) as run, patch.object(sys, 'argv', ['windows_launcher.py']), \
                    patch('builtins.print'):
                self.assertEqual(launcher.main(), 0)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args.args[0][1:], ['-m', 'assistant', '--open-browser'])
            self.assertIn('自动下载 Chromium', (root / '.state/startup.log').read_text(encoding='utf-8'))

    def test_failed_browser_download_preserves_profile_and_stops_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.make_project(root)
            profile=root/'.state/browser-profile/fixture';profile.parent.mkdir(parents=True);profile.write_text('kept')
            with patch.object(launcher,'ROOT',root),patch.dict(os.environ,{'CUPK_STATE_DIR':str(root/'.state'),'CUPK_CHROMIUM':''}),patch.object(launcher,'running_assistant',return_value=False),patch.object(launcher,'prepare_python',return_value=Path(sys.executable)),patch.object(launcher,'probe',return_value=False),patch.object(launcher.shutil,'which',return_value=None),patch.object(launcher,'run',return_value=1) as run,patch.object(sys,'argv',['windows_launcher.py']),patch('builtins.print'):
                self.assertEqual(launcher.main(),1)
                run.assert_called_once()
            self.assertEqual(profile.read_text(),'kept')
            self.assertIn('浏览器下载未完成',(root/'.state/startup.log').read_text(encoding='utf-8'))

    def test_app_failure_is_logged_and_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_project(root)
            with patch.object(launcher, 'ROOT', root), patch.dict(os.environ, {'CUPK_STATE_DIR': str(root / '.state')}), \
                    patch.object(launcher, 'running_assistant', return_value=False), \
                    patch.object(launcher, 'prepare_python', return_value=Path(sys.executable)), \
                    patch.object(launcher, 'probe', return_value=True), patch.object(launcher, 'run', return_value=1), \
                    patch.object(sys, 'argv', ['windows_launcher.py']), patch('builtins.print'):
                self.assertEqual(launcher.main(), 1)
            self.assertIn('服务未能启动', (root / '.state/startup.log').read_text(encoding='utf-8'))

    def test_install_browser_is_separate_and_preserves_service_option(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_project(root)
            with patch.object(launcher, 'ROOT', root), patch.dict(os.environ, {'CUPK_STATE_DIR': str(root / '.state')}), \
                    patch.object(launcher, 'prepare_python', return_value=Path(sys.executable)), \
                    patch.object(launcher, 'run', return_value=0) as run, \
                    patch.object(sys, 'argv', ['windows_launcher.py', '--install-browser']), patch('builtins.print'):
                self.assertEqual(launcher.main(), 0)
                self.assertEqual(run.call_args.args[0][1:], ['-m', 'playwright', 'install', 'chromium'])

    def test_child_output_is_logged_and_failure_exit_is_preserved(self):
        log = io.StringIO()
        code = 'import sys; print("download failed https://fixture:secret@example.test/simple"); sys.exit(7)'
        with patch('builtins.print'):
            result = launcher.run([sys.executable, '-u', '-c', code], log, {**os.environ, 'PYTHONUTF8': '1'})
        self.assertEqual(result, 7)
        self.assertIn('download failed', log.getvalue())
        self.assertNotIn('fixture:secret', log.getvalue())

    def test_unsupported_existing_venv_is_kept_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            binary.parent.mkdir(parents=True)
            binary.write_text('fixture-invalid-environment')
            with patch.object(launcher, 'ROOT', root), patch.object(launcher, 'python_supported', return_value=False):
                with self.assertRaisesRegex(launcher.StartupError, '保留 .state'):
                    launcher.prepare_python(io.StringIO(), os.environ.copy())
            self.assertEqual(binary.read_text(), 'fixture-invalid-environment')
