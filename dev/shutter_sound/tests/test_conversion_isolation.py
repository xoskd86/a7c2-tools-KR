"""Every conversion is a fresh offline job, independent of camera task state."""
import contextlib
import copy
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import media
import native_codec
import sound_cli as cli
import trial


class ConversionIsolationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.source=self.root/'click.wav'
        native_codec.write_wav(self.source,[round(5000*math.sin(i*.2)) for i in range(4800)],24000)
        self.work=self.root/'sound'

    def pending(self):
        self.work.mkdir()
        value=dict(schema=cli.SCHEMA,pending=dict(command='on',job='unresolved-native-job',outcome='needs_review'),
            native_job='unresolved-native-job',boot_job='unresolved-persistent-job',history=[])
        cli.save(self.work,value)
        return value

    def test_pending_camera_job_is_unchanged_and_does_not_lock_repeated_conversion(self):
        state=self.pending();before=(self.work/'sound.private.json').read_bytes();source=self.source.read_bytes()
        with patch.object(trial,'factory',side_effect=AssertionError('No USB')),patch.object(cli,'load_job',side_effect=AssertionError('No camera-job dependency')):
            first=cli.import_audio(self.work,state,self.source,duration=.1)
            first_folder=cli.selected_conversion(self.work,state)
            second=cli.import_audio(self.work,state,self.source,duration=.15)
            second_folder=cli.selected_conversion(self.work,state)
        self.assertNotEqual(first_folder,second_folder)
        self.assertEqual((self.work/'sound.private.json').read_bytes(),before)
        self.assertEqual(self.source.read_bytes(),source)
        self.assertEqual(state['pending']['outcome'],'needs_review')
        self.assertFalse(first['camera_io']);self.assertFalse(second['camera_io'])
        self.assertTrue(Path(first['preview']).is_file())

    def test_cli_fresh_conversion_needs_neither_camera_enable_nor_backup(self):
        reports=[]
        with patch.object(trial,'factory',side_effect=AssertionError('No USB')),contextlib.redirect_stdout(io.StringIO()):
            code=cli.main(['import',str(self.source),'--workspace',str(self.work),'--duration','.1'],report=reports.append)
        self.assertEqual(code,0);self.assertEqual(reports[0]['phase'],'converted_offline')
        self.assertFalse((self.work/'sound.private.json').exists())
        self.assertTrue((self.work/'conversion.private.json').is_file())
        # Enabling later can initialize the device workspace beside conversions.
        _,state=cli.workspace(self.work,True,self.root/'backup')
        self.assertIsNone(state['pending'])

    def test_failed_conversion_removes_own_files_and_retains_last_success(self):
        self.pending();cli.import_audio(self.work,None,self.source,duration=.1)
        pointer=(self.work/'conversion.private.json').read_bytes()
        folders=set((self.work/'jobs').iterdir())
        with patch.object(native_codec,'candidate',side_effect=ValueError('encoder failed')):
            with self.assertRaisesRegex(ValueError,'encoder failed'):
                cli.import_audio(self.work,None,self.source,duration=.1)
        self.assertEqual(set((self.work/'jobs').iterdir()),folders)
        self.assertEqual((self.work/'conversion.private.json').read_bytes(),pointer)
        self.assertEqual(list(self.work.glob('conversion-*.pending')),[])

    def test_cancelled_or_invalid_conversion_leaves_no_partial_output(self):
        for index,failure in enumerate((ValueError('bad input'),KeyboardInterrupt())):
            output=self.root/('failed-'+str(index))
            def partial(source,path,**options):
                Path(path).write_bytes(b'partial WAV');raise failure
            with patch.object(media,'extract',side_effect=partial):
                with self.assertRaises(type(failure)):media.convert(self.source,output,duration=.1)
            self.assertFalse(output.exists())

    def test_existing_output_is_never_deleted(self):
        output=self.root/'existing';output.mkdir();(output/'keep.txt').write_text('keep')
        with self.assertRaises(FileExistsError):media.convert(self.source,output,duration=.1)
        self.assertEqual((output/'keep.txt').read_text(),'keep')

    def test_camera_actions_supersede_unresolved_jobs_and_read_the_current_body(self):
        state=self.pending();state['backup_root']=str(self.root/'backup');cli.save(self.work,state)
        cli.import_audio(self.work,state,self.source,duration=.1)
        self.assertEqual(state['pending']['command'],'on')  # conversion alone changes no camera state
        import boot_trial
        for action in (lambda:cli.audition(self.work,state),lambda:cli.install(self.work,state,100)):
            with patch.object(trial,'factory',side_effect=RuntimeError('current camera read')),                  patch.object(boot_trial,'require_dependencies'):
                with self.assertRaisesRegex(RuntimeError,'current camera read'):action()
            self.assertNotEqual((state['pending'] or {}).get('job'),'unresolved-native-job')
        superseded=[h for h in state['history'] if h.get('phase')=='superseded_by_current_selection']
        self.assertEqual(superseded[0]['job'],'unresolved-native-job')

    def test_import_error_message_does_not_claim_unknown_camera_writes(self):
        output=io.StringIO()
        with contextlib.redirect_stderr(output):
            result=cli.main(['import',str(self.root/'missing.wav'),'--workspace',str(self.work),'--duration','.1'])
        self.assertEqual(result,2);self.assertIn('이번에는 카메라 읽기/쓰기를 하지 않았습니다',output.getvalue())
        self.assertNotIn('do not repeat unknown writes',output.getvalue())
