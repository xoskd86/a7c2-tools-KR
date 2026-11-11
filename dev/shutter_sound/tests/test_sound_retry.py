"""Read-only enable failure -> conversion -> audition, without blind replay."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sound_cli as cli
import trial
import buffer_trial


def journal(stage='shutter.staged.so', mode=0):
    return [dict(event=e, stage=stage, mode=mode) for e in (
        'native_stage_intent', 'native_intent', 'native_result_verified', 'native_cleanup_verified')]


@pytest.fixture
def failed_on(tmp_path):
    root, state = cli.workspace(tmp_path/'sound', True, tmp_path/'backup')
    job = root/'jobs'/'old-native-on'
    job.mkdir(parents=True)
    value = dict(schema='a7c2-shutter-job/1', phase='needs_review',
        identity=dict(model='ILCE-7CM2', serial_sha256='synthetic'),
        camera_firmware_written=False, journal=journal()+journal('lut-runtime-staged.so')+
        journal('lut-program-staged.so'))
    trial.save(job, value)
    state.update(native_job=str(job), pending=dict(command='on',job=str(job),outcome='needs_review'))
    cli.save(root,state)
    conversion=root/'converted'; conversion.mkdir()
    (conversion/'custom-native.private.bin').write_bytes(bytes(64))
    (root/'conversion.private.json').write_text(json.dumps(dict(conversion=str(conversion))))
    return root,state,job,value


def test_failed_on_adopts_fresh_enabled_support_then_auditions(failed_on):
    root,state,job,old=failed_on
    before=(job/'job.private.json').read_bytes()
    def adopt(path,copy,zig,**options):
        # A fresh request reads the connected body instead of reusing old evidence.
        assert options==dict(expected=None,adopt_only=True)
        path.mkdir(parents=True)
        result=dict(phase='written',identity=old['identity'],adopted_existing_enabled=True,
                    complete_readback_and_settings_verified=True,electronic_only_gate=True,camera_firmware_written=False)
        trial.save(path,result)
        return result
    def audition(path,original,*args,**options):
        assert original != job
        assert cli.load_job(original)['adopted_existing_enabled']
        assert options['payload']==bytes(64)
        return dict(phase='temporary_pcm_registered_cold_reload_required_after_test')
    with patch.object(trial,'preflight',side_effect=adopt), patch.object(trial,'change') as write, \
         patch.object(buffer_trial,'run',side_effect=audition) as run:
        cli.audition(root,state)
        assert run.call_count==1
        write.assert_not_called()
    assert state['pending'] is None
    assert (job/'job.private.json').read_bytes()==before
    assert state['history'][0]['phase']=='superseded_by_current_selection'


def test_support_off_produces_specific_instruction_and_next_enable_is_unblocked(failed_on):
    root,state,job,old=failed_on
    def off(path,copy,zig,**options):
        path.mkdir(parents=True)
        trial.save(path,dict(old,journal=[]))
        raise ValueError('전자 셔터음이 아직 켜져 있지 않습니다. 먼저 "전자 셔터음 켜기"를 누르세요')
    with patch.object(trial,'preflight',side_effect=off), patch.object(buffer_trial,'run') as run:
        with pytest.raises(ValueError,match='아직 켜져 있지 않습니다'):cli.audition(root,state)
        run.assert_not_called()
    with patch.object(trial,'preflight',return_value={'phase':'written','adopted_existing_enabled':True}), \
         patch.object(trial,'change') as write:
        cli.on(root,state,100)
        write.assert_not_called()
    assert state['pending'] is None


@pytest.mark.parametrize('alter', ['write_intent','staging_only','missing_cleanup','unknown_worker',
                                  'wrong_schema','written_flag','unrelated_event','missing_mode','missing_journal'])
def test_unresolved_or_mutating_evidence_is_superseded_never_replayed(failed_on,alter):
    root,state,job,value=failed_on
    if alter=='write_intent':value['journal']+=journal('lut-program-staged.so',1)
    elif alter=='staging_only':value['journal']+=journal()[:1]
    elif alter=='missing_cleanup':value['journal'].pop()
    elif alter=='unknown_worker':value['journal']+=journal('buffer.so')
    elif alter=='wrong_schema':value['schema']='a7c2-empty-sound-buffer-trial/1'
    elif alter=='written_flag':value['camera_firmware_written']=True
    elif alter=='unrelated_event':value['journal'].append(dict(event='settings_write'))
    elif alter=='missing_mode':value['journal'][0].pop('mode')
    elif alter=='missing_journal':value.pop('journal')
    trial.save(job,value)
    before=(job/'job.private.json').read_bytes()
    with patch.object(trial,'factory',side_effect=RuntimeError('current camera read')),          patch.object(trial,'change',side_effect=AssertionError('No replay')):
        with pytest.raises(RuntimeError,match='current camera read'):cli.audition(root,state)
    # The old record is archived unchanged; it is never retired as safe or replayed.
    assert (job/'job.private.json').read_bytes()==before
    assert (state['pending'] or {}).get('job')!=str(job)  # only this request's own step may be open
    assert not any(h.get('phase')=='failed_before_mutation' for h in state['history'])
    assert any(h.get('phase')=='superseded_by_current_selection' and h.get('job')==str(job) for h in state['history'])


def test_audio_registration_mode_zero_is_not_readonly_enable(failed_on):
    root,state,job,value=failed_on
    state['pending']['command']='audition'
    assert not cli.retire_readonly_failure(root,state)
    assert state['pending']


def test_conversion_selection_survives_retirement(failed_on):
    root,state,job,value=failed_on
    selected=(root/'conversion.private.json').read_bytes()
    assert cli.retire_readonly_failure(root,state)
    assert (root/'conversion.private.json').read_bytes()==selected
    assert not cli.retire_readonly_failure(root,state)


@pytest.mark.parametrize('service_only,recorded', [(False,'legacy'),(True,'legacy'),
                                                (False,'fresh'),(False,'extra')])
def test_legacy_sound_identity_runs_actual_connection_branch(service_only,recorded):
    from looktool import devicesession
    import usb.core
    import usb.backend.libusb1
    identity=dict(model='ILCE-7CM2',serial_sha256='synthetic')
    expected=(None if recorded=='fresh' else dict(identity,usb_serial_sha256='optional-field')
              if recorded=='extra' else identity)
    camera=trial.factory(expected)
    endpoint=Mock(iSerialNumber=1)
    link=Mock(identity=identity)
    link.__enter__=Mock(return_value=link)
    transport=SimpleNamespace(Session=Mock(return_value=link),RecoverySession=Mock(return_value=link),
        get_device=Mock(return_value=endpoint),
        usb=SimpleNamespace(util=SimpleNamespace(dispose_resources=Mock(),get_string=Mock(return_value='USB0123456789'))),
        libusb_package=SimpleNamespace(find_library=Mock()))
    def find(**kwargs):
        return endpoint if kwargs['idProduct']==(0x0336 if service_only else 0x0e8a) else None
    with patch.object(devicesession,'modules',return_value=(transport,SimpleNamespace(MODEL='ILCE-7CM2'))), \
         patch.object(usb.core,'find',side_effect=find), \
         patch.object(usb.backend.libusb1,'get_backend',return_value=Mock()):
        assert camera._connect() is camera
    selected=transport.RecoverySession if service_only else transport.Session
    other=transport.Session if service_only else transport.RecoverySession
    selected.assert_called_once_with(None if recorded=='fresh' else identity)
    other.assert_not_called()
    link.__enter__.assert_called_once()
    assert camera.identity==identity


@pytest.mark.parametrize('scenario', ['reenumeration','absent','usb_error','session_error'])
def test_actual_sound_connector_waits_only_for_enumeration_never_replays_auth(scenario):
    from looktool import devicesession
    import usb.core
    import usb.backend.libusb1
    identity=dict(model='ILCE-7CM2',serial_sha256='synthetic')
    camera=trial.factory(identity)
    link=Mock(identity=identity)
    link.__enter__=Mock(return_value=link)
    transport=SimpleNamespace(Session=Mock(return_value=link),RecoverySession=Mock(return_value=link),
        usb=SimpleNamespace(util=SimpleNamespace(dispose_resources=Mock())),
        libusb_package=SimpleNamespace(find_library=Mock()))
    if scenario=='session_error':link.__enter__.side_effect=ValueError('현재 카메라 본체가 백업한 본체와 다릅니다')
    calls=[]
    def find(**kwargs):
        calls.append(kwargs['idProduct'])
        if scenario=='usb_error':raise TimeoutError('USB reply missing')
        if scenario=='absent':return None
        if scenario=='reenumeration' and len(calls)<3:
            assert not transport.Session.called
            return None
        return Mock()
    with patch.object(devicesession,'modules',return_value=(transport,SimpleNamespace())), \
         patch.object(usb.core,'find',side_effect=find), \
         patch.object(usb.backend.libusb1,'get_backend',return_value=Mock()), \
         patch.object(trial.time,'sleep') as sleep, \
         patch.object(trial.time,'monotonic',side_effect=[0,16] if scenario=='absent' else [0]*10):
        if scenario=='reenumeration':
            camera._connect()
            assert calls==[0x0e8a,0x0336,0x0e8a]
            sleep.assert_called_once_with(.5)
            transport.Session.assert_called_once_with(identity)
        else:
            with pytest.raises((ValueError,TimeoutError)):camera._connect()
            sleep.assert_not_called()
    transport.RecoverySession.assert_not_called()
    assert link.__enter__.call_count==(1 if scenario=='session_error' or scenario=='reenumeration' else 0)


def test_readonly_connection_check_preserves_unresolved_workspace(failed_on):
    import candidate
    root,state,job,value=failed_on
    before=(root/'sound.private.json').read_bytes()
    camera=Mock()
    camera.__enter__=Mock(return_value=camera)
    camera.__exit__=Mock(return_value=False)
    cp=bytearray(candidate.OFFSET+2)
    cp[candidate.SELECTOR_TABLE:candidate.SELECTOR_TABLE+5]=candidate.TABLE_BEFORE
    cp[candidate.GATE:candidate.GATE+20]=candidate.LEGACY_GATE
    camera.read.side_effect=[b'AP',bytes(cp)]
    with patch.object(trial,'factory',return_value=camera) as factory, \
         patch.object(candidate,'guard') as guard:
        result=cli.check_connection(state)
    factory.assert_called_once_with(value['identity'])
    assert [call.args[0] for call in camera.read.call_args_list]==['/usr/lib/appFw.so','/cp/cpapp-b.bin']
    assert not camera.temp_write.called
    assert result['sound_support_enabled_on_disk'] is True
    assert result['camera_firmware_written'] is False
    assert (root/'sound.private.json').read_bytes()==before
