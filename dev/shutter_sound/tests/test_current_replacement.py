"""Current-request replacement, terminal-report ownership and JIT-free planning."""
import builtins
import json
from pathlib import Path
import struct
import threading
from unittest.mock import Mock, patch

import pytest
import boot_sound
import boot_trial
import replacement
import sound_cli
import trial


IDENTITY=dict(model='ILCE-7CM2',serial_sha256='fixture')
BOOT1=b'11111111-1111-1111-1111-111111111111'
BOOT2=b'22222222-2222-2222-2222-222222222222'


def camera(files):
    obj=Mock();obj.identity=dict(IDENTITY)
    obj.__enter__=Mock(return_value=obj);obj.__exit__=Mock(return_value=False)
    obj.read.side_effect=lambda path,missing=False:files.get(path)
    obj.temp_read.side_effect=lambda name,missing=False:files.get('/tmp/jiritsu/'+name)
    obj.temp_remove.side_effect=lambda name:files.pop('/tmp/jiritsu/'+name,None)
    return obj


@pytest.mark.parametrize('command',['install','audition'])
@pytest.mark.parametrize('pending',['missing_job','failed_job','no_pending'])
def test_new_selection_archives_history_and_reuses_one_live_connection(tmp_path,command,pending):
    root,state=sound_cli.workspace(tmp_path/'sound',True,tmp_path/'backup')
    old=root/'jobs/old';old.mkdir(parents=True)
    if pending=='failed_job':trial.save(old,dict(identity=IDENTITY,phase='needs_review',journal=[]))
    if pending!='no_pending':state['pending']=dict(command=command,job=str(old),outcome='needs_review')
    state.update(native_job=str(old),native_identity=dict(IDENTITY,serial_sha256='stale'))
    files={'/proc/sys/kernel/random/boot_id':BOOT1};cam=camera(files)
    with replacement.CurrentRequest(root,state,command):
        assert state['pending'] is None and 'native_job' not in state and 'native_identity' not in state
        with patch.object(trial,'factory',return_value=cam) as connect:
            with replacement.BorrowedCamera(replacement.active(),None) as first:
                with replacement.BorrowedCamera(replacement.active(),IDENTITY) as second:assert first is second
            connect.assert_called_once()
        cam.__exit__.assert_not_called()
    cam.__exit__.assert_called_once()
    assert old.is_dir()


def test_only_actual_concurrent_request_is_locked(tmp_path):
    errors=[]
    def compete():
        try:
            with replacement.command_lock(tmp_path/'sound'):pass
        except RuntimeError as exc:errors.append(str(exc))
    with replacement.command_lock(tmp_path/'sound'):
        thread=threading.Thread(target=compete);thread.start();thread.join()
        assert len(errors)==1
    with replacement.command_lock(tmp_path/'sound'):pass


@pytest.mark.parametrize('command',['install','audition','on','off','verify','restore-sound'])
@pytest.mark.parametrize('boot',[b'',BOOT1])
def test_old_jobs_reports_and_boot_proof_never_gate_new_sound_request(tmp_path,command,boot):
    root,state=sound_cli.workspace(tmp_path/'sound',True,tmp_path/'backup')
    marker=b'old marker with no report or token evidence'
    state.update(pending=dict(command='audition',job=str(root/'missing-old-job')),
        replacement_stop=dict(boot_id='',files={'shutter.custom.lease':marker.hex()}))
    files={'/proc/sys/kernel/random/boot_id':boot,'/tmp/jiritsu/shutter.custom.lease':marker}
    cam=camera(files)
    with patch.object(trial,'factory',return_value=cam):
        with replacement.CurrentRequest(root,state,command) as request:assert request.open(None) is cam
    assert state['pending'] is None and 'replacement_stop' not in state
    assert not any('boot_id' in c.args[0] for c in cam.read.call_args_list)
    if command=='audition':assert '/tmp/jiritsu/shutter.custom.lease' not in files
    else:assert files['/tmp/jiritsu/shutter.custom.lease']==marker


def test_stale_sound_worker_files_are_archived_and_replaced_without_reading_old_jobs(tmp_path):
    root,state=sound_cli.workspace(tmp_path/'sound',True,tmp_path/'backup')
    names=('shutter.staged.so','shutter.probe.result','a7c2.probe.result')
    files={'/tmp/jiritsu/'+name:b'incomplete old bytes' for name in names}
    cam=camera(files)
    with patch.object(trial,'factory',return_value=cam):
        with replacement.CurrentRequest(root,state,'audition') as request:
            request.open(None)
            assert all((request.evidence/(name+'.before.private.bin')).read_bytes()==b'incomplete old bytes' for name in names)
    assert not files


def test_occupied_native_loader_is_not_overwritten_or_automatically_stopped(tmp_path):
    root,state=sound_cli.workspace(tmp_path/'sound',True,tmp_path/'backup')
    files={'/tmp/jiritsu/libJit.so':b'execution entry in use'};cam=camera(files)
    with patch.object(trial,'factory',return_value=cam),pytest.raises(RuntimeError,match='실행 진입점이 아직 사용 중'):
        with replacement.CurrentRequest(root,state,'install') as request:request.open(None)
    cam.temp_remove.assert_not_called()
    assert cam.link.mutating is True


def test_changing_camera_files_stop_without_deleting_them(tmp_path):
    root,state=sound_cli.workspace(tmp_path/'sound',True,tmp_path/'backup')
    cam=camera({});calls=[b'first',b'changed']
    def read(path,missing=False):return calls.pop(0) if path=='/tmp/jiritsu/shutter.staged.so' else None
    cam.read.side_effect=read
    with patch.object(trial,'factory',return_value=cam),pytest.raises(RuntimeError,match='계속 바뀌고 있'):
        with replacement.CurrentRequest(root,state,'audition') as request:request.open(None)
    cam.temp_remove.assert_not_called()














def test_production_sound_planner_handles_stock_mixed_and_installed_without_jit():
    edits=boot_sound.expected_windows()
    source=bytearray(max(at+len(old) for at,old,new in edits)+4)
    for at,old,new in edits:source[at:at+len(old)]=old
    real_import=builtins.__import__
    def no_jit(name,*args,**kwargs):
        if name.split('.')[0] in ('unicorn','keystone'):raise AssertionError('Production sound must not use JIT')
        return real_import(name,*args,**kwargs)
    with patch('builtins.__import__',side_effect=no_jit),patch.object(boot_sound,'execute',side_effect=AssertionError('JIT')):
        target,csv,planned,report=boot_sound.plan(bytes(source),bytes(64),current=True)
        assert planned==edits and report['emulator_executed'] is False
        at,old,new=edits[0];source[at:at+len(new)]=new
        mixed,_,remaining,_=boot_sound.plan(bytes(source),bytes(64),current=True)
        assert mixed==target and len(remaining)==len(edits)-1
        assert boot_sound.plan(target,bytes(64),current=True)[2]==[]
        source[at]^=1
        with pytest.raises(ValueError,match='conflict'):boot_sound.plan(bytes(source),bytes(64),current=True)


@pytest.mark.parametrize('old_csv',[b'',b'broken private CSV'])
def test_files_only_previous_attempt_is_replaced_using_current_files(tmp_path,old_csv):
    csv=tmp_path/'csv';csv.write_bytes(b'new')
    files={boot_trial.PATHS[1]:old_csv,'/usr/share/avsystemsound/.a7c2-sound-audio.tmp':b'old-partial'}
    cam=camera(files);components=dict(csv=str(csv))
    with patch.object(boot_trial.files_worker,'build',return_value=tmp_path/'worker') as build:
        result=boot_trial.update_plan(cam,tmp_path,components,bytes(64),None)
    assert result['paths'][0].endswith('custom-shutter-a.bin')
    assert build.call_args.kwargs['previous_csv']==old_csv
    assert build.call_args.kwargs['temporary_files']==[b'old-partial',None]
    assert result['files'] and result['publish_files']




def test_actual_save_prepare_uses_byte_planner_instead_of_emulator(tmp_path):
    source=bytearray(boot_sound.PATH+len(boot_sound.OLD_PATH))
    for at,old,new in boot_sound.expected_windows():source[at:at+len(old)]=old
    with patch.object(boot_sound,'verify',side_effect=AssertionError('No production emulation')), \
         patch.object(boot_trial.candidate,'patch',return_value={'cpapp-b.bin':b'cp'}), \
         patch('looktool.nativebuild.compile_worker'), \
         patch.object(boot_trial,'program_worker',return_value={'path':'program'}), \
         patch.object(boot_trial.files_worker,'build',return_value=tmp_path/'files'):
        target,parts=boot_trial.prepare(bytes(source),b'cp',bytes(64),tmp_path/'build')
    assert boot_sound.installed(target['appFw.so'])
    assert len(parts['restore_windows'])==len(boot_sound.expected_windows())
