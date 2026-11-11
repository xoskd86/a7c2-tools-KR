"""Restore original sound is not blocked by a stopped or missing latest record."""
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sound_cli as cli
import trial
import boot_trial

IDENTITY = dict(model='ILCE-7CM2', serial_sha256='synthetic')


def install_job(root, name, phase):
    job = root / 'jobs' / name
    job.mkdir(parents=True)
    trial.save(job, dict(phase=phase, identity=IDENTITY, components={'files': 'files.so'}, journal=[]))
    return job


def test_restore_uses_the_newest_completed_install_when_the_latest_stopped(tmp_path):
    root, state = cli.workspace(tmp_path / 'sound', True, tmp_path / 'backup')
    good = install_job(root, 'a-persistent', 'installed_cold_test_pending')
    broken = install_job(root, 'b-persistent', 'needs_review')
    state.update(boot_job=str(broken), pending=dict(command='restore-sound', job=str(broken), outcome='needs_review'))
    cli.save(root, state)
    with patch.object(boot_trial, 'restore', return_value={'phase': 'restored_cold_test_pending'}) as restore:
        cli.restore_sound(root, state)
    assert restore.call_args.args[0] == good
    assert state['boot_job'] == str(good)
    assert any(h.get('job') == str(broken) for h in state['history'])
    assert cli.load_job(broken)['phase'] == 'needs_review'  # archived unchanged, never repeated


def test_restore_without_any_usable_record_points_to_factory_restore(tmp_path):
    root, state = cli.workspace(tmp_path / 'sound', True, tmp_path / 'backup')
    install_job(root, 'only', 'needs_review')
    with patch.object(trial, 'factory', side_effect=AssertionError('No camera')), \
         patch.object(boot_trial, 'restore', side_effect=AssertionError('No restore')):
        with pytest.raises(ValueError, match='공장 기능 복원'):
            cli.restore_sound(root, state)
