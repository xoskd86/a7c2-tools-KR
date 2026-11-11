"""Recovery remains pending until loaded-state verification passes."""
import json,os
from pathlib import Path
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6 import QtWidgets as W
from desktopapp import jobs,reloadguide
from desktopapp.window import Window


@pytest.fixture
def pending_window(tmp_path,monkeypatch):
    app=W.QApplication.instance() or W.QApplication([])
    monkeypatch.setenv('A7C2_DESKTOP_DATA',str(tmp_path/'app'))
    window=Window();window.timer.stop()
    work=tmp_path/'recovery';work.mkdir()
    state=dict(schema='a7c2-direct-job/1',mode='factory_reset',phase='AWAITING_RELOAD',
               readonly_after_reload_verified=False)
    (work/'direct.private.json').write_text(json.dumps(state))
    window.settings['factory_direct_work']=str(work)
    window.fields['factory_failed_work'].setText(str(work))
    yield window,work,state
    window.process=None;window.close()


def finish(window,work,phase,action='direct_factory_reset',success=True,error='not loaded'):
    window.current_job=jobs.new_job(action,{})
    (window.current_job/'status.private.json').write_text(json.dumps(dict(
        phase='SUCCEEDED' if success else 'FAILED',action=action,error=error,
        result=dict(workspace=str(work),mode='factory_reset',phase=phase))))
    window.process=Mock()
    window.finished(0 if success else 2,None)


def test_written_recovery_is_70_percent_and_requires_reboot_verification(pending_window):
    window,work,state=pending_window
    with patch.object(window,'show_reload_guide') as popup:
        finish(window,work,'AWAITING_RELOAD')
    assert window.progress.value()==70
    assert '복원이 아직 끝나지 않았습니다' in window.status.text()
    assert '재시작 후 확인하고 복원 완료'==window.factory_button.text()
    assert '아직 끝나지 않았습니다' in window.factory_status.text()
    popup.assert_called_once_with(setup=False,mode='factory_reset')
    assert not window.factory_accept.isChecked()


def test_only_verified_recovery_reaches_100_percent(pending_window):
    window,work,state=pending_window
    state.update(phase='LOADED',readonly_after_reload_verified=True)
    (work/'direct.private.json').write_text(json.dumps(state))
    with patch.object(window,'show_reload_guide') as popup:
        finish(window,work,'LOADED','direct_verify')
    assert window.progress.value()==100
    assert '확인을 통과' in window.status.text()
    assert '확인을 통과했습니다' in window.factory_status.text()
    assert not window.factory_accept.isChecked()
    popup.assert_not_called()


def test_failed_verify_keeps_pending_state(pending_window):
    window,work,state=pending_window
    finish(window,work,'AWAITING_RELOAD','direct_verify',False)
    assert window.progress.value()==0
    assert '아직 끝나지 않았습니다' in window.factory_status.text()
    assert window.factory_button.text()=='재시작 후 확인하고 복원 완료'


def test_restart_dialog_explicitly_requires_followup_check():
    text=reloadguide.text(mode='factory_reset')
    for phrase in ('복원이 아직 끝나지 않았습니다','재시작한 뒤 카메라의 실제 로드 상태를 확인해야 합니다','최소 1분간 기다립니다','USB를 다시 연결','재시작 후 확인하고 복원 완료','확인을 통과해야만'):
        assert phrase in text


@pytest.mark.parametrize('old_phase',['WAIT_RELOAD','COMPLETE','NEEDS_REVIEW'])
def test_verified_factory_reset_clears_active_install_state_and_survives_reopen(pending_window,old_phase):
    from desktopapp.paths import data_root,write_json,load_settings
    window,work,state=pending_window
    setup=data_root()/'work/old-setup'
    write_json(setup/'setup.private.json',dict(phase=old_phase,options=dict(looks=[])))
    old_sound=window.fields['sound_work'].text()
    active=('setup_work','look_direct_work','look_resume_source','look_job_dir','fl23_direct_work')
    window.settings.update({k:str(setup) for k in active})
    state.update(phase='LOADED',readonly_after_reload_verified=True)
    write_json(work/'direct.private.json',state)
    finish(window,work,'LOADED','direct_verify')
    assert all(k not in load_settings() for k in active)
    assert window.fields['sound_work'].text()!=old_sound
    assert load_settings()['sound_work']==window.fields['sound_work'].text()
    assert window.setup_button.text()=='원클릭 설치'
    assert window.setup_result_button.isHidden()
    assert window.setup_note.isHidden()
    assert window.setup_presets.isEnabled()
    assert (setup/'setup.private.json').exists()  # Evidence stays archived.
    assert window.setup_args()['workspace']!=str(setup)
    reopened=Window();reopened.timer.stop()
    try:
        assert reopened.setup_button.text()=='원클릭 설치'
        assert reopened.setup_note.isHidden()
    finally:reopened.close()


def test_pending_recovery_keeps_install_state_until_verified(pending_window):
    window,work,state=pending_window
    window.settings['setup_work']='old-setup'
    with patch.object(window,'show_reload_guide'):
        finish(window,work,'AWAITING_RELOAD')
    assert window.settings['setup_work']=='old-setup'


@pytest.mark.parametrize('error',['[Errno 13] Access denied (insufficient permissions)','No normal USB identity'])
def test_connection_failure_does_not_claim_an_unfinished_restore(pending_window,error):
    from desktopapp.paths import write_json
    window,work,state=pending_window
    write_json(work/'direct.private.json',dict(schema='a7c2-direct-job/1',mode='factory_reset',
        phase='NEEDS_REVIEW',failed_during='READING',journal=[],error=error))
    window.refresh_factory_controls()
    assert '시작되지 않았' in window.factory_status.text()
    assert '복원 계속' not in window.factory_button.text()


def test_existing_recovery_startup_uses_real_app_entry_without_pytest_module_path():
    from dev.desktop.startup_state_smoke import run
    import sys
    from desktopapp.paths import ROOT
    result=run([sys.executable,str(ROOT/'desktop.py')],cases=('factory_connection_failed',))
    assert result['cases']['factory_connection_failed']['pages']==7


def temporary_conflict_state():
    # Diagnostic (21): runtime and vertical probes completed, alias stage did
    # not start because a shared name worker already existed on the camera.
    journal=[]
    for prefix,stage in (('', 'checkpoint.staged.so'),('vertical_', 'lut-runtime-staged.so')):
        journal.extend(dict(event=prefix+kind,stage=stage,mode=0) for kind in
                       ('native_stage_intent','native_intent','native_result_verified','native_cleanup_verified'))
    return dict(mode='factory_reset',phase='NEEDS_REVIEW',failed_during='PLANNED',
                identity=dict(model='ILCE-7CM2',serial_sha256='test-body'),journal=journal,
                error='Existing temporary state needs recovery: lut-names-staged.so')


def test_diagnostic21_offers_prewrite_restart_without_claiming_recovery_complete(pending_window):
    from desktopapp.paths import write_json
    from looktool.factoryrecover import pending
    window,work,_=pending_window
    state=temporary_conflict_state()
    write_json(work/'direct.private.json',state)
    original=(work/'direct.private.json').read_bytes()
    with patch.object(window,'show_reload_guide') as popup:
        finish(window,work,'NEEDS_REVIEW',success=False,error=state['error'])
    popup.assert_called_once_with(mode='factory_prewrite_temporary')
    assert window.progress.value()==0
    assert window.factory_button.text()=='전원 차단 후 복원 재시도(이번에는 아직 쓰지 않음)'
    assert '이번 복원은 아직 쓰지 않았' in window.status.text()
    assert (work/'direct.private.json').read_bytes()==original
    assert pending(work.parent,work) is None  # Next click plans a fresh restore.


@pytest.mark.parametrize('change', ['write','unclean','resume','writing_phase','active_plugin'])
def test_unknown_or_started_recovery_never_gets_prewrite_retry_advice(change):
    from looktool.factoryrecover import prewrite_temporary_conflict
    state=temporary_conflict_state()
    if change=='write':state['journal'].append(dict(event='native_intent',mode=1))
    elif change=='unclean':state['journal'].pop()
    elif change=='resume':state['resume_from']='previous-restore'
    elif change=='writing_phase':state['failed_during']='WRITING'
    elif change=='active_plugin':state['error']='Existing temporary state needs recovery: libJit.so'
    assert not prewrite_temporary_conflict(state)


def test_temporary_conflict_guidance_requires_restart_then_retry_not_verify():
    text=reloadguide.text(mode='factory_prewrite_temporary')
    for phrase in ('아직 쓰기를 시작하지 않았','최소 1분간 기다립니다','USB를 다시 연결','전원 차단 후 복원 재시도','이전 LUT 작업은 필요 없습니다'):
        assert phrase in text


def test_finished_restore_writes_retire_old_sound_records_before_verification(pending_window):
    window,work,state=pending_window
    window.settings['setup_work']='old-setup'
    old=window.fields['sound_work'].text()
    with patch.object(window,'show_reload_guide'):
        finish(window,work,'AWAITING_RELOAD')
    assert window.settings['setup_work']=='old-setup'
    assert window.fields['sound_work'].text()!=old
    assert window.settings['sound_work']==window.fields['sound_work'].text()
