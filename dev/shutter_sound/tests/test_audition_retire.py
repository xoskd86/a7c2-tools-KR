"""An audition that never reached its native worker must not block saving."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import sound_cli as cli
import trial


def pending_audition(tmp_path,journal,report=False,**extra):
    root,state=cli.workspace(tmp_path/'sound',True,tmp_path/'backup')
    job=root/'jobs'/'20261022T015656Z-audition-x';job.mkdir(parents=True)
    trial.save(job,dict(schema='a7c2-empty-sound-buffer-trial/1',phase='needs_review',
        identity=dict(model='ILCE-7CM2',serial_sha256='body'),camera_firmware_written=False,
        wire_format=0,journal=journal,**extra))
    if report:(job/'report.private.bin').write_bytes(bytes(208))
    state.update(audio_job=str(job),pending=dict(command='audition',job=str(job),outcome='needs_review'))
    cli.save(root,state)
    return root,state


def test_field_case_with_empty_journal_is_retired(tmp_path,capsys):
    # Field case: the audition stopped while reading an unmatched /tmp marker.
    root,state=pending_audition(tmp_path,[])
    cli.require_idle(state,root)
    assert state['pending'] is None and state['audio_job'] is None
    assert state['history'][-1]['reason']=='stopped_before_native_worker'
    assert '어떤 사운드도 등록하지 않았습니다' in capsys.readouterr().out


@pytest.mark.parametrize('journal,report,extra',[
    ([dict(event='native_stage_intent',stage='shutter.staged.so',mode=0)],False,{}),
    ([dict(event='native_intent',stage='shutter.staged.so',mode=0)],False,{}),
    ([],True,{}),
    ([],False,{'result':{'phase':1}}),
],ids=['staged','invoked','report-present','result-recorded'])
def test_any_native_evidence_keeps_blocking(tmp_path,journal,report,extra):
    root,state=pending_audition(tmp_path,journal,report,**extra)
    with pytest.raises(ValueError,match='unresolved'):
        cli.require_idle(state,root)
    assert state['pending']['command']=='audition'
