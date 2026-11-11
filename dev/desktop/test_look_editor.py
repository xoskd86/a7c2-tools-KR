"""Incremental GUI selection and saved-job routing, without camera access."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6 import QtWidgets as W
from desktopapp import jobs,presets
from desktopapp.paths import data_root,write_json
from desktopapp.window import Window


class LookEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=W.QApplication.instance() or W.QApplication([])

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        env=patch.dict(os.environ,A7C2_DESKTOP_DATA=str(self.root/'app'));env.start();self.addCleanup(env.stop)

    def window(self):
        window=Window();window.timer.stop()
        def close():window.process=None;window.close()
        self.addCleanup(close);return window

    def pending(self,phase='AWAITING_RELOAD'):
        root=data_root()/'work'/'looks-pending'
        operations=[dict(slot=4,action='restore'),dict(presets.catalog()[5],action='write')]
        operations[1]['name']='Custom GR'
        state=dict(mode='install_all',phase=phase,bundle_options=dict(
            slot_operations=operations,looks=[{k:v for k,v in operations[1].items() if k!='action'}],reset_slots=[4]))
        write_json(root/'direct.private.json',state)
        write_json(data_root()/'desktop.json',dict(look_direct_work=str(root)))
        return root,state

    def test_empty_default_ignores_old_prefilled_six_paths(self):
        write_json(data_root()/'desktop.json',{f'look_batch_file_{i}':'old.a7look' for i in range(1,7)})
        window=self.window();self.assertEqual(window.look_editor.values(),[])
        self.assertFalse(window.look_button.isEnabled())
        row=window.look_editor.add_row();self.assertEqual(row.value(),dict(slot=1,action='restore'))
        self.assertTrue(window.look_button.isEnabled())
        self.assertIn('커스텀 2, 3, 4, 5, 6',window.look_editor.preserved.text())

    def test_target_file_and_name_stay_together_and_duplicates_are_unselectable(self):
        window=self.window();one=window.look_editor.add_row(2);two=window.look_editor.add_row(4)
        self.assertFalse(two.slot.model().item(1).isEnabled())
        one.action.setCurrentIndex(1)
        self.assertFalse(window.look_button.isEnabled())
        one.set_file(presets.catalog()[1]['path']);one.name.setText('My CC')
        self.assertTrue(window.look_button.isEnabled())
        self.assertEqual(window.look_editor.values()[0]['name'],'My CC')
        self.assertEqual(one.details.currentIndex(),1)
        self.assertEqual(one.file.parentWidget(),one.name.parentWidget())
        self.assertNotIn('P:',one.file.text())
        window.look_editor.remove_row(one)
        self.assertEqual(window.look_editor.values(),[dict(slot=4,action='restore')])
        self.assertTrue(two.slot.model().item(1).isEnabled())

    def test_max_six_items_all_default_restore(self):
        window=self.window()
        for _ in range(6):window.look_editor.add_row()
        self.assertEqual([r['slot'] for r in window.look_editor.values()],list(range(1,7)))
        self.assertTrue(all(r['action']=='restore' for r in window.look_editor.values()))
        self.assertFalse(window.look_editor.add.isEnabled())

    def test_written_plan_does_not_lock_new_operations_or_require_verify(self):
        root,state=self.pending();window=self.window()
        self.assertTrue(window.look_editor.isEnabled())
        self.assertIsNone(window.look_pending_state())
        self.assertEqual(window.look_editor.values(),[])
        self.assertFalse(hasattr(window,'look_accept'))
        self.assertFalse(window.pages.widget(4).findChildren(W.QPushButton,'action_direct_verify'))
        window.look_editor.add_row(2)
        with patch.object(W.QMessageBox,'question',return_value=W.QMessageBox.Yes),patch('desktopapp.window.QtCore.QProcess'):
            window.look_button.click()
        request=json.loads((window.current_job/'request.private.json').read_text())
        self.assertEqual(request['action'],'direct_looks_import')
        self.assertNotEqual(request['arguments']['workspace'],str(root))

    def test_resource_pause_continues_saved_job_without_reselecting_files(self):
        root,state=self.pending('AWAITING_RESOURCE_RELOAD');window=self.window()
        with patch.object(W.QMessageBox,'question',return_value=W.QMessageBox.Yes),patch('desktopapp.window.QtCore.QProcess'):
            window.look_button.click()
        request=json.loads((window.current_job/'request.private.json').read_text())
        self.assertEqual(request['action'],'direct_resume_looks')
        self.assertEqual(request['arguments']['failed_workspace'],str(root))
        self.assertNotIn('operations',request['arguments'])

    def test_unknown_failed_result_never_creates_a_fresh_write(self):
        root,state=self.pending('NEEDS_REVIEW');state['error']='unknown program result'
        write_json(root/'direct.private.json',state);window=self.window()
        with patch.object(jobs,'new_job') as new,patch.object(W.QMessageBox,'warning') as warning, \
                patch('desktopapp.window.QtGui.QDesktopServices.openUrl'):
            window.look_button.click()
        new.assert_not_called();warning.assert_called_once()

    def test_proven_read_only_failure_keeps_plan_editable_for_explicit_retry(self):
        root,state=self.pending('NEEDS_REVIEW')
        state.update(failed_during='READING',journal=[],error='USB unavailable before reads')
        write_json(root/'direct.private.json',state);before=(root/'direct.private.json').read_bytes()
        window=self.window();self.assertTrue(window.look_editor.isEnabled())
        self.assertIn('지난번에는 쓰지 않음',window.look_button.text())
        with patch.object(W.QMessageBox,'question',return_value=W.QMessageBox.Yes),patch('desktopapp.window.QtCore.QProcess'):
            window.look_button.click()
        request=json.loads((window.current_job/'request.private.json').read_text())
        self.assertEqual(request['action'],'direct_looks_import')
        self.assertNotEqual(request['arguments']['workspace'],str(root))
        self.assertEqual((root/'direct.private.json').read_bytes(),before)

    def test_read_only_retry_needs_complete_native_cleanup_and_no_write_intent(self):
        from desktopapp.lookbatch import read_only_failure
        base=dict(phase='NEEDS_REVIEW',failed_during='PLANNED',journal=[
            dict(event='native_stage_intent',stage='worker.so',mode=0),
            dict(event='native_cleanup_verified',stage='worker.so',mode=0)])
        self.assertTrue(read_only_failure(base))
        for state in (dict(base,journal=base['journal'][:1]),dict(base,failed_during='WRITING'),
                dict(base,journal=base['journal']+[dict(event='native_intent',mode=1)]),
                dict(base,failed_during=None)):
            self.assertFalse(read_only_failure(state))

    def test_background_result_uses_actual_workspace_even_without_finished_callback(self):
        root,state=self.pending();candidate=self.root/'unused-candidate';job=self.root/'background-job'
        write_json(job/'request.private.json',dict(action='direct_resume_looks',arguments=dict(workspace=str(candidate),failed_workspace=str(root))))
        write_json(job/'status.private.json',dict(phase='SUCCEEDED',action='direct_resume_looks',result=dict(workspace=str(root))))
        write_json(data_root()/'desktop.json',dict(look_direct_work=str(candidate),look_resume_source=str(root),look_job_dir=str(job)))
        window=self.window();self.assertIsNone(window.look_pending_state())
        self.assertTrue(window.look_editor.isEnabled())

    def test_background_completion_is_refreshed_without_restarting_gui(self):
        root,state=self.pending();job=self.root/'background-job'
        write_json(job/'request.private.json',dict(action='direct_looks_import',arguments=dict(workspace=str(root))))
        write_json(job/'status.private.json',dict(phase='RUNNING',action='direct_looks_import'))
        write_json(data_root()/'desktop.json',dict(look_direct_work=str(root),look_job_dir=str(job)))
        window=self.window();self.assertEqual(window.look_pending_state()[1]['phase'],'BACKGROUND_UNCONFIRMED')
        state['phase']='LOADED';write_json(root/'direct.private.json',state)
        write_json(job/'status.private.json',dict(phase='SUCCEEDED',action='direct_looks_import',result=dict(workspace=str(root))))
        window.poll_job()
        self.assertIsNone(window.look_pending_state());self.assertTrue(window.look_editor.isEnabled())
        self.assertEqual(window.look_editor.values(),[])

    def test_optional_old_verification_keeps_new_unsent_operations(self):
        root,state=self.pending('LOADED');window=self.window();window.look_editor.add_row(2)
        window.current_job=jobs.new_job('direct_verify',dict(workspace=str(root)))
        write_json(window.current_job/'status.private.json',dict(action='direct_verify',phase='SUCCEEDED',
            result=dict(mode='install_all',phase='LOADED',workspace=str(root))))
        window.process=Mock();window.finished(0,None)
        self.assertEqual(window.look_editor.values(),[dict(slot=2,action='restore')])

    def test_setup_label_follows_new_install_wait_and_completion(self):
        root=self.root/'setup';state=dict(phase='COMPLETE',options=dict(looks=[],fl23=True,sound=False,vertical=False))
        write_json(root/'setup.private.json',state);write_json(data_root()/'desktop.json',dict(setup_work=str(root)))
        window=self.window();self.assertIn('새 원클릭 설치 시작',window.setup_button.text())
        state['phase']='WAIT_RELOAD';write_json(root/'setup.private.json',state)
        window.current_job=jobs.new_job('setup_all',{})
        write_json(window.current_job/'status.private.json',dict(action='setup_all',phase='SUCCEEDED',result=state))
        window.process=Mock()
        with patch.object(window,'show_reload_guide'):window.finished(0,None)
        self.assertEqual(window.setup_button.text(),'설치 계속')
        self.assertFalse(window.setup_fl23.isEnabled())
        self.assertEqual(window.setup_args()['workspace'],str(root))
        state['phase']='COMPLETE';write_json(root/'setup.private.json',state)
        write_json(window.current_job/'status.private.json',dict(action='setup_all',phase='SUCCEEDED',result=state))
        window.process=Mock()
        with patch.object(window,'show_setup_result'):window.finished(0,None)
        self.assertIn('새 원클릭 설치 시작',window.setup_button.text())
        self.assertTrue(window.setup_fl23.isEnabled())
