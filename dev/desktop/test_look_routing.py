"""Look-editor routing across fresh startup and resumed workers; no device I/O."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtWidgets as W
from desktopapp import jobs
from desktopapp.paths import write_json
from desktopapp.window import Window


ROOT = Path(__file__).resolve().parents[2]


class LookRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = W.QApplication.instance() or W.QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        environment = patch.dict(os.environ, {
            'A7C2_DESKTOP_DATA': str(self.root / 'app'),
            'QT_QPA_PLATFORM': 'offscreen',
        })
        environment.start()
        self.addCleanup(environment.stop)

    def window(self):
        value = Window()
        value.timer.stop()
        def close():
            value.process = None
            value.close()
        self.addCleanup(close)
        return value

    def test_first_source_launch_initializes_look_backend_without_prior_device_action(self):
        # A child interpreter deliberately avoids pytest's conftest path setup
        # and cached looktool imports. Mock QProcess so no worker can execute.
        script = textwrap.dedent('''
            import json, sys
            from pathlib import Path
            from unittest.mock import patch
            sys.path.insert(0, str(Path.cwd() / 'src'))
            assert 'looktool' not in sys.modules
            from PySide6 import QtWidgets as W
            from desktopapp.window import Window
            app = W.QApplication([])
            window = Window()
            window.timer.stop()
            window.look_editor.add_row(4)
            with patch.object(W.QMessageBox, 'question', return_value=W.QMessageBox.Yes), \\
                 patch.object(W.QMessageBox, 'warning') as warning, \\
                 patch('desktopapp.window.QtCore.QProcess'):
                window.launch_look_changes()
            warning.assert_not_called()
            request = json.loads((window.current_job / 'request.private.json').read_text(encoding='utf-8'))
            assert request['action'] == 'direct_looks_import'
            assert request['arguments']['operations'] == [{'slot': 4, 'action': 'restore'}]
            window.process = None
            window.close()
            print('fresh-look-route-ok')
        ''')
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
            env=environment, capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('fresh-look-route-ok', result.stdout)

    def test_continuation_tracks_actual_returned_workspace_and_survives_gui_restart(self):
        for phase in ('AWAITING_RESOURCE_RELOAD',):
            with self.subTest(phase=phase):
                window = self.window()
                requested = self.root / phase / 'unused-request-directory'
                actual = self.root / phase / 'actual-program-install'
                operations = [dict(slot=4, action='restore')]
                write_json(actual / 'direct.private.json', dict(
                    phase=phase, mode='install_all',
                    bundle_options=dict(looks=[], reset_slots=[4], slot_operations=operations)))
                window.settings['look_direct_work'] = str(requested)
                window.current_job = jobs.new_job('direct_resume_looks', dict(workspace=str(requested)))
                write_json(window.current_job / 'status.private.json', dict(
                    phase='SUCCEEDED', action='direct_resume_looks',
                    result=dict(workspace=str(actual), phase=phase, mode='install_all')))
                window.process = Mock()
                with patch.object(window, 'show_reload_guide') as popup:
                    window.finished(0, None)
                popup.assert_called_once()
                self.assertIsNone(window.process)
                self.assertEqual(window.settings['look_direct_work'], str(actual))
                self.assertEqual(window.look_pending_state()[0], str(actual))
                self.assertEqual(window.look_editor.values(), operations)
                self.assertFalse(window.look_editor.isEnabled())
                self.assertTrue(window.look_button.isEnabled())
                self.assertNotIn('작업 항목을 추가한 뒤 계속', window.look_button.text())
                self.assertFalse(requested.exists())

                restarted = self.window()
                self.assertEqual(restarted.look_pending_state()[0], str(actual))
                self.assertEqual(restarted.look_editor.values(), operations)
                self.assertFalse(restarted.look_editor.isEnabled())

    def test_completed_readonly_verification_clears_only_finished_operation_list(self):
        actual = self.root / 'installed'
        write_json(actual / 'direct.private.json', dict(
            phase='AWAITING_RELOAD', mode='install_all',
            bundle_options=dict(reset_slots=[2, 5], looks=[])))
        write_json(self.root / 'app' / 'desktop.json', dict(look_direct_work=str(actual)))
        window = self.window()
        self.assertEqual(len(window.look_editor.values()), 0)
        write_json(actual / 'direct.private.json', dict(phase='LOADED', mode='install_all'))
        window.current_job = jobs.new_job('direct_verify', dict(workspace=str(actual)))
        write_json(window.current_job / 'status.private.json', dict(
            phase='SUCCEEDED', action='direct_verify',
            result=dict(workspace=str(actual), phase='LOADED', mode='install_all')))
        window.process = Mock()
        with patch.object(window, 'show_reload_guide') as popup:
            window.finished(0, None)
        popup.assert_not_called()
        self.assertEqual(window.look_editor.values(), [])
        self.assertTrue(window.look_editor.isEnabled())
        self.assertFalse(window.look_button.isEnabled())
        self.assertEqual(window.settings['look_direct_work'], str(actual))


if __name__ == '__main__':
    unittest.main()
